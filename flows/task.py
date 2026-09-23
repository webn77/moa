"""DM 에서 할 일 올리기 — **한 칸씩 묻는다** (2026-09-22 사장님 결정).

  사람: 충전성공에 할 일 등록하려고
  모아: 무슨 일인가요? 한 줄로                 ← ①
  사람: 충전 실패 알림이 두 번 와요
  모아: 누가 할까요?                           ← ③ (②프로젝트는 「충전성공」 으로 이미 알았다)
  사람: 제가
  모아: 언제까지 하면 될까요?                   ← ④
  사람: 이번 주
  모아: 이렇게 올릴까요? → 「네」 → 카드·현황판

**왜 바꿨나** (2026-09-22 사장님: 「할일을 채우는것도 다시 정리해보자고! 프로젝트 등록처럼
그 틀을 바탕으로 단계로」). 예전에는 AI 가 ❓ 가 박힌 초안을 내놓고 이렇게 말했다:

  이렇게 봤는데 맞을까요? ❓ 자리만 한 줄 채워 주시면 할 일로 만들어 둘게요.
    왜  ❓ / 바뀌는 것  충전 성공 시점에 … / 기대와 확인  ❓ / 체크리스트  ❓

무엇을 어디에 써야 하는지 알 수 없고, 빈 칸 넷을 내미는 건 **양식을 요구하는 것**이다.
`project.md` 에 이미 적어 둔 약속과 어긋난다 — 「작업자에게 틀을 요구하지 않는다」.

**어느 프로젝트인지는 아는 경우 묻지 않는다** (사장님: 「이미 알고 있다면 넘어가고」)
  · 말에 이름이나 앞말이 있으면 그것 — 「**충전성공**에 할 일 등록」
  · 프로젝트가 하나뿐이면 그것
그래서 질문은 보통 **셋**이고, 프로젝트가 여럿인데 안 말하셨을 때만 넷이 된다.

**체크리스트는 여기서 묻지 않는다** (사장님이 정함: 「카드에서 버튼으로」). 필요할 때
그 할 일의 [✨ 정리해 줘] 를 누르면 그때 AI 가 채운다 — 안 누르면 AI 를 안 쓴다.
"""
import datetime
import re

import core
from common import PROJECTS, log
from flows.ask import CANCEL, COMMANDS, HEAD, LATER, cancelled, command, later, yes as _yes
from messages import say
from slack import api
from store import STATE, save


# 시작하는 말 — **만드는 낱말이 함께 있어야** 잡는다. 「내 할 일」·「할 일 목록」 은 찾는 말이다
START = re.compile(r"(할\s*일|이슈|업무|작업)\S*\s*(를|을|로|은|는)?\s*\S{0,4}\s*(등록|만들|추가|올려|생성)"
                   r"|등록\s*(할래|하려|해줘|하자|할게)")
# **다른 등록과 섞이지 않는다** — 「깃허브 등록 하려고」 가 할 일 등록으로 잡혔다
# (2026-09-22 시뮬레이션). 갈래 순서로도 막았지만 **순서에 기대지 않는다** —
# 순서는 나중에 누가 바꿀 수 있고, 그러면 조용히 어긋난다.
# 「프로젝트」 는 여기 넣지 않는다: 「두 번째 프로젝트에 할일 등록」 은 진짜 할 일이다
NOT_MINE = re.compile(r"깃허브|깃헙|github|레포|repo|저장소", re.I)
_HEAD = re.compile(HEAD, re.I)
# 「제가 할게요」 처럼 문장으로 오기도 하고 「제가」 한 마디로 오기도 한다.
# **설명할 수 있는 낱말만 둔다** — 뜻 모를 낱말을 방어용으로 끼워 두면 다음 사람이
# 잘못 고친다 (「나가」·「제요」 를 그래서 뺐다)
WHO_ME = ("제가", "내가", "저요", "나요", "저", "나")
DUE_IN = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})\s*/\s*(\d{1,2})|(\d{1,2})\s*월\s*(\d{1,2})\s*일")
# 확인 단계에서 무엇을 고치려는 말인지 가른다
# **「일」 을 넣으면 안 된다** — 「할**일** 등록 하려고」 의 「일」 에 걸려서 제목이
# 「등록 하려고」 가 됐다 (2026-09-22 시뮬레이션이 잡았다). 짧은 낱말은 우연히 걸린다
TITLE_IN = re.compile(r"(?:제목|이름)\s*(?:은|는|을|를|이|가)?\s*[:：]?\s*(\S.*)")
WHO_IN = re.compile(r"담당\s*(?:은|는|을|를|이|가)?\s*[:：]?\s*(\S.*)")
DUE_WORD = re.compile(r"(?:언제까지|기한|목표일|마감)\s*(?:은|는|을|를|이|가)?\s*[:：]?\s*(\S.*)")
# 확인 화면에서 **체크리스트를 그 자리에서 더한다** (2026-09-23 사장님: 「체크리스트 추가
# 하거나 수정 할 수 있는거지」). AI 가 낸 것은 **추천**이므로 사람이 손댈 자리가 있어야 한다.
# 더하기와 지우기 **둘만** 둔다 — 「2번을 이렇게 고쳐」 까지 받으려면 번호를 세고 되읽어 줘야
# 하는데, 그건 올린 뒤 📝 설명 쓰기 창이 이미 훨씬 잘 한다 (한 줄에 하나씩 통째로 고친다)
LIST_IN = re.compile(r"체크리스트\s*(?:에|는|은|을|를|이|가|도)?\s*[:：]?\s*(.*)", re.S)
LIST_OFF = ("없이", "없음", "지워", "빼", "비워", "필요 없", "안 할")
STEP_WHAT = {"title": "무슨 일인가", "project": "어느 프로젝트", "who": "누가 할까", "due": "언제까지",
             "confirm": "마지막 확인"}


