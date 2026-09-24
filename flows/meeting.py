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
import gcal
import gh_link
from flows.ask import ASKING, HEAD, NOPE, cancelled, titleable
# 프로젝트를 고르는 세 함수는 할 일 등록과 **똑같아야 한다** — 베끼면 갈라진다
from flows.task import _pick_project, _project_list, _project_of
from messages import say
from common import BOT, CHANNEL, HANDLE, LABEL, PROJECTS, REQUEST, log  # noqa: E402,F401


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
            "text": f"{state} · {core.meet_label(m)}{issue} · 만든 사람 {m['by']}"}]},
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
               "action_id": "show_meeting", "value": m["card_ts"]},
              {"type": "button", "text": {"type": "plain_text", "text": "✍️ 논의 적기"},
               "action_id": "meet_note", "value": m["card_ts"]}]
        blocks.append({"type": "actions", "elements": el})
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
            "text": say("mtg_redo_hint", bot=HANDLE)}]})
    else:
        # **적을 길을 먼저 준다** (2026-09-23 사장님: 「확정 버튼이 먼저 나오면 안될거 같고
        # 작성 할 수 있게 해줘야 할거 같은데」). 예전에는 확정 단추만 덩그러니 있어서,
        # 무엇을 어디에 써야 하는지 모른 채 누르면 **빈 표**가 나왔다.
        #
        # 실시간으로 회의하면 스레드에 그냥 쓰면 되고, 끝나고 혼자 정리할 땐 창이 편하다 —
        # 둘 다 받는다. **확정은 논의가 쌓인 뒤에만** 보인다: 없을 때 눌러 봐야 건질 게 없다
        el = [{"type": "button", "text": {"type": "plain_text", "text": "✍️ 논의 적기"},
               "action_id": "meet_note", "value": m["card_ts"],
               **({} if m.get("talked") else {"style": "primary"})}]
        if m.get("talked"):
            el.append({"type": "button", "text": {"type": "plain_text", "text": "📝 회의록 확정"},
                       "action_id": "finish_meeting", "value": m["card_ts"], "style": "primary"})
        blocks.append({"type": "actions", "elements": el})
    return blocks


async def new_meeting(s, title, issue_no, by, channel, every="once", date=None, weekday=None,
                      time=None, pkey=None):
    """회의 카드를 만든다. `every`·`weekday`·`time`·`pkey` 는 **덧붙임이라 안 줘도 된다** —
    예전처럼 부르면 오늘 한 번 하는 회의다 (옛 카드와 시험 대역이 그대로 돌아간다)."""
    m = {"id": f"M{STATE.get('next_m', 1)}", "title": title, "issue": issue_no, "by": by,
         "date": date or datetime.date.today().isoformat(), "summary": None, "file": None,
         "channel": channel, "every": every, "weekday": weekday, "time": time, "pkey": pkey}
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
    # **아무것도 안 나왔으면 저장하지 않는다** (2026-09-23 사장님 실사용: 「회의록 작성 누르면
    # 이상한게 나오는거같은데」). 논의가 얕으면 AI 가 **빈 표만** 돌려준다 — 「정한 것 없음」·
    # 「액션 아이템 -」 이 줄줄이 적힌 문서가 레포에 남고, 사람은 그걸 회의록이라고 받는다.
    # 스레드가 비었을 때는 위에서 이미 막지만, **차 있는데 건질 게 없는** 경우가 더 흔하다
    if not any(r.get(k) for k in ("decisions", "action_items", "issue_changes", "new_issues")) \
            and not [a for a in (r.get("agenda_results") or []) if (a.get("decision") or "").strip()]:
        await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": m["card_ts"],
                  "text": say("mtg_nothing")})
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
# **참석자는 ⑤ 에서 묻는다** (2026-09-24 사장님: 「초대할 사람을 누구냐고 물어보고 안 적으면
# 전원 · 혼자만 참석하는 것일 수도」). 앞 칸 답에 `@사람` 을 섞어 적으셨으면 적어 두고 ⑤ 는 건너뛴다
WHO = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
STEP_WHAT = {"title": "무슨 회의인가", "project": "어느 프로젝트", "every": "한 번만인가 정기인가",
             "when": "언제", "who": "누구를 초대할까"}
