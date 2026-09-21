"""처음 오신 분께 **한 번에 한 걸음** (2026-09-22 사장님 결정).

예전에는 DM 을 열면 **할 수 있는 일 여덟 줄**을 한꺼번에 보여 줬다. 읽을 것은 많은데
**무엇부터 하면 되는지는 없었다.** 앱 홈도 마찬가지였다 — 아무것도 없는 사람에게
「진행률 0% · 담당 없음에서 하나 골라 보세요」 를 보여 줬는데, **고를 게 없었다.**

  1️⃣ 무엇을 이루려는지 한 줄
  2️⃣ 같이 할 사람 부르기      ← 이게 핵심이다
  3️⃣ 첫 할 일 적기
  4️⃣ 누가 언제까지 · 현황판

**2번이 핵심인 이유**: 모아가 가려는 곳은 「사람 한 명이 AI 와 일한다」 가 아니라
**「팀이 AI 와 같이 일한다」** 이다 (사장님). 혼자 다 차리고 끝나면 방향과 어긋난다.

**어느 걸음인지는 데이터에서 계산한다** — 따로 세어 두지 않는다. 세어 두면 사람이
Slack 에서 직접 한 일(사람을 초대하거나 카드를 만든 것)과 어긋나고, 그러면 이미 한 일을
또 하라고 조른다. 계산이 한 박자 느릴 수는 있어도 **거짓말은 하지 않는다.**
"""
from common import HERE, PROJECTS, log
from docs import load_team, project_info
from messages import say
from store import STATE, open_cards, save


DONE = "done"        # 건너뛰었거나 다 끝낸 사람
# **어간이 변한다** — 「건너뛰」 로는 「건너뛸래요」 가 안 걸린다 (시험이 잡았다, 2026-09-22).
# 한국어 낱말을 목록으로 맞출 때 늘 따라오는 함정이라 **변하지 않는 앞부분**까지만 적는다.
SKIP = ("됐어", "건너", "괜찮아", "나중에", "알아서 할게", "그만", "skip")


def goal_set():
    """목표가 자리표시 그대로인가. 새 팀 문서는 「(한 문장 — 무엇을 이루나)」 로 시작한다."""
    goal = (project_info()[0] or "").strip()
    return bool(goal) and not goal.startswith("(") and "목표 미정" not in goal


def team_joined():
    """PM 말고 한 사람이라도 더 있나 — setup 은 PM 한 줄만 써 둔다."""
    return len(load_team()) > 1


def step(user=None):
    """지금 어느 걸음인가 (1~4). 다 끝났거나 건너뛰었으면 None."""
    if user and user in STATE.setdefault("onboard_done", []):
        return None
    if not goal_set():
        return 1
    if not team_joined():
        return 2
    cards = open_cards()
    if not cards:
        return 3
    if not any(c.get("assignee") for c in cards):
        return 4
    return None


def skipped(text):
    return any(s in (text or "") for s in SKIP)


def give_up(user):
    """「됐어요」 — 다시 안 조른다."""
    STATE.setdefault("onboard_done", []).append(user)
    save()


def nudge(user=None, head=True):
    """지금 걸음 안내. 다 끝났으면 빈 글자.

    **`channel` 을 늘 넘긴다** — 2·3번 걸음이 「<#…> 에서 하세요」 라고 방을 가리킨다.
    안 넘기면 그 자리에서 KeyError 가 나고, 인사 한 줄 때문에 DM 전체가 안 간다.
    """
    n = step(user)
    if not n:
        return ""
    body = say(f"onboard_{n}", channel=room())
    return (say("onboard_head", n=n) + "\n\n" + body) if head else body


def room():
    """첫 프로젝트 방 — 안내에서 「여기에 쓰세요」 라고 가리킬 곳."""
    return (PROJECTS[0].get("channel") or "") if PROJECTS else ""


# 온보딩이 삼키면 안 되는 말 — 이미 뜻이 있는 것들. 1번 걸음은 **아무 글이나** 목표로 받으므로
# 이 울타리가 없으면 「현황」 이 프로젝트 목표가 된다
NOT_A_GOAL = ("현황", "목록", "도움말", "정리", "내 할 일", "프로젝트", "회의", "캔버스", "상세")


def set_goal(text):
    """project.md 의 목표 한 줄을 채운다. 자리표시를 못 찾으면 아무것도 안 하고 False."""
    import re
    p = HERE / "project.md"
    if not p.exists():
        return False
    txt = p.read_text(encoding="utf-8")
    new, n = re.subn(r"(## 목표\s*\n+)\*\*.*?\*\*", lambda m: m.group(1) + f"**{text}**", txt, count=1)
    if not n:
        return False
    p.write_text(new, encoding="utf-8")
    return True


async def catch(s, e, q):
    """DM 의 이 말이 온보딩에 속하면 처리하고 True. 아니면 False (다른 갈래로 간다)."""
    user, ch = e.get("user"), e.get("channel")
    if skipped(q):
        give_up(user)
        await say_to(s, ch, say("onboard_skip"))
        return True
    n = step(user)
    if n != 1:
        return False                      # 2~4번 걸음은 Slack 에서 직접 하는 일이라 여기서 안 받는다
    word = q.strip()
    if len(word) < 4 or any(x in word for x in NOT_A_GOAL):
        return False
    if not set_goal(word[:120]):
        return False
    log(f"목표 정함: {word[:40]} ← {user}")
    await say_to(s, ch, say("onboard_goal_ok", what=word[:120]) + "\n\n" + nudge(user, head=False))
    return True


async def say_to(s, ch, text):
    from slack import api
    await api(s, "chat.postMessage", body={"channel": ch, "text": text, "unfurl_links": False})
