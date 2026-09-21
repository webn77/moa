"""DM 에서 프로젝트 만들기 — 세 마디면 방과 작업판까지 (2026-09-21 사장님 결정).

**DM 이 시작점이다.** 예전에는 터미널에서 `setup.py --data … --project … --pm …` 처럼
옵션 여섯 개를 외워야 했다. 그건 앱과 토큰을 만드는 사람(관리자) 한 번뿐이어야 하고,
**프로젝트를 늘리는 일은 Slack 안에서** 되어야 한다 — 프로젝트를 늘리는 건 자주 하는 일이니까.

  사람: 프로젝트 만들기
  모아: 어떤 일인가요?            ← 이름
  사람: 결제 개편
  모아: 번호 앞말은 `PAY` 로 할까요?  ← 할 일마다 붙는 앞말. 고칠 기회를 준다
  사람: 네
  모아: 누구와 함께 하나요?         ← @멘션
  사람: @민수 @지은
  모아: ✅ 방 만들고 작업판 붙였어요

**되돌릴 수 없는 것을 만들기 전에 한 번 더 묻는다** — 방과 작업판은 지우기 번거롭다.
그래서 이름을 받자마자 만들지 않고 앞말·사람을 물으며 **오타를 드러낼 기회**를 둔다.

만든 뒤에는 `common.reload_projects()` 로 **그 자리에서** 설정을 다시 읽는다. 안 그러면
만들어 놓고도 다시 띄우기 전까지 봇이 그 프로젝트를 모른다 (그 이유는 그 함수의 설명에).
"""
import json
import re

from common import log, reload_projects
from messages import say
from slack import api
from store import STATE, save


# 시작하는 말. 「프로젝트」 가 들어가고 만들자는 뜻이면 잡는다 — 낱말을 길게 늘어놓는 대신
# 두 조각(프로젝트 + 만들다)이 함께 있는지를 본다 (#71 에서 배운 것)
START = re.compile(r"프로젝트\s*(를)?\s*(하나\s*)?(새로\s*)?(만들|생성|추가|시작)|새\s*프로젝트")
CANCEL = ("취소", "그만", "안 할래", "안할래", "아니요", "아니야", "됐어")
YES = ("네", "웅", "ㅇㅇ", "응", "그래", "좋아", "그걸로", "그거로", "예", "맞아", "ok", "오케이")
ALONE = ("혼자", "나만", "저만", "없어", "없음")
# 앞말은 **영문 2~6글자**. 예전엔 4글자까지라 `charge` 가 튕겼는데 **왜 튕겼는지 말도 안 했다**
# (2026-09-22 사장님 실측 — 답을 했는데 같은 질문이 또 오면 고장으로 보인다)
KEY_OK = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,5}$")

# 사람은 이름만 딱 말하지 않는다 — 「충전성공이라는 프로젝트야」 · 「충전성공 이게 프로젝트 이름이야」.
# 통째로 받으면 그게 방 이름이 되고 카드마다 따라다닌다 (2026-09-22 사장님 실측).
# **순서가 중요하다** — 「이름은 X」 를 먼저 보면 「…이름이야」 의 「야」 를 이름으로 잡는다
CALL = re.compile(r"(새\s*)?프로젝트\s*(를)?\s*(하나\s*)?(새로\s*)?(만들|생성|추가|시작)\S*|새\s*프로젝트")
NAME_CUT = (
    re.compile(r"(.+?)\s*이게\s*(?:프로젝트\s*)?이름"),               # 「X 이게 프로젝트 이름이야」
    re.compile(r"(.+?)\s*(?:이)?라는\s*(?:프로젝트|이름)"),           # 「X 라는 프로젝트」
    re.compile(r"(?:프로젝트\s*)?이름은?\s*[:：]?\s*(\S.{1,})"),      # 「프로젝트 이름은 X」
)
TAIL = re.compile(r"\s*(?:이야|이에요|예요|입니다|이다|야|임|요)\s*[!.~]*$")


def _name_of(text):
    """사람이 한 말에서 **프로젝트 이름만** 골라낸다. 못 고르면 다듬기만 한 원문.

    붙잡는 모양은 시험에 다 적어 뒀다 (tests/test_project.py NameTest). 지어내지 않는다 —
    못 고르면 원문을 주고, 다음 줄에서 「…이군요」 로 되읽어 주므로 사람이 바로 안다.
    """
    s = re.sub(r"\s+", " ", (text or "").strip())
    s = CALL.sub(" ", s).strip(" ,.!~")               # 「프로젝트 하나 만들어줘」 같은 부름말은 뺀다
    for rx in NAME_CUT:
        m = rx.search(s)
        if m and len(m.group(1).strip(" ,.!~")) >= 2:
            s = m.group(1)
            break
    return TAIL.sub("", s).strip(" ,.!~\"'「」")[:60]


def _slug(title):
    """방 이름 — Slack 은 대문자·공백·마침표를 안 받는다. 한글은 받는다."""
    s = re.sub(r"[\s.]+", "-", (title or "").strip().lower())
    s = re.sub(r"[^0-9a-z가-힣\-_]", "", s).strip("-")
    return ("프로젝트-" + s)[:70] or "프로젝트"


