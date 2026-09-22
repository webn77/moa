"""이벤트 · 버튼 · 명령 · 창 제출을 받아 flows 로 넘긴다 (#30 에서 bot.py 를 나눔)."""
import asyncio, time, re, datetime
from messages import say
from common import BOT, CHANNEL, PROJECTS, HERE, ISSUE_NAME, REACT, PREFIX, REQUEST, log, plevel  # noqa: E402,F401
from docs import load_stages, load_team  # noqa: E402,F401
from slack import api, mood, name_of, thinking, working  # noqa: E402,F401
from store import STATE, chan, add_log, ann_snap, decision_snap, log_line, progress, save  # noqa: E402,F401


def is_mine(e):
    """봇이 보낸 메시지인가. 이름을 실어 보내기 시작한 뒤로(9/20) 「이름 없음」 으로는 구분할 수 없다 —
    이걸 놓쳐서 봇이 자기 카드를 사람 요청으로 읽고 카드를 31개 만들었다. 가상 팀원(다른 이름)만 사람 취급한다."""
    return bool(e.get("bot_id")) and e.get("username", BOT) == BOT


# 「할 일로 만들어줘」 — **옛말(이슈)도 계속 받는다.** 안내는 「할 일」 로 바꿨지만 손에 익은 분과
# 옛 스레드가 있다 (2026-09-21, research/words.md)
MAKE = re.compile(r"(할\s*일|이슈)(로)?\s*(만들|등록|올려|생성)|등록해|만들어\s*줘|티켓")
# 새 이슈는 **@PA 에게 말하는 것**으로 통일한다 (사장님 지적 9/20).
# 한때 「이슈:」 「요청:」 「티켓:」 을 더 받았는데, 이모지가 불편하다는 말에 말을 세 개 더 만든 꼴이었다 —
# 줄여야 할 때 늘렸다. 🎫 는 손에 익은 분을 위해 남기되 안내에는 쓰지 않는다.
NEW = re.compile(r"^\s*(?:🎫|:ticket:)\s*(.+)", re.S)
MERGE = re.compile(r"(?:합치|중복|같은\s*일|묶어)\D*#?(\d{1,4})|#(\d{1,4})\D*(?:합치|중복|같은\s*일|묶어)")
# **AI 를 부를지 말지 가르는 값싼 울타리** (2026-09-22). 뭘 **만들자는 뜻**이 있을 법한
# 말에만 `ai.intent` 를 부른다 — 모든 DM 을 AI 에게 보이면 구독 한도가 인사말에도 녹는다.
#
# **만드는 낱말만 본다.** 처음에는 「프로젝트」·「할 일」·「이슈」 도 넣었는데 「내 할 일」 이
# 걸렸다 (시험이 잡았다) — 그건 찾는 말이고, 찾는 말마다 AI 를 부르면 아낀 게 없다.
ASKISH = re.compile(r"만들|생성|등록|추가|시작|올려|새로|새\s*프로젝트|잡아\s*줘")
# 백슬래시가 둘이면 「빈칸」 이 아니라 「\ 글자」 를 찾는다 — 그래서 「@PA 이슈 정리」 가 안 먹었다 (2026-09-20 발견)
TIDY = re.compile(r"^(정리|이슈\s*정리|치우|비우)")     # 「@PA 정리」 — 카드 스레드 밖에서만
ORDER = re.compile(r"^(순서|선행|앞선\s*일|의존)")      # 「@PA 순서」 — 선행을 한 화면에 모아 고치기 (#68)
MTG = re.compile(r"^(회의|미팅)(?!록)\s*(?:(?:카드|열어|잡아|만들|시작)\w*\s*)*[:：]?\s*(?:#(\d+)\s*)?(.*)$")   # 「@PA 회의 스프린트 점검」
# 문장 속 #40 · PA-40 — 붙여 쓴 글자 뒤는 안 잡는다. **둘 다 받는다**: 옛 스레드·캔버스에
# 맨 `#40` 이 이미 깔려 있고, 앞말을 켠 뒤에도 사람이 예전처럼 쓸 수 있어야 한다 (#66)
REF = re.compile(r"(?:^|[\s(\[<])(?:" + (re.escape(PREFIX) + r"-|" if PREFIX else "") + r"#)(\d{1,4})\b")


