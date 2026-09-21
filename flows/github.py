"""정본 md · GitHub 내보내기 · 댓글 가져오기 · backlog.md (#30 에서 bot.py 를 나눔)."""
import asyncio, hashlib, json, pathlib
import core
import gh_link
from messages import say
from common import AI_LABEL, CHANNEL, HERE, LABEL, NEXT_MIN, PLEVEL, log, plevel  # noqa: E402,F401
from docs import load_stages, load_team, need_hours  # noqa: E402,F401
from slack import api, mood  # noqa: E402,F401
from store import STATE, chan, save  # noqa: E402,F401


GAP = 300               # 커밋을 모으는 창 (초) — 바뀐 게 생겼을 때만 열린다


def fingerprints(c):
    """(파일 지문, GitHub 지문). 파일은 body_md 가 그리는 전부, GitHub 은 사람이 보는 것만."""
    spec = c.get("spec") or {}
    gh = json.dumps([c.get("title"), c.get("status"), c.get("assignee"),
                     [spec.get(k) for k in ("why", "change", "expect", "not_doing")],
                     spec.get("done_criteria"), sorted(c.get("checked") or [])], ensure_ascii=False)
    fil = gh + json.dumps(c.get("edits") or [], ensure_ascii=False, sort_keys=True)
    return (hashlib.sha256(fil.encode()).hexdigest()[:16], hashlib.sha256(gh.encode()).hexdigest()[:16])


async def commit_window(wait=GAP):
    """모인 파일을 한 번에 커밋한다. 창이 이미 열려 있으면 아무 일도 하지 않는다."""
    if _WINDOW.get("open"):
        return
    _WINDOW["open"] = True
    try:
        await asyncio.sleep(wait)
        out = await asyncio.get_running_loop().run_in_executor(None, gh_link.flush)
        log(f"정본 커밋: {out}")
    finally:
        _WINDOW["open"] = False


_WINDOW = {"open": False}


async def watch_cards(s, every=30):
    """바뀐 카드를 스스로 찾아 내보낸다 — 내보내는 자리는 여기 하나다 (#58).

    예전에는 카드를 고치는 자리마다 export_github 을 손으로 불렀다. 8곳이었고, 그 중 두 곳을
    빠뜨려서 정의가 있는데 GitHub 에 없는 카드가 21건 생겼다. 부르는 것을 기억하게 하는 대신
    바뀐 것을 찾아오게 한다 — 새 기능을 만드는 사람은 이 파일을 몰라도 된다.

    안 바뀌었으면 아무 일도 하지 않는다(지문 비교, API 0번). 번호 순으로 돈다 — gh#N = 카드 #N 이라
    작은 번호가 먼저 올라가야 한다.
    """
    while True:
        await asyncio.sleep(every)
        await sweep(s)


async def sweep(s):
    """바뀐 카드를 한 번 훑는다. 내보낸 개수를 돌려준다."""
    n = 0
    for c in sorted(STATE["cards"].values(), key=lambda x: x["no"]):
        if c.get("file_hash") == fingerprints(c)[0]:
            continue
        try:
            await export_github(s, c, c.get("card_ts"))
            n += 1
        except Exception as e:
            log(f"내보내기 실패 #{c['no']}: {e}")
    return n