def _suggest_key(title, taken):
    """번호 앞말 후보 — 영문이 있으면 그 머리글자, 없으면 P2·P3… (이미 쓰는 것은 피한다).

    한글 이름에서 읽을 만한 약칭을 뽑을 방법이 없다. 지어내서 주는 것보다 **사람에게 묻는 게**
    낫고, 그래도 빈 칸보다는 기본값이 있어야 한 마디로 끝난다.
    """
    letters = re.sub(r"[^A-Za-z]", "", title or "")[:3].upper()
    if letters and letters not in taken:
        return letters
    n = 2
    while f"P{n}" in taken:
        n += 1
    return f"P{n}"


def _asking(user):
    return (STATE.get("new_project") or {}).get(user)


async def _take_title(s, ch, th, st, name):
    """이름을 받고 앞말을 물어본다 — 시작할 때 같이 말했든, 따로 답했든 같은 길."""
    from common import PROJECTS
    st["title"] = name
    st["key"] = _suggest_key(name, {p.get("key") for p in PROJECTS if p.get("key")})
    st["step"] = "key"
    save()
    await _say(s, ch, say("proj_ask_key", title=name, k=st["key"]), th)
    return True


async def _say(s, ch, text, thread=None):
    """**물어본 글 아래 스레드로** (2026-09-22) — 세 마디 대화가 맨 위에 흩어지면 읽기 어렵다."""
    body = {"channel": ch, "text": text, "unfurl_links": False}
    if thread:
        body["thread_ts"] = thread
    await api(s, "chat.postMessage", body=body)


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
    cv = await api(s, "conversations.canvases.create", body={
        "channel_id": ch, "document_content": {"type": "markdown", "markdown": "모아가 곧 채워요."}})
    canvas = cv.get("canvas_id")
    if not canvas:
        log(f"프로젝트 작업판 실패: {cv.get('error')}")      # 방은 살리고 작업판만 비워 둔다 — 나중에 붙일 수 있다
    cfg = dict(config.CFG)
    cfg["projects"] = [dict(p) for p in PROJECTS] + [
        {"key": st["key"], "name": name, "channel": ch, "canvas": canvas,
         "request": (PROJECTS[0].get("request") or PROJECTS[0].get("channel")), "repo": None}]
    config.PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    reload_projects()                      # 다시 띄우지 않아도 이 자리에서 알게 된다
    log(f"프로젝트 만듦 {st['key']} #{name} ({'새로' if made else '있던 방'}) ← {st['by']}")
    return {"channel": ch, "name": name, "people": len(people), "made": made}, None


async def maybe(s, e, q):
    """DM 의 이 말이 프로젝트 만들기와 관련 있으면 처리하고 True. 아니면 False (다른 갈래로 간다)."""
    user, ch = e.get("user"), e.get("channel")
    th = e.get("thread_ts") or e.get("ts")
    st = _asking(user)
    if st is None:
        if not START.search(q):
            return False
        st = {"step": "title", "by": user}
        STATE.setdefault("new_project", {})[user] = st
        # **이름을 같이 말했으면 또 묻지 않는다** — 「프로젝트 하나 만들어줘, 충전성공이야」
        # 라고 했는데 「어떤 일인가요?」 가 오면 사람은 자기 말을 못 들은 줄 안다 (2026-09-22 실측)
        name = _name_of(q)
        if len(name) >= 2:
            return await _take_title(s, ch, th, st, name)
        save()
        await _say(s, ch, say("proj_ask_name"), th)
        return True
    if q.strip() in CANCEL:
        STATE["new_project"].pop(user, None); save()
        await _say(s, ch, say("proj_cancel"), th)
        return True
    from common import PROJECTS
    taken = {p.get("key") for p in PROJECTS if p.get("key")}
    if st["step"] == "title":
        return await _take_title(s, ch, th, st, _name_of(q) or q.strip()[:60])
    if st["step"] == "key":
        word = q.strip()
        if word.lower() not in YES:
            if not KEY_OK.match(word):
                # 같은 질문만 되풀이하면 **답을 못 들은 것처럼** 보인다 (2026-09-22 실측)
                await _say(s, ch, say("proj_key_bad", word=word[:20], k=st["key"]), th)
                return True
            if word.upper() in taken:
                await _say(s, ch, say("proj_key_taken", k=word.upper()), th)
                return True
            st["key"] = word.upper()
        st["step"] = "who"; save()
        await _say(s, ch, say("proj_ask_who"), th)
        return True
    if st["step"] == "who":
        st["who"] = [] if any(a in q for a in ALONE) else re.findall(r"<@(U[A-Z0-9]+)>", q)
        save()
        await _say(s, ch, say("proj_making"), th)
        try:
            got, err = await _create(s, e, st)
        except Exception as ex:                       # 만들다 터져도 묻는 상태로 붙잡아 두지 않는다
            got, err = None, f"{type(ex).__name__}"
        STATE["new_project"].pop(user, None); save()
        if not got:
            await _say(s, ch, say("proj_fail", err=err), th)
            return True
        head = "" if got["made"] else say("proj_exists", name=got["name"]) + "\n"
        await _say(s, ch, head + say("proj_done", title=st["title"], channel=got["channel"],
                                     n=got["people"], k=st["key"]), th)
        return True
    return False