WHO_ALL = re.compile(r"^\s*(1|1번|모두|전원|다|전부|다같이|다 같이|방 사람 모두|모두 초대)\s*[.!]?\s*$")
WHO_SOLO = re.compile(r"^\s*(2|2번|나만|저만|혼자|혼자만|나 혼자|저 혼자|나|저)\s*[.!]?\s*$")


def _who_line(uids):
    return " ".join(f"<@{u}>" for u in uids)


def _asking_meet(user):
    return (STATE.get("new_meeting") or {}).get(user)


def _msteps(st):
    """이 대화에서 물을 칸 — **아는 것은 빼고 센다** (할 일 등록과 같은 규칙).

    **날짜와 시간을 한 칸에서 받는다.** 「내일 2시」 라고 한 번에 말씀하시는 것이 자연스럽고,
    칸을 나누면 셋이 넷이 된다 (사장님: 「쉽게 가야해」). 시간만 빠졌으면 그 칸에서 한 번
    더 여쭐 뿐, 단계 수는 안 늘린다.
    """
    out = ["title"]
    if not st.get("proj_known"):
        out.append("project")
    return out + ["every", "when", "who"]


def _mwhere(st):
    """지금 몇 번째 칸인가 — (번호, 전체, 무엇)."""
    steps = _msteps(st)
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
    if step == "project":
        text = say("meet_ask_project", step=no, list=_project_list())
    elif step == "every":
        text = say("meet_ask_every", step=no)
    elif step == "when":
        text = (say("meet_ask_date", step=no) if st.get("every") == "once"
                else say("meet_ask_weekday", step=no, every=core.EVERY[st["every"]]))
    elif step == "who":
        text = say("meet_ask_who", step=no)
    else:
        return False
    await _msay(s, ch, head + text, th)
    return True


async def _after_name(s, ch, th, st, name):
    """이름을 받았다 — 프로젝트를 알면 ② 로, 모르면 프로젝트부터 묻는다."""
    head = say("meet_title_ok", title=name) + "\n\n"
    return await _mask(s, ch, th, st, "every" if st.get("proj_known") else "project", head)