def _by_no(n):
    return next((c for c in STATE["cards"].values() if c["no"] == n), None)


async def unfurl_refs(s, e):
    """어느 채널에서든 `#40` 이라고 쓰면 그 이슈를 작게 펼친다 (#54).

    사람들이 실제로 이야기하는 자리에서 이슈를 보게 한다 — 앱 홈이나 프로젝트 방으로 건너가지 않게.
    봇이 초대된 채널에서만 온다(Slack 기본). 읽기만 한다 — 여기서 아무것도 바꾸지 않는다.
    카드 스레드 안에서는 펼치지 않는다: 이미 그 이슈 안이라 같은 것이 두 번 보인다.
    """
    if e.get("thread_ts") in STATE["cards"]:
        return
    nos, seen = [], set()
    for m in REF.finditer(e.get("text") or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            nos.append(n)
    hits = [c for n in nos[:3] for c in [_by_no(n)] if c]         # 한 번에 최대 3개 — 도배 방지
    for c in hits:
        await api(s, "chat.postMessage", body={
            "channel": e["channel"], "thread_ts": e.get("thread_ts") or e["ts"], "unfurl_links": False,
            "text": f"#{c['no']} {c['title']}", "blocks": peek_blocks(c)})
    if hits:
        log(f"펼침 {[c['no'] for c in hits]} ← {e.get('channel')}")


async def say_hello(s, e):
    """DM 을 처음 연 사람에게 한 번만 인사한다.

    열자마자 빈 화면이면 무엇을 할 수 있는지 알 길이 없다 (2026-09-20 사장님 지적).
    **한 번만** 한다 — 열 때마다 인사하면 대화가 인사로 찬다.
    """
    said = STATE.setdefault("greeted", [])
    if e.get("user") in said:
        return
    said.append(e["user"])
    save()
    # **안내문 전체를 붙이지 않는다** (2026-09-22 사장님 지적) — 읽을 것만 많고 무엇부터
    # 할지는 없었다. 안내문은 **앱 홈**에 둔다(거기가 개인 자리다). 여기는 **다음 한 걸음**만
    await api(s, "chat.postMessage", body={"channel": e["channel"], "unfurl_links": False,
              "text": (say("dm_hello") + "\n\n" + nudge(e["user"])).strip(), **mood("부탁")})
    log(f"DM 인사 → {e.get('user')}")


async def _mention(s, e, q):
    """`@PA …` 뒤에 쓴 말로 갈라 준다. DM 과 같은 갈래를 쓴다 — 배울 것을 늘리지 않는다."""
    async with thinking(s, e, say("thinking"), say("thinking_read"), say("thinking_almost")):
        await _route(s, e, q)


async def _route(s, e, q):
    if "현황" in q:
        await show_digest(s, e["channel"], e.get("user"))          # 부른 자리에서 나에게만
    elif TIDY.match(q):
        await tidy_propose(s, e["channel"], e.get("user"))         # 정리 제안 (#57)
    elif ORDER.match(q):
        await tidy_order(s, e["channel"], e.get("user"))           # 선행 한 화면 (#68)
    elif MTG.match(q) and MTG.match(q).group(3).strip():
        g = MTG.match(q)                                           # 「@PA 회의 스프린트 점검」
        await new_meeting(s, g.group(3).strip()[:60], g.group(2), await name_of(s, e), REQUEST)
    elif MAKE.search(q):
        await propose_issue(s, e, q)                               # 초안 + 버튼 (#54, #11)
    else:
        await find(s, e, q)                                        # 목록 · 사람 · 검색 (#54)


async def on_dm(s, e):
    """DM — 부르지 않아도 되는 것만 빼면 채널과 같다.

    배울 것을 늘리지 않는다: 채널에서 `@PA 목록` 이면 DM 에서는 그냥 `목록` 이다.
    혼자 조용히 묻는 자리라 현황·찾기·초안 모두 여기서 된다.
    """
    q = (e.get("text") or "").strip()
    if not q:
        return
    # **DM 에서도 스레드가 기본이다** (2026-09-22 사장님: 「dm 스레드가 기본이야 없애지말아줘」).
    # 물은 글과 답이 붙어 있어야 나중에 무엇에 대한 답인지 안다.
    #
    # 같은 날 오전에 이걸 껐던 적이 있다 — 「글 남겨도 작동을 안 하는데?」 의 원인을 접힌
    # 스레드로 봤다. 그런데 **그때 AI 도 죽어 있었다** (Anthropic 500). 두 가지를 한꺼번에
    # 고치면 어느 쪽이 원인이었는지 못 가린다. AI 는 살아났고, 스레드는 사장님이 원하는 기본값이다.
    th = e.get("thread_ts") or e.get("ts")
    # ⏳ 반응은 빼고 「…하는 중」 하나만 쓴다 — 둘 다 뜨니 같은 말이 두 번이었다
    # (2026-09-21 사장님: 「굳이 2개 다 나올 필요가 있나」).
    #
    # **모든 갈래를 감싼다** (2026-09-22 사장님: 「로딩이 동작 안 하는 거 같은데」). 예전에는
    # 프로젝트 만들기·첫 걸음이 이 블록 **앞에서** 끝났다 — 그 두 갈래는 상태 줄이 아예 안 떴다.
    # 감싸는 자리를 옮기는 것으로 끝난다: `return` 이 나가도 `finally` 가 상태를 끈다
    async with thinking(s, e, say("thinking"), say("thinking_read"), say("thinking_almost")):
        # **프로젝트 만들기가 먼저다** — 묻는 중이면 그 답을 다른 갈래가 가로채면 안 된다.
        # 「결제 개편」 이 프로젝트 이름인데 검색어로 받으면 대화가 끊긴다 (2026-09-21)
        if await new_project(s, e, q):
            return
        if await new_task(s, e, q):           # 할 일 올리기 — 같은 틀로 한 칸씩 (2026-09-22)
            return
        if await onboard_catch(s, e, q):      # 처음 오신 분의 한 걸음 — 「됐어요」 만 여기서 받는다
            return
        if "현황" in q:
            await show_digest(s, e["channel"], e["user"], th)
        elif TIDY.match(q):
            await tidy_propose(s, e["channel"], e["user"])
        elif ORDER.match(q):
            await tidy_order(s, e["channel"], e["user"])
        elif e.get("thread_ts") and await refresh_draft(s, e):
            return
        elif ASKISH.search(q):
            # **여기서만 AI 에게 묻는다** — 「무엇을 하려는 말인가」 한 번 (2026-09-22).
            # 낱말로 안 걸리는 말(「새로 시작하는 일 하나 올려줘」)은 뜻으로 가른다.
            # 값싼 갈래가 아무것도 못 집었을 때만 온다 — 「현황」·「목록」 은 위에서 끝나고
            # ASKISH 에 안 걸리는 말(인사·질문)은 AI 를 아예 안 쓴다
            kind = await intent(q)
            if kind == "project":
                await new_project(s, e, q, force=True)
            elif kind == "task":
                await new_task(s, e, q, force=True)
            else:
                await find(s, e, q)
        else:
            await find(s, e, q)


async def on_event(s, e, me):
    t = e.get("type")
    if t == "app_home_opened" and e.get("tab") == "home":
        await publish_home(s, e["user"])
        return
    if t == "app_home_opened" and e.get("tab") == "messages":
        await say_hello(s, e)                                 # DM 을 처음 열면 한 번만 인사 (2026-09-20)
        return
    # **사람이 쓴 것에만 반응한다.** 2026-09-20 에 같은 방에 다른 AI 앱(Ringo)을 붙였다 —
    # 그 앱이 「#12」 를 쓰면 우리가 펼치고, 거기에 그 앱이 또 답하면 둘이 주고받는다.
    # 봇끼리 말을 주고받게 두면 아무도 안 보는 사이에 방이 찬다
    if t == "message" and e.get("channel_type") == "im" and not e.get("bot_id") and e.get("user"):
        await on_dm(s, e)                                     # DM — 채널에서 @PA 뒤에 쓰는 말과 같게 (2026-09-20)
        return
    if t == "message" and not e.get("subtype") and not e.get("bot_id") and e.get("user"):
        if e.get("thread_ts") and await refresh_draft(s, e):      # 초안 스레드에 더 쓰면 다시 정리한다 (#54)
            return
        await unfurl_refs(s, e)                                   # #40 → 작은 카드 (어느 채널에서든, #54)
    # 팀 대화방은 프로젝트마다 다를 수 있고 **없을 수도 있다** — 그때는 프로젝트 방이 곧 팀 대화방이다
    if t == "message" and e.get("channel") in {p.get("request") or p.get("channel") for p in PROJECTS} \
            and not e.get("subtype") in ("message_changed", "message_deleted"):
        if is_mine(e):
            return
        hit = NEW.match(e.get("text", ""))
        if not e.get("thread_ts") and hit:
            await propose_issue(s, e, hit.group(1))       # 내용이 갖춰져야 번호가 나간다 (사장님 지시 9/20)
        return
    if t == "message" and e.get("channel") in {p.get("channel") for p in PROJECTS} and not e.get("subtype") in ("message_changed", "message_deleted"):
        mine = is_mine(e)                                         # 봇 자신의 카드·답글은 무시
        if mine:
            return
        thread = e.get("thread_ts")
        text = e.get("text", "")
        hit = NEW.match(text)
        if not thread and hit:
            await propose_issue(s, e, hit.group(1))      # 내용이 갖춰져야 번호가 나간다
        elif thread in STATE["cards"]:
            c = STATE["cards"][thread]
            hit = re.search(r"담당\s*<@(U[A-Z0-9]+)>", text)
            if hit:
                c["assignee"], c["assign_src"] = hit.group(1), "human"
                await redraw(s, c)
            elif f"<@{me}>" in text:
                pass                                                  # 호출은 app_mention 이 처리한다
            elif c.get("await_reason") is not None and c["await_reason"] < len(c.get("edits", [])) \
                    and c["edits"][c["await_reason"]].get("by") == e.get("user"):
                ed = c["edits"][c.pop("await_reason")]                    # 바꾼 이유 — 고친 사람의 다음 답글
                ed["reason"] = text.strip()[:120]
                if ed.get("ts"):                                          # 그 한 줄을 고쳐 이유를 넣는다
                    await api(s, "chat.update", body={"channel": chan(c), "ts": ed["ts"], "text": log_line(ed)})
                save()
            elif c.get("coach") in (None, "asking", "proposed") and c["status"] not in ("done", "cancelled"):
                await coach(s, c, "reply")                            # 구체화 중 — 부르지 않아도 이어서 대화
            elif text.strip().endswith("?"):
                await answer(s, c, thread, text)                      # 정리가 끝난 뒤엔 질문에만
    elif t == "app_mention" and e.get("thread_ts") in STATE.get("meetings", {}):
        if "회의록" in e.get("text", "") and "다시" in e.get("text", ""):
            await finish_meeting(s, STATE["meetings"][e["thread_ts"]], e.get("user"))
    elif t == "app_mention" and not e.get("thread_ts"):
        q = re.sub(r"<@" + re.escape(me) + r">", "", e.get("text", "")).strip()
        await _mention(s, e, q)                # ⏳ 대신 「…하는 중」 상태 줄 — _mention 안에 있다
    elif t == "app_mention" and e.get("thread_ts") in STATE["cards"]:
        c = STATE["cards"][e["thread_ts"]]
        q = re.sub(r"<@U[A-Z0-9]+>", "", e.get("text", "")).strip()
        hit = MERGE.search(q)
        if hit:                                               # 「@PA 합치기 #57」 — 낱말이 안 겹쳐도 사람이 안다
            await merge_into(s, c, int(hit.group(1) or hit.group(2)), e)
        elif q.startswith("정리"):
            await refine(s, c, e["thread_ts"])
        elif q.startswith(("상세", "#", "정본", "파일")):
            await show_md(s, c, e["thread_ts"])
        else:
            await answer(s, c, e["thread_ts"], q)
    elif t == "reaction_added" and e.get("reaction") == "pushpin":
        await save_meeting(s, e)
    elif t == "reaction_added" and e.get("item", {}).get("ts") in STATE["cards"]:
        c = STATE["cards"][e["item"]["ts"]]
        status = REACT.get(e.get("reaction"))
        if status:
            status = resolve(c, status, e.get("user"))
            if status == c["status"]:
                return
            snap, asnap = decision_snap(), ann_snap(c)
            before = c["status"]
            if c["status"] != status:
                c["since"] = datetime.date.today().isoformat()
                c["at"] = time.time()                                  # 리드타임·멈춤 계산용
            c["status"] = status
            if status == "doing" and (not c.get("assignee") or c.get("assign_src") == "ai"):
                c["assignee"], c["assign_src"] = e.get("user"), "human"     # 👀 누른 사람이 맡는다
            for moved_no, where in balance():                           # 끝나면 다음 1순위를 당긴다
                m = next((x for x in STATE["cards"].values() if x["no"] == moved_no), None)
                if m and m is not c:
                    await api(s, "chat.update", body={"channel": chan(m), "ts": m["card_ts"],
                              "text": f"🎫 #{m['no']} {m['title']}", "blocks": card_blocks(m)})
            await redraw(s, c)
            await note_decisions(s, snap, e.get("user"), c["no"])
            await announce(s, c, asnap, e.get("user"))


async def on_message_action(s, payload):
    """메시지 ⋮ 메뉴의 「이슈로 만들기」. 이모지를 못 찾아도 여기로 만들 수 있다."""
    m = payload.get("message", {})
    ch = payload.get("channel", {}).get("id", REQUEST)
    await propose_issue(s, {"channel": ch, "ts": m.get("thread_ts") or m.get("ts"),
                            "user": payload.get("user", {}).get("id")}, m.get("text", "")[:200])


async def on_view_submit(s, payload):
    """창에서 저장을 누르면 카드·GitHub·보드·캔버스를 한 번에 맞춘다."""
    if payload["view"].get("callback_id") == "take_submit":
        return await save_take(s, payload)
    if payload["view"].get("callback_id") == "edit_content_submit":
        return await save_content(s, payload)
    if payload["view"].get("callback_id") == "review_back_submit":
        c = STATE["cards"].get(payload["view"].get("private_metadata"))
        why = ((payload["view"]["state"]["values"].get("why") or {}).get("v") or {}).get("value")
        if c:
            await review_answer(s, c, payload.get("user", {}).get("id"), False, (why or "").strip()[:200] or None)
        return
    v = payload["view"]
    c = STATE["cards"].get(v.get("private_metadata"))
    if not c:
        return
    vals = v["state"]["values"]
    snap, asnap = decision_snap(), ann_snap(c)
    before = c["status"]
    new = resolve(c, vals.get("s", {}).get("status", {}).get("selected_option", {}).get("value") or c["status"],
                  payload.get("user", {}).get("id"))
    if new != c["status"]:
        c["since"] = datetime.date.today().isoformat()
        c["at"] = time.time()                                          # 리드타임·멈춤 계산용
    c["status"] = new
    picked = vals.get("a", {}).get("assignee", {}).get("selected_user")
    if picked and picked != c.get("assignee") and c.get("assign_src") == "ai":
        c["ai_outcome"] = "changed"                            # 지표: AI 배정을 사람이 바꿈
    if picked:
        c["assignee"], c["assign_src"] = picked, "human"
    ft = (vals.get("f", {}).get("feature", {}).get("selected_option") or {}).get("value")
    if ft:
        c["feature"] = ft
    sg = (vals.get("g", {}).get("stage", {}).get("selected_option") or {}).get("value")
    if sg:
        c["stage"] = sg if sg in {x["name"] for x in load_stages()} else None
    pr = (vals.get("p", {}).get("priority", {}).get("selected_option") or {}).get("value")
    if pr and pr.isdigit() and int(pr) != plevel(c):
        c["plevel"], c["plevel_src"], c["prio_reason"] = int(pr), "human", "PM이 정함"
    du = vals.get("d", {}).get("due", {}).get("selected_date")
    if du and du != c.get("due"):
        c["due"], c["due_src"] = du, "human"
    for moved_no, where in balance():                         # 일이 몰리면 뒤로, 비면 당긴다
        m = next((x for x in STATE["cards"].values() if x["no"] == moved_no), None)
        if m and m is not c:
            await api(s, "chat.update", body={"channel": chan(m), "ts": m["card_ts"],
                      "text": f"🎫 #{m['no']} {m['title']}", "blocks": card_blocks(m)})
    await redraw(s, c)
    await note_decisions(s, snap, payload.get("user", {}).get("id"), c["no"])
    await announce(s, c, asnap, payload.get("user", {}).get("id"))
    log(f"창에서 바꿈 #{c['no']} → {c['status']}")


SLOW = ("review_ok", "review_back", "close_done", "check_dc", "set_status", "spec_ok",
        "fill_spec", "fill_all", "fill_after", "draft_make", "same_as", "tidy_drop", "tidy_later", "canvas_now")


async def on_action(s, payload):
    """버튼 · 선택 · 체크박스 — ACTIONS 표에서 찾아 넘긴다. 처리 내용은 flows/ 에 있다.

    오래 걸리는 단추는 누른 그 글에 ⏳ 를 달아 둔다 — 완료 처리·정리는 몇 초씩 걸리는데
    아무 반응이 없어서 먹통처럼 보였다 (2026-09-20 사장님 지적).
    """
    msg = payload.get("message") or {}
    for a in payload.get("actions", []):
        aid = a.get("action_id", "")
        # draft_make_PA 처럼 뒤에 프로젝트가 붙는 단추가 있다 (#70) — 앞부분으로 찾는다
        f = (ACTIONS.get(aid) or (act_detail if aid.startswith("detail") else None)
             or (ACTIONS["draft_make"] if aid.startswith("draft_make_") else None))
        if not f:
            continue
        if (aid in SLOW or aid.startswith("draft_make")) and msg.get("ts"):
            async with working(s, (payload.get("channel") or {}).get("id"), msg["ts"]):
                await f(s, payload, a)
        else:
            await f(s, payload, a)


def _user(p):
    return p.get("user", {}).get("id")


def _card(a, from_block=False):
    return STATE["cards"].get((a.get("block_id") or "").partition(":")[2] if from_block else a.get("value"))


async def act_set(s, p, a):                    # ⚙️ 설정의 담당 · 우선순위 · 목표일 · 상태 · 단계 · 기능
    c = _card(a, True)
    if c:
        val = (a.get("selected_option") or {}).get("value") or a.get("selected_date") or a.get("selected_user")
        await apply_change(s, c, a["action_id"], val, _user(p))


async def act_risk_fix(s, p, a):
    await apply_fix(s, a.get("value", ""), _user(p),
                    (p.get("message") or {}).get("thread_ts") or (p.get("container") or {}).get("thread_ts"))


async def act_spec_ok(s, p, a):
    c = _card(a)
    if c and c.get("spec_draft"):
        await confirm_spec(s, c, _user(p))


async def act_edit_content(s, p, a):
    c = _card(a)
    if c:                                      # 상세 팝업 안에서 누르면 그 위에 한 장 더 연다
        await open_content_editor(s, p["trigger_id"], c, push=p.get("container", {}).get("type") == "view")


async def act_edit_card(s, p, a):              # 앱 홈 「✏️ 상태·담당」 창
    c = _card(a)
    if c:
        await open_editor(s, p, c)


async def act_pull(s, p, a):                   # ✋ 내가 할게요 — 맡기 전에 언제까지를 묻는다 (#52)
    c = _card(a)
    if c:
        await open_take_editor(s, p["trigger_id"], c, _user(p))


async def act_check(s, p, a):                  # 완료 조건 체크박스
    c = _card(a, True)
    if c:
        await check_criteria(s, c, a.get("selected_options") or [], _user(p))


async def act_close_done(s, p, a):
    c = _card(a)
    if c and c["status"] not in ("done", "cancelled"):
        await close_done(s, c, _user(p), (p.get("message") or {}).get("ts"))


async def act_review_ok(s, p, a):
    c = _card(a)
    if c:
        await review_answer(s, c, _user(p), True)


async def act_review_back(s, p, a):            # 무엇이 더 필요한지 한 줄 받는 창 (비워도 된다)
    c = _card(a)
    if c and c["status"] == "review":
        await api(s, "views.open", body={"trigger_id": p["trigger_id"], "view": review_back_view(c)})


async def act_mtg_apply(s, p, a):
    await apply_meeting_change(s, a.get("value", ""), _user(p), p.get("message"))


async def act_finish_meeting(s, p, a):
    m = STATE.get("meetings", {}).get(a.get("value"))
    if m:
        await finish_meeting(s, m, _user(p))


async def act_detail(s, p, a):                 # 앱 홈 「자세히 볼 것」 → 팝업 (버튼·고르는 칸 둘 다)
    a = dict(a, value=(a.get("selected_option") or {}).get("value") or a.get("value"))
    if a.get("value") in DETAIL:
        await api(s, "views.open", body={"trigger_id": p["trigger_id"], "view": {
            "type": "modal", "title": {"type": "plain_text", "text": DETAIL[a["value"]][:24]},
            "close": {"type": "plain_text", "text": "닫기"}, "blocks": detail_blocks(a["value"])}})


async def act_show_meeting(s, p, a):
    m = STATE.get("meetings", {}).get(a.get("value"))
    path = HERE / (m.get("file") and f"meetings/{m['file']}" or "")
    if m and path.exists():
        await api(s, "views.open", body={"trigger_id": p["trigger_id"], "view": {
            "type": "modal", "title": {"type": "plain_text", "text": m["id"]},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": f"📄 `meetings/{m['file']}`"}]},
                       {"type": "section", "text": {"type": "mrkdwn", "text": md_for_slack(path)[:2900]}}]}})