def _asking(user):
    return (STATE.get("new_task") or {}).get(user)


def _steps(st):
    """이 대화에서 물을 칸 — **아는 것은 빼고 센다.** 「3/3」 이라고 했는데 넷을 물으면 안 된다."""
    out = ["title"]
    if not st.get("pkey_known"):
        out.append("project")
    return out + ["who", "due"]


def _where(st):
    """지금 몇 번째 칸인가 — (번호, 전체, 무엇)."""
    steps = _steps(st)
    step = st.get("step") or "title"
    n = steps.index(step) + 1 if step in steps else len(steps)
    return n, len(steps), STEP_WHAT.get(step, "확인")


MAX = 10                     # 한 번에 올릴 상한 (사장님이 정함) — 회의록을 통째로 붙이면 40개가 생긴다
# **줄바꿈·글머리표·번호만 쪼갠다.** 쉼표는 안 쓴다 — 「A, B를 고쳐요」 는 한 일이다
LINES = re.compile(r"[\n\r]+")
BULLET = re.compile(r"^\s*(?:[-•*·–—]|\d{1,2}\s*[.)])\s*")


def _titles_of(text):
    """여러 줄이면 여러 개로 본다 — (제목 목록, 버린 줄 수).

    **새 명령을 만들지 않는다** (2026-09-22 사장님: 「여러 할일을 한번에 등록하는 경우도
    있자나」). 1️⃣ 칸에 그냥 여러 줄을 붙이면 된다. 질문 수는 개수와 무관하게 같다 —
    「누가·언제까지」 는 **전부에 한 번** 묻는다 (사장님이 정함).
    """
    out, dropped = [], 0
    for line in LINES.split(text or ""):
        one = BULLET.sub("", line).strip()[:60]
        if not one:
            continue                      # 빈 줄은 센 것에도 안 넣는다 — 줄 사이를 띄운 것일 뿐이다
        if _titleable(one):
            out.append(one)
        else:
            dropped += 1                  # 이모지만 있는 줄 같은 것 — 몇 개 뺐는지 밝힌다
    return out, dropped


# **묻는 말은 제목이 아니다** (2026-09-23 사장님 실사용). 「이번주에 할일 한번에 만들려고
# 하는데 가능해?」 가 그대로 할 일 이름이 됐다. 봇이 방금 「무슨 일인가요」 라고 물었으니
# 무엇이 와도 답으로 받던 탓인데, 사람은 **물어 놓고 되물을 수 있다.**
ASKING = re.compile(r"\?\s*$|가능(해|한가|할까|하나)|되나요|되나\?|할\s*수\s*있|어떻게\s*(해|하)|방법이")
# **「그게 아니라」 는 답이 아니라 되돌리자는 말이다** (같은 대화에서 나왔다).
# 이걸 프로젝트 이름으로 받아서 「그 프로젝트를 못 찾았어요」 가 나갔다
# 맨 「아니」 는 안 넣는다 — 그건 머리말이라 `_HEAD` 가 이미 떼고, 넣으면
# 「아니 그게 아니고요」 의 앞 「아니」 만 먹고 나머지를 제목으로 받는다 (시험이 잡았다)
NOPE = re.compile(r"^\s*(?:그게|그거|그건|그 말)?\s*(?:아니라|아니고|아니야|아냐|말고)[\s,.!~]*")


