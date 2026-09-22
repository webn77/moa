"""DM 에서 프로젝트 만들기 — **한 칸씩 묻는다** (2026-09-22 사장님 결정).

  사람: 프로젝트 만들기
  모아: 프로젝트 이름을 알려 주세요            ← ①
  사람: 충전성공
  모아: 번호 앞말은 `CH` 로 할까요?             ← ② 할 일마다 붙는 앞말 (영문 두 자)
  사람: 네
  모아: 무엇을 이루려는 일인가요? 한 줄로        ← ③
  사람: 충전 실패를 하루 40건에서 20건으로 줄인다
  모아: 이렇게 만들까요? → 「네」 → 방·작업판

**왜 AI 에게 통째로 안 맡기나** (2026-09-22 사장님: 「우리는 구독으로 동작하는데 차라리
하나씩 받아서 하는 건 어때?」). 하루 전까지는 대화가 올 때마다 AI 에게 칸을 채우게 했다.
세 마디면 **AI 를 세 번** 부르고, 구독 한도는 그렇게 녹는다. 게다가 AI 가 안 되는 날에는
(9/22 Anthropic 500) 프로젝트를 아예 못 만들었다.

지금은 **뜻을 고를 때 한 번만** 부른다 (`ai.intent`). 흐름이 시작된 뒤로는 AI 가 없어도 돈다 —
묻는 칸이 정해져 있으니 지어낼 것도 없다.

**되돌릴 수 없는 것을 만들기 전에 한 번 더 묻는다** — Slack 은 채널 삭제가 없다. 그래서
칸이 다 차면 만들지 않고 **되읽어 준다.**

**등록 대화는 스레드 안에서 한다** (2026-09-22 사장님 지시: 「스레드에서는 프로젝트명
알려주세요…」 · 「스레드 처리가 안 되는데 이유는?」). 같은 날 오전에 **DM 전체에서** 스레드를
끊었는데, 그건 「글 남겨도 작동을 안 하는데?」 의 원인이 **답이 접힌 스레드 안에 숨어 있던 것**
이었기 때문이다. 그때 등록 대화까지 같이 끊겼다.

둘은 다른 경우다
  · 묻지 않은 답 (찾기·현황·인사) → **맨 위에.** 스레드에 넣으면 사람이 못 본다
  · 물어서 주고받는 대화 (등록)   → **스레드에.** 대여섯 마디가 맨 위에 흩어지면 읽기 어렵고,
    다른 이야기와 섞인다. 사람이 **스레드를 열고 기다리는 중**이라 숨지도 않는다

만든 뒤에는 `common.reload_projects()` 로 **그 자리에서** 설정을 다시 읽는다. 안 그러면
만들어 놓고도 다시 띄우기 전까지 봇이 그 프로젝트를 모른다 (그 이유는 그 함수의 설명에).
"""
import json
import re

from common import log, reload_projects
# 「네」·「취소」·「나중에」 같은 대꾸와 막을 낱말은 **틀에서 가져온다** — 등록 흐름이 둘이
# 되었으니 (프로젝트·할 일) 이 낱말이 두 곳에서 갈라지면 안 된다 (2026-09-22)
from flows.ask import CANCEL, COMMANDS, HEAD, LATER, yes as _yes
from messages import say
from slack import api
from store import STATE, save


# 시작하는 말. 「프로젝트」 가 들어가고 만들자는 뜻이면 잡는다 — 낱말을 길게 늘어놓는 대신
# 두 조각(프로젝트 + 만들다)이 함께 있는지를 본다 (#71 에서 배운 것).
# **이 낱말로 안 걸리는 말은 `ai.intent` 가 받는다** — 그래서 여기를 늘릴 일이 줄었다
START = re.compile(r"프로젝트\s*(를)?\s*(하나\s*)?(새로\s*)?(만들|생성|추가|시작|등록)|새\s*프로젝트")
# 앞말은 **영문 2~6글자**. 사장님이 정한 기본은 **두 자**(`_suggest_key`)지만, 받는 쪽은
# 넓게 둔다 — 예전에 4글자까지라 `charge` 가 튕겼고 **왜 튕겼는지 말도 안 했다**
# (2026-09-22 사장님 실측 — 답을 했는데 같은 질문이 또 오면 고장으로 보인다)
KEY_OK = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,5}$")
# 사람은 앞말만 딱 쓰지 않는다 — 「앞말은 ch로」 · 「CH 로 해줘」 (2026-09-22 사장님 실측).
# 통째로 받으면 「번호 앞말로 쓸 수 없어요」 만 되풀이하며 **대화가 갇힌다**
KEY_IN = re.compile(r"(?:앞말|키|코드)\s*(?:은|는|을|를)?\s*[:：]?\s*([A-Za-z][A-Za-z0-9]{1,5})\b"
                    r"|\b([A-Za-z][A-Za-z0-9]{1,5})\s*(?:로|으로)\s*(?:해|하자|할게|주세요|해줘)?")
