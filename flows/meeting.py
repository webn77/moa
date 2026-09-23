"""회의 · 회의 결정 → 이슈 (#30 에서 bot.py 를 나눔).

**회의를 잡는 것도 한 칸씩 묻는다** (2026-09-23 사장님: 「이것도 동일하게 하자고 회의
만들자고 해서 정기회의 인지 이번만인지 등등 물어보면서 만들기 하고」):

  사람: 회의 만들자
  모아: 무슨 회의인가요?                       ← ①
  사람: 주간 점검
  모아: 한 번만 하는 회의인가요, 정기 회의인가요?  ← ②
  사람: 매주
  모아: 무슨 요일에 할까요?                     ← ③
  사람: 화요일
  모아: 매주 화요일로 잡았어요 → 회의 카드

**세 칸만 묻는다.** 참석자·장소·시간·아젠다는 안 묻는다 — 회의 스레드에 적으면 되고,
물을 것이 늘수록 안 쓰게 된다 (사장님: 「쉽게 가야해」). 한 번만 하는 회의면 ③ 이
요일 대신 날짜다.
"""
import asyncio, json, re, datetime
import core
import gh_link
from flows.ask import ASKING, HEAD, NOPE, cancelled, titleable
from messages import say
from common import BOT, CHANNEL, HANDLE, LABEL, REQUEST, log  # noqa: E402,F401


def mch(m):
    """그 회의 카드가 있는 방. 회의는 사람들이 이야기하는 팀 대화방에 둔다 — 프로젝트 방은 이슈·기록만 (2026-09-20 결정).
    예전 회의는 프로젝트 방에 있으므로 카드에 적힌 방을 따른다."""
    return m.get("channel") or CHANNEL
from docs import load_stages, load_team  # noqa: E402,F401
from slack import api, name_of  # noqa: E402,F401
from store import STATE, chan, add_log, open_cards, ref, save  # noqa: E402,F401


async def save_meeting(s, e):
    """📌 가 달린 메시지를 회의록·기록으로 레포에 저장한다."""
    item = e.get("item", {})
    ts, ch = item.get("ts"), item.get("channel") or CHANNEL      # 📌 는 어느 방에서든
    d = await api(s, "conversations.replies", channel=ch, ts=ts, limit=200)
    msgs = d.get("messages", [])
    if not msgs:
        return
    lines = []
    for m in msgs:
        lines.append(f"- **{await name_of(s, m)}**: {m.get('text', '').strip()}")
    title = re.sub(r"\s+", " ", msgs[0].get("text", ""))[:40] or "기록"
    link = (await api(s, "chat.getPermalink", channel=ch, message_ts=ts)).get("permalink", "")
    try:
        path = await asyncio.get_running_loop().run_in_executor(
            None, gh_link.save_note, title, "\n".join(lines), link)
    except Exception as ex:
        log(f"회의록 저장 실패: {ex}")
        return
    await api(s, "chat.postMessage", body={"channel": ch, "thread_ts": ts,
              "text": say("pinned", path=path)})
    log(f"회의록 저장 {path}")


def meeting_blocks(m):
    issue = f" · 할 일 #{m['issue']}" if m.get("issue") else ""
    state = {"planned": "🗓️ 예정", "recorded": "📝 기록됨 · 제안 대기", "applied": "✅ 반영 완료"}[
        m.get("stage") or ("recorded" if m.get("file") else "planned")]
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*🗓️ {m['id']} {m['title']}*"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": f"{state} · {m['date']}{issue} · 만든 사람 {m['by']}"}]},
    ]
    # 정기 회의는 **다음이 언제인지 카드에 적어 둔다** — 안 적으면 「정기라고 했는데 다음이
    # 오긴 하나」 를 아무도 확인할 수 없다. 끄는 길도 같은 자리에 둔다 (2026-09-23)
    if (m.get("every") or "once") != "once" and m.get("weekday") is not None:
        nxt = core.next_meet(m["every"], m["weekday"], m["date"])
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": say("meet_card_every", label=core.meet_label(m), next=nxt or "미정")},
                       "accessory": {"type": "button", "text": {"type": "plain_text", "text": "정기 끄기"},
                                     "action_id": "meet_stop", "value": m["card_ts"]}})
    if m.get("agenda"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*아젠다*\n" +
                       "\n".join(f"{i}. {a}" for i, a in enumerate(m["agenda"], 1))}})
    if m.get("summary"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": m["summary"][:2800]}})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": say("mtg_hint")}]})
    if m.get("file"):                                  # 확정된 회의 — 볼 것만 남긴다
        el = [{"type": "button", "text": {"type": "plain_text", "text": "📄 회의록 보기"},
               "action_id": "show_meeting", "value": m["card_ts"]}]
        blocks.append({"type": "actions", "elements": el})
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": say("mtg_redo_hint", bot=HANDLE)}]})
    else:
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "📝 회의록 확정"},
             "action_id": "finish_meeting", "value": m["card_ts"], "style": "primary"}]})
    return blocks