async def export_github(s, c, thread_ts=None):
    """정본 md + GitHub 이슈로 내보낸다 — 내보내는 자리는 여기 하나다 (#58).

    올릴 수 있는 것: 번호가 있고 · 정의가 있고 · 검증을 통과한 카드.
    통과 못 하면 두고, 정의가 채워져 다시 불리면 그때 올라간다.
    보낼 내용이 안 바뀌었으면 Slack·GitHub 을 부르지 않는다.
    """
    fil, gh = fingerprints(c)
    if c.get("file_hash") == fil:                     # 아무것도 안 바뀜 — API 를 한 번도 부르지 않는다
        return
    # 검증은 **막지 않는다.** #60 부터 번호를 받을 때 이슈가 먼저 생기므로, 여기서 멈추면
    # GitHub 이 옛 상태로 남는다 (2026-09-20: 취소한 #63 이 GitHub 에서 열린 채였다).
    # 대신 빠진 것을 스레드에 한 번 알린다 — 같은 말을 되풀이하지 않게 지난 것과 비교한다.
    block, warn = core.check_card(c, list(STATE["cards"].values()),
                                  "number", stage_names={x["name"] for x in load_stages()})
    if block and thread_ts and c.get("gate_said") != block:
        c["gate_said"] = block
        await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": thread_ts,
                  "text": say("gh_block", why="\n".join(f"• {x}" for x in block))})
    elif not block:
        c.pop("gate_said", None)
    link = c.get("permalink") or (await api(s, "chat.getPermalink", channel=chan(c),
                                            message_ts=c["card_ts"])).get("permalink", "")
    c["permalink"] = link
    first = not c.get("tracker")
    push = c.get("gh_hash") != gh                     # GitHub 에 보낼 내용이 바뀐 경우만 API 를 부른다
    try:
        tracker, path, git = await asyncio.get_running_loop().run_in_executor(
            None, gh_link.export, c, link, push)
    except Exception as e:
        log(f"GitHub 내보내기 실패: {e}")
        if thread_ts:
            await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": thread_ts,
                      "text": say("gh_fail", err=str(e)[:150])})
        return
    c["file_hash"], c["gh_hash"] = fil, (gh if push else c.get("gh_hash"))
    save()
    log(f"내보냄 #{c['no']} → {tracker} ({git})" + ("" if push else " · GitHub 은 그대로"))
    asyncio.get_running_loop().create_task(commit_window())
    if warn:
        log(f"#{c['no']} 알림 — {' · '.join(warn)}")
    if first and tracker and thread_ts:
        await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": thread_ts,
                  "text": say("gh_made", url=c.get("issue_url", ""), tracker=tracker, path=pathlib.Path(path).relative_to(HERE))})


def write_backlog():
    """레포의 backlog.md — 우선순위 순 백로그. 계산식과 입력 숫자를 함께 남긴다."""
    team = load_team()
    rows = []
    for c in sorted(STATE["cards"].values(), key=lambda c: (c["status"] in ("done", "cancelled"), c.get("rank", 999))):
        s = c.get("scores") or {}
        who = team.get(c.get("assignee"), {}).get("name", "-")
        tag = ("🎨 " if c.get("design") else "") + ("💡" if c.get("prio_src") == "ai" else "✋")
        h = c.get("hours") or {}
        rows.append(f"| {c.get('rank', '-')} | {PLEVEL[plevel(c)]} | #{c['no']} {c['title']} | {who} | "
                    f"{LABEL[c['status']]} | {s.get('value', '-')}·{s.get('urgency', '-')}·{s.get('goal_fit', '-')}·"
                    f"{s.get('effort', '-')} | {c.get('score', '-')} | "
                    f"{h.get('min', '-')}~{h.get('max', '-')}h · {AI_LABEL.get(c.get('ai'), '-')} · 사람 {need_hours(c) if h else '-'}h | "
                    f"{tag} {c.get('prio_reason', '')} |")
    body = f"""# 백로그

자동 생성 — 봇이 우선순위를 다시 계산할 때마다 갱신한다. 손으로 고치지 않는다 (순서를 바꾸려면 카드의 ✏️ 상태·담당).

## 계산식
점수 = (가치 + 긴급 + 목표 적합 × 2) ÷ 노력 · 각 1~5 · AI 추정, 사람이 고칠 수 있음
1. 선행 이슈가 안 끝났으면 지금 불가 → 다음
2. 점수 순으로 사람마다 최대 동시 수까지 → 🔥 지금
3. 중복·목표 적합 1·점수 {NEXT_MIN} 미만 → 💤 나중, 나머지 → ⏭️ 다음
4. 사람이 정한 것(✋)은 계산에서 뺀다
운영 규칙 전체는 priority.md

| 순위 | 우선순위 | 이슈 | 담당 | 상태 | 가치·긴급·목표·노력 | 점수 | 예상 시간 · AI | 근거 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(rows)}
"""
    p = HERE / "backlog.md"
    p.write_text(body, encoding="utf-8")
    gh_link.commit(p, "backlog 우선순위 갱신")


