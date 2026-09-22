"""이슈 찾기 — 번호를 몰라도 찾는다 (#54).

「누가 뭐 하고 있나」를 보려면 번호를 알아야 했다. 앱 홈까지 건너가야 했고, 대화하던 자리를 떠나야 했다.
그래서 `@PA` 뒤에 몇 글자만 쓰면 그 자리에서 목록이 뜨게 한다.

  @PA 목록            열려 있는 이슈 전부
  @PA 내 할 일         내가 맡은 일
  @PA 홍길동          그 사람이 맡은 일  (`@홍길동` 멘션도 같다)
  @PA 담당 없는 일     아무도 안 맡은 일
  @PA 캔버스          제목·정의에 그 말이 든 이슈

답은 **부른 사람에게만 보인다** — 채널을 어지럽히지 않는다. 읽기만 하고 아무것도 바꾸지 않는다 (#11).
"""
import re

from common import log
from messages import say
from docs import due_text, load_team
from slack import api
from common import PREFIX
from store import STATE, open_cards, save, tag


ALL = ("목록", "리스트", "전체", "다 보여", "전부")
# **띄어쓰기로 갈라지지 않는다** (#71, 2026-09-21 사장님 실측) — 「내 할일」 이 안 먹혔다.
# 목록에 「내 할 일」(다 띄움)·「내할일」(다 붙임)만 있고 **중간 형태가 빠져** 있었다.
# 한국어는 띄어쓰기가 흔들리므로 `_norm()` 이 양쪽에서 공백을 지운 뒤 맞춰 본다.
MINE = ("내 할 일", "내 일", "내꺼", "내 거", "내 것", "나는", "내가", "제 할 일", "내 업무", "제 업무",
        "나 뭐해", "뭐해야", "뭐 하면 되", "맡은 일")
NOBODY = ("담당 없는", "아무도", "빈 일", "안 맡은")
# **물음표는 도움말이 아니다** (#71, 2026-09-21 실측). 예전에는 "?" 가 여기 있어서
# 「PA-68 어떻게 됐어?」 · 「지금 뭐 해?」 처럼 **물음표가 든 질문이 전부 인사말을 받았다.**
HELP = ("도움말", "뭐 할 수 있", "뭘 할 수 있", "어떻게 써", "사용법", "help")
# 인사에는 인사로 답한다 — 검색어로 받으면 「‘하이’ 로 찾은 이슈 없어요」 가 나온다 (2026-09-20 실측)
HELLO = ("하이", "안녕", "헬로", "hi", "hello", "반가", "여보세요", "ㅎㅇ")
# 인사는 아니지만 검색할 말도 아니다 — 짧게 받고 만다 (#71)
# PA-68 · #68 둘 다 받는다 — 읽는 쪽(handlers.REF)과 같은 규칙이다 (#66·#71)
NUM = re.compile(r"(?:^|[\s(\[<])(?:" + (re.escape(PREFIX) + r"-|" if PREFIX else "") + r"#)(\d{1,4})\b")
CHAT = ("고마워", "고맙", "감사", "수고", "잘했", "좋아요", "ㅋㅋ", "ㅎㅎ", "굿", "오케이", "ㅇㅇ", "넵", "네네")


def _norm(s):
    """맞춰 보기 전에 공백을 지운다 — 「내 할일」·「내할일」·「내 할 일」 은 같은 말이다 (#71).

    **찾을 말(q)과 목록의 낱말 양쪽에 똑같이 해야 한다** — 한쪽만 하면 공백이 든 낱말이 영영 안 걸린다.
    """
    return re.sub(r"\s+", "", s or "").lower()


def _has(q, keys):
    n = _norm(q)
    return any(_norm(k) in n for k in keys)


def guide_for(user, seen):
    """처음 보는 사람에게만 여섯 줄 안내, 그 뒤엔 한 줄 (#71, 2026-09-21 사장님 지적).

    못 알아들을 때마다 안내를 통째로 붙였더니 **같은 글을 연달아 두 번** 보게 됐다.
    `seen` 은 DM 인사를 이미 받은 사람 목록(STATE["greeted"]) 이다 — 그 인사에 안내가 들어 있어서
    **본 사람을 세는 목록이 하나면 된다.** 둘로 나누면 DM 쪽과 채널 쪽이 어긋난다.
    """
    return say("guide_more") if user and user in (seen or []) else say("guide")


def _line(c, team):
    from views.card import state_line
    return f"{state_line(c, team).split(' · ')[0]}  *#{c['no']}* {c['title'][:52]}" \
           + (f"  ·  {due_text(c)}" if due_text(c) else "")


def _list(title, cards, team, tail=None):
    if not cards:
        return f"*{title}*\n없어요."
    rows = "\n".join(_line(c, team) for c in cards[:15])
    more = f"\n…외 {len(cards) - 15}건" if len(cards) > 15 else ""
    return f"*{title}* {len(cards)}건\n{rows}{more}\n\n_{tail or '번호를 쓰면 그 자리에서 펼쳐져요 — 예: #' + str(cards[0]['no'])}_"


def _order(cards):
    from core import plevel
    return sorted(cards, key=lambda c: ({"doing": 0, "blocked": 1, "review": 2}.get(c["status"], 3),
                                        plevel(c), c.get("rank", 99)))


def _who(q, team):
    """이름이나 멘션으로 사람을 찾는다. 못 찾으면 None."""
    hit = re.search(r"<@(U[A-Z0-9]+)>", q)
    if hit and hit.group(1) in team:
        return hit.group(1)
    for uid, t in team.items():
        if t.get("name") and t["name"] in q:
            return uid
    return None