async def new_meeting(s, title, issue_no, by, channel, every="once", date=None, weekday=None):
    """회의 카드를 만든다. `every`·`weekday` 는 **덧붙임이라 안 줘도 된다** — 예전처럼
    부르면 오늘 한 번 하는 회의다 (옛 카드와 시험 대역이 그대로 돌아간다)."""
    m = {"id": f"M{STATE.get('next_m', 1)}", "title": title, "issue": issue_no, "by": by,
         "date": date or datetime.date.today().isoformat(), "summary": None, "file": None,
         "channel": channel, "every": every, "weekday": weekday}
    STATE["next_m"] = STATE.get("next_m", 1) + 1
    d = await api(s, "chat.postMessage", body={"channel": channel, "text": f"🗓️ {m['id']} {title}",
                                               "blocks": meeting_blocks(dict(m, card_ts="tmp"))})
    m["card_ts"] = d["ts"]
    STATE.setdefault("meetings", {})[d["ts"]] = m
    await api(s, "chat.update", body={"channel": channel, "ts": d["ts"],
              "text": f"🗓️ {m['id']} {title}", "blocks": meeting_blocks(m)})
    save()
    await render_canvas(s)
    log(f"회의 {m['id']} 생성 ({core.meet_label(m)})")
    return m


async def finish_meeting(s, m, user):
    """스레드 전체를 회의록으로 정리해서 파일로 확정한다. 다시 눌러도 같은 파일을 갱신한다."""
    await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": m["card_ts"],
              "text": say("mtg_working")})
    msgs = (await api(s, "conversations.replies", channel=mch(m), ts=m["card_ts"], limit=200)).get("messages", [])
    lines = []
    for x in msgs[1:]:
        if x.get("bot_id") and not x.get("username"):
            continue
        lines.append(f"{await name_of(s, x)}: {x.get('text', '').strip()}")
    if not lines:
        await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": m["card_ts"],
                  "text": say("mtg_empty")})
        return
    team = load_team()
    issues = "\n".join(f"#{c['no']} {c['title'][:40]}" for c in sorted(open_cards(), key=lambda c: c["no"]))
    system = ("너는 회의록을 정리하는 서기다. 대화에 있는 사실만 쓴다. 정해지지 않은 것은 '미정'. "
              "이슈 번호는 대화에 나오거나 아래 이슈 목록과 분명히 같은 일일 때만 쓴다. "
              "issue_changes 는 회의에서 **정한** 변경만 (논의만 한 것은 넣지 않는다). field 는 "
              "담당|우선순위|목표일|단계|상태|체크리스트 중 하나, to 는 담당=팀원 이름, 우선순위=P1~P4, 목표일=YYYY-MM-DD, "
              "상태=대기|진행 중|보류|완료|취소, 체크리스트=추가할 조건 한 줄. reason 은 회의에서 나온 이유 한 줄. "
              "JSON 한 개만 출력: {\"agenda_results\":[{\"item\":\"\",\"discussion\":\"\",\"decision\":\"\"}],"
              "\"decisions\":[\"\"],\"action_items\":[{\"what\":\"\",\"owner\":\"\",\"due\":\"\",\"issue\":null}],"
              "\"issue_changes\":[{\"issue\":번호,\"field\":\"\",\"to\":\"\",\"reason\":\"\"}],"
              "\"new_issues\":[{\"title\":\"\",\"why\":\"\"}],\"next_meeting\":\"\"}")
    try:
        raw = await ask_ai(system, f"회의: {m['title']}" + (f" (이슈 #{m['issue']})" if m.get("issue") else "")
                           + f"\n팀원: {', '.join(t['name'] for t in team.values())}\n이슈 목록:\n{issues}\n\n대화:\n"
                           + "\n".join(lines))
        r = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception as e:
        await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": m["card_ts"],
                  "text": say("mtg_fail", err=str(e)[:120])})
        return
    body = gh_link.render_minutes(r, m.get("agenda"))
    link = (await api(s, "chat.getPermalink", channel=mch(m), message_ts=m["card_ts"])).get("permalink", "")
    m["permalink"] = link
    loop = asyncio.get_running_loop()
    path = await loop.run_in_executor(None, gh_link.save_meeting, m, body, link)
    m["summary"] = "*정한 것*\n" + ("\n".join(f"• {x}" for x in r.get("decisions") or []) or "• 없음")
    await meeting_to_issues(s, m, r, path)
    await api(s, "chat.update", body={"channel": mch(m), "ts": m["card_ts"],
              "text": f"🗓️ {m['id']} {m['title']}", "blocks": meeting_blocks(m)})
    await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": m["card_ts"],
              "text": say("mtg_saved", path=path)})
    save()
    await render_canvas(s)
    log(f"회의록 확정 {m['id']} → {path}")


