"""Slack 호출 — api · 이름 (#30 에서 bot.py 를 나눔)."""
import os

import config
from common import BOT, log  # noqa: E402,F401
from docs import load_team  # noqa: E402,F401


# 봇 표정 — 메시지마다 그림 주소를 같이 보낸다. 그래서 워크스페이스마다 이모지를 올릴 필요가 없다 (9/20).
# 팀이 다른 그림을 쓰고 싶으면 config.json 에 icons(주소 앞부분)나 moods(이모지 이름)를 적으면 그게 이긴다.
ICON_BASE = config.CFG.get("icons") or "https://raw.githubusercontent.com/webn77/pa-icons/main/"
FACE = {"기본": "pa-base.png", "생각": "pa-think.png", "완료": "pa-done.png", "부탁": "pa-ask.png", "막힘": "pa-stuck.png"}
MOODS = dict(config.CFG.get("moods") or {})          # {"완료": "pa-완료", ...} — 워크스페이스 이모지를 쓰고 싶을 때만


def mood(name):
    """상황에 맞는 봇 표정. 이모지가 설정돼 있으면 그것, 아니면 그림 주소."""
    if MOODS.get(name):
        return {"icon_emoji": f":{MOODS[name]}:"}
    return {"icon_url": ICON_BASE + FACE[name]} if name in FACE else {}


async def check_moods(s):
    """켤 때 한 번 — 워크스페이스에 그 이모지가 정말 있는지 본다. 없으면 그 표정만 끈다.
    (없는 이모지를 쓰면 메시지 옆에 :pa-완료: 같은 글자가 그대로 보인다 — 9/20 실측)"""
    if not MOODS:
        return
    r = await api(s, "emoji.list")
    if not r.get("ok"):                      # emoji:read 권한이 없으면 확인하지 않고 그대로 둔다
        log(f"이모지 확인 못 함({r.get('error')}) — 표정을 그대로 씁니다")
        return
    have = set(r.get("emoji") or {})
    gone = [k for k, v in MOODS.items() if v not in have]
    for k in gone:
        MOODS.pop(k)
    log(f"표정 {len(MOODS)}개 사용" + (f" · 없어서 끔: {', '.join(gone)}" if gone else ""))


