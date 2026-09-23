"""앱 홈 · 팝업 — 화면만 만든다 (#30 에서 bot.py 를 나눔)."""
import re, datetime
import core
from messages import say
from common import BOARD, HERE, LABEL, MARK, PLEVEL, PNAME, PROJECTS, QUEUE, REQUEST, fill, mday, plevel  # noqa: E402,F401
from docs import current_stage, deadline, project_info, due_text, feature_state, load_features, load_initiatives, load_stages, load_team, schedule  # noqa: E402,F401
from slack import api  # noqa: E402,F401
from store import open_cards, STATE, day_meta, project_of, ref, timeline  # noqa: E402,F401


# 자주 여는 다섯은 단추로, 나머지는 ⋯ 메뉴로 (Slack 오버플로우는 다섯 개까지).
# 고르는 상자(static_select)는 입력 칸처럼 보여서 어색하다 (사장님 지적 2026-09-20)
MAIN = {"issues": "📋 할 일 목록", "board": "📌 작업 보드", "timeline": "📆 날짜별 진행",
        "backlog": "🙋 담당 없는 일", "stages": "🗺️ 로드맵"}
MORE = {"roadmap": "🔴 우선순위별", "features": "🧩 기능별", "schedule": "⏱️ 일정",
        "meetings": "🗓️ 회의", "howto": "❓ 사용법 · 순서 규칙"}
DETAIL = {**MAIN, **MORE, "benchmark": "🔍 벤치마킹", "rules": "🧮 순서 규칙"}


def project_line(box=None):
    """프로젝트가 어떻게 가고 있나 한 칸 — 목표 · 판단일 · 지금 단계 진행률 · 막힘.

    캔버스에만 있던 것을 홈으로 끌어온다. 앱 홈을 열었을 때 「우리 어디쯤인가」 가 안 보이면
    목록만 잔뜩 있는 화면이 된다 (사장님 지적 2026-09-20).
    """
    import datetime
    goal, _ = project_info()
    end, cur = deadline(), current_stage()
    cs = [c for c in STATE["cards"].values() if c.get("stage") == cur and c["status"] != "cancelled"]
    fin = sum(c["status"] == "done" for c in cs)
    pct = round(100 * fin / len(cs)) if cs else 0
    bar = "▓" * round(pct / 10) + "░" * (10 - round(pct / 10))
    blocked = [c for c in open_cards() if c["status"] == "blocked"]
    rows = [f"🎯 {goal or '목표 미정'}"]
    if end:
        rows.append(f"📅 판단일 {end} (D-{(end - datetime.date.today()).days})")
    rows.append(f"{bar} {cur or '단계 미정'} {pct}% ({fin}/{len(cs)})")
    rows.append(f"⛔ 막힘 {len(blocked)}건" if blocked else "⛔ 막힌 일 없음")
    if box:
        return box("", rows)
    return {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(rows)[:2900]}}