async def act_fill_spec(s, p, a):
    """[✨ 정리해 줘] — 제목만 있는 이슈를 AI가 채운다. 채운 뒤에도 사람이 고칠 수 있다 (#57)."""
    c = _card(a)
    if c:
        await refine(s, c, c["card_ts"])


async def act_show_md(s, p, a):                # 📄 상세 팝업 — 막히면 스레드로
    c = _card(a)
    if not c:
        return
    d = await api(s, "views.open", body={"trigger_id": p["trigger_id"], "view": {
        "type": "modal", "title": {"type": "plain_text", "text": f"이슈 #{c['no']}"},
        "close": {"type": "plain_text", "text": "닫기"}, "blocks": card_detail_blocks(c)}})
    if not d.get("ok"):
        await show_md(s, c, c["card_ts"])


async def act_canvas_now(s, p, a):          # 앱 홈 「🔄 작업판 새로고침」 — 10분을 기다리지 않고 바로 (내용이 같으면 안 쓴다)
    await render_canvas_now(s)
    await publish_home(s, _user(p))


ACTIONS = {
    **{k: act_set for k in ("set_status", "set_prio", "set_due", "set_assignee", "set_stage", "set_feature")},
    "risk_fix": act_risk_fix, "spec_ok": act_spec_ok, "edit_content": act_edit_content, "edit_card": act_edit_card,
    "pull_card": act_pull, "accept_assign": act_pull,       # accept_assign — 예전 카드에 남은 버튼
    "check_dc": act_check, "close_done": act_close_done, "review_ok": act_review_ok, "review_back": act_review_back,
    "mtg_apply": act_mtg_apply, "finish_meeting": act_finish_meeting, "show_meeting": act_show_meeting, "show_md": act_show_md,
    "card_menu": lambda s, p, a: act_card_menu(s, p, a), "canvas_now": act_canvas_now,
    # 초안 → 카드 (#54) — 이 버튼이 번호를 만든다. 이름은 맨 아래에서 오므로 부를 때 찾는다
    **{k: (lambda s, p, a: tidy_decide(s, p, a)) for k in ("tidy_drop", "tidy_later", "tidy_keep")},
    "fill_spec": act_fill_spec, "fill_all": lambda s, p, a: tidy_fill_all(s, p, a),
    "fill_after": lambda s, p, a: tidy_fill_after(s, p, a),
    "same_as": lambda s, p, a: same_as(s, p, a), "not_same": lambda s, p, a: not_same(s, p, a),
    "draft_make": lambda s, p, a: make_from_draft(s, p, a),   # draft_make_PA 처럼 뒤에 프로젝트가 붙기도 한다 (#70)
    "drop_draft": lambda s, p, a: drop_draft(s, p, a),
}