def _titleable(text):
    """할 일 이름으로 쓸 글자가 두 자 이상 남나 — 이모지만이면 목록에서 못 알아본다."""
    return len(re.sub(r"[^0-9A-Za-z가-힣]", "", text or "")) >= 2


def _short(name):
    """설정에 적힌 방 이름에서 사람이 부르는 이름만 — `프로젝트-충전성공` → `충전성공`."""
    return re.sub(r"^프로젝트[-\s]*", "", name or "").strip()


def _title_of(text):
    """시작하는 말에서 **할 일 이름만** 골라낸다. 못 고르면 빈 글자.

    「충전성공에 할 일 등록하려고」 를 통째로 받으면 그게 할 일 제목이 된다 —
    프로젝트 등록에서 똑같이 겪은 고장이다 (그 파일의 `_name_of` 설명에).
    """
    s = _HEAD.sub("", re.sub(r"\s+", " ", (text or "").strip()))
    m = TITLE_IN.search(s)
    if m and _titleable(m.group(1)):
        # **꼬리말을 함부로 떼지 않는다.** 프로젝트 이름과 달리 할 일 제목은 **문장**이다 —
        # 「알림이 두 번 와요」 에서 「요」 를 떼면 「…와」 가 된다 (시험이 잡았다, 2026-09-22).
        # 그래서 「이에요·예요·입니다」 처럼 **떼도 뜻이 남는 것만** 뗀다
        return re.sub(r"\s*(?:이에요|예요|입니다)\s*[!.~]*$", "",
                      m.group(1)).strip(" ,.!~\"'「」")[:60]
    return ""


def _project_of(text):
    """말에서 프로젝트를 찾는다 — 「충전성공에 할 일」 · 「CH 에」. 못 찾으면 None.

    **아는 것은 묻지 않는다** (사장님). 이름은 방 이름에서 `프로젝트-` 를 뗀 것으로 맞춘다.
    """
    s = (text or "")
    # **빈칸을 지우고 맞춘다** — 「두번째 프로젝트」 라고 붙여 쓰면 `두 번째 프로젝트` 와 안 맞는다
    flat = re.sub(r"\s+", "", s).lower()
    for p in PROJECTS:
        name = _short(p.get("name"))
        if name and len(re.sub(r"\s+", "", name)) >= 2 and re.sub(r"\s+", "", name).lower() in flat:
            return p
    for p in PROJECTS:                      # 앞말은 짧아서 우연히 걸리기 쉽다 — 이름을 먼저 본다
        key = (p.get("key") or "").strip()
        if key and re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", s, re.I):
            return p
    return None


def _pick_project(text):
    """번호(「1」)나 이름으로 고른다. 못 고르면 None."""
    s = (text or "").strip()
    m = re.fullmatch(r"(\d{1,2})\s*(?:번|번째|요|이요|입니다)?[.!~]*", s)
    if m and 1 <= int(m.group(1)) <= len(PROJECTS):
        return PROJECTS[int(m.group(1)) - 1]
    return _project_of(s)


def _project_list():
    return "\n".join(f"  {i + 1}. {_short(p.get('name')) or '이름 없음'}"
                     + (f" (`{p['key']}`)" if p.get("key") else "")
                     for i, p in enumerate(PROJECTS))


def _who_of(text, by):
    """누가 할까 — 멘션 · 「제가」 · 「나중에」. 못 읽으면 False (다시 묻는다)."""
    m = re.search(r"<@(U[A-Z0-9]+)>", text or "")
    if m:
        return m.group(1)
    s = (text or "").strip().rstrip("요!.~ ")
    if s in WHO_ME or any(w in (text or "") for w in ("제가", "내가", "저요", "제거")):
        return by
    if later(text):
        return None
    return False