MFIELD = {"담당": "set_assignee", "우선순위": "set_prio", "목표일": "set_due", "단계": "set_stage", "상태": "set_status"}


def meeting_change_value(ch):
    """회의 변경 제안 → apply_change 에 넘길 값. 못 알아들으면 None (버튼을 달지 않는다)."""
    f, to = ch.get("field"), str(ch.get("to") or "").strip()
    if f == "담당":
        return next((u for u, t in load_team().items() if t["name"] == to), None)
    if f == "우선순위":
        hit = re.search(r"[1-4]", to)
        return hit.group(0) if hit else None
    if f == "목표일":
        return to if re.fullmatch(r"\d{4}-\d{2}-\d{2}", to) else None
    if f == "단계":
        return to if to in {x["name"] for x in load_stages()} else None
    if f == "상태":
        return next((k for k, v in LABEL.items() if v == to), None)
    if f == "체크리스트":
        return to or None
    return None


async def meeting_to_issues(s, m, r, path):
    """회의에서 다룬 이슈마다 카드 스레드에 남긴다 — 무엇을 정했나 · 할 일 · 바꾸기로 한 것([적용] 버튼).
    바꾸는 건 사람이 [적용]을 눌러야 — 회의 대화를 AI가 잘못 읽을 수 있다. 다시 확정해도 같은 제안은 한 번만."""
    by = {c["no"]: c for c in STATE["cards"].values()}
    touched = {}
    if m.get("issue") and str(m["issue"]).isdigit():
        touched.setdefault(int(m["issue"]), {"acts": [], "chg": []})
    for a in r.get("action_items") or []:
        if str(a.get("issue") or "").isdigit() and int(a["issue"]) in by:
            touched.setdefault(int(a["issue"]), {"acts": [], "chg": []})["acts"].append(a)
    for ch in r.get("issue_changes") or []:
        if str(ch.get("issue") or "").isdigit() and int(ch["issue"]) in by:
            touched.setdefault(int(ch["issue"]), {"acts": [], "chg": []})["chg"].append(ch)
    posted = set(m.setdefault("posted", []))
    loop = asyncio.get_running_loop()
    mlink = f"<{m['permalink']}|회의 {m['id']} {m['title']}>" if m.get("permalink") else f"회의 {m['id']} {m['title']}"
    for no, t in touched.items():
        c = by.get(no)
        if not c or not c.get("card_ts"):
            continue
        await loop.run_in_executor(None, gh_link.link_meeting_to_issue, no, path, m["title"])
        if t["acts"]:
            await loop.run_in_executor(None, gh_link.add_actions_to_issue, no, m["title"], t["acts"])
        key = json.dumps([no, t["acts"], t["chg"]], ensure_ascii=False, sort_keys=True)
        if key in posted:
            continue
        posted.add(key)
        add_log(c, f"회의 {m['id']}에서 다룸", m["title"][:20], icon="🗓️", kind="meeting")
        lines = [f"🗓️ {mlink} 에서 이 이슈를 다뤘어요"]
        lines += [f"• 할 일: {a.get('what')} · {a.get('owner') or '담당 미정'} · {a.get('due') or '기한 미정'}" for a in t["acts"]]
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]}}]
        for ch in t["chg"]:
            val = meeting_change_value(ch)
            txt = f"*{ch.get('field')}* → {ch.get('to')}" + (f"  ·  이유: {ch['reason']}" if ch.get("reason") else "")
            b = {"type": "section", "text": {"type": "mrkdwn", "text": f"회의에서 바꾸기로 한 것 — {txt}"[:2900]}}
            if val:
                cid = f"{m['card_ts']}|{no}|{len(m.setdefault('changes', []))}"
                m["changes"].append({"no": no, "field": ch.get("field"), "val": val, "to": ch.get("to"),
                                     "reason": ch.get("reason"), "done": None})
                b["accessory"] = {"type": "button", "text": {"type": "plain_text", "text": "적용"},
                                  "style": "primary", "action_id": "mtg_apply", "value": cid}
            else:
                b["text"]["text"] += "\n_값을 알아듣지 못했어요. 위 ⚙️ 설정에서 직접 바꿔 주세요_"
            blocks.append(b)
        if t["chg"]:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                           "text": say("mtg_issue_note")}]})
        await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"],
                  "text": f"🗓️ 회의 {m['id']} 에서 다룸", "blocks": blocks, "unfurl_links": False})
    m["posted"] = sorted(posted)
    if touched:
        await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": m["card_ts"],
                  "text": say("mtg_to_issues", refs=", ".join(ref(n, 12) for n in touched))})