# 「목표는 …」 — 확인 단계에서 무엇을 고치려는 말인지 가른다
GOAL_IN = re.compile(r"목표\s*(?:는|은|을|를|이|가)?\s*[:：]?\s*(\S.*)")


def _key_of(text):
    """말에서 번호 앞말만 골라낸다. 못 고르면 빈 글자."""
    s = (text or "").strip()
    if KEY_OK.match(s):
        return s.upper()
    m = KEY_IN.search(s)
    return (m.group(1) or m.group(2)).upper() if m else ""

# 사람은 이름만 딱 말하지 않는다 — 「충전성공이라는 프로젝트야」 · 「충전성공 이게 프로젝트 이름이야」.
# 통째로 받으면 그게 방 이름이 되고 카드마다 따라다닌다 (2026-09-22 사장님 실측).
# **순서가 중요하다** — 「이름은 X」 를 먼저 보면 「…이름이야」 의 「야」 를 이름으로 잡는다
CALL = re.compile(r"(새\s*)?프로젝트\s*(를)?\s*(하나\s*)?(새로\s*)?(만들|생성|추가|시작|등록)\S*|새\s*프로젝트")
NAME_CUT = (
    re.compile(r"(.+?)\s*(?:이게|이|가)\s*(?:프로젝트\s*)?이름"),      # 「X 이게/X이 프로젝트 이름이야」
    re.compile(r"(.+?)\s*(?:이)?라는\s*(?:프로젝트|이름)"),           # 「X 라는 프로젝트」
    re.compile(r"(?:프로젝트\s*)?이름은?\s*[:：]?\s*(\S.{1,})"),      # 「프로젝트 이름은 X」
)
TAIL = re.compile(r"\s*(?:이야|이에요|예요|입니다|이다|야|임|요)\s*[!.~]*$")
_HEAD = re.compile(HEAD, re.I)        # 머리말 떼기 — 틀에 있는 낱말로 (flows/ask.py)


