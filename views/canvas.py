"""채널 캔버스 (#30 에서 bot.py 를 나눔)."""
import asyncio, datetime, hashlib, time
import config
import core
from common import BOT, CANVAS, CHANNEL, HERE, LABEL, PLEVEL, QUEUE, fill, log, plevel  # noqa: E402,F401
from docs import BASE_KPI, current_stage, deadline, due_text, feature_state, load_features, load_initiatives, load_stages, load_team, load_why, md_table, metrics, project_info, schedule, vacant_roles  # noqa: E402,F401
from slack import api  # noqa: E402,F401
from store import STATE, chan, open_cards, save  # noqa: E402,F401
from store import ref as _ref  # noqa: E402,F401


def ref(no, width=14):
    """캔버스는 마크다운이라 Slack 링크 표기가 글자로 찍힌다 — 여기서는 글자만 쓴다 (링크는 link_of 가 단다)."""
    return _ref(no, width, link=False)


_CANVAS = {"last": None, "pending": False}


GAP = 120          # 캔버스 최소 간격 (초). 조용하면 5초 뒤 반영, 몰아칠 때만 2분에 한 번 모아서
#                    10분이었다 — 유령 칸을 줄이려고. 그런데 지문 비교가 들어오면서 「같으면 안 쓴다」 가
#                    이미 헛쓰기를 막고 있어서, 10분은 진짜 바뀐 것까지 늦추기만 했다 (2026-09-20 사용자 지적)


async def render_canvas(s, wait=5):
    """캔버스 갱신 요청을 모은다. 캔버스를 쓸 때마다 열어 둔 화면에 옛 칸이 쌓인다 — Slack 앱의 문제라 봇이 없앨 수 없다
    (9/20 실험: 통째 교체 → 열린 화면 칸 188→214 · 칸 하나만 교체 → 표 모양이 깨져 그대로 남음). 그래서 쓰는 횟수를 줄인다:
    ① 내용이 같으면 안 쓴다(지문 저장) ② 최소 GAP 간격으로 모아서 한 번. 앱 홈은 기다리지 않고 바로 갱신.

    **부르는 쪽을 붙잡지 않는다.** 기다리는 일은 뒤에서 돈다 — 예전에는 이 함수를 await 하면 최대 10분을
    그 자리에서 서 있었고, 2026-09-20 에 카드 하나 만드는 통로가 10분 막혔다. 호출처마다 조심하는 대신
    여기서 막는다."""
    if _CANVAS["pending"]:
        return
    _CANVAS["pending"] = True
    asyncio.create_task(_render_canvas_later(s, wait))


async def _render_canvas_later(s, wait):
    """모으는 시간만큼 기다렸다가 쓴다. 이름에 render_canvas 가 들어가야 한다 — 끌 때 common.drain 이 이걸 보고 건너뛴다."""
    try:
        await asyncio.sleep(wait)
        left = GAP - (time.time() - STATE.get("canvas_at", 0))
        if left > 0:
            await refresh_homes(s)
            await asyncio.sleep(left)
    finally:
        _CANVAS["pending"] = False
    await _render_canvas(s)


def _repo_line():
    """기록이 어디로 올라가는지 한 줄 (2026-09-22 사장님: 「레포 정보를 현황판에도 노출하는게
    맞을거 같은데」). 안 붙였으면 빈 글자 — 없는 것을 있다고 하지 않는다."""
    import config
    from common import CANVAS, PROJECTS
    hit = next((p for p in PROJECTS if p.get("canvas") == CANVAS), None)
    repo = (hit or {}).get("repo") or config.GITHUB.get("repo")
    if repo:
        return f"\n📦 기록·할 일 → [{repo}](https://github.com/{repo})\n"
    url = config.CFG.get("git_remote")
    return f"\n📦 기록 → `{url}`\n" if url else ""


def _canvas_title():
    """캔버스 이름 — 그 프로젝트 방 이름. 방이 여럿이면 어느 작업판인지 여기서 갈린다."""
    from common import CANVAS, ISSUE_NAME, PROJECTS
    hit = next((p for p in PROJECTS if p.get("canvas") == CANVAS), None)
    name = (hit or {}).get("name") or ISSUE_NAME or "프로젝트"
    return f"📋 {name} 작업판"