async def apply_meeting_change(s, cid, user, msg):
    """회의 변경 제안의 [적용] — 결정 이력에 「회의 M# 결정 · 이유」 로 남는다."""
    mts, no, idx = cid.split("|")
    m = STATE.get("meetings", {}).get(mts)
    ch = m and (m.get("changes") or [None] * (int(idx) + 1))[int(idx)]
    c = next((x for x in STATE["cards"].values() if x["no"] == int(no)), None)
    if not ch or not c or ch.get("done"):
        return
    how, why = f"회의 {m['id']} 결정", ch.get("reason") or m["title"]
    if ch["field"] == "체크리스트":
        old_title, old_spec = c["title"], dict(c.get("spec") or {})
        sp = dict(old_spec)
        sp["done_criteria"] = (sp.get("done_criteria") or []) + [ch["val"]]
        c["spec"] = sp
        await record_change(s, c, user, old_title, old_spec, why, how=how)
        await redraw(s, c)
    else:
        await apply_change(s, c, MFIELD[ch["field"]], ch["val"], user, how=how, why=why)
    ch["done"] = user
    save()
    if msg and msg.get("ts"):                     # 누른 버튼 자리를 「적용함」 으로 바꾼다
        who = load_team().get(user, {}).get("name", "누군가")
        blocks = msg.get("blocks") or []
        for b in blocks:
            if (b.get("accessory") or {}).get("value") == cid:
                b.pop("accessory")
                b["text"]["text"] += f"\n✅ {who} 님이 적용했어요"
        await api(s, "chat.update", body={"channel": CHANNEL, "ts": msg["ts"], "text": "회의에서 다룸", "blocks": blocks})
    log(f"회의 변경 적용 {cid} ← {user}")


# ── 회의 만들기 — 한 칸씩 (2026-09-23) ────────────────────────────────────────────
#
# 시작하는 말. **「회의록」 은 여기 안 걸린다** — 그건 이미 있는 회의를 보자는 말이다.
# `handlers.py` 의 `MTG` 는 채널에서 `@모아` 를 부를 때 쓰고, 이건 DM 에서 쓴다
MEET_START = re.compile(r"(회의|미팅)(?!록)\S*\s*(를|을|로|은|는)?\s*\S{0,4}\s*(등록|만들|추가|잡|열|시작|생성|잡아)"
                        r"|(회의|미팅)(?!록)\s*(할래|하려|하자|할게|해줘)")