def reply_for(q, user, team, seen=()):
    """무엇을 물었나 → 보여 줄 글. 순수 계산이라 시험할 수 있다. `seen` 은 안내를 이미 본 사람들."""
    q = re.sub(r"<@U[A-Z0-9]+>\s*", lambda m: m.group(0) if "U" in m.group(0) else "", q).strip()
    nm = lambda u: team.get(u, {}).get("name", "그분")
    # **도움말은 언제나 전부** — 달라고 한 것이니까. 인사는 아니다 (#71)
    if _has(q, HELP):
        return say("hello") + "\n\n" + say("guide")
    if not q or (len(q) <= 8 and _has(q, HELLO)):
        return say("hello") + "\n\n" + guide_for(user, seen)
    # **우리가 만든 표기를 우리가 알아듣는다** (#71) — `PA-68` · `#68` 이 들어오면 그 이슈를 보여 준다
    hit = NUM.search(q)
    if hit:
        c = next((x for x in STATE["cards"].values() if x["no"] == int(hit.group(1))), None)
        if c:
            return _list(f"🔍 {tag(c['no'], c)}", [c], team, "자세한 것은 카드의 📄 상세에서 볼 수 있어요")
        return say("no_such_issue", no=hit.group(1))
    if len(q) <= 12 and _has(q, CHAT):      # 잡담을 검색어로 받지 않는다
        return say("chat_back")
    if _has(q, NOBODY):
        return _list("🙋 아무도 안 맡은 일", _order([c for c in open_cards() if not c.get("assignee")]), team)
    if _has(q, MINE):                    # 내 것을 먼저 본다 — 「내 목록」 은 목록이 아니라 내 것
        return _list("📋 내가 맡은 일", _order([c for c in open_cards() if c.get("assignee") == user]), team)
    if _has(q, ALL):
        return _list("📋 열려 있는 할 일", _order(open_cards()), team,
                     "좁혀 보시려면 `@PA 내 할 일` · `@PA 홍길동` · `@PA 담당 없는 일` · `@PA 캔버스`")
    uid = _who(q, team)
    if uid:
        return _list(f"📋 {nm(uid)} 님이 맡은 일", _order([c for c in open_cards() if c.get("assignee") == uid]), team)
    word = re.sub(r"<@U[A-Z0-9]+>", "", q).strip()[:40]
    # **물음표로 끝나면 찾아 달라는 말이 아니다** (PA-75, 2026-09-21 실측).
    # 「뭘 도와줄 수 있어?」 가 검색어가 되어 **「봇이 자기가 뭘 하는지 설명한다」 카드를 찾아왔다** —
    # 봇에게 물은 것을 이슈 제목으로 받은 셈이다. 0건일 때 거르는 것만으로는 늦다
    if word.rstrip().endswith("?"):
        return say("dont_get_it") + "\n\n" + guide_for(user, seen)
    hits = [c for c in STATE["cards"].values()
            if word.lower() in (c["title"] + " " + " ".join(str(v) for v in (c.get("spec") or {}).values())).lower()]
    if not hits:
        # 못 찾았으면 **무엇을 할 수 있는지 같이 보여 준다** — 「없어요」 만 하면 다음에 뭘 해야 할지 모른다.
        # 낱말 셋이 넘거나 물음표로 끝나면 **찾아 달라는 말이 아니다** — 「'지금 뭐 해?' 로는 못 찾았어요」 는
        # 검색어로 받았다는 뜻이라 봇이 말귀를 못 알아듣는 것처럼 보인다 (#71)
        if len(word.split()) >= 3 or word.rstrip().endswith(("?", "요", "까", "나")):
            return say("dont_get_it") + "\n\n" + guide_for(user, seen)
        return say("find_none", word=word) + "\n\n" + guide_for(user, seen)
    return _list(f"🔍 「{word}」 로 찾은 할 일", _order([c for c in hits if c["status"] not in ("done", "cancelled")])
                 + [c for c in hits if c["status"] in ("done", "cancelled")], team)


async def find(s, e, q):
    """부른 사람에게만 보이게 답한다."""
    # 채널에서만 `@PA` 를 쓰는 사람은 DM 인사를 받은 적이 없다 — 여기서 여섯 줄을 보여 줬으면
    # **그 사람도 본 것으로 센다.** 안 그러면 채널 사용자는 영영 매번 전체 안내를 본다 (#71)
    seen = STATE.setdefault("greeted", [])
    user = e.get("user")
    text = reply_for(q, user, load_team(), seen)
    if user and user not in seen and say("guide") in text:
        seen.append(user)
        save()
    body = {"channel": e["channel"], "text": text, "unfurl_links": False}
    if e.get("channel_type") == "im":                  # DM 은 원래 나만 보는 자리다
        # **DM 에서도 스레드가 기본이다** (2026-09-22 사장님: 「dm 스레드가 기본이야
        # 없애지말아줘」). 물은 글과 답이 붙어 있어야 나중에 무엇에 대한 답인지 안다 —
        # 답이 맨 위에 쭉 쌓이면 질문과 답의 짝이 흩어진다.
        #
        # 같은 날 오전에 이걸 껐던 적이 있다. 「글 남겨도 작동을 안 하는데?」 의 원인을
        # 접힌 스레드로 봤는데, **진짜 원인은 AI 가 죽어 있던 것**이었다 (Anthropic 500).
        # 한 번에 두 가지를 고치면 어느 쪽이 원인인지 못 가린다 — 이번엔 스레드를 남긴다
        body["thread_ts"] = e.get("thread_ts") or e["ts"]
        await api(s, "chat.postMessage", body=body)
    else:
        await api(s, "chat.postEphemeral", body={**body, "user": e.get("user")})
    log(f"찾기: {q[:40]} ← {e.get('user')}")