def _due_of(text):
    """언제까지 — 「오늘」·「내일」·「이번 주」·「9/30」·「2026-09-30」. 못 읽으면 False.

    **주 단위는 그 주의 금요일로 본다** — 「이번 주」 라고 하면 사람은 주말 전을 뜻한다.
    「나중에」 면 None (목표일 없이 만든다).
    """
    s = (text or "").strip()
    today = datetime.date.today()
    if any(x in s for x in LATER):
        return None
    for word, days in (("오늘", 0), ("내일", 1), ("모레", 2)):
        if word in s:
            return (today + datetime.timedelta(days=days)).isoformat()
    if "이번" in s and "주" in s:
        return (today + datetime.timedelta(days=(4 - today.weekday()) % 7)).isoformat()
    if "다음" in s and "주" in s:
        return (today + datetime.timedelta(days=(4 - today.weekday()) % 7 + 7)).isoformat()
    m = DUE_IN.search(s)
    if m:
        y, mo, d = (m.group(1), m.group(2), m.group(3)) if m.group(1) else \
                   (None, m.group(4) or m.group(6), m.group(5) or m.group(7))
        try:
            return datetime.date(int(y) if y else today.year, int(mo), int(d)).isoformat()
        except ValueError:
            return False
    return False


WORD_DUE = {"1": "오늘", "2": "내일", "3": "이번 주", "4": "다음 주", "5": "나중에"}


def _due_pick(text):
    """묻는 자리에서만 — 번호를 말로 바꿔 읽는다. 번호가 아니면 그대로 읽는다."""
    return _due_of(WORD_DUE.get((text or "").strip(), text))


def _due_text(iso):
    if not iso:
        return "정하지 않았어요"
    d = datetime.date.fromisoformat(iso)
    left = (d - datetime.date.today()).days
    return f"{d.month}/{d.day}" + (f" (D-{left})" if left > 0 else " (오늘)" if left == 0 else " (지났어요)")


async def _say(s, ch, text, thread=None):
    """**등록 대화는 스레드 안에서** — 묻고 답하는 대여섯 마디를 한 덩이로 묶는다."""
    body = {"channel": ch, "text": text, "unfurl_links": False}
    if thread:
        body["thread_ts"] = thread
    await api(s, "chat.postMessage", body=body)


async def _again(s, ch, th, st, text):
    """물은 것과 다른 답이 왔을 때 — **어디에 있는지와 나가는 길**을 늘 함께 (사장님 지시)."""
    n, total, what = _where(st)
    st["miss"] = st.get("miss", 0) + 1        # 몇 번째로 못 알아들었나 — 세 번째엔 더 좁게 묻는다
    await _say(s, ch, text + "\n\n" + say("ask_where", kind="할 일 등록", n=n, total=total, what=what), th)
    save()
    return True


def _keycap(n):
    """1 → 1️⃣. **번호는 계산해서 붙인다** — 건너뛴 칸이 있으면 글로 박아 둔 번호가 어긋난다."""
    return f"{n}\ufe0f\u20e3"


async def _ask(s, ch, th, st, step, head=""):
    """다음 칸을 묻는다 — 아는 칸은 건너뛰고, 번호는 남은 칸으로 센다."""
    st["step"], st["miss"] = step, 0          # 칸이 넘어가면 못 알아들은 횟수도 처음으로
    save()
    no = _keycap(_where(st)[0])
    if step == "project":
        text = say("task_ask_project", step=no, list=_project_list())
    elif step == "who":
        text = say("task_ask_who", step=no)
    elif step == "due":
        text = say("task_ask_due", step=no)
    else:
        return await _show(s, ch, th, st)
    await _say(s, ch, head + text, th)
    return True


def _titles(st):
    return st.get("titles") or []


def _list_of(text):
    """「체크리스트 …」 뒤에 쓴 것 → 더할 항목들. 지우자는 말이면 빈 목록.

    줄바꿈·가운뎃점·쉼표로 나눈다. 제목과 달리 **쉼표로도 나눈다** — 항목은 짧은 할 거리라
    한 줄에 「a, b, c」 로 쓰는 것이 자연스럽다 (제목은 쉼표가 문장 안에 들어가서 못 나눈다).
    """
    body = (text or "").strip()
    if len(body) < 12 and any(x in body for x in LIST_OFF):
        return []
    return core.clean_items(re.split(r"[\n·,]+", body))


def _numbered(st):
    """번호 붙인 제목 — **체크리스트가 있으면 그 아래 붙인다** (2026-09-23 사장님:
    「체크리스트 만드는건 할일만들때」). 확인 화면에서 한눈에 보고 「네」 하면 그대로 올라간다."""
    dc = st.get("dc") or {}
    rows = []
    for i, x in enumerate(_titles(st)):
        rows.append(f"  {i + 1}. {x}")
        rows += [f"       ☐ {y}" for y in dc.get(x, [])]
    return "\n".join(rows)


