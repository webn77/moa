"""골든 화면 뽑기 (#30) — 고정한 데이터(tests/golden/data)와 고정한 날짜로 모든 화면을 JSON 한 개로 낸다.

  MOA_DATA=tests/golden/data python3 tests/golden/render.py > out.json

bot.py 를 나누는 동안 이 결과가 한 글자도 바뀌지 않아야 한다 (tests/test_golden.py).
Slack·GitHub·AI 는 부르지 않는다 — 가짜 api 가 받은 것을 모은다.
"""
import asyncio
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
FIXED = _dt.datetime(2026, 9, 21, 9, 30)


class FakeDate(_dt.date):
    @classmethod
    def today(cls):
        return cls(FIXED.year, FIXED.month, FIXED.day)


class FakeDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(FIXED.year, FIXED.month, FIXED.day, FIXED.hour, FIXED.minute)


class FakeModule:
    date, datetime, timedelta = FakeDate, FakeDatetime, _dt.timedelta


def freeze():
    """datetime 을 쓰는 우리 모듈 전부의 날짜를 고정한다 (나눈 뒤 모듈이 늘어도 그대로)."""
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None) or ""
        if f.startswith(root) and getattr(m, "datetime", None) is _dt:
            m.datetime = FakeModule


class Clock:
    """time.time() 도 고정 — 상태에 찍히는 시각(작업판 갱신 등)이 실행할 때마다 달라지지 않게."""
    def __init__(self, t=1790000000.0):
        self.t = t

    def time(self):
        self.t += 1
        return self.t


def freeze_clock(clock):
    import time as _t
    for m in ours():
        if getattr(m, "time", None) is _t:
            m.time = clock


def ours():
    root = os.path.abspath(os.path.join(HERE, "..", ".."))
    return [m for m in list(sys.modules.values()) if (getattr(m, "__file__", None) or "").startswith(root)]


class Bot:
    """나누기 전(bot.py 하나) · 뒤(여러 모듈) 모두에서 이름으로 찾고, 바꿔 끼울 땐 전부에 끼운다."""
    def __getattr__(self, name):
        import types
        for m in ours():
            v = getattr(m, name, None)
            if v is not None and not isinstance(v, types.ModuleType):   # views.digest(모듈) 과 digest(함수) 를 헷갈리지 않게
                return v
        raise AttributeError(name)

    def __setattr__(self, name, value):
        for m in ours():
            if hasattr(m, name):
                setattr(m, name, value)


def main():
    import bot as _bot                               # noqa: E402,F401  (MOA_DATA 를 먼저 정해야 한다)
    try:
        import handlers  # noqa: F401  나눈 뒤 — 모든 모듈을 불러 둔다
    except ImportError:
        pass
    bot = Bot()
    freeze()
    freeze_clock(Clock())
    sent = []

    async def fake_api(s, method, body=None, **kw):
        sent.append({"method": method, **({"body": body} if body else {}), **kw})
        if method == "users.list":
            return {"members": []}
        return {"ok": True, "ts": "1.0", "permalink": "https://x"}

    bot.api = fake_api
    bot.save = lambda: None
    out = {}
    cards = sorted(bot.STATE["cards"].values(), key=lambda c: c["no"])
    for c in cards:
        k = f"#{c['no']}"
        out[f"card {k}"] = bot.card_blocks(c)
        out[f"ctl {k}"] = bot.ctl_blocks(c)
        out[f"detail {k}"] = bot.card_detail_blocks(c)
        out[f"state_line {k}"] = bot.state_line(c)
    for key in bot.DETAIL:
        out[f"popup {key}"] = bot.detail_blocks(key)
    out["digest"] = bot.digest()
    out["risks"] = bot.risk_blocks()

    async def run():
        for uid in bot.load_team():
            await bot.publish_home(None, uid)
        bot._CANVAS["last"] = None
        await bot._render_canvas(None)
    asyncio.run(run())
    out["homes"] = [x["body"]["view"]["blocks"] for x in sent if x["method"] == "views.publish"]
    out["canvas"] = [x["body"] for x in sent if x["method"] == "canvases.edit"]
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