def meet_room(st_or_m, dm=None):
    """이 회의 카드를 **어느 방에** 둘까 — **항상 그 프로젝트 방**이다 (2026-09-24 사장님:
    「회의는 팀대화가 아니라 프로젝트 방에 기록하는 게 맞다」 · 「프로젝트 방이 기본적으로 있잖아」).

    프로젝트를 만들면 방이 같이 생기므로 묻지도 고르지도 않는다. 예전에는 프로젝트마다 `meeting`
    칸으로 방을 따로 정하거나 비워서 DM 에만 둘 수 있었는데, 아무 프로젝트도 안 써서 지웠다.
    그리고 그게 없을 때 `request` 로 떨어져서, 요청 방을 같이 쓰는 프로젝트들의 회의가 전부
    #팀-대화 에 쌓였다.

    프로젝트 방을 못 찾을 때만(설정이 깨졌을 때) 대화하던 DM 에 둔다.
    """
    # **약칭이 빈 글자인 프로젝트도 프로젝트다** — 첫 프로젝트는 약칭 없이 만들어진다.
    # `if key` 로 가렸더니 「모아-시험」 회의가 프로젝트 방이 아니라 DM 에 남았다 (2026-09-24 실측)
    key = st_or_m.get("pkey") if isinstance(st_or_m, dict) else None
    p = next((x for x in PROJECTS if (x.get("key") or "") == key), None) if key is not None else None
    if p is None and len(PROJECTS) == 1:
        p = PROJECTS[0]
    return (p or {}).get("channel") or dm


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
        # **아는 것은 묻지 않는다** — 프로젝트가 하나뿐이면 그것이고, 말에 이름이 있으면 그것이다
        hit = _project_of(q) if len(PROJECTS) > 1 else (PROJECTS[0] if PROJECTS else None)
        if hit is not None:
            st["pkey"], st["proj_known"] = hit.get("key") or "", True
        STATE.setdefault("new_meeting", {})[user] = st
        save()
    # **시작한 스레드 안에서만 이어 간다** (사장님: 「스레드 안에서 시작한 건 거기에서 이야기가 맞어」)
    th = st.get("th") or e.get("thread_ts") or e.get("ts")
    if not opened and e.get("thread_ts") != th:
        if MEET_START.search(q) and not MEET_NOT.search(q):
            STATE["new_meeting"].pop(user, None); save()
            return await maybe(s, e, q, force=force)
        return False
    # 참석자 `@사람` 은 떼어 적어 두고, 남은 글자를 원래 답으로 읽는다
    who = WHO.findall(q)
    if who:
        for uid in who:
            if uid not in st.setdefault("who", []):
                st["who"].append(uid)
        q = WHO.sub(" ", q).strip()
        save()
        if not q and not opened and st.get("step") != "who":   # `@사람` 만 — 적어 두고 하던 칸을 다시
            n, total, what = _mwhere(st)
            await _msay(s, ch, say("meet_who_noted", who=_who_line(st["who"])) + "\n\n"
                        + say("ask_where", kind="회의 만들기", n=n, total=total, what=what), th)
            return True
    if opened:
        # 「회의 만들자」 처럼 이름이 없으면 묻는다. 「회의 주간 점검」 이면 그게 곧 이름이다
        name = _title_after(q)
        if titleable(name):
            st["title"] = name[:60]
            return await _after_name(s, ch, th, st, name[:60])
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
            return await _after_name(s, ch, th, st, rest[:60])
        st["step"] = "title"
        return await _magain(s, ch, th, st, say("meet_nope"))

    # ① 무슨 회의인가 — **적으신 그대로** 받는다.
    # 다만 「만들자」 처럼 **만들자는 낱말만** 오면 이름이 아니다 — 봇이 물은 것을 되풀이하신 것이다
    if step == "title":
        name = _title_after(q) or q.strip()
        if not titleable(name) or NOT_A_NAME.match(name):
            return await _magain(s, ch, th, st, say("meet_title_bad", word=q.strip()[:20]))
        st["title"] = name[:60]
        return await _after_name(s, ch, th, st, name[:60])

    # ② 어느 프로젝트 — **여럿일 때만** 묻는다
    if step == "project":
        p = _pick_project(q)
        if p is None:
            return await _magain(s, ch, th, st, say("meet_project_bad", list=_project_list()))
        # **`proj_known` 은 여기서 건드리지 않는다.** 그건 「처음부터 알고 있어서 이 칸을
        # 안 묻는다」 는 뜻이라, 칸 수를 세는 데 쓴다 — 답을 받았다고 켜면 대화 도중에
        # 「4칸 중 3번째」 가 「3칸 중 2번째」 로 줄어든다 (시험이 잡았다). 할 일 등록도 같다
        st["pkey"] = p.get("key") or ""
        return await _mask(s, ch, th, st, "every")

    # ③ 한 번만인가 정기인가
    if step == "every":
        every = core.meet_every(q)
        if not every:
            return await _magain(s, ch, th, st, say("meet_every_bad"))
        st["every"] = every
        return await _mask(s, ch, th, st, "when")

    # ④ 언제 — 정기면 요일, 한 번이면 날짜. **시간도 같은 칸에서 받는다**
    if step == "when":
        if not st.get("date"):                       # 날짜(요일)를 아직 못 받았다
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
        # **시간은 같은 칸에서 한 번 더 여쭌다** — 칸을 늘리지 않으려는 것이다 (사장님: 「쉽게 가야해」).
        # 「내일 2시」 처럼 한 번에 말씀하시면 여기서 바로 잡히고, 안 적으셨으면 한 줄 더 오간다
        hm = core.meet_time(q)
        if hm is None:
            if st.get("asked_time"):                 # 두 번째인데도 못 읽었다
                return await _magain(s, ch, th, st, say("meet_time_bad"))
            st["asked_time"] = True
            save()
            await _msay(s, ch, say("meet_ask_time"), th)
            return True
        st["time"] = list(hm)
        if st.get("who"):                            # 앞 칸에서 `@사람` 을 이미 적으셨다
            return await _mbuild(s, ch, th, st, user)
        return await _mask(s, ch, th, st, "who")

    # ⑤ 누구를 초대할까 — `@사람` · 1 모두(기본) · 2 나만
    if step == "who":
        if st.get("who"):                            # 맨 위에서 `@사람` 을 떼어 적어 뒀다
            return await _mbuild(s, ch, th, st, user)
        if WHO_SOLO.match(q):
            st["who"] = [user]                       # 만든 사람 — 다른 팀원이 만들어도 그 사람 캘린더에
        elif WHO_ALL.match(q):
            st["who_all"] = True
        else:
            return await _magain(s, ch, th, st, say("meet_who_bad"))
        return await _mbuild(s, ch, th, st, user)
    return False