async def _make_lists(s, st):
    """확인 직전에 **체크리스트를 미리 만들어 둔다.** AI 한 번으로 제목 전부를 처리한다.

    묻지 않는다 — 방금 세 가지를 답하셨는데 또 물으면 같은 대화를 두 번 하는 셈이다.
    보여 주고 「네」 면 그대로 간다. 고치실 것은 올린 뒤 📝 설명 쓰기 에서.
    **실패해도 그냥 간다** — 등록이 AI 때문에 막히면 안 된다.
    """
    if st.get("dc") is not None:
        return
    from ai import checklists
    st["dc"] = await checklists(_titles(st))
    save()


def _read_back(st):
    """제목을 되읽어 주는 줄 — 하나면 그 한 줄, 여럿이면 번호를 붙인 목록."""
    got = _titles(st)
    if len(got) == 1:
        return say("task_title_ok", title=got[0])
    head = say("task_titles_ok", n=len(got), list=_numbered(st))
    notes = []
    if st.get("over"):
        notes.append(f"{MAX}개까지만 올려요 — 나머지 {st['over']}개는 다시 부탁드려요")
    if st.get("dropped"):
        notes.append(f"글자가 두 자 안 되는 줄 {st['dropped']}개는 뺐어요")
    return head + ("\n" + say("task_titles_note", note=" · ".join(notes)) if notes else "")


async def _after_title(s, ch, th, st):
    """제목을 **한 번** 되읽어 준다 — 못 들은 줄 알면 같은 말을 또 하신다."""
    return await _ask(s, ch, th, st, "who" if st.get("pkey_known") else "project",
                      head=_read_back(st) + "\n\n")


async def _show(s, ch, th, st):
    """되읽어 준다 — 번호는 GitHub 에서 받아 오고, 받은 번호는 되돌리지 않는다."""
    from docs import load_team
    st["step"] = "confirm"
    save()
    who = st.get("who")
    name = _short(next((p.get("name") for p in PROJECTS if p.get("key") == st.get("pkey")), "")) or "프로젝트"
    whom = (load_team().get(who, {}).get("name") or f"<@{who}>") if who else "아직 없어요"
    got = _titles(st)
    await _make_lists(s, st)
    items = (st.get("dc") or {}).get(got[0]) if got else None
    if len(got) == 1:
        text = say("task_confirm", title=got[0], name=name, who=whom, due=_due_text(st.get("due")),
                   dc=("\n".join(f"    ☐ {x}" for x in items) if items else "    아직 없어요 — 올린 뒤 채우셔도 돼요"))
    else:
        text = say("task_confirm_many", n=len(got), list=_numbered(st), name=name,
                   who=whom, due=_due_text(st.get("due")))
    await _say(s, ch, text, th)
    return True