def detail_groups(key):
    """앱 홈 버튼 → 팝업 내용. [(제목, 설명, [줄])] — 묶음마다 제목·설명·목록·구분선으로 그린다."""
    team = load_team()
    nm = lambda u: team.get(u, {}).get("name", "미정")
    item = lambda c, extra="": f"{MARK.get(c['status'], '')} {link_of(c, True, ref(c['no'], 26, link=False))}  ·  {extra}"
    groups = []
    if key == "benchmark" and not (HERE / "research" / "benchmark.md").exists():
        groups.append(("벤치마킹", "", ["아직 조사 자료가 없어요. 데이터 폴더의 `research/benchmark.md` 에 쓰면 여기에 보여요"]))
    elif key == "benchmark":                  # research/benchmark.md 를 팝업용으로 — 표는 줄 목록으로 바꾼다
        body = (HERE / "research" / "benchmark.md").read_text(encoding="utf-8").split("---", 2)[-1]
        for part in re.split(r"\n## ", "\n" + body)[1:]:
            title, *rest = part.split("\n")
            lines, meta = [], ""
            for ln in rest:
                ln = ln.strip().replace("**", "*")
                if ln.startswith("|"):
                    cells = [x.strip() for x in ln.strip("|").split("|")]
                    if cells[0].startswith("---"):
                        continue
                    if not meta:
                        meta = " / ".join(cells)                 # 머리줄은 설명으로
                        continue
                    lines.append(f"• *{cells[0]}* — " + " · ".join(cells[1:]))
                elif ln.startswith("- "):
                    lines.append("• " + ln[2:])
                elif ln and not ln.startswith("#"):
                    lines.append(ln)
            groups.append((title.strip(), meta, lines))
    elif key == "features":
        feats = load_features()
        order = [i["name"] for i in load_initiatives()] + ["기반"]
        for f, ini in sorted(feats.items(), key=lambda x: order.index(x[1]) if x[1] in order else 99):
            cs = sorted([c for c in STATE["cards"].values() if c.get("feature") == f and c["status"] != "cancelled"],
                        key=lambda c: (c["status"] == "done", c.get("rank", 99)))
            fin = sum(c["status"] == "done" for c in cs)
            st = {"있음": "✅", "만드는 중": "🔧", "예정": "▫️"}.get(feature_state().get(f), "")
            groups.append((f"{st} {f}  (할 일 {fin}/{len(cs)})", f"할 일: {ini} · {feature_state().get(f, '')}",
                           [item(c, nm(c.get("assignee") or c.get("suggested"))) for c in cs]))
        rest = [c for c in STATE["cards"].values() if c.get("feature") not in feats and c["status"] != "cancelled"]
        if rest:
            groups.append((f"기능 미정 ({len(rest)})", "카드 ✏️ 에서 기능을 골라 주세요",
                           [item(c, nm(c.get("assignee") or c.get("suggested"))) for c in rest]))
    elif key == "stages":
        names = {x["name"] for x in load_stages()}
        cur = current_stage()
        for st in load_stages() + [{"name": None, "end": None, "state": "", "what": "단계가 정해지면 들어가요", "gate": ""}]:
            cs = sorted([c for c in STATE["cards"].values() if c["status"] != "cancelled" and
                         (c.get("stage") == st["name"] if st["name"] else c.get("stage") not in names)],
                        key=lambda c: (c["status"] == "done", c.get("rank", 99)))
            fin = sum(c["status"] == "done" for c in cs)
            if st["name"]:
                badge = "✅ " if st["state"] == "끝남" else "▶ " if st["name"] == cur else ""
                when = f"~{st['end'].month}/{st['end'].day}" if st["end"] else "날짜 미정"
                title = f"{badge}{st['name']}  ({when}" + (f" · {fin}/{len(cs)} 끝남)" if cs else ")")
                meta = f"{st['what']}\n*통과 기준* {st['gate']}"
            else:
                title, meta = f"💤 단계 밖 — 나중 ({len(cs)})", st["what"]
            groups.append((title, meta, [item(c, nm(c.get("assignee") or c.get("suggested"))) for c in cs]))
    elif key == "roadmap":                   # 우선순위별 — 담당 있는 일 · 없는 일 함께
        for lv in PLEVEL:
            cs = sorted([c for c in open_cards() if plevel(c) == lv], key=lambda c: (c["status"] != "doing", c.get("rank", 99)))
            groups.append((f"{PLEVEL[lv]} {PNAME[lv]} ({len(cs)})", "", [
                item(c, f"{LABEL[c['status']]} · " + (f"담당 {nm(c.get('assignee'))}" if c.get("assignee") else
                        f"담당 없음 (추천 {nm(c.get('suggested'))})") + (f" · {due_text(c)}" if due_text(c) else ""))
                for c in cs]))
    elif key == "timeline":                  # 날짜별 진행 — 날마다 무엇이 움직였고, 끝난 일은 계획대로였나
        for d, es in list(timeline(STATE["cards"].values()).items())[:10]:
            meta = day_meta(es)
            groups.append((mday(d) + ("  오늘" if d == datetime.date.today().isoformat() else ""), meta,
                           [f"{e.get('icon', '•')} {ref(c['no'], 18)} — {e['what']} · {e['who']}"
                            + (f" ({e['reason']})" if e.get("reason") else "") for c, e in es if e.get("what") != "만듦"]
                           + ([f"🎫 새 할 일: " + ", ".join(ref(c["no"], 12) for c, e in [x for x in es if x[1].get("what") == "만듦"][:8])
                               + (f" 외 {n - 8}건" if (n := sum(e.get("what") == "만듦" for _, e in es)) > 8 else "")]
                              if any(e.get("what") == "만듦" for _, e in es) else [])))
    elif key == "backlog":                   # 담당 없는 일 — 자리가 나면 추천 담당에게
        cs = sorted([c for c in open_cards() if not c.get("assignee")], key=lambda c: (plevel(c), c.get("rank", 99)))
        groups.append((f"담당 없는 일 ({len(cs)})", "사람마다 맡은 일이 5개(진행 3 + 대기 2)를 넘지 않게 나머지는 담당 없이 둬요. 하나 끝나면 우선순위 높은 일부터 추천 담당에게 가요",
                       [item(c, f"{PLEVEL[plevel(c)]} · 추천 {nm(c.get('suggested'))}") for c in cs]))
    elif key == "board":
        for k in BOARD:
            cs = sorted([c for c in STATE["cards"].values() if c["status"] == k and (c.get("assignee") or k == "done")],
                        key=lambda c: c["no"])
            groups.append((f"{LABEL[k]} ({len(cs)})", "", [item(c, nm(c.get("assignee"))) for c in cs]))
    elif key == "issues":
        for k, t in (("open", "진행 중·할 일"), ("done", "끝남"), ("cancelled", "취소")):
            cs = sorted([c for c in STATE["cards"].values()
                         if (c["status"] == k if k != "open" else c["status"] not in ("done", "cancelled"))],
                        key=lambda c: c["no"])
            groups.append((f"{t} ({len(cs)})", "", [
                item(c, (c.get("cancel_reason") or "") if k == "cancelled" else
                     nm(c.get("assignee")) + (f" · <{c['issue_url']}|GitHub>" if c.get("issue_url") else ""))
                for c in cs]))
    elif key == "schedule":
        end = deadline()
        lines = []
        for w, need, have, cut in schedule():
            ok = "✅ 맞음" if need <= have else f"⚠️ {round(need - have, 1)}시간 넘침"
            lines.append(f"*{w}*  필요 {need}시간 / 가능 {have}시간 — {ok}"
                         + (("\n        미룰 후보: " + ", ".join(ref(c["no"], 16) for c in cut)) if cut else ""))
        groups.append((f"판단일 {end}" if end else "판단일 미정",
                       "필요 = 예상 시간 × AI 할인 (🤖 ×0.3 · 🤝 ×0.6 · 🧑 ×1.0) · 가능 = team.md 주당 시간 × 남은 주", lines))
    elif key == "meetings":
        # **없는 명령을 안내하지 않는다** — 여기와 캔버스가 `/meeting 제목` 이라고 적어
        # 두었는데 그런 슬래시 명령은 **만든 적이 없다** (2026-09-23 발견). 안내가 거짓말이면
        # 사람은 한 번 해 보고 안 쓴다. 이 저장소가 같은 모양으로 여러 번 넘어졌다
        ms = sorted(STATE.get("meetings", {}).values(), key=lambda x: x["date"], reverse=True)
        groups.append((f"회의 ({len(ms)})", "새 회의는 저에게 「회의 만들기」 라고 하시면 한 칸씩 여쭤볼게요", [
            f"🗓️ {m['date']}  " + (f"<{m['permalink']}|{m['title']}>" if m.get("permalink") else m["title"])
            + (f"  ·  🔁 {core.meet_label(m)}" if (m.get("every") or "once") != "once" else "")
            + (f"  ·  할 일 #{m['issue']}" if m.get("issue") else "") + ("  ·  📝 기록됨" if m.get("file") else "  ·  예정")
            for m in ms]))
    elif key == "rules":
        groups.append(("우선순위", "AI는 숫자만 추정하고, 우선순위는 계산식이 정해요. PM이 정하면 그게 우선이에요",
                       ["*점수 = (가치 + 긴급 + 목표 적합 × 2) ÷ 노력*  (각 1~5)",
                        "🔴 P1 긴급 — 사람만 붙인다 (PoC 전 필수 등). AI는 P2~P4",
                        "🟠 P2 높음 7점 이상 · 🟡 P3 보통 4~7 · ⚪ P4 낮음 4 미만 · 로드맵 단계 밖·중복은 P4"]))
        groups.append(("상태", "", ["`대기` 시작 전 · `진행 중` · `보류` 막혔거나 멈춤 · `완료` · `취소`",
                                   "카드에 👀 = 진행 중 · ⛔ = 보류 · ✅ = 완료, 또는 ⋯ → ✏️"]))
        groups.append(("담당", "", [f"사람마다 맡은 일은 5개까지 (진행 3 + 대기 {QUEUE}) — 우선순위 높은 것부터",
                                   "나머지는 🙋 담당 없는 일. 하나 끝나면 우선순위 높은 일부터 추천 담당에게 자동으로",
                                   "💡 AI 추천 → 본인이 👍 맡을게요로 확정 · 기다리기 싫으면 ⋯ → ✋ 내가 가져갈게요"]))
        groups.append(("목표일", "", ["📅 완료 목표일 — 배정되면 AI가 예상 시간으로 먼저 제안(💡), 본인이 ⋯ → ✏️ 에서 고친다", "⚠️ = 목표일 지남"]))
    elif key == "howto":
        head = fill((HERE / "canvas_head.md").read_text(encoding="utf-8"))
        lines = []
        for line in head.split("---", 1)[-1].splitlines():
            cells = [x.strip() for x in line.strip().strip("|").split("|")]
            if line.startswith("|") and len(cells) == 2 and not cells[0].startswith("---") and cells[0] != "하고 싶은 것":
                lines.append(f"*{cells[0]}*\n        {cells[1].replace('**', '*')}")
        groups.append(("이렇게 쓰세요", "카드와 캔버스는 봇이 알아서 고칩니다", lines))
    return groups