def env():
    """토큰 두 개를 읽어 온다. **없으면 무엇을 해야 하는지 말해 준다** (2026-09-21).

    예전에는 파일이 없으면 `read_text()` 가 날 것 그대로 터졌다 — 갓 받은 사람이 제일 먼저
    만나는 화면이 파이썬 traceback 이었다. 어디에 무엇을 만들어야 하는지는 한 줄도 없었다.
    """
    if not config.ENV_FILE.exists():
        # 파일이 없으면 **환경 변수**를 본다 — 시험은 Slack 을 부르지 않으므로 가짜 값이면 충분하고,
        # 도커·CI 처럼 파일을 못 두는 자리에서도 돌아간다 (2026-09-21).
        got = {k: os.environ[k] for k in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN") if os.environ.get(k)}
        if got:
            return got
        raise SystemExit(f"토큰 파일이 없어요: {config.ENV_FILE}\n"
                         "  `python3 setup.py` 를 먼저 돌리면 만들어 드려요 (docs/install.md)")
    out = {}
    for line in config.ENV_FILE.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip()
    return out


ENV = env()
NAMES = {}


FACE_METHODS = ("chat.postMessage", "chat.postEphemeral")


async def api(s, method, body=None, token="SLACK_BOT_TOKEN", **params):
    h = {"Authorization": "Bearer " + ENV[token]}
    url = "https://slack.com/api/" + method
    if method in FACE_METHODS and body is not None:
        add = {} if body.keys() & {"icon_url", "icon_emoji"} else mood("기본")   # 따로 정한 표정이 없으면 기본 얼굴
        if "username" not in body:
            add["username"] = BOT                 # 이름도 메시지에 실어 보낸다 — 앱 설정을 바꾸지 않아도 @PA 로 보인다
        body = {**body, **add}
    if body is not None:
        r = await s.post(url, json=body, headers=h)
    else:
        r = await s.get(url, params=params, headers=h)
    d = await r.json()
    if not d.get("ok"):
        log(f"{method} 실패: {d.get('error')}")
    return d


async def name_of(s, m):
    if m.get("username"):
        return m["username"]
    uid = m.get("user")
    if not uid:
        return "봇"
    if uid not in NAMES:
        u = (await api(s, "users.info", user=uid)).get("user", {})
        NAMES[uid] = u.get("real_name") or u.get("name") or uid
    return NAMES[uid]


_NAMES = {}


async def person(s, uid):
    """사람 이름 — team.md 에 없으면(요청자 등) Slack 프로필에서. 멘션 없이 이름만 쓸 때."""
    if not uid:
        return "누군가"
    if uid in load_team():
        return load_team()[uid]["name"]
    if uid not in _NAMES:
        u = (await api(s, "users.info", user=uid)).get("user") or {}
        _NAMES[uid] = (u.get("profile") or {}).get("display_name") or u.get("real_name") or "누군가"
    return _NAMES[uid]


import asyncio, contextlib, time  # noqa: E402
from messages import say  # noqa: E402,F401   문구는 한 곳에 (messages.py 는 config 만 보므로 돌지 않는다)


@contextlib.asynccontextmanager
async def working(s, channel, ts, face="⏳"):
    """하는 동안 그 메시지에 ⏳ 를 달아 둔다. 끝나면 뗀다 (2026-09-20 사장님 지적).

    AI 를 부르면 몇 초씩 걸리는데 그동안 아무 반응이 없어서 먹통처럼 보였다.
    말을 한 줄 더 보내는 대신 반응을 쓴다 — 스레드가 안내문으로 차지 않는다.
    실패해도 일을 막지 않는다: 반응을 못 달아도 하던 일은 그대로 한다.
    """
    on = False
    if channel and ts:
        r = await api(s, "reactions.add", body={"channel": channel, "timestamp": ts, "name": "hourglass_flowing_sand"})
        on = bool(r.get("ok"))
    try:
        yield
    finally:
        if on:
            await api(s, "reactions.remove", body={"channel": channel, "timestamp": ts, "name": "hourglass_flowing_sand"})


@contextlib.asynccontextmanager
async def thinking(s, e, *steps):
    """사람이 보낸 말 **바로 아래**에 「…하는 중」 을 띄운다 — Slack 이 AI 앱에 주는 자리 (2026-09-21).

    매니페스트에 `agent_view` 가 있어야 우리 글자가 나온다. 없으면 Slack 기본값
    「PA 앱이 작업 중」 만 뜨고, `loading_messages` 를 보내면 아예 안 뜬다 (그날 실측).

    **켜면 반드시 꺼야 한다** — 「the loading state no longer clears on its own」 (Slack 문서).
    실제로 안 꺼서 사장님 화면에서 계속 돌았다. 그래서 `with` 로 묶어 `finally` 에서 끈다.

    `Step.waiting` 과 자리가 다르다 — 저건 **봇이 올린 줄**을 고쳐 쓰고, 이건 **사람이 쓴 줄**
    아래에 Slack 이 그려 준다. 둘 다 쓰면 같은 말이 두 군데 보이므로, 이 자리를 쓰는 갈래에서는
    `Step.waiting` 을 쓰지 않는다.

    **`thread_ts` 는 스레드 뿌리여야 한다** (2026-09-22 실측). 스레드 **안의 답글 ts** 를 주면
    `invalid_thread_ts` 로 막힌다 — 그래서 스레드에서 주고받는 동안에는 상태 줄이 한 번도
    안 떴다. DM 스레드를 기본으로 되돌린 뒤에 드러난 것이다 (사장님: 「로딩이 동작 안 하는 거 같은데」).

        setStatus(thread_ts=뿌리)   → ok: True
        setStatus(thread_ts=답글)   → ok: False · invalid_thread_ts
    """
    ch, ts = e.get("channel"), e.get("thread_ts") or e.get("ts")
    steps = [x for x in steps if x] or [say("thinking")]
    if ch and ts:
        body = {"channel_id": ch, "thread_ts": ts, "status": steps[0]}
        if len(steps) > 1:
            body["loading_messages"] = list(steps)
        await api(s, "assistant.threads.setStatus", body=body)
    try:
        yield
    finally:
        if ch and ts:
            await api(s, "assistant.threads.setStatus", body={"channel_id": ch, "thread_ts": ts, "status": ""})


class Step:
    """「…하는 중」 한 줄을 띄우고, **그 자리를 결과로 바꾼다** (2026-09-20 사장님 지적).

    안내와 결과를 따로 올리면 스레드가 두 배로 길어지고, 무엇이 최신인지 흐려진다.
    한 자리에서 「고르는 중 → 결과」 로 이어지면 읽는 사람이 따라가기 쉽다.
    올리지 못해도 일을 막지 않는다 — 그때는 결과만 새로 올린다.
    """

    def __init__(self, s, channel, thread_ts=None):
        self.s, self.channel, self.thread_ts, self.ts = s, channel, thread_ts, None

    async def say(self, text, **kw):
        """처음이면 올리고, 이미 있으면 그 자리를 고친다."""
        body = {"channel": self.channel, "text": text, "unfurl_links": False, **kw}
        if self.thread_ts:
            body["thread_ts"] = self.thread_ts
        if self.ts:
            await api(self.s, "chat.update", body={**{k: v for k, v in body.items() if k != "thread_ts"},
                                                   "ts": self.ts})
        else:
            d = await api(self.s, "chat.postMessage", body=body)
            self.ts = d.get("ts")
        return self.ts

    async def drop(self):
        """치운다 — 보여 줄 것이 없을 때."""
        if self.ts:
            await api(self.s, "chat.delete", body={"channel": self.channel, "ts": self.ts})
            self.ts = None

    @contextlib.asynccontextmanager
    async def waiting(self, *steps, every=6):
        """오래 걸리는 일 동안 **무엇을 하고 있는지** 단계를 바꿔 보여 준다 (2026-09-21).

        Slack 에는 돌아가는 그림도, 봇이 쓸 수 있는 「입력 중…」 도 없다. 할 수 있는 건
        그 자리를 주기적으로 고쳐 쓰는 것뿐이다.

        처음에는 한 줄에 지난 시간만 붙였는데(「…하는 중 (18초)」), 같은 방에 있는 Ringo 가
        **단계를 바꿔** 보여 주는 것을 보고 따라 한다 — 「필요한 정보를 찾는 중」 →
        「관련 내용을 살펴보는 중」 → 「답변을 정리하는 중」. 같은 기다림인데 덜 답답하다
        (2026-09-21 사장님과 함께 실측).

        단계를 하나만 주면 예전처럼 그 줄에 시간만 붙는다. 마지막 단계에 닿으면 거기서
        시간만 늘린다 — 없는 단계를 지어내지 않는다.
        """
        steps = [s for s in steps if s] or ["하는 중이에요…"]

        async def tick():
            t0, i = time.time(), 0
            while True:
                await asyncio.sleep(every)
                i = min(i + 1, len(steps) - 1)
                await self.say(f"{steps[i]}  ({round(time.time() - t0)}초)")
        job = asyncio.create_task(tick())
        try:
            await self.say(steps[0])
            yield self
        finally:
            job.cancel()