# **옵션 뒤에 맨 `\w*` 를 두면 안 된다** (2026-09-23 실사용이 잡았다). 그렇게 두었더니
# 만드는 낱말이 없을 때 `\w*` 가 **아무 낱말이나 한 개** 먹었다 — 「회의 주간 점검」 의 제목이
# 「점검」 이 됐다. 어미는 만드는 낱말에 **붙어 있을 때만** 먹는다.
#
# 한 글자 어간(잡·열)은 어미를 **하나하나 적는다**: `잡\w*` 로 두면 「회의 **잡담방** 개선」 의
# 「잡담방」 을 먹는다. 두 글자 이상은 그럴 일이 드물어 세 글자까지 봐준다
TITLE_AFTER = re.compile(r"^\s*(?:회의|미팅)\s*"
                         r"(?:(?:카드|만들기|만들|만드|시작|생성|등록|추가)\w{0,3}"
                         r"|(?:잡|열)(?:아줘|어줘|아|자|어|을까|기|지))?\s*[:：]?\s*(.*)$")
# **만드는 낱말만 있는 답은 제목이 아니다** — 「만들자」 가 회의 이름이 됐다 (실사용).
# ① 칸에서 「만들자」 라고 답하시면 봇이 물은 것을 되풀이하신 것이지, 이름을 주신 게 아니다
NOT_A_NAME = re.compile(r"^(?:회의|미팅|카드)?\s*(?:만들|만드|잡|열|시작|생성|등록|추가|해줘|해|하자|할래)\w{0,3}$")
WORD_DATE = {"1": "오늘", "2": "내일", "3": "모레"}


def _title_after(text):
    """「회의 주간 점검」 의 뒷말 — 만들자는 낱말만 있으면 빈 글자."""
    m = TITLE_AFTER.match((text or "").strip())
    got = (m.group(1) if m else (text or "")).strip(" -—:·")
    if not titleable(got) or NOT_A_NAME.match(got):
        return ""
    return got


def _date_pick(text):
    """묻는 자리에서만 — 번호를 말로 바꿔 읽는다."""
    return WORD_DATE.get((text or "").strip(), text)


def _weekday_pick(text):
    """묻는 자리에서만 — 1~7 을 요일로 읽는다. **주말도 받는다**: 묻는 글에는 평일만
    보여 주지만(보통 그것이면 되니까), 토·일에 모이는 팀을 번호로 막을 까닭이 없다."""
    s = (text or "").strip()
    return f"{core.WEEK[int(s) - 1]}요일" if s.isdigit() and 1 <= int(s) <= 7 else text


async def _room_emails(s, channel):
    """그 방 사람 전원의 이메일 — 봇은 뺀다 (`is_bot`). (이메일 목록, 하나라도 못 읽었나) 를 돌려준다.

    `users:read.email` 권한이 없으면 프로필에 `email` 이 안 온다 — 그때는 그 사람만 빼고
    나머지는 초대하되, **일정은 그래도 만든다** — 초대 못 한 것 때문에 회의 자체가 막히면 안 된다.
    """
    members = (await api(s, "conversations.members", channel=channel, limit=1000)).get("members") or []
    return await _emails_of(s, members)


async def _emails_of(s, uids):
    """이 사람들의 이메일 — 봇·나간 사람은 뺀다. (이메일 목록, 하나라도 못 읽었나)."""
    emails, missed = [], False
    for uid in uids:
        u = (await api(s, "users.info", user=uid)).get("user") or {}
        if u.get("is_bot") or u.get("deleted"):
            continue
        email = (u.get("profile") or {}).get("email")
        if email:
            emails.append(email)
        else:
            missed = True
    return emails, missed