async def maybe(s, e, q, force=False):
    """DM 의 이 말이 할 일 올리기와 관련 있으면 처리하고 True. 아니면 False.

    프로젝트 등록과 **같은 틀**이다 (사장님 지시). 여기서도 AI 를 부르지 않는다 —
    묻는 칸이 정해져 있으니 지어낼 것이 없다.
    """
    user, ch = e.get("user"), e.get("channel")
    st, opened = _asking(user), False
    if st is None:
        if not (force or (START.search(q) and not NOT_MINE.search(q))):
            return False
        opened = True
        st = {"by": user, "step": "title", "th": e.get("thread_ts") or e.get("ts")}
        hit = _project_of(q) if len(PROJECTS) > 1 else (PROJECTS[0] if PROJECTS else None)
        if hit is not None:                 # **아는 것은 묻지 않는다** (사장님)
            st["pkey"], st["pkey_known"] = hit.get("key") or "", True
        STATE.setdefault("new_task", {})[user] = st
        save()
    # **답은 사람이 쓴 자리로 간다** (2026-09-23 사장님: 「스레드에 안 적고 그냥 채팅에
    # 적었는데 스레드 답변으로 들어가네」). 예전에는 처음 시작한 스레드로만 답해서,
    # 바깥에 쓰면 답이 **다른 데서** 나왔다 — 쓴 사람 눈에는 아무 말이 없는 것과 같다.
    th = e.get("thread_ts") or e.get("ts") or st.get("th")
    if opened:
        name = _title_of(q)                 # 「제목은 X」 처럼 또렷이 말했을 때만 줍는다
        if _titleable(name):
            st["titles"] = [name]
            return await _after_title(s, ch, th, st)
        await _say(s, ch, say("task_ask_title", step=_keycap(1)), th)
        return True
    if cancelled(q):
        STATE["new_task"].pop(user, None); save()
        await _say(s, ch, say("task_cancel"), th)
        return True
    if command(q):
        return await _again(s, ch, th, st, say("ask_busy", word=q.strip(), kind="할 일을 올리는"))

    step = st.get("step")
    # **「그게 아니라」 는 되돌리자는 말이다** — 어느 단계에서 와도 그 칸을 비우고 다시 묻는다.
    # 뒤에 붙은 말이 답이면 그것까지 받는다 (「그게 아니라 알림 고치기」)
    # 확인 단계는 빼 둔다 — 거기에는 「어느 걸 고칠까요」 라는 **더 나은 되묻기**가 이미 있다
    if step != "confirm" and NOPE.match(_HEAD.sub("", q.strip())):
        rest = NOPE.sub("", _HEAD.sub("", q.strip())).strip(" ,.!~") or ""
        if step == "title":
            st.pop("titles", None)
        elif step in ("project", "who", "due"):
            st.pop({"project": "pkey", "who": "who", "due": "due"}[step], None)
        if rest and _titleable(rest) and not ASKING.search(rest):
            # 「그게 아니라 **결제 화면 문구 고치기**」 — 뒤에 붙은 말이 곧 고친 제목이다.
            # 단계가 지나갔어도 제목으로 돌아간다: 사람이 고치겠다는 건 보통 **방금 한 말**이다
            got, dropped = _titles_of(rest)
            if got:
                st["titles"], st["dropped"] = got[:MAX], dropped
                st["over"] = max(0, len(got) - MAX)
                st["step"] = "title"
                return await _after_title(s, ch, th, st)
        st["step"] = "title" if step != "confirm" else step
        return await _again(s, ch, th, st, say("task_nope"))

    # ① 무슨 일인가 — 여기서는 **적으신 그대로** 받는다. 제목은 사람이 읽을 한 줄이다
    if step == "title":
        if ASKING.search(q) and not NOPE.match(q.strip()):
            # 물어보신 것에 **답을 하고** 다시 묻는다 — 되묻기만 하면 같은 말을 또 하시게 된다
            return await _again(s, ch, th, st, say("task_title_asked"))
        got, dropped = _titles_of(q)
        if not got:
            return await _again(s, ch, th, st,
                                say("task_title_bad", word=q.strip()[:20] or "빈 글자"))
        st["titles"], st["dropped"] = got[:MAX], dropped
        st["over"] = max(0, len(got) - MAX)
        return await _after_title(s, ch, th, st)

    # ② 어느 프로젝트 — 번호로도 이름으로도 받는다
    if step == "project":
        hit = _pick_project(q)
        if hit is None:
            # **두 번 못 알아들었으면 더 좁게 묻는다** — 같은 줄을 되풀이하면 고장으로 보인다
            if st.get("miss", 0) >= 2:
                return await _again(s, ch, th, st, say(
                    "task_project_only", list=_project_list(),
                    what=" · ".join(str(i + 1) for i in range(len(PROJECTS)))))
            return await _again(s, ch, th, st, say("task_project_bad", list=_project_list()))
        st["pkey"] = hit.get("key") or ""
        return await _ask(s, ch, th, st, "who")

    # ③ 누가 — 번호로도 받는다 (사장님 승낙 2026-09-22)
    if step == "who":
        who = {"1": st["by"], "2": None}.get(q.strip()) if q.strip() in ("1", "2") else _who_of(q, st["by"])
        if who is False:
            return await _again(s, ch, th, st, say("task_ask_who", step=_keycap(_where(st)[0])))
        st["who"] = who
        return await _ask(s, ch, th, st, "due")

    # ④ 언제까지 — 번호로도 받는다. **번호 풀이는 묻는 자리에서만** 한다:
    # 확인 단계에서 「3」 은 3일을 뜻할 수도 있어 여기서만 골라 준다
    if step == "due":
        due = _due_pick(q)
        if due is False:
            return await _again(s, ch, th, st, say("task_due_bad"))
        st["due"] = due
        return await _show(s, ch, th, st)

    # ⑤ 확인 — **고치자는 말을 먼저 본다.** 「네 근데 담당은 @홍길동」 처럼 맞장구와 고칠 것이
    # 한 문장에 올 수 있다. 대꾸를 먼저 보면 옛 값 그대로 올려 버린다 (2026-09-22)
    s2 = _HEAD.sub("", q.strip())
    m = TITLE_IN.search(s2)
    if m and _titleable(m.group(1)):
        st["titles"] = [m.group(1).strip(" ,.!~\"'「」")[:60]]
    elif WHO_IN.search(s2) and _who_of(WHO_IN.search(s2).group(1), st["by"]) is not False:
        st["who"] = _who_of(WHO_IN.search(s2).group(1), st["by"])
    elif DUE_WORD.search(s2) and _due_of(DUE_WORD.search(s2).group(1)) is not False:
        st["due"] = _due_of(DUE_WORD.search(s2).group(1))
    elif LIST_IN.search(s2) and len(_titles(st)) == 1:
        # 여러 개를 한 번에 올릴 때는 안 받는다 — 어느 것의 체크리스트인지 알 수 없다.
        # 그때는 올린 뒤 카드마다 📝 에서 고치는 것이 오히려 짧다
        key, add = _titles(st)[0], _list_of(LIST_IN.search(s2).group(1))
        cur = (st.get("dc") or {}).get(key) or []
        st.setdefault("dc", {})[key] = core.clean_items(cur + add) if add else []
    elif _pick_project(s2) is not None and len(PROJECTS) > 1:
        st["pkey"] = _pick_project(s2).get("key") or ""
    elif _yes(q):
        return await _build(s, ch, th, st, user)
    else:
        return await _again(s, ch, th, st, say("task_fix_how"))
    return await _show(s, ch, th, st)