# **할 일 만들기와 섞이지 않는다** — 「다음 회의 자료 만들기 할일 추가」 는 회의가 아니라
# 할 일이다. `handlers.py` 에서 할 일이 먼저 불리니 실제로는 안 샜지만, **순서에 기대지
# 않는다** (같은 저장소가 `NOT_MINE` 에 그렇게 적어 두고도 오늘 한 번 새게 두었다)
MEET_NOT = re.compile(r"할\s*일|할일|이슈|업무|작업")
_HEAD = re.compile(HEAD, re.I)
STEP_WHAT = {"title": "무슨 회의인가", "every": "한 번만인가 정기인가", "when": "언제"}


def _asking_meet(user):
    return (STATE.get("new_meeting") or {}).get(user)


def _mwhere(st):
    """지금 몇 번째 칸인가 — (번호, 전체, 무엇). **회의는 늘 세 칸이다.**"""
    steps = ["title", "every", "when"]
    step = st.get("step") or "title"
    n = steps.index(step) + 1 if step in steps else len(steps)
    return n, len(steps), STEP_WHAT.get(step, "확인")


def _keycap(n):
    return f"{n}️⃣"


async def _msay(s, ch, text, thread=None):
    """`flows/task.py` 의 `_say` 와 같다 — **DM 은 스레드만** (사장님: 「노노 dm 은 스레드만」)."""
    body = {"channel": ch, "text": text, "unfurl_links": False}
    if thread:
        body["thread_ts"] = thread
    await api(s, "chat.postMessage", body=body)


async def _magain(s, ch, th, st, text):
    """못 알아들었을 때 — **어디에 있는지와 나가는 길**을 늘 함께."""
    n, total, what = _mwhere(st)
    st["miss"] = st.get("miss", 0) + 1
    await _msay(s, ch, text + "\n\n" + say("ask_where", kind="회의 만들기", n=n, total=total, what=what), th)
    save()
    return True


async def _mask(s, ch, th, st, step, head=""):
    """다음 칸을 묻는다. ③ 은 **앞 칸의 답에 따라 달라진다** — 정기면 요일, 한 번이면 날짜."""
    st["step"], st["miss"] = step, 0
    save()
    no = _keycap(_mwhere(st)[0])
    if step == "every":
        text = say("meet_ask_every", step=no)
    elif step == "when":
        text = (say("meet_ask_date", step=no) if st.get("every") == "once"
                else say("meet_ask_weekday", step=no, every=core.EVERY[st["every"]]))
    else:
        return False
    await _msay(s, ch, head + text, th)
    return True