def detail_blocks(key):
    """묶음마다 제목(header) · 설명(context) · 목록(section) · 구분선. 목록이 길면 3,000자 단위로 나눈다."""
    blocks = []
    for title, meta, lines in detail_groups(key):
        blocks.append({"type": "header", "text": {"type": "plain_text", "text": title[:150], "emoji": True}})
        if meta:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": meta[:2900]}]})
        buf = ""
        for ln in lines or ["_없어요_"]:
            if len(buf) + len(ln) > 2800:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": buf}})
                buf = ""
            buf += ln + "\n"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": buf}})
        blocks.append({"type": "divider"})
    return blocks[:-1][:100] if blocks else []


async def refresh_homes(s):
    """모든 사람의 앱 홈(내 할 일)을 다시 그린다. 이벤트를 기다리지 않고 먼저 그려 둔다."""
    if "humans" not in STATE:
        us = (await api(s, "users.list", limit=200)).get("members", [])
        STATE["humans"] = [u["id"] for u in us if not u.get("is_bot") and not u.get("deleted")
                           and u["id"] != "USLACKBOT"]
    for uid in STATE["humans"]:
        await publish_home(s, uid)


def plan_when(c, today=None):
    """이 일이 **언제 칸**에 들어가나 — (순서, 이름). 지난 날짜는 「오늘」 에 얹는다:
    못 한 일은 오늘 할 일이다. 매달 다른 말을 만들지 않는다 — 넷이면 충분하다.

    오늘 날짜를 안 주면 **여기서** 읽는다. 이 모듈의 `datetime` 은 골든이 고정하므로,
    `datetime` 을 안 쓰는 쪽(`flows/find.py`)에서도 그냥 부르면 날짜가 고정된다.
    """
    today = today or datetime.date.today()
    try:
        d = datetime.date.fromisoformat(c.get("start") or "")
    except ValueError:
        return 4, "언제 할지 미정"
    if d <= today:
        return 0, "오늘"
    if d == today + datetime.timedelta(days=1):
        return 1, "내일"
    if d <= today + datetime.timedelta(days=(4 - today.weekday()) % 7):
        return 2, "이번 주"
    return 3, "그다음"


