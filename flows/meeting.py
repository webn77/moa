"""회의 · 회의 결정 → 이슈 (#30 에서 bot.py 를 나눔)."""
import asyncio, json, re, datetime
import gh_link
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


async def new_meeting(s, title, issue_no, by, channel):
    m = {"id": f"M{STATE.get('next_m', 1)}", "title": title, "issue": issue_no, "by": by,
         "date": datetime.date.today().isoformat(), "summary": None, "file": None, "channel": channel}
    STATE["next_m"] = STATE.get("next_m", 1) + 1
    d = await api(s, "chat.postMessage", body={"channel": channel, "text": f"🗓️ {m['id']} {title}",
                                               "blocks": meeting_blocks(dict(m, card_ts="tmp"))})
    m["card_ts"] = d["ts"]
    STATE.setdefault("meetings", {})[d["ts"]] = m
    await api(s, "chat.update", body={"channel": channel, "ts": d["ts"],
              "text": f"🗓️ {m['id']} {title}", "blocks": meeting_blocks(m)})
    save()
    await render_canvas(s)
    log(f"회의 {m['id']} 생성")


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


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from ai import ask_ai  # noqa: E402,F401
from flows.status import apply_change, record_change, redraw  # noqa: E402,F401
from views.canvas import render_canvas  # noqa: E402,F401
