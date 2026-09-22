"""처음 오신 분께 **한 번에 한 걸음** (2026-09-22 사장님 결정).

예전에는 DM 을 열면 **할 수 있는 일 여덟 줄**을 한꺼번에 보여 줬다. 읽을 것은 많은데
**무엇부터 하면 되는지는 없었다.** 앱 홈도 마찬가지였다 — 아무것도 없는 사람에게
「진행률 0% · 담당 없음에서 하나 골라 보세요」 를 보여 줬는데, **고를 게 없었다.**

  1️⃣ 지금 하고 계신 프로젝트 올리기   ← 이름 · 번호 앞말 · 목표를 거기서 여쭌다
  2️⃣ 첫 할 일 적기
  3️⃣ 누가 언제까지 · 현황판

**목표 걸음을 없앴다** (2026-09-22). 예전에는 2번에서 목표를 따로 물었는데, 프로젝트를 만들 때
세 번째로 이미 여쭌다 (`flows/project.py`). 두 군데서 물으면 **이미 답한 것을 또 물어보고**,
그게 사람 눈에는 「말이 안 통한다」 로 보인다 — 이 세션에서 가장 여러 번 고친 고장 모양이다.

**1번이 프로젝트인 이유** (2026-09-22 사장님): 지금 **하고 계신 일**부터 올려야 남의 이야기가
되지 않는다. 모아가 가려는 곳은 「사람 한 명이 AI 와 일한다」 가 아니라
**「팀이 AI 와 함께 프로젝트를 만들어 간다」** 이므로, 같이 하실 분은 그 방에서 부른다.

`setup.py` 가 만든 첫 방은 **자리만 잡아 둔 것**이다. 그래서 1번은 「프로젝트가 둘 이상인가」 로
센다 — 사람이 DM 으로 자기 프로젝트를 하나라도 올렸다는 뜻이다.

**어느 걸음인지는 데이터에서 계산한다** — 따로 세어 두지 않는다. 세어 두면 사람이
Slack 에서 직접 한 일(사람을 초대하거나 카드를 만든 것)과 어긋나고, 그러면 이미 한 일을
또 하라고 조른다. 계산이 한 박자 느릴 수는 있어도 **거짓말은 하지 않는다.**
"""
from common import PROJECTS, log
from messages import say
from slack import api
from store import STATE, open_cards, save


DONE = "done"        # 건너뛰었거나 다 끝낸 사람
# **어간이 변한다** — 「건너뛰」 로는 「건너뛸래요」 가 안 걸린다 (시험이 잡았다, 2026-09-22).
# 한국어 낱말을 목록으로 맞출 때 늘 따라오는 함정이라 **변하지 않는 앞부분**까지만 적는다.
SKIP = ("됐어", "건너", "괜찮아", "나중에", "알아서 할게", "그만", "skip")


def own_project():
    """사람이 **자기 프로젝트**를 하나라도 올렸나.

    `setup.py` 는 늘 방 하나를 만들어 둔다 — 그건 자리만 잡은 것이라 세지 않는다.
    둘째부터가 사람이 DM 으로 올린 것이다.
    """
    return len(PROJECTS) > 1


def step(user=None):
    """지금 어느 걸음인가 (1~3). 다 끝났거나 건너뛰었으면 None."""
    if user and user in STATE.setdefault("onboard_done", []):
        return None
    if not own_project():
        return 1
    cards = open_cards()
    if not cards:
        return 2
    if not any(c.get("assignee") for c in cards):
        return 3
    return None


def skipped(text):
    return any(s in (text or "") for s in SKIP)


def give_up(user):
    """「됐어요」 — 다시 안 조른다."""
    STATE.setdefault("onboard_done", []).append(user)
    save()


def nudge(user=None, head=True):
    """지금 걸음 안내. 다 끝났으면 빈 글자.

    **`channel` 을 늘 넘긴다** — 2번 걸음이 「<#…> 에서 하세요」 라고 방을 가리킨다.
    안 넘기면 그 자리에서 KeyError 가 나고, 인사 한 줄 때문에 DM 전체가 안 간다.
    """
    n = step(user)
    if not n:
        return ""
    body = say(f"onboard_{n}", channel=room())
    return (say("onboard_head", n=n) + "\n\n" + body) if head else body


def room():
    """「여기에 쓰세요」 라고 가리킬 방 — **방금 만든 프로젝트**다.

    setup 이 만든 첫 방을 가리키면 안 된다. 사람이 1번 걸음에서 자기 프로젝트를 올렸는데
    **엉뚱한 방을 가리키면** 거기 쓴 글이 아무 데도 안 걸린다.
    """
    return (PROJECTS[-1].get("channel") or "") if PROJECTS else ""


async def catch(s, e, q):
    """DM 의 이 말이 온보딩에 속하면 처리하고 True. 아니면 False (다른 갈래로 간다).

    **여기서 받는 것은 「됐어요」 하나다** (2026-09-22). 예전에는 목표 한 줄도 여기서 받았는데,
    그러면 **아무 글이나 목표로 받게** 되고 「현황」 이 프로젝트 목표가 됐다. 그래서 울타리
    낱말을 계속 늘려야 했다. 지금은 목표를 프로젝트 만들 때 **묻는 자리에서만** 받는다 —
    묻지 않은 자리에서 받으면 무엇이든 받게 되고, 그게 이 봇이 가장 자주 낸 고장이었다.
    """
    user, ch = e.get("user"), e.get("channel")
    th = e.get("thread_ts") or e.get("ts")      # DM 에서도 스레드가 기본 (사장님이 정함 9/22)
    if skipped(q):
        give_up(user)
        log(f"온보딩 그만: ← {user}")
        await say_to(s, ch, say("onboard_skip"), th)
        return True
    return False


async def say_to(s, ch, text, thread=None):
    """**물어본 글 아래 스레드로** 답한다 (2026-09-22) — 맨 위에 답하면 짝이 흩어진다.

    `api` 는 **맨 위에서** 불러온다. 함수 안에서 불러오면 시험이 갈아 끼울 수 없어서
    이 갈래를 아예 못 시험한다 (2026-09-22 시뮬레이션 중에 드러났다).
    """
    body = {"channel": ch, "text": text, "unfurl_links": False}
    if thread:
        body["thread_ts"] = thread
    await api(s, "chat.postMessage", body=body)