async def _gcal_note(s, m):
    """회의를 구글 캘린더에 올리고, DM 안내 끝에 붙일 한 줄을 돌려준다 (없으면 빈 글자).

    **멱등** — 이미 `gcal_id` 가 있으면(재시도) 다시 만들지 않는다. `ready()` 가 False 면
    (연결 안 된 팀) 이나 시간이 없는 회의면 — 캘린더 이야기 자체를 꺼내지 않고 조용히
    건너뛴다(사장님 결정, 실패가 아니다). **캘린더가 진짜 실패해도 회의는 이미 만들어져
    있다** — 다만 그건 조용히 넘어가지 않고 한 줄 알린다(계획: 「로그 ⚠️ + DM 스레드에 한 줄」).
    """
    if m.get("gcal_id") or not gcal.ready() or not m.get("time"):
        return ""
    attendees, missed = None, False
    if m.get("who"):                                 # 만들 때 `@사람` 으로 고르셨다 — 그분들만
        attendees, missed = await _emails_of(s, m["who"])
    elif m.get("who_all") or gcal.INVITE == "room":  # 「모두」 · 안 고르셨다 — 프로젝트 방 사람 전원
        attendees, missed = await _room_emails(s, mch(m))
    try:
        made = await gcal.create(m, attendees)
    except Exception as ex:
        log(f"⚠️ 구글 캘린더 만들기 실패: {type(ex).__name__}: {ex}")
        made = None
    save()
    if not made:
        return say("gcal_fail")
    note = say("gcal_added", link=made["gcal_link"]) if made.get("gcal_link") else say("gcal_added_plain")
    if missed:
        note += "\n" + say("gcal_no_email")
    return note


async def _mbuild(s, ch, th, st, user):
    """다 물었다 — 회의 카드를 만든다. 대화는 여기 DM 에 남고, **카드는 프로젝트가 정한 방에**
    간다 (`meet_room`). 프로젝트 방을 못 찾을 때만 카드도 이 DM 에 남는다."""
    STATE.get("new_meeting", {}).pop(user, None)
    save()
    room = meet_room(st, dm=ch)
    try:
        m = await new_meeting(s, st["title"], None, await name_of(s, {"user": user}), room,
                              every=st.get("every") or "once", date=st.get("date"),
                              weekday=st.get("weekday"), time=st.get("time"), pkey=st.get("pkey"))
    except Exception as ex:
        log(f"회의 만들기 실패: {type(ex).__name__}: {ex}")
        await _msay(s, ch, say("mtg_fail", err=str(ex)[:120]), th)
        return True
    if st.get("who"):
        m["who"] = list(st["who"])
    elif st.get("who_all"):
        m["who_all"] = True
    when = core.meet_label(m)
    # 구글 캘린더는 회의가 **만들어진 뒤에** 붙인다 — `new_meeting` 자체는 건드리지 않아
    # 옛 시험·대역이 그대로 돈다 (2026-09-24)
    gcal_note = await _gcal_note(s, m)
    if room == ch:                                   # 방에 안 올렸다 — 카드가 바로 여기 있다
        key = "meet_made_here" if (m.get("every") or "once") == "once" else "meet_made_here_every"
        text = say(key, title=m["title"], when=when)
    else:
        key = "meet_made" if (m.get("every") or "once") == "once" else "meet_made_every"
        text = say(key, title=m["title"], when=when, ch=room)
    await _msay(s, ch, text + (f"\n{gcal_note}" if gcal_note else ""), th)
    return True


async def open_note(s, trigger, card_ts):
    """✍️ 논의 적기 — **한 칸짜리 창**. 적은 것이 그대로 회의 스레드에 올라간다."""
    m = STATE.get("meetings", {}).get(card_ts)
    if not m:
        return
    await api(s, "views.open", body={"trigger_id": trigger, "view": {
        "type": "modal", "callback_id": "meet_note_submit", "private_metadata": card_ts,
        "title": {"type": "plain_text", "text": "회의 논의"},
        "submit": {"type": "plain_text", "text": "올리기"}, "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn",
             "text": f"*🗓️ {m['title']}* · {core.meet_label(m)}"}]},
            {"type": "input", "block_id": "note", "optional": False,
             "label": {"type": "plain_text", "text": "무엇을 이야기했나요?"},
             "element": {"type": "plain_text_input", "action_id": "v", "multiline": True},
             "hint": {"type": "plain_text",
                      "text": "정한 것 · 누가 뭘 하기로 했는지를 적어 주시면 회의록으로 정리해 드려요"}},
        ]}})