async def render_canvas_now(s):
    """🔄 버튼 — 기다리지 않고 **반드시** 다시 쓴다 (2026-09-22).

    예전에는 「내용이 같으면 안 쓴다」 를 이 버튼에도 걸었다. 그래서 **밖에서 캔버스가
    망가지면 고칠 방법이 없었다** — 봇은 자기가 그린 지문만 보고 「그대로다」 라고 믿는다.
    사람이 실수로 지웠을 때도 같다. 지문을 지워서 이번 한 번은 반드시 쓰게 한다.

    (2026-09-22 실측: API 로 캔버스를 통째로 덮어쓴 뒤 🔄 를 눌러도 되살아나지 않았다.)
    """
    STATE.pop("canvas_hash", None)
    _CANVAS["last"] = ""
    await _render_canvas(s)


async def _render_canvas(s):
    """캔버스 = 프로젝트 한눈에. 목표·진행률·누가 뭘 맡나·로드맵 요약만. 자세한 건 앱 홈 버튼."""
    cards = sorted(STATE["cards"].values(), key=lambda c: c["no"])
    for c in list(cards) + list(STATE.get("meetings", {}).values()):   # 카드로 뛰는 링크
        if not c.get("permalink"):
            c["permalink"] = (await api(s, "chat.getPermalink", channel=chan(c),
                                        message_ts=c["card_ts"])).get("permalink", "")
    team = load_team()
    today = datetime.date.today()
    goal, draft = project_info()
    end = deadline()
    when = f"판단일 {end} (D-{(end - today).days})" if end else "판단일 미정"

    # 👥 누가 뭘 맡나 — 역할과 맡은 일(우선순위순)을 한 표에
    def item(c):
        return (f"{PLEVEL[plevel(c)].split()[0]} {link_of(c, text=ref(c['no'], 14))} `{LABEL[c['status']]}`"
                + (f" {due_text(c)}" if due_text(c) else ""))

    rows = []
    for uid, t in team.items():
        mine = sorted([c for c in open_cards() if c.get("assignee") == uid],
                      key=lambda c: ({"doing": 0, "blocked": 1}.get(c["status"], 2), plevel(c), c.get("rank", 99)))
        rows.append(f"| ![](@{uid}) · **{t['role']}** | {t['areas']} | {' · '.join(item(c) for c in mine) or '없음'} |")
    for role, who, state in vacant_roles():
        rows.append(f"| {role} · **빈 역할** | 대행 {who} ({state}) | - |")
    pool = sorted([c for c in open_cards() if not c.get("assignee")], key=lambda c: (plevel(c), c.get("rank", 99)))
    if pool:
        rows.append(f"| 🙋 **담당 없는 일** | 자리가 나면 추천 담당에게 | {len(pool)}건 — "
                    + " · ".join(item(c) for c in pool[:4]) + (" …" if len(pool) > 4 else "") + " |")
    people = "\n".join(rows) or "| - | team.md 에 팀원을 적어 주세요 | - |"

    # 🎯 할 일마다 — 바뀌는 것 · 진척(그 할 일에 붙은 이슈의 완료율) · 성공 기준
    feats, m = load_features(), metrics()
    fstate = feature_state()
    def prog_of(name):                 # 진척 = 그 할 일의 기능 중 「있음」 비율 (카드 없이 만든 기능도 있다)
        fs = [f for f, i in feats.items() if i == name]
        fin = sum(fstate.get(f) == "있음" for f in fs)
        p = round(100 * fin / len(fs)) if fs else 0
        doing = sum(fstate.get(f) == "만드는 중" for f in fs)
        return (f"{'▓' * round(p / 10)}{'░' * (10 - round(p / 10))} 기능 {fin}/{len(fs)}"
                + (f" · 만드는 중 {doing}" if doing else "")) if fs else "-"
    rows_i = []
    for i in load_initiatives():
        now_v, ok = m.get(i["kpi"], ("-", False))
        rows_i.append(f"| **{i['name']}** | {i['after']} | {prog_of(i['name'])} | "
                      f"{i['kpi']} **{now_v}** / {i['target']} {'✅' if ok else '⏳'} |")
    # 「기반」 은 할 일 표에 없다 — Pain point 와 짝이 아니라 그 넷을 만들려면 있어야 하는 층이라서.
    # 재지 않기로 했으므로(사장님 결정 9/20) 성공 기준 자리에 왜 비어 있는지를 적는다. 상세도 같은 말을 쓴다
    rows_i.append(f"| 기반 | PoC 전에 있어야 하는 것 (상시 실행·권한·안전) | {prog_of('기반')} | {BASE_KPI} |")
    kpi = "\n".join(rows_i)
    double = "\n".join(f"| {n} | **{m.get(n, ('-',))[0]}** | {tg} |"
                        for n, _, tg in md_table("project.md", "### 2배를 재는 법", 3))

    # 🗺️ 로드맵 — 단계별 진행률 (단계에 속한 이슈의 완료율)
    road, cur = [], current_stage()
    for st in load_stages():
        cs = [c for c in STATE["cards"].values() if c.get("stage") == st["name"] and c["status"] != "cancelled"]
        fin = sum(c["status"] == "done" for c in cs)
        p = round(100 * fin / len(cs)) if cs else 0
        prog = f"{'▓' * round(p / 10)}{'░' * (10 - round(p / 10))} {p}% ({fin}/{len(cs)})" if cs else \
            ("✅ 끝남" if st["state"] == "끝남" else "-")
        doing = sorted([c for c in cs if c["status"] != "done" and c.get("assignee")],
                       key=lambda c: (c["status"] != "doing", plevel(c), c.get("rank", 99)))
        mark = "▶ " if st["name"] == cur else "✅ " if st["state"] == "끝남" else ""
        road.append(f"| {mark}**{st['name']}** | {f'~{st["end"].month}/{st["end"].day}' if st['end'] else '미정'} | {prog} | "
                    f"{' · '.join(link_of(c, text=ref(c['no'], 12)) for c in doing[:3]) or (st['gate'] if st['state'] == '끝남' else st['what'])} |")
    outside = [c for c in open_cards() if c.get("stage") not in {x["name"] for x in load_stages()}]
    road.append(f"| 💤 단계 밖 (나중) | | {len(outside)}건 | 단계가 정해지면 들어가요 |")
    road = "\n".join(road)

    notes = []
    blocked = [c for c in open_cards() if c["status"] == "blocked"]
    if blocked:
        notes.append("⛔ **막힘** " + " · ".join(link_of(c, text=ref(c["no"], 16)) for c in blocked))
    # 🔒 는 ⛔ 와 다른 것이다 — ⛔ 는 사람이 「나 막혔어요」 로 바꾼 상태, 🔒 는 선행이 아직 안 끝난 것 (#68).
    # 앞선 일이 끝나면 저절로 빠진다 (core.relations 가 끝난 선행을 대기에서 뺀다).
    waits = [(c, core.relations(c, STATE["cards"])[0]) for c in open_cards()]
    waits = [(c, w) for c, w in waits if w]
    if waits:
        # 기다리는 쪽만 제목을 달고 선행은 번호만 — 양쪽 다 제목을 달면 한 줄이 화면을 넘어간다
        notes.append("🔒 **앞선 일 기다림** " + " · ".join(
            f"{link_of(c, text=ref(c['no'], 16))} ← {' '.join('#%d' % x['no'] for x in w)}"
            for c, w in sorted(waits, key=lambda t: t[0]["no"])[:5])
            + (f" …외 {len(waits) - 5}건" if len(waits) > 5 else ""))
    sch = schedule()
    if sch:
        notes.append("⏱️ **일정** " + " · ".join(
            f"{w} " + ("✅ 맞음" if need <= have else f"⚠️ {round(need - have, 1)}시간 넘침")
            for w, need, have, _ in sch))

    # 🧩 기능 — 할 일 → 기능 → 이슈가 한 줄로 이어진다
    SMARK = {"있음": "✅ 있음", "만드는 중": "🔧 만드는 중", "예정": "▫️ 예정"}
    frows, order = [], [i["name"] for i in load_initiatives()] + ["기반"]
    for f, ini in sorted(feats.items(), key=lambda x: order.index(x[1]) if x[1] in order else 99):
        cs = [c for c in STATE["cards"].values() if c.get("feature") == f and c["status"] != "cancelled"]
        live_ = sorted([c for c in cs if c["status"] != "done"], key=lambda c: c.get("rank", 99))
        fin = len(cs) - len(live_)
        issues = " · ".join(link_of(c, text=ref(c["no"], 12)) for c in live_[:4]) + (f" …외 {len(live_) - 4}" if len(live_) > 4 else "")
        tail = " · ".join(x for x in [issues, f"✅ {fin}건 끝남" if fin else ""] if x) or "-"
        frows.append(f"| {ini} | **{f}** | {SMARK.get(fstate.get(f), '-')} | {tail} |")
    feature_tbl = "\n".join(frows)

    # 🗓️ 회의록 — 모아서 위에
    ms = sorted(STATE.get("meetings", {}).values(), key=lambda x: x["date"], reverse=True)
    meet_tbl = "\n".join(
        f"| {m['date']} | " + (f"[{m['title']}]({m['permalink']})" if m.get("permalink") else m["title"])
        + f" | {ref(m['issue'], 14) if m.get('issue') else '-'} | "
        + (f"📝 `meetings/{m['file']}`" if m.get("file") else "예정") + " |" for m in ms) \
        or "| - | 아직 없어요 — `/meeting 제목` 으로 만들면 여기에 모여요 | - | - |"
    why = "\n".join(f"- {x}" for x in load_why()) or "- _project.md 「## 왜 (문제)」 를 채워 주세요_"

    head = fill((HERE / "canvas_head.md").read_text(encoding="utf-8"))
    intro = head.split("---", 1)[0].strip() if "---" in head else ""
    # 맨 위에 제목 한 줄 — **채널 탭 이름과는 다른 것이다.**
    #
    # Slack 캔버스는 **제목이 본문과 별개 칸**이고, 그 칸이 탭 이름이 된다. 그리고
    # **채널 캔버스는 그 칸을 API 로 못 정한다** (2026-09-22 실측: `canvases.edit` 에 title 을
    # 얹으면 invalid_arguments. `title` 은 따로 떠 있는 캔버스를 만드는 `canvases.create` 에만 있다).
    # 그래서 탭에는 「제목 없음」 이 뜨고, **사람이 한 번 적어야 한다** — 앱 아이콘과 같은 경우다.
    #
    # 이 H1 은 그 대신 **본문 맨 위에 이름을 남긴다.** 탭을 안 고친 팀도 캔버스를 열면
    # 어느 프로젝트의 작업판인지 바로 안다. 프로젝트마다 다르므로 문서가 아니라 여기서 붙인다.
    md = f"""# {_canvas_title()}

{intro}
{_repo_line()}
## 😣 Pain point

{why}

## 🎯 목표{" (초안 — PM 확정 필요)" if draft else ""}

**{goal}**

| 할 일 | 바뀌는 것 | 진척 | 성공 기준 · 지금 / 목표 |
| --- | --- | --- | --- |
{kpi}

## 🧩 기능 — 할 일마다 무엇을 만드나 · 연결된 이슈

| 할 일 | 기능 | 상태 | 이슈 |
| --- | --- | --- | --- |
{feature_tbl}

## 🗺️ 로드맵 — {when}

| 단계 | 끝나는 날 | 진행 | 하는 중 |
| --- | --- | --- | --- |
{road}

{chr(10).join("> " + n for n in notes)}

## 👥 누가 뭘 맡나

| 사람 · 역할 | 맡는 영역 | 맡은 일 (우선순위순) |
| --- | --- | --- |
{people}

> 🔴 P1 긴급 · 🟠 P2 높음 · 🟡 P3 보통 · ⚪ P4 낮음 — 상태는 `대기` `진행 중` `보류` `완료`. 사람마다 맡은 일은 5개까지(진행 3 + 대기 {QUEUE}), 나머지는 🙋 담당 없는 일. 💡 = AI 추천 (카드의 **👍 맡을게요** 로 확정)

## 🗓️ 회의록

| 언제 | 회의 | 이슈 | 기록 |
| --- | --- | --- | --- |
{meet_tbl}

---

📂 목록 · 규칙 · 조사 자료는 **{BOT} 홈** 버튼에서 (🔴 우선순위별 · 🙋 담당 없는 일 · 🧮 규칙 · 🔍 벤치마킹 …)

_봇이 자동으로 채웁니다 · 바뀐 게 있을 때만 다시 그려요_
"""
    digest_ = hashlib.sha1(md.encode("utf-8")).hexdigest()
    # 같으면 캔버스를 건드리지 않는다 — 재시작해도 잊지 않게 지문을 저장한다. 통째로 바꿀 때마다
    # 열어 둔 화면에 지운 칸이 쌓인다 (9/20 재시작 20번 뒤 「작업판에 중복」 제보)
    if md == _CANVAS["last"] or STATE.get("canvas_hash") == digest_:
        _CANVAS["last"] = md
        await refresh_homes(s)
        return
    r = await api(s, "canvases.edit", body={"canvas_id": CANVAS, "changes": [
        {"operation": "replace", "document_content": {"type": "markdown", "markdown": md}}]})
    if r.get("ok"):                          # 실패했으면 기억하지 않는다 — 다음에 다시 그린다
        _CANVAS["last"], STATE["canvas_hash"], STATE["canvas_at"] = md, digest_, time.time()
        save()
        log("캔버스 다시 그림")
    await refresh_homes(s)


HOME_LINK = f"https://slack.com/app_redirect?app={config.CFG.get('app_id', '')}&team={config.CFG.get('workspace', '')}"   # 캔버스엔 버튼을 못 넣어서 홈으로 보낸다


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from views.card import link_of  # noqa: E402,F401
from views.home import refresh_homes  # noqa: E402,F401