def _name_of(text):
    """사람이 한 말에서 **프로젝트 이름만** 골라낸다. 못 고르면 다듬기만 한 원문.

    붙잡는 모양은 시험에 다 적어 뒀다 (tests/test_project.py NameTest). 지어내지 않는다 —
    못 고르면 원문을 주고, 다음 줄에서 「…이군요」 로 되읽어 주므로 사람이 바로 안다.
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    # 머리말은 이름이 아니다 — 「**오케이** 충전성공으로 …」 를 통째로 받으면 방 이름이 그렇게 된다
    # (2026-09-22 사장님 실측: 「오케이 충전성공으로」 가 이름이 됐다)
    s = _HEAD.sub("", s)
    s = CALL.sub(" ", s).strip(" ,.!~")               # 「프로젝트 하나 만들어줘」 같은 부름말은 뺀다
    # 꼬리에 붙은 「…으로 만들자」 · 「…로 만들어줘」 도 이름이 아니다
    s = re.sub(r"\s*(?:으로|로)?\s*(?:만들|생성|추가|시작|등록)\S*\s*$", "", s).strip()
    s = re.sub(r"(으로|로)\s*$", "", s).strip()        # 「충전성공으로」 → 「충전성공」
    for rx in NAME_CUT:
        m = rx.search(s)
        if m and len(m.group(1).strip(" ,.!~")) >= 2:
            s = m.group(1)
            break
    return TAIL.sub("", s).strip(" ,.!~\"'「」")[:60]


def _fix_of(text):
    """확인 단계에서 **무엇을 고치려는 말인지** 본다. 못 알아들으면 (None, None).

    통째로 이름으로 받으면 「아니 그게 아니고…」 가 프로젝트 이름이 된다. 그래서
    **알아들은 것만** 고치고, 못 알아들으면 다시 보여 주며 어떻게 말하면 되는지 알려 준다.
    """
    s = _HEAD.sub("", (text or "").strip())       # 「아니 …」 의 「아니」 가 이름에 붙었다
    m = GOAL_IN.search(s)
    if m:
        return "goal", m.group(1).strip()[:120]
    k = _key_of(s)
    if k:
        return "key", k
    for rx in NAME_CUT:
        m = rx.search(s)
        if m and _nameable(m.group(1)):
            return "name", TAIL.sub("", m.group(1)).strip(" ,.!~\"'「」")[:60]
    return None, None


def _slug(title):
    """방 이름 — Slack 은 대문자·공백·마침표를 안 받는다. 한글은 받는다."""
    s = re.sub(r"[\s.]+", "-", (title or "").strip().lower())
    s = re.sub(r"[^0-9a-z가-힣\-_]", "", s).strip("-")
    return ("프로젝트-" + s)[:70] or "프로젝트"


def _repo_name(title):
    """프로젝트 이름으로 레포 이름을 짓는다 — `사내포털` → `사내포털`.

    **글자 수로 자르면 안 된다** — 「프로젝트-」 는 다섯 글자인데 넷을 잘라 `-사내포털` 이 됐다
    (2026-09-22 시뮬레이션이 잡았다). 앞뒤 하이픈도 떼어 낸다: GitHub 이 안 받는다.
    """
    s = re.sub(r"^프로젝트-", "", _slug(title)).strip("-")
    return s or "moa"


def _suggest_key(title, taken):
    """번호 앞말 후보 — **영문 두 자** (사장님이 정함: 「앞 2자리 구분 영문 대문자」).

    영문 이름이면 그 머리 두 자, 한글만이면 P2·P3… (이미 쓰는 것은 피한다). 한글 이름에서
    읽을 만한 약칭을 뽑을 방법이 없다. 지어내서 주는 것보다 **사람에게 묻는 게** 낫고,
    그래도 빈 칸보다는 기본값이 있어야 한 마디로 끝난다.
    """
    letters = re.sub(r"[^A-Za-z]", "", title or "")[:2].upper()
    if len(letters) == 2 and letters not in taken:
        return letters
    n = 2
    while f"P{n}" in taken:
        n += 1
    return f"P{n}"


def _asking(user):
    return (STATE.get("new_project") or {}).get(user)


def _nameable(name):
    """방 이름으로 쓸 글자가 두 자 이상 남나 — 이모지·기호만이면 방 이름이 통째로 비어
    모든 프로젝트가 `#프로젝트` 하나로 뭉친다 (2026-09-22 예외 시험)."""
    return len(re.sub(r"[^0-9A-Za-z가-힣]", "", name or "")) >= 2


def _taken():
    from common import PROJECTS
    return {p.get("key") for p in PROJECTS if p.get("key")}


async def _say(s, ch, text, thread=None):
    """**등록 대화는 스레드 안에서** — 묻고 답하는 대여섯 마디를 한 덩이로 묶는다.

    맨 위에 답하는 것은 **묻지 않은 답**(찾기·현황·인사)일 때다. 그건 숨으면 안 되니까.
    여기는 사람이 **스레드를 열고 기다리는 중**이라 숨지 않는다 (위 설명에 그 갈림).
    """
    body = {"channel": ch, "text": text, "unfurl_links": False}
    if thread:
        body["thread_ts"] = thread
    await api(s, "chat.postMessage", body=body)


# 「안 할래요」 — **어미가 변한다.** 목록으로 맞추면 「안 할래」 는 되고 「안 할래요」 는 안 된다
# (2026-09-22 시험이 잡았다 — 이 저장소가 네 번째로 같은 함정에 빠졌다)
NO_REPO = re.compile(r"^\s*(안\s*할|안할|아니|없|괜찮|필요\s*없|나중|건너|스킵|skip|패스)", re.I)
# 몇 단계 중 어디인지 — 되물을 때마다 같이 알린다.
# **4번째 칸은 있을 때만 센다** (2026-09-22 사장님: 「프로젝트 등록 할때 붙이는건 어떨까?」) —
# `gh` 가 없거나 로그인이 안 되어 있으면 묻지 않고, 그러면 세 칸이다
STEP_WHAT = {"title": "프로젝트 이름", "key": "번호 앞말", "goal": "목표 한 줄",
             "repo": "기록을 어디에", "confirm": "마지막 확인"}


def _steps(st):
    # **늘 넷이다** (2026-09-22 사장님: 「고객에게 github주소를 물어보고 연결하는거 까지 하면
    # 되는거 아님?」). 예전에는 `gh` 가 있는지 보고 물을지 말지를 정했는데, 그러면 칸 수가
    # 맥마다 달라지고 **사내 서버는 `gh` 로 보이지도 않는다.** 그냥 묻고, 주시는 대로 쓴다
    return ["title", "key", "goal", "repo"]


def _where(st):
    steps, step = _steps(st), st.get("step") or "title"
    n = steps.index(step) + 1 if step in steps else len(steps)
    return n, len(steps), STEP_WHAT.get(step, "확인")


async def _again(s, ch, th, st, text):
    """물은 것과 다른 답이 왔을 때 — **어디에 있는지와 나가는 길**을 늘 함께 알린다.

    (2026-09-22 사장님: 「만약 다른 대답하면 다시 되물어서 프로젝트 등록 진행 단계라고
    안내하고 취소하려면 나가면 된다고 안내」.) 되묻는 말만 오면 사람은 자기가 **어디에
    갇혔는지** 모른다 — 9/22 에 실제로 그랬다. 답이 안 맞는 건 사람 잘못이 아니고,
    봇이 지금 무엇을 묻는 중인지 말하지 않은 탓이다.
    """
    n, total, what = _where(st)
    await _say(s, ch, text + "\n\n" + say("ask_where", kind="프로젝트 등록",
                                        n=n, total=total, what=what), th)
    save()
    return True


async def _ask_key(s, ch, th, st):
    st["step"] = "key"
    save()
    await _say(s, ch, say("proj_ask_key", title=st["title"], k=st["key"]), th)
    return True


async def _ask_goal(s, ch, th, st):
    st["step"] = "goal"
    save()
    await _say(s, ch, say("proj_ask_goal", title=st["title"], k=st["key"]), th)
    return True


def _repo_target(text):
    from flows.repo import target_of
    return target_of(text)


async def _ask_repo(s, ch, th, st):
    """**번호로 고르게 한다** — 주소를 찾아 와서 붙여넣는 건 번거롭다 (사장님 지적).

    목록은 `gh` 가 준 **있는 레포**다. 못 받으면(로그인 없음·`gh` 없음) 주소를 적어 달라고 한다.
    """
    st["step"] = "repo"
    try:
        from flows.repo import mine, repo_lines
        got = await mine()
    except Exception as ex:
        log(f"레포 목록 실패: {type(ex).__name__}")
        got = []
    st["repos"] = [r for r, _, _ in got]
    # 「새로 만들어줘」 를 위해 **올릴 곳**을 기억해 둔다 — 요즘 쓰는 레포의 주인이다
    st["owner"] = st["repos"][0].split("/")[0] if st["repos"] else ""
    save()
    from flows.repo import repo_lines as lines
    await _say(s, ch, say("proj_ask_repo", step=f"{_where(st)[0]}\ufe0f\u20e3", list=lines(got)), th)
    return True


async def _show(s, ch, th, st):
    """되읽어 준다 — **되돌릴 수 없는 일 앞에는 늘 확인이 있다.**"""
    st["step"] = "confirm"
    save()
    where = say("proj_repo_no")
    if st.get("repo"):
        where = say("proj_repo_github" if st.get("rkind") == "github" else "proj_repo_git",
                    repo=st["repo"], make=" (제가 비공개로 만들어요)" if st.get("rmake") else "")
    await _say(s, ch, say("proj_confirm", title=st["title"], k=st["key"],
                          goal=st.get("goal") or "(아직 없음)", where=where), th)
    return True


async def _take_title(s, ch, th, st, name):
    """이름을 받고 앞말을 물어본다 — 시작할 때 같이 말했든, 따로 답했든 같은 길."""
    if not _nameable(name):
        st["step"] = "title"
        return await _again(s, ch, th, st, say("proj_name_bad", word=(name or "")[:20] or "빈 글자"))
    st["title"] = name
    st["key"] = _suggest_key(name, _taken())
    return await _ask_key(s, ch, th, st)


async def _create(s, e, st):
    """방·작업판을 만들고 설정에 넣는다. 실패하면 만들다 만 것을 남기지 않는다."""
    import config
    from common import PROJECTS
    name, ch = _slug(st["title"]), None
    d = await api(s, "conversations.create", body={"name": name})
    if d.get("ok"):
        ch, made = d["channel"]["id"], True
    elif d.get("error") == "name_taken":                 # 이미 있으면 그걸 쓴다 — 두 개로 갈라지면 더 헷갈린다
        made = False
        lst = await api(s, "conversations.list", types="public_channel", exclude_archived="true", limit=1000)
        hit = next((c for c in lst.get("channels") or [] if c["name"] == name), None)
        if not hit:
            return None, d.get("error") or "name_taken"
        ch = hit["id"]
        await api(s, "conversations.join", body={"channel": ch})
    else:
        return None, d.get("error") or "unknown"
    people = [st["by"]] + [u for u in st.get("who") or [] if u != st["by"]]
    if people:
        await api(s, "conversations.invite", body={"channel": ch, "users": ",".join(people)})
    # **물어본 목표는 보이는 곳에 남긴다** — 답이 어디에도 안 나오는 질문은 질문이 아니라 고장이다.
    # 이 작업판은 봇이 10분마다 그리는 본 작업판과 다르다 (그건 팀 하나에 하나뿐이다).
    # 그래서 여기에 **사람이 방금 한 말**을 적어 둔다 — 열면 무엇을 이루려는 방인지 바로 안다
    goal = (st.get("goal") or "").strip()
    head = (f"# 📋 {st['title']} 작업판\n\n"
            + (f"## 🎯 목표\n\n**{goal}**\n\n" if goal else "")
            + f"할 일은 `{st['key']}-1` 부터 번호가 붙어요. 이 방에 「🎫 무슨 일」 이라고 한 줄 쓰시면 돼요.\n")
    cv = await api(s, "conversations.canvases.create", body={
        "channel_id": ch, "document_content": {"type": "markdown", "markdown": head}})
    canvas = cv.get("canvas_id")
    if not canvas:
        log(f"프로젝트 작업판 실패: {cv.get('error')}")      # 방은 살리고 작업판만 비워 둔다 — 나중에 붙일 수 있다
    cfg = dict(config.CFG)
    cfg["projects"] = [dict(p) for p in PROJECTS] + [
        {"key": st["key"], "name": name, "channel": ch, "canvas": canvas, "goal": goal or None,
         "request": (PROJECTS[0].get("request") or PROJECTS[0].get("channel")), "repo": None}]
    config.PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    reload_projects()                      # 다시 띄우지 않아도 이 자리에서 알게 된다
    log(f"프로젝트 만듦 {st['key']} #{name} ({'새로' if made else '있던 방'}) ← {st['by']}")
    return {"channel": ch, "name": name, "people": len(people), "made": made,
            "canvas": canvas, "canvas_err": cv.get("error")}, None


async def maybe(s, e, q, force=False):
    """DM 의 이 말이 프로젝트 만들기와 관련 있으면 처리하고 True. 아니면 False.

    **한 칸씩 묻는다** — ①이름 ②앞말 ③목표, 그리고 확인. 여기서는 AI 를 부르지 않는다:
    구독 한도를 세 배로 쓰지 않고, AI 가 안 되는 날에도 프로젝트를 만들 수 있다.

    `force` 는 `ai.intent` 가 「프로젝트 등록이다」 라고 고른 경우다 — 낱말로는 안 걸리지만
    뜻은 그것인 말(「새로 시작하는 일 하나 올려줘」)을 여기로 들여보낸다.
    """
    user, ch = e.get("user"), e.get("channel")
    st, opened = _asking(user), False
    if st is None:
        if not (force or START.search(q)):
            return False
        opened = True
        # **부르신 글 아래에 스레드를 연다** — 등록 대화는 여기서 끝까지 이어진다.
        # 사람이 맨 위에 답을 써도 흐름은 이어지고, 답은 이 스레드에 모인다
        st = {"by": user, "step": "title", "th": e.get("thread_ts") or e.get("ts")}
        # **4번째 칸을 물을 수 있나** — `gh` 가 없거나 로그인이 안 되어 있으면 묻지 않는다.
        # 물어 놓고 마지막에 「gh 가 없어요」 라고 하면 헛일을 시킨 것이다.
        # 여는 자리에서 한 번만 본다 — 칸 수를 세어 「n/4」 라고 말해야 하니까
        STATE.setdefault("new_project", {})[user] = st
        save()
    # **연 스레드 안에서 이어 간다.** 사람이 맨 위에 답을 써도 (thread_ts 없이) 답은 이
    # 스레드에 모인다 — 대여섯 마디가 한 덩이로 남아야 나중에 읽을 수 있다.
    # 다른 스레드에서 말했으면 그 스레드에 답한다 — 그건 사람이 만든 덩이다
    th = e.get("thread_ts") or st.get("th")
    if opened:
        # **뜻으로 들어온 말에서는 이름을 줍지 않는다** (2026-09-22 시뮬레이션이 잡았다).
        # 「새로 시작하는 일 하나 올려줘」 가 통째로 프로젝트 이름이 됐다 — AI 는 「프로젝트를
        # 만들려는 말」 이라고만 했고 **이름이 무엇인지는 말하지 않았다.** 부름말(`START`)이
        # 걸렸을 때만 그 말에서 부름말을 떼고 남은 것을 이름으로 본다
        name = _name_of(q) if START.search(q) else ""
        if _nameable(name):                 # 시작하는 말에 이름이 함께 오면 그것부터 받는다
            return await _take_title(s, ch, th, st, name)
        await _say(s, ch, say("proj_ask_name"), th)
        return True
    if q.strip() in CANCEL:
        STATE["new_project"].pop(user, None); save()
        await _say(s, ch, say("proj_cancel"), th)
        return True
    if q.strip() in COMMANDS:          # 「현황」 이 프로젝트 이름이 되면 안 된다
        return await _again(s, ch, th, st, say("ask_busy", word=q.strip(), kind="프로젝트를 만드는"))

    step, taken = st.get("step"), _taken()

    # ① 이름
    if step == "title":
        return await _take_title(s, ch, th, st, _name_of(q))

    # ② 번호 앞말 — 「네」 면 제안한 것, 아니면 말에서 골라낸다
    if step == "key":
        w = q.strip()
        if not _yes(q):
            got = _key_of(q)
            if not got:
                # **이름을 고치려는 말이면 이름으로 돌아간다** — 「아니 X가 이름이야」 를 앞말로 받으면
                # 「앞말로 쓸 수 없어요」 만 되풀이하며 대화가 갇힌다 (2026-09-22 실측).
                #
                # 다만 **또렷이 말한 것만** 받는다 (`_fix_of`). 예전에는 `_name_of` 로 받았는데
                # 「이건 앞말이 아니라 문장이에요」 가 **프로젝트 이름이 됐다** (시험이 잡았다) —
                # 꼬리말만 떼면 아무 문장이나 이름처럼 보인다
                what, val = _fix_of(q)
                if what == "name":
                    return await _take_title(s, ch, th, st, val)
                if what == "goal":                 # 「목표는 …」 을 미리 말했으면 받아 두고 앞말만 다시
                    st["goal"] = val
                    return await _ask_key(s, ch, th, st)
                return await _again(s, ch, th, st, say("proj_key_bad", word=w[:20], k=st["key"]))
            if got in taken:
                return await _again(s, ch, th, st, say("proj_key_taken", k=got))
            st["key"] = got
        return await _ask_goal(s, ch, th, st)

    # ③ 목표 한 줄 — 지금 못 정할 수도 있다. **묻는 칸 때문에 갇히지 않는다**
    if step == "goal":
        w = q.strip()
        if any(x in w for x in LATER):
            st["goal"] = ""
        elif len(re.sub(r"\s", "", w)) < 4:
            return await _again(s, ch, th, st, say("proj_goal_bad"))
        else:
            st["goal"] = w[:120]
        return await _ask_repo(s, ch, th, st)

    # ④ 기록을 어디에 쌓을까 — **있을 때만 묻는다.** 「안 할래요」 면 이 맥에만 둔다
    if step == "repo":
        if NO_REPO.match(q.strip()):
            st["repo"], st["rkind"] = "", ""
            return await _show(s, ch, th, st)
        from flows.repo import pick
        picked = pick(q, [(r, 0, 0) for r in st.get("repos") or []])
        if picked:                          # 번호로 고르셨다 — 가장 쉬운 길
            st["repo"], st["rkind"], st["rmake"] = picked, "github", False
            return await _show(s, ch, th, st)
        make = any(x in q for x in ("만들어", "새로", "만들자", "생성"))
        kind, target = _repo_target(q)
        if not target and make and st.get("owner"):
            # **이름을 안 주셔도 만든다** — 프로젝트 이름으로 (사장님: 「쉽게 할 방법?」)
            st["repo"], st["rkind"], st["rmake"] = f"{st['owner']}/{_repo_name(st['title'])}", "github", True
            return await _show(s, ch, th, st)
        if not target:
            return await _again(s, ch, th, st, say("proj_repo_bad", word=q.strip()[:30] or "빈 글자"))
        st["repo"], st["rkind"] = target, kind
        st["rmake"] = kind == "github" and make
        return await _show(s, ch, th, st)

    # ④ 확인 — **고치자는 말을 먼저 본다.** 「그래 앞말은 PAY 로」 처럼 맞장구와 고칠 것이
    # 한 문장에 올 수 있다 (2026-09-22). 대꾸를 먼저 보면 옛 값 그대로 만들어 버린다
    what, val = _fix_of(q)
    if what == "key" and val in taken:
        return await _again(s, ch, th, st, say("proj_key_taken", k=val))
    if what == "key":
        st["key"] = val
    elif what == "name":
        st["title"] = val
    elif what == "goal":
        st["goal"] = val
    elif _yes(q):
        return await _build(s, ch, th, st, user)
    else:
        return await _again(s, ch, th, st, say("proj_fix_how"))
    return await _show(s, ch, th, st)


async def _build(s, ch, th, st, user):
    """정말로 만든다 — 여기까지 오면 확인을 받은 것이다."""
    await _say(s, ch, say("proj_making"), th)
    try:
        got, err = await _create(s, {"user": user}, st)
    except Exception as ex:
        got, err = None, f"{type(ex).__name__}"
    STATE["new_project"].pop(user, None); save()
    if not got:
        await _say(s, ch, say("proj_fail", err=err), th)
        return True
    head = "" if got["made"] else say("proj_exists", name=got["name"]) + "\n"
    detail = say("proj_canvas_ok") if got["canvas"] else say("proj_canvas_no", err=got["canvas_err"] or "이유 모름")
    goal = (st.get("goal") or "").strip()
    detail += say("proj_goal_ok", goal=goal) if goal else say("proj_goal_no")
    # **방을 먼저 만들고 그다음 붙인다** — 붙이다 막혀도 방과 작업판은 남는다.
    # 몸은 `flows/repo.py` 의 `attach` 하나다 (입구가 둘, 몸은 하나)
    if st.get("repo"):
        from flows.repo import attach
        lines, err = await attach(st["repo"], st["key"], st.get("rmake"), st.get("rkind") or "github")
        detail += "".join(lines) if lines else ""
        detail += say("proj_repo_fail", err=str(err)[:120]) if err else ""
    else:
        detail += say("proj_repo_no_done")        # 이 맥에만 둔다 — 그게 무슨 뜻인지 말한다
    await _say(s, ch, head + say("proj_done", title=st["title"], channel=got["channel"],
                                 n=got["people"], k=st["key"], detail=detail), th)
    return True