async def save_note(s, card_ts, text, user):
    """창에 적은 논의를 회의 스레드에 올린다 — **사람이 쓴 것처럼 그 자리에** 남는다."""
    m = STATE.get("meetings", {}).get(card_ts)
    if not m or not (text or "").strip():
        return
    who = load_team().get(user, {}).get("name") or await name_of(s, {"user": user})
    await api(s, "chat.postMessage", body={"channel": mch(m), "thread_ts": card_ts,
              "text": f"*{who}*: {text.strip()}"[:3900], "unfurl_links": False})
    await mark_talked(s, card_ts)
    log(f"회의 논의 적음 {m['id']} ← {user}")


async def mark_talked(s, card_ts):
    """이 회의에 **논의가 생겼다**고 표시하고 카드를 다시 그린다.

    이게 없으면 「📝 회의록 확정」 단추가 영영 안 나타난다 — 스레드에 사람이 직접 쓴 글은
    `handlers.py` 가, 창으로 적은 것은 `save_note` 가 여기로 온다. **봇이 쓴 글은 세지 않는다**
    (부르는 쪽에서 거른다): 10분 전 알림 때문에 「논의했다」 가 되면 안 된다.
    """
    m = STATE.get("meetings", {}).get(card_ts)
    if not m or m.get("talked"):
        return
    m["talked"] = True
    save()
    await api(s, "chat.update", body={"channel": mch(m), "ts": card_ts,
              "text": f"🗓️ {m['id']} {m['title']}", "blocks": meeting_blocks(m)})


async def soon_meetings(s, now=None):
    """회의 **10분 전에 그 방에 알린다** — 아침 루프가 매분 부른다.

    사장님: 「슬랙으로 바로 만드는거야」 — 밖으로 나가지 않고 슬랙 안에서 알림까지 끝낸다.
    **한 회의에 한 번만** 알린다 (`told`). 봇이 몇 분 늦게 돌아도 놓치지 않게 5~15분 전
    구간을 본다 — 딱 10분만 보면 그 분에 못 돌았을 때 영영 안 간다.
    """
    import datetime as dt
    now = now or dt.datetime.now()
    sent = []
    for m in STATE.get("meetings", {}).values():
        if m.get("told") or not m.get("time") or not m.get("date"):
            continue
        try:
            h, mi = m["time"]
            at = dt.datetime.combine(dt.date.fromisoformat(m["date"]), dt.time(h, mi))
        except (TypeError, ValueError):
            continue
        left = (at - now).total_seconds() / 60
        if not 5 <= left <= 15:
            continue
        link = (await api(s, "chat.getPermalink", channel=mch(m),
                          message_ts=m["card_ts"])).get("permalink", "")
        await api(s, "chat.postMessage", body={"channel": mch(m),
                  "text": say("meet_soon", title=m["title"], link=link), "unfurl_links": False})
        m["told"] = True
        sent.append(m["id"])
    if sent:
        save()
        log(f"회의 알림 {', '.join(sent)}")
    return sent


async def stop_every(s, card_ts, user):
    """정기 끄기 — **이 회의는 그대로 두고** 다음 회만 안 만든다. 지우는 게 아니다.

    캘린더에 올라간 반복 일정도 오늘까지만 돌게 끊는다(`gcal.stop_series`) — 안 그러면
    「정기 끄기」 를 눌러도 구글 캘린더에는 계속 초대가 온다. 캘린더 쪽이 실패해도 정기
    끄기 자체는 그대로 된다 — 카드가 먼저다.
    """
    m = STATE.get("meetings", {}).get(card_ts)
    if not m or (m.get("every") or "once") == "once":
        return
    try:
        await gcal.stop_series(m)
    except Exception as ex:
        log(f"⚠️ 구글 캘린더 정기 끄기 실패: {type(ex).__name__}: {ex}")
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
        # **캘린더는 반복 일정 하나** — 다시 만들지 않고 같은 gcal_id 를 물려준다. 안 물려주면
        # 「정기 끄기」 가 이 새 카드에서 눌렸을 때 gcal_id 를 몰라 캘린더 쪽은 계속 돈다
        for k in ("gcal_id", "gcal_link", "gcal_rrule"):
            if m.get(k):
                new[k] = m[k]
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
