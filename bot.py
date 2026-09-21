#!/usr/bin/env python3
"""Slack 프로젝트 비서 봇 — Socket Mode 로 상시 실행한다. 실행과 끄기만 여기, 나머지는 모듈에.

  handlers.py   이벤트 · 버튼 · 명령 · 창 제출 → flows/
  flows/        요청 · 상태 · 회의 · 일정 위험 · GitHub          views/  카드 · 홈 · 캔버스 · 현황
  구조와 규칙은 docs/architecture.md · 설치는 docs/install.md

끄기: SIGTERM(kill) · Ctrl+C 를 받으면 새 이벤트를 그만 받고, 하던 일을 마친 뒤 저장하고 꺼진다.
AI 는 이 맥에 로그인된 Claude Code 구독(claude -p)으로 부른다. 토큰은 config.json 의 env_file 에서 읽고 출력하지 않는다.
"""
import asyncio, fcntl, json, os, signal, sys
import aiohttp
import gh_link  # noqa: E402,F401
from common import BOT, CHANNEL, HERE, drain, guard, log  # noqa: E402,F401
from docs import load_team  # noqa: E402,F401
from flows.fix import morning  # noqa: E402,F401
import flows.control as control  # noqa: E402,F401
from flows.github import watch_cards, watch_github  # noqa: E402,F401
from handlers import on_action, on_event, on_message_action, on_view_submit, refresh_ctls  # noqa: E402,F401
from slack import api, check_moods, mood  # noqa: E402,F401
from store import chan, save  # noqa: E402,F401
from views.canvas import render_canvas  # noqa: E402,F401


STOP = {"ws": None, "stopping": False}
_LOCK = None            # **모듈이 들고 있어야 한다** — 이 변수가 사라지면 파일이 닫히고 잠금도 풀린다


def claim():
    """봇은 늘 하나 — 이미 도는 봇이 있으면 뜨지 않는다 (#10 「봇 프로세스는 늘 1개」).

    **LaunchAgent 로는 이게 안 된다.** launchd 는 자기가 띄운 것 하나만 관리해서, 손으로
    `nohup` 으로 또 띄우면 그냥 지나간다 — 2026-09-21 에 실제로 둘이 떠서 25초 동안 같은
    Slack 연결에 붙어 있었다. 봇이 둘이면 각자 자기 메모리의 카드를 들고 있다가 **나중에
    저장한 쪽이 이긴다** (`store.py` 가 cards.json 을 켤 때 한 번만 읽는다).

    PID 를 적어 두고 살아 있나 보는 방식은 번호가 돌려 쓰이면 오판한다. 파일 잠금은
    **프로세스가 죽으면 OS 가 알아서 푼다** — 남은 찌꺼기를 따질 일이 없다.
    """
    global _LOCK
    _LOCK = open(HERE / "bot.pid", "w")
    try:
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _LOCK.write(str(os.getpid()))
    _LOCK.flush()
    return True


def stop():
    """SIGTERM · Ctrl+C — 새 이벤트를 그만 받고, 하던 일을 마친 뒤 꺼진다 (재시작 중 끊김 방지, #30)."""
    STOP["stopping"] = True
    log("끄는 중 — 하던 일을 마치고 꺼져요")
    if STOP["ws"] is not None:
        asyncio.get_running_loop().create_task(STOP["ws"].close())