async def _build(s, ch, th, st, user):
    """정말로 올린다 — 여기까지 오면 확인을 받은 것이다.

    **번호는 하나씩 받는다** (GitHub). 중간에 막히면 거기까지만 올라가므로
    **올라간 것만** 알린다 — 안 한 일을 했다고 말하지 않는다 (이 저장소의 약속).
    """
    from docs import load_team
    from flows.intake import add_issue
    from store import chan, ref
    await _say(s, ch, say("task_making"), th)
    got, made, err = _titles(st), [], None
    for i, title in enumerate(got):
        try:
            items = (st.get("dc") or {}).get(title)
            c = await add_issue(s, title, user, project=st.get("pkey"),
                                assignee=st.get("who"), due=st.get("due"), ask=False,
                                score=(i == len(got) - 1),
                                spec={"done_criteria": items} if items else None)
        except Exception as ex:
            c, err = None, f"{type(ex).__name__}"
            log(f"할 일 올리기 실패: {type(ex).__name__}: {ex}")
        if not c:
            err = err or "번호를 못 받았어요"
            break
        made.append(c)
        if st.get("who") and st["who"] != user:      # 남에게 맡기셨으면 그분 DM 으로
            from flows.status import tell_assigned
            await tell_assigned(s, c, user)
    STATE["new_task"].pop(user, None); save()
    if not made:
        await _say(s, ch, say("task_fail", err=err or "번호를 못 받았어요"), th)
        return True
    who = st.get("who")
    whom = (load_team().get(who, {}).get("name") or f"<@{who}>") if who else "아직 없어요"
    room = chan(made[0])
    lines = []
    for c in made:
        link = (await api(s, "chat.getPermalink", channel=chan(c),
                          message_ts=c["card_ts"])).get("permalink", "")
        lines.append(f"  • <{link}|{ref(c['no'])}>" if link else f"  • {ref(c['no'])}")
    if len(made) < len(got):                 # 하다가 막혔다 — 올라간 것만 적는다
        text = say("task_done_some", n=len(got), done=len(made), err=err, list="\n".join(lines))
    elif len(made) == 1:
        text = say("task_done", link=lines[0].split("|")[0].lstrip(" •<"), ref=ref(made[0]["no"]),
                   channel=room, who=whom, due=_due_text(st.get("due")))
    else:
        text = say("task_done_many", n=len(made), list="\n".join(lines), channel=room,
                   who=whom, due=_due_text(st.get("due")))
    await _say(s, ch, text, th)
    log(f"할 일 올림 {[c['no'] for c in made]} ← {user}")
    return True