def plan_blocks(mine, line, today=None, cap=12):
    """내 계획 — 날짜 칸마다 한 줄씩, 줄마다 날짜 고르개 (2026-09-22 사장님 지시).

    **묻지 않는다.** `core.plan` 이 미리 채워 둔 `start` 를 그대로 보여 주고, 사람은 옮기기만
    한다 — 빈 계획표를 내밀면 채우는 일이 하나 더 느는 것이다 (「많은 걸 요청 하지 말자고」).

    고르개는 `block_id` 로 카드를 찾는다 — 카드 ⚙️ 와 같은 길이라 핸들러가 하나로 끝난다.
    """
    today = today or datetime.date.today()
    rows = sorted(mine, key=lambda c: (plan_when(c, today)[0], c.get("start") or "9999",
                                       plevel(c), c.get("rank", 99)))
    if not rows:
        return [{"type": "section", "text": {"type": "mrkdwn", "text": say("home_today_none")}}]
    out, seen = [], None
    for c in rows[:cap]:
        _, name = plan_when(c, today)
        if name != seen:
            seen = name
            out.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{name}*"}]})
        pick = {"type": "datepicker", "action_id": "set_start",
                "placeholder": {"type": "plain_text", "text": "언제 할까요"}}
        if c.get("start"):
            pick["initial_date"] = c["start"]
        out.append({"type": "section", "block_id": f"card:{c.get('card_ts') or c['no']}",
                    "text": {"type": "mrkdwn", "text": line(c)[:2900]}, "accessory": pick})
    if len(rows) > cap:
        out.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"_…외 {len(rows) - cap}건. 「목록」 이라고 쓰시면 전부 보여요_"}]})
    return out