async def act_card_menu(s, p, a):              # 예전 카드의 「⋯」 메뉴 — 원래 버튼으로
    kind, _, ts_ = (a.get("selected_option") or {}).get("value", "").partition(":")
    f = {"edit": act_edit_card, "detail": act_show_md, "pull": act_pull}.get(kind)
    if f:
        await f(s, p, {"action_id": {"edit": "edit_card", "detail": "show_md", "pull": "pull_card"}[kind], "value": ts_})


CTL_VER = 3          # 카드·⚙️ 모양이 바뀌면 올린다 — 켜질 때 한 번, 천천히 새로 그린다 (3: 끝난 일 취소선)


async def refresh_ctls(s):
    if STATE.get("ctl_ver") == CTL_VER:
        return
    for c in [x for x in STATE["cards"].values() if x.get("card_ts")]:
        if c["status"] in ("done", "cancelled"):
            await api(s, "chat.update", body={"channel": chan(c), "ts": c["card_ts"],
                      "text": f"🎫 #{c['no']} {c['title']}", "blocks": card_blocks(c)})
        elif c.get("ctl_ts"):
            await ensure_ctl(s, c)
        else:
            continue
        await asyncio.sleep(1.5)                       # chat.update 한도(분당 약 50) 안에서
    STATE["ctl_ver"] = CTL_VER
    save()
    log(f"⚙️ 설정 메시지 새로 그림 (v{CTL_VER})")


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from ai import answer, coach, intent, refine  # noqa: E402,F401
from flows.onboard import catch as onboard_catch, nudge  # noqa: E402
from flows.project import maybe as new_project  # noqa: E402
from flows.task import maybe as new_task  # noqa: E402
from flows.find import find  # noqa: E402,F401
from flows.fix import apply_fix, post_digest, show_digest  # noqa: E402,F401
from flows.intake import confirm_spec, drop_draft, make_from_draft, merge_into, new_card, not_same, propose_issue, refresh_draft, same_as, show_md  # noqa: E402,F401
from flows.meeting import apply_meeting_change, finish_meeting, new_meeting, save_meeting  # noqa: E402,F401
from flows.status import announce, apply_change, balance, check_criteria, close_done, ensure_ctl, note_decisions, open_content_editor, open_editor, open_take_editor, post_log, record_change, redraw, resolve, save_content, save_take, take_card  # noqa: E402,F401
from flows.review import review_answer  # noqa: E402,F401
from flows.tidy import decide as tidy_decide, fill_after_all as tidy_fill_after, order as tidy_order, fill_all as tidy_fill_all, propose as tidy_propose  # noqa: E402,F401
from views.canvas import render_canvas, render_canvas_now  # noqa: E402,F401
from views.card import peek_blocks, card_blocks, card_detail_blocks, md_for_slack, review_back_view  # noqa: E402,F401
from views.home import DETAIL, detail_blocks, publish_home  # noqa: E402,F401