async def import_github(s):
    """밖에서 만든 GitHub 이슈를 카드로 가져온다 (#60의 되가져오기).

    번호의 주인이 GitHub 이라 **번호가 이미 맞다** — 카드만 붙이면 된다. 그래서 합칠 일도, 충돌도 없다.
    제목·본문은 GitHub 것을 그대로 쓰고, 그 뒤로는 Slack 이 주인이 된다(한쪽 방향 그대로).
    한 번 가져온 번호는 다시 가져오지 않는다 — 카드를 취소해도 되살아나지 않게.
    """
    if not gh_link.REPO:
        return
    known = {c["no"] for c in STATE["cards"].values()} | set(STATE.get("gh_imported") or [])
    try:
        found = await asyncio.get_running_loop().run_in_executor(None, gh_link.open_issues_without_cards, known)
    except Exception as e:
        log(f"GitHub 이슈 훑기 실패: {e}")
        return
    for g in found[:5]:                                   # 한 번에 5건까지 — 처음 붙일 때 쏟아지지 않게
        STATE.setdefault("gh_imported", []).append(g["number"])
        c = await new_card(s, {"text": g["title"][:60]}, CHANNEL, number=g["number"])
        if not c:
            continue
        c["gh_no"], c["issue_url"], c["tracker"] = g["number"], g["url"], f"{gh_link.REPO}#{g['number']}"
        c["by"], c["request"] = "GitHub", (g.get("body") or "")[:1500]
        save()
        await asyncio.get_running_loop().run_in_executor(          # 제목에 번호를 붙여 짝을 보이게
            None, gh_link.adopt, g["number"], c["title"])
        await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"],
                  "text": say("gh_import", url=g["url"], tracker=c["tracker"])})
        log(f"GitHub 에서 가져옴 #{g['number']}")


async def check_sync(s, tell=None):
    """카드와 GitHub 을 맞대어 어긋난 것을 알린다 (#59). 아침 현황과 같이 돈다.

    동기화는 조용히 깨진다 — 사람이 알 방법이 없으면 며칠 뒤에야 드러난다.
    어긋난 게 없으면 **아무 말도 하지 않는다.**
    """
    if not gh_link.REPO:
        return []
    try:
        issues = await asyncio.get_running_loop().run_in_executor(None, gh_link.all_issues)
    except Exception as e:
        log(f"대조 실패: {e}")
        return []
    bad = core.reconcile(list(STATE["cards"].values()), issues)
    log(f"GitHub 대조: 카드 {len(STATE['cards'])} · 이슈 {len(issues)} · 어긋남 {len(bad)}")
    if bad and tell:
        rows = "\n".join(f"• #{n} — {why}" for n, why in bad[:15])
        await api(s, "chat.postMessage", body={"channel": tell, "unfurl_links": False,
                  "text": say("sync_off", n=len(bad)) + "\n" + rows, **mood("막힘")})
    return bad


async def watch_github(s):
    """GitHub 댓글을 카드 스레드로 가져온다. 2분마다. 방향은 GitHub → Slack 한쪽."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(120)
        await import_github(s)                            # 밖에서 만든 이슈 먼저 (#60)
        for c in list(STATE["cards"].values()):
            seen = c.setdefault("gh_seen", [])
            try:
                got = await loop.run_in_executor(None, gh_link.new_comments, c, seen)
            except Exception as e:
                log(f"GitHub 댓글 조회 실패: {e}")
                continue
            for who, body in got:
                await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"],
                          "text": say("gh_comment", who=who, tracker=c["tracker"], body=body[:1500])})
                log(f"GitHub 댓글 가져옴 #{c['no']} ({who})")
            if got:
                save()


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from flows.intake import new_card  # noqa: E402,F401
