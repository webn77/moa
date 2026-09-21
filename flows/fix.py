"""일정 위험 [적용] · 아침 현황 (#30 에서 bot.py 를 나눔)."""
import asyncio, datetime
import gh_link
from messages import say
from common import CHANNEL, HERE, log, mday  # noqa: E402,F401
from docs import current_stage, load_stages, load_team  # noqa: E402,F401
from slack import api  # noqa: E402,F401
from store import STATE, decision_snap, ref, save  # noqa: E402,F401


async def apply_fix(s, value, user, thread_ts=None):
    """위험 추천을 적용한다."""
    kind, _, arg = value.partition(":")
    by = {c["no"]: c for c in STATE["cards"].values()}
    snap = {n: (c.get("assignee"), c.get("priority"), c.get("stage"), c.get("due")) for n, c in by.items()}
    dsnap = decision_snap()
    team = load_team()
    who = team.get(user, {}).get("name", "누군가")
    done = ""
    if kind == "stage":
        names = [x["name"] for x in load_stages()]
        cur = current_stage()
        nxt = names[names.index(cur) + 1] if cur in names and names.index(cur) + 1 < len(names) else None
        nos = [int(x) for x in arg.split(",") if x]
        for n in nos:
            if n in by and nxt:
                by[n]["stage"] = nxt
        done = f"{len(nos)}건을 {nxt} 단계로 옮겼어요 — " + ", ".join(ref(n, 10) for n in nos)
    elif kind == "capcut":
        cc = STATE.setdefault("capcut", {})
        cc[arg] = cc.get(arg, 0) + 1
        done = f"{team.get(arg, {}).get('name', arg)} 님이 맡는 개수를 하나 줄였어요 (되돌리려면 PM에게)"
    elif kind == "delay":
        n, _, days = arg.partition(":")
        c = by.get(int(n))
        if c:
            base = max(datetime.date.fromisoformat(c["due"]) if c.get("due") else datetime.date.today(), datetime.date.today())
            c["due"], c["due_src"] = (base + datetime.timedelta(days=int(days or 3))).isoformat(), "human"
            done = f"{ref(c['no'], 16)} 목표일을 {int(c['due'][5:7])}/{int(c['due'][8:])}로 미뤘어요"
    elif kind == "start":
        c = by.get(int(arg))
        if c and c.get("assignee"):
            await api(s, "chat.postMessage", body={"channel": CHANNEL, "thread_ts": c["card_ts"],
                      "text": say("fix_start", uid=c["assignee"], due=mday(c["due"]))})
            done = "담당자께 알림을 보냈어요"
    elif kind == "unassign":
        c = by.get(int(arg))
        if c:
            c["assignee"] = None; c.pop("assign_src", None)
            c["no_auto"] = True                       # 누가 직접 가져가기 전까지 자동 배정하지 않는다
            done = f"{ref(c['no'], 16)} 을 담당 없음으로 돌렸어요 — 누가 직접 가져갈 때까지 자동 배정 안 해요"
    elif kind in ("ping", "ask", "confirm"):
        nos = [int(x) for x in arg.split(":")[0].split(",") if x]
        for n in nos:
            c = by.get(n)
            if not c or not c.get("assignee"):
                continue
            msg = say(f"fix_{kind}", uid=c["assignee"], n=arg.split(":")[1] if ":" in arg else "")
            await api(s, "chat.postMessage", body={"channel": CHANNEL, "thread_ts": c["card_ts"], "text": msg})
        done = f"담당자께 알림을 보냈어요 ({len(nos)}건)"
    place()
    # 바뀐 카드만 다시 그린다 — 전부 다시 올리면 chat.update 한도(분당 약 50)에 걸린다. 스레드 ⚙️ 도 함께
    changed = [c for n, c in by.items() if snap[n] != (c.get("assignee"), c.get("priority"), c.get("stage"), c.get("due"))]
    for c in changed:
        await api(s, "chat.update", body={"channel": CHANNEL, "ts": c["card_ts"], "text": f"#{c['no']} {c['title']}", "blocks": card_blocks(c)})
        if c.get("ctl_ts"):
            await ensure_ctl(s, c)
    await note_decisions(s, dsnap, user, how="일정 위험 추천 적용", why=done or None)
    save()
    asyncio.create_task(render_canvas(s))                  # 캔버스는 5초 묶음 — 버튼 응답을 붙잡지 않게
    if thread_ts and done:
        await api(s, "chat.postMessage", body={"channel": CHANNEL, "thread_ts": thread_ts, "text": say("applied", who=who, done=done)})
    log(f"위험 적용 {value} ← {who}")