async def main():
    # **가장 먼저 자리를 잡는다** — 둘째 봇은 git pull 도, cards.json 도, Slack 연결도 건드리면 안 된다.
    # LaunchAgent(KeepAlive) 아래서는 여기서 나간 뒤 30초 뒤 또 뜬다. 고장이 아니라 「자리 있나」 를
    # 되묻는 것이다 — 먼저 뜬 봇이 살아 있는 한 계속 비켜선다
    if not claim():
        log("이미 도는 봇이 있어서 뜨지 않아요 (bot.pid 가 잠겨 있어요)")
        sys.exit(1)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop)
    async with aiohttp.ClientSession() as s:
        me = (await api(s, "auth.test")).get("user_id")
        # `note` 는 **잘 됐든 막혔든 사람에게 할 말**이다 — 예전에는 막혔을 때만 있었다.
        # 잘 됐을 때도 「지금 어느 버전이 도는지」 를 알려 준다 (2026-09-21 사장님 요청)
        ok, note = await loop.run_in_executor(None, gh_link.pull)   # 켤 때 한 번 git pull — 안전할 때만 (#59)
        log(f"git pull {'받음' if ok else '막힘'}: {note}" if note else "git pull 안 함 (git 아님)")
        if note:                                                   # 조용히 묻히면 며칠 뒤에야 드러난다
            # **DM 으로 간다** (2026-09-21 사장님 결정) — 봇을 켠 것은 한 사람인데 예전에는
            # 프로젝트 방 전체가 봤다. 팀원에게는 손쓸 수 없는 소식이다. PM 이 없는 팀이면
            # 프로젝트 방으로 떨어뜨린다 — 알림이 사라지게 두지 않는다 (`req()` 와 같은 생각)
            team = load_team()
            for to in [u for u, t in team.items() if "PM" in (t.get("role") or "")] or [chan()]:
                await api(s, "chat.postMessage", body={"channel": to, "unfurl_links": False,
                          "text": f"{'🔄' if ok else '⚠️'} {note}", **mood("기본" if ok else "막힘")})
        background = {asyncio.create_task(guard(watch_github(s))), asyncio.create_task(guard(morning(s))),
                      asyncio.create_task(guard(watch_cards(s)))}   # 바뀐 카드를 스스로 내보낸다 (#58)
        await check_moods(s)                                  # 없는 이모지를 쓰지 않게
        asyncio.create_task(guard(refresh_ctls(s)))
        asyncio.create_task(guard(render_canvas(s)))          # 꺼져 있던 동안 놓친 변경 — 같으면 안 쓴다
        control.ASK = HERE / "ask.jsonl"                      # 도구(Claude·스크립트)가 일을 맡기는 통로 (#56)
        background.add(asyncio.create_task(guard(control.watch_asks(s))))
        while not STOP["stopping"]:
            url = (await api(s, "apps.connections.open", body={}, token="SLACK_APP_TOKEN")).get("url")
            if not url:
                await asyncio.sleep(10)
                continue
            log("연결됨")
            async with s.ws_connect(url, heartbeat=30) as ws:
                STOP["ws"] = ws
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    d = json.loads(msg.data)
                    if d.get("envelope_id"):
                        await ws.send_json({"envelope_id": d["envelope_id"]})
                    if d.get("type") == "disconnect":
                        break
                    if d.get("type") == "events_api":
                        ev = d["payload"]["event"]
                        asyncio.create_task(guard(on_event(s, ev, me)))
                    elif d.get("type") == "interactive":
                        p = d["payload"]
                        kind = p.get("type")
                        log(f"상호작용: {kind} {p.get('callback_id', '')}"
                            f"{[a.get('action_id') for a in p.get('actions', [])]}")
                        if kind == "message_action":
                            asyncio.create_task(guard(on_message_action(s, p)))
                        elif kind == "view_submission":
                            asyncio.create_task(guard(on_view_submit(s, p)))
                        else:
                            asyncio.create_task(guard(on_action(s, p)))
            if not STOP["stopping"]:
                log("끊김 — 다시 연결")
        for t in background:
            t.cancel()
        done, pending = await drain(skip=background)
        save()
        log(f"정본 커밋 마무리: {gh_link.flush()}")     # 5분 창에 모여 있던 것 (#58)
        log(f"꺼짐 — 마친 일 {done}건" + (f" · 못 마친 일 {pending}건" if pending else ""))


if __name__ == "__main__":
    print(f"{BOT} 시작 — 데이터 {HERE} · 로그는 bot.log")
    asyncio.run(main())