async def maybe(s, e, q, force=False):
    """DM 의 이 말이 회의 만들기와 관련 있으면 처리하고 True. 아니면 False.

    `flows/task.py` 와 **같은 틀**이다 — 스레드 규칙·취소·「그게 아니라」·묻는 말 가려내기가
    모두 같다. 다른 것은 묻는 칸 셋뿐이다. 여기서도 AI 를 부르지 않는다.
    """
    user, ch = e.get("user"), e.get("channel")
    st, opened = _asking_meet(user), False
    if st is None:
        if not (force or (MEET_START.search(q) and not MEET_NOT.search(q))):
            return False
        opened = True
        st = {"by": user, "step": "title", "th": e.get("thread_ts") or e.get("ts")}
        STATE.setdefault("new_meeting", {})[user] = st
        save()
    # **시작한 스레드 안에서만 이어 간다** (사장님: 「스레드 안에서 시작한 건 거기에서 이야기가 맞어」)
    th = st.get("th") or e.get("thread_ts") or e.get("ts")
    if not opened and e.get("thread_ts") != th:
        if MEET_START.search(q) and not MEET_NOT.search(q):
            STATE["new_meeting"].pop(user, None); save()
            return await maybe(s, e, q, force=force)
        return False
    if opened:
        # 「회의 만들자」 처럼 이름이 없으면 묻는다. 「회의 주간 점검」 이면 그게 곧 이름이다
        name = _title_after(q)
        if titleable(name):
            st["title"] = name[:60]
            return await _mask(s, ch, th, st, "every", say("meet_title_ok", title=name[:60]) + "\n\n")
        await _msay(s, ch, say("meet_ask_title", step=_keycap(1)), th)
        return True
    if cancelled(q):
        STATE["new_meeting"].pop(user, None); save()
        await _msay(s, ch, say("meet_cancel"), th)
        return True

    step = st.get("step")

    # **묻는 말이면 되묻지 않고 답을 한다** — 봇이 물었다고 무엇이든 답으로 받으면 갇힌다.
    #
    # **읽어 보기 전에 가린다.** 못 읽었을 때만 가리면 늦는다 — 「정기가 뭐야?」 에는 「정기」 가
    # 들어 있어서 ② 의 답(매주)으로 **잘 읽히고**, 그대로 매주 회의가 잡힌다 (시험이 잡았다).
    # 값이 읽히느냐와 답이냐는 다른 문제다
    if ASKING.search(q):
        return await _magain(s, ch, th, st, say({"every": "meet_asked_every",
                                                 "when": "meet_asked_when"}.get(step, "meet_asked_mid")))

    # 「그게 아니라」 — 그 칸을 비우고 다시 묻는다
    if NOPE.match(_HEAD.sub("", q.strip())):
        rest = NOPE.sub("", _HEAD.sub("", q.strip())).strip(" ,.!~")
        st.pop({"title": "title", "every": "every", "when": "when"}.get(step, "title"), None)
        if step == "when":
            st.pop("date", None); st.pop("weekday", None)
        if rest and titleable(rest) and not ASKING.search(rest):
            st["title"] = rest[:60]
            return await _mask(s, ch, th, st, "every", say("meet_title_ok", title=rest[:60]) + "\n\n")
        st["step"] = "title"
        return await _magain(s, ch, th, st, say("meet_nope"))

    # ① 무슨 회의인가 — **적으신 그대로** 받는다
    if step == "title":
        name = _title_after(q) or q.strip()
        if not titleable(name):
            return await _magain(s, ch, th, st, say("meet_title_bad", word=q.strip()[:20]))
        st["title"] = name[:60]
        return await _mask(s, ch, th, st, "every", say("meet_title_ok", title=name[:60]) + "\n\n")

    # ② 한 번만인가 정기인가
    if step == "every":
        every = core.meet_every(q)
        if not every:
            return await _magain(s, ch, th, st, say("meet_every_bad"))
        st["every"] = every
        return await _mask(s, ch, th, st, "when")

    # ③ 언제 — 정기면 요일, 한 번이면 날짜
    if step == "when":
        if st.get("every") == "once":
            when = core.meet_when(_date_pick(q))
            if not when:
                return await _magain(s, ch, th, st, say("meet_date_bad"))
            st["date"], st["weekday"] = when, None
        else:
            wd = core.weekday_of(_weekday_pick(q))
            if wd is None:
                return await _magain(s, ch, th, st, say("meet_weekday_bad"))
            st["weekday"] = wd
            st["date"] = core.next_meet(st["every"], wd, datetime.date.today() - datetime.timedelta(days=1))
        return await _mbuild(s, ch, th, st, user)
    return False


TITLE_AFTER = re.compile(r"^\s*(?:회의|미팅)\s*(?:카드|만들기|만들|만드|열|잡|시작|생성|등록|추가)?\w*\s*[:：]?\s*(.*)$")
WORD_DATE = {"1": "오늘", "2": "내일", "3": "모레"}


def _title_after(text):
    """「회의 주간 점검」 의 뒷말 — 만들자는 낱말만 있으면 빈 글자."""
    m = TITLE_AFTER.match((text or "").strip())
    got = (m.group(1) if m else (text or "")).strip(" -—:·")
    return "" if not titleable(got) else got


def _date_pick(text):
    """묻는 자리에서만 — 번호를 말로 바꿔 읽는다."""
    return WORD_DATE.get((text or "").strip(), text)


def _weekday_pick(text):
    """묻는 자리에서만 — 1~7 을 요일로 읽는다. **주말도 받는다**: 묻는 글에는 평일만
    보여 주지만(보통 그것이면 되니까), 토·일에 모이는 팀을 번호로 막을 까닭이 없다."""
    s = (text or "").strip()
    return f"{core.WEEK[int(s) - 1]}요일" if s.isdigit() and 1 <= int(s) <= 7 else text