async def post_digest(s, reason=""):
    main, detail = digest()
    d = await api(s, "chat.postMessage", body={"channel": CHANNEL, "text": "현황", "unfurl_links": False, "blocks": main})
    if d.get("ok"):
        await api(s, "chat.postMessage", body={"channel": CHANNEL, "thread_ts": d["ts"], "text": detail[:3900], "unfurl_links": False})
        rb = risk_blocks()
        if rb:
            await api(s, "chat.postMessage", body={"channel": CHANNEL, "thread_ts": d["ts"], "text": "위험과 추천", "blocks": rb[:45]})
        STATE.setdefault("digests", []).append({"date": datetime.date.today().isoformat(), "ts": d["ts"], "by": reason})
        save()
    log(f"현황 게시 ({reason})")
    return d.get("ts")


async def show_digest(s, channel, user):
    """현황을 **부른 자리에서 그 사람에게만** 보여 준다 (2026-09-20 사용자 지적).

    예전에는 어디서 물어도 프로젝트 방에 새 글을 올렸다 — 방이 현황으로 쌓이고,
    다른 채널에서 물으면 링크만 받아 건너가야 했다. 지금 상태를 보는 건 읽기라서 팀에 알릴 일이 아니다.
    팀이 같이 보는 현황은 아침에 한 번 자동으로 올라간다(morning).
    """
    main, detail = digest()
    rb = risk_blocks()
    body = {"channel": channel, "text": "현황", "unfurl_links": False,
            "blocks": (main + (rb[:20] if rb else ""))[:48]}
    if channel.startswith("D"):                        # DM 은 원래 나만 보는 자리다
        await api(s, "chat.postMessage", body=body)
    else:
        await api(s, "chat.postEphemeral", body={**body, "user": user})
    log(f"현황 보여 줌 → {user}")


async def morning(s):
    """평일 아침 9시 현황 · 저녁 7시 하루 묶음 — 각각 하루 한 번만."""
    while True:
        now = datetime.datetime.now()
        day = now.date().isoformat()
        if now.weekday() < 5 and now.hour == 9 and STATE.get("digest_day") != day:
            STATE["digest_day"] = day
            await post_digest(s, "아침 9시")
            await remind_reviews(s)
            await check_sync(s, tell=CHANNEL)          # 카드와 GitHub 이 어긋났으면 알린다 (#59)
        if now.hour >= 19 and STATE.get("daily_day") != day:
            STATE["daily_day"] = day
            await asyncio.get_running_loop().run_in_executor(None, write_daily, day)
        await asyncio.sleep(60)


def write_daily(day):
    """그날 있었던 일을 `daily/날짜.md` 로 (#59). 아무 일도 없었으면 쓰지 않는다.

    파일을 쓰고 커밋 창에 넣기만 한다 — Slack 에 올리지 않는다. 저녁에 한 줄 더 뜨면
    읽는 사람이 늘 보는 것이 아니라 **넘기는 것**이 된다. 필요하면 레포에서 본다.
    """
    md = daily_md(day)
    if not md:
        return
    p = HERE / "daily" / f"{day}.md"
    p.parent.mkdir(exist_ok=True)
    if p.exists() and p.read_text(encoding="utf-8") == md:      # 같으면 안 쓴다
        return
    p.write_text(md, encoding="utf-8")
    gh_link.queue(p, f"daily {day}")
    log(f"하루 묶음 {day} ({len(md.splitlines())}줄)")


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from flows.status import ensure_ctl, note_decisions, place  # noqa: E402,F401
from flows.github import check_sync  # noqa: E402,F401
from flows.review import remind_reviews  # noqa: E402,F401
from views.canvas import render_canvas  # noqa: E402,F401
from views.card import card_blocks  # noqa: E402,F401
from views.digest import daily_md, digest, risk_blocks  # noqa: E402,F401