async def publish_home(s, user):
    """앱 홈 = 내 할 일. 나한테 맡겨진 것, 내가 요청한 것, 다가오는 회의만.

    **아무것도 없으면 현황판을 보여 주지 않는다** (2026-09-22 사장님 지적). 예전에는 갓 깐 팀에도
    「진행률 0% (0/0) · 담당 없음에서 하나 골라 보세요」 를 띄웠는데 — **고를 게 없었다.**
    빈 현황판은 첫인상을 망친다. 그럴 때는 *어디서 시작하는지*만 말해 준다.
    """
    from flows.onboard import nudge
    from flows.status import plan as make_plan
    # **계획은 열 때마다 다시 짠다** — 날짜가 지나면 어제 짠 계획은 이미 틀리다.
    # 사람이 옮긴 날(`start_src="human"`)은 그대로 두므로 여러 번 불러도 안전하다
    make_plan()
    cards = sorted(STATE["cards"].values(), key=lambda c: c["no"])
    if not cards and nudge(user):
        # **네 걸음을 여기 또 적지 않는다** — 그건 DM 몫이다 (2026-09-22). 여기서는
        # 「어디로 가면 되는지」 한 줄과 버튼 하나. Slack 지침: 화면마다 주된 행동 하나
        await api(s, "views.publish", body={"user_id": user, "view": {"type": "home", "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": say("home_empty")}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "❓ 뭘 할 수 있나요", "emoji": True},
                 "action_id": "detail_howto", "value": "howto"}]}]}})
        return
    mine = [c for c in cards if c.get("assignee") == user and c["status"] not in ("done", "cancelled")]
    asked = [c for c in cards if c.get("by_id") == user and c["status"] != "cancelled"]
    meets = [m for m in STATE.get("meetings", {}).values() if m.get("stage", "planned") != "applied"]
    order = {"blocked": 0, "doing": 1, "todo": 2}
    pidx = {"now": 0, "next": 1, "later": 2}
    mine.sort(key=lambda c: (order.get(c["status"], 9), plevel(c), c.get("rank", 99)))

    def line(c):
        """한 줄에 하나 — 카드마다 칸을 쓰면 화면이 금세 지저분해진다 (사장님 지적 2026-09-20).

        「내 일」 은 **사람**에게 딸린 것이라 원래 프로젝트를 가로지른다. 프로젝트가 둘 이상일 때만
        어느 프로젝트인지 붙인다 — 하나뿐인데 붙이면 모든 줄에 같은 말이 반복된다 (#66).
        """
        s = state_line(c).split(" · ")
        tail = " · ".join(x for x in s[:1] + [x for x in s[1:] if x.startswith("📅")])
        where = f"  ·  📁 {project_of(c)['name']}" if len(PROJECTS) > 1 else ""
        return f"{link_of(c, True, ref(c['no'], 30, link=False))}  ·  {tail}{where}"

    head = lambda x: {"type": "header", "text": {"type": "plain_text", "text": x}}
    sec = lambda x: {"type": "section", "text": {"type": "mrkdwn", "text": x[:2900]}}

    def box(title, lines):
        """묶음을 인용줄로 감싼다 — 왼쪽 세로선이 생겨 어디까지가 한 묶음인지 보인다 (사장님 결정 9/20).
        Slack 에 테두리 박스는 없다. 이슈마다 칸을 쓰면 카드처럼 보이지만 화면이 두 배로 길어진다."""
        body = "\n".join(f"> {x}" for x in lines)
        return sec((f"*{title}*\n" if title else "") + body)
    # 버튼 13개를 늘어놓지 않는다 — 다 읽어야 해서 아무것도 안 누르게 된다 (사장님 지적 9/20)
    look = [{"type": "button", "text": {"type": "plain_text", "text": v, "emoji": True},
             "action_id": f"detail_{k}", "value": k} for k, v in MAIN.items()]
    look.append({"type": "overflow", "action_id": "detail_pick",
                 "options": [{"text": {"type": "plain_text", "text": v[:75], "emoji": True}, "value": k}
                             for k, v in MORE.items()]})
    to_review = [c for c in cards if c["status"] == "review" and c.get("review_by") == user]

    # ① 내 계획 — **언제 뭘 하면 되나** (2026-09-22 사장님: 「todo리스트 자동생성 하고
    # 수정도 가능하게 해줘서 내가 언제 뭘 하면 되는지를 계획할 수 있게」).
    #
    # 날짜는 `core.plan` 이 **묻지 않고** 채워 둔다 — 사람은 이미 있는 계획을 옮기기만 한다.
    # 줄마다 날짜 고르개를 달아 그 자리에서 옮긴다. 한 번 옮기면 AI 가 다시 안 민다.
    blocks = [head("내 계획")]
    if to_review:
        for c in to_review:
            blocks += review_blocks(c, f"🔍 *확인해 주실 일*  {line(c)}")
    blocks += plan_blocks(mine, line)

    # ② 프로젝트 — 우리 어디쯤인가. **하나일 때만** 그린다 — 목표·로드맵은 데이터 폴더당 하나라
    # 프로젝트가 둘이면 어느 것을 그려도 거짓말이 된다. 여럿일 때의 모양은 #70 에서 (#66)
    if len(PROJECTS) == 1:
        blocks.append(project_line(box))

    # ③ 「내가 맡은 일 나머지」 칸은 **뺐다** (2026-09-22) — 계획이 이미 내 일을 전부
    # 날짜에 놓아 보여 준다. 남겨 두면 같은 이슈가 한 화면에 두 번 나온다
    if not mine:
        blocks.append(sec(say("home_none")))
    # **담당 없는 일은 누구나 가져갈 수 있다** (2026-09-23 사장님: 「언제든지 다른 사람이
    # 가져가서 할 수 있게」). 예전에는 **AI 가 그 사람을 추천한 것만** 보여 줬다 — 그래서
    # 추천이 안 붙은 일은 아무 사람의 화면에도 안 떴다. 여러 명에게 따로 뿌리는 장치를
    # 만들 것 없이, 담당 없는 일을 **모두에게 열어 두면** 된다. 추천받은 것이 위로 온다.
    # 한 번 「못 받아요」 한 일은 그 사람 칸에 다시 안 올린다 — 거절이 뜻을 가지려면 그래야 한다
    pickable = sorted([c for c in cards if not c.get("assignee")
                       and user not in (c.get("declined") or [])
                       and c["status"] not in ("done", "cancelled")],
                      key=lambda c: (c.get("suggested") != user, plevel(c), c.get("rank", 99)))[:3]
    if pickable:
        blocks.append(box("✋ 가져갈 수 있는 일", [line(c) for c in pickable]))
    if asked:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": say("home_asked", n=len(asked))}]})
    if meets:
        blocks.append(box("🗓️ 다가오는 회의", [
            f"{m['id']} {m['title']} · {m['date']}" + (f" · 할 일 #{m['issue']}" if m.get("issue") else "")
            for m in meets[:3]]))

    # ④ 둘러보기와 안내는 맨 아래
    blocks += [{"type": "divider"},
               {"type": "actions", "elements": look[:5]},      # 한 줄에 다섯까지
               {"type": "actions", "elements": look[5:] + [
                   {"type": "button", "text": {"type": "plain_text", "text": "🔄 작업판 새로고침"},
                    "action_id": "canvas_now", "value": "now"}]},
               ]
    # **맨 아래 안내문 여덟 줄을 빼 둔다** (2026-09-22 사장님 지적: 「이것도 필요없고?」).
    # 오버플로의 「❓ 사용법」 이 같은 것을 **더 자세히** 보여 주므로 중복이었고, Slack 이 낸
    # 홈 탭 지침도 「설정·도움말은 버튼 뒤에 둔다 · 행동을 너무 늘리지 않는다」 라고 한다.
    # 홈은 **지금 내 상태**를 보는 곳이고, **무엇을 할까**는 DM 쪽 몫이다.
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
        "text": f"갱신 {datetime.datetime.now().strftime('%m-%d %H:%M')}"
                + (f" · 작업판 {datetime.datetime.fromtimestamp(STATE['canvas_at']).strftime('%m-%d %H:%M')}"
                   if STATE.get("canvas_at") else "")}]})
    await api(s, "views.publish", body={"user_id": user, "view": {"type": "home", "blocks": blocks[:100]}})


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from views.card import link_of, review_blocks, state_line  # noqa: E402,F401