async def _mbuild(s, ch, th, st, user):
    """다 물었다 — 회의 카드를 만든다. **카드는 팀 대화방에** (2026-09-20 결정), 대화는 여기 DM 에 남는다."""
    STATE.get("new_meeting", {}).pop(user, None)
    save()
    try:
        m = await new_meeting(s, st["title"], None, await name_of(s, {"user": user}), REQUEST,
                              every=st.get("every") or "once", date=st.get("date"), weekday=st.get("weekday"))
    except Exception as ex:
        log(f"회의 만들기 실패: {type(ex).__name__}: {ex}")
        await _msay(s, ch, say("mtg_fail", err=str(ex)[:120]), th)
        return True
    key = "meet_made" if (m.get("every") or "once") == "once" else "meet_made_every"
    await _msay(s, ch, say(key, title=m["title"], when=core.meet_label(m), ch=REQUEST), th)
    return True


async def stop_every(s, card_ts, user):
    """정기 끄기 — **이 회의는 그대로 두고** 다음 회만 안 만든다. 지우는 게 아니다."""
    m = STATE.get("meetings", {}).get(card_ts)
    if not m or (m.get("every") or "once") == "once":
        return
    m["every"] = "once"
    save()
    await api(s, "chat.update", body={"channel": mch(m), "ts": card_ts,
              "text": f"🗓️ {m['id']} {m['title']}", "blocks": meeting_blocks(m)})
    await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": card_ts, "text": say("meet_stop")})
    log(f"정기 회의 끔 {m['id']} ← {user}")


async def due_meetings(s, today=None):
    """정기 회의의 **다음 회를 미리 열어 둔다** — 아침 루프가 하루 한 번 부른다 (#fix.morning).

    이게 없으면 「정기」 는 카드에 적힌 글자일 뿐이다 (사장님 기준: 만들었으면 계속 도는지
    누가 검사하나). **가장 마지막 회만 본다** — 같은 정기에서 카드가 여러 장이면 마지막
    것에서만 다음을 잰다. 안 그러면 회마다 새 카드가 나와 방이 찬다.

    되돌아본 날짜는 안 만든다: 봇이 며칠 꺼져 있었으면 **지난 회는 건너뛰고** 앞으로 올
    회만 연다. 지난 회의를 이제 와 열어 봐야 아무도 안 모인다.
    """
    today = today or datetime.date.today()
    made, live = [], {}
    for m in STATE.get("meetings", {}).values():
        if (m.get("every") or "once") == "once" or m.get("weekday") is None:
            continue
        key = (m.get("title"), m.get("every"), m.get("weekday"), m.get("channel"))
        if key not in live or m["date"] > live[key]["date"]:
            live[key] = m
    for m in live.values():
        every = m["every"]
        nxt = core.next_meet(every, m["weekday"], m["date"])
        if not nxt or datetime.date.fromisoformat(nxt) > today:
            continue                          # 아직 그날이 아니다
        new = await new_meeting(s, m["title"], m.get("issue"), m["by"], mch(m),
                                every=every, date=nxt, weekday=m["weekday"])
        # **정기 표시는 맨 마지막 회에만 둔다** — 회마다 남겨 두면 「정기 끄기」 가 여러 장에
        # 흩어져서, 어느 것을 눌러야 멈추는지 알 수 없다. 옛 회는 지난 회의로 조용히 남는다
        m["every"] = "once"
        await api(s, "chat.update", body={"channel": mch(m), "ts": m["card_ts"],
                  "text": f"🗓️ {m['id']} {m['title']}", "blocks": meeting_blocks(m)})
        await api(s, "chat.postMessage", body={"channel": mch(new), "thread_ts": new["card_ts"],
                  "text": say("meet_auto", label=core.meet_label(new))})
        made.append(new["id"])
    if made:
        save()
        log(f"정기 회의 열기 {', '.join(made)}")
    return made


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from ai import ask_ai  # noqa: E402,F401
from flows.status import apply_change, record_change, redraw  # noqa: E402,F401
from views.canvas import render_canvas  # noqa: E402,F401
