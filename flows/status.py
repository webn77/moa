"""상태·담당·목표일 바꾸기 — 확인 대기 · 결정 이력 · 알림 (#30 에서 bot.py 를 나눔)."""
import asyncio, re, time, datetime
import core
from messages import MSG, say
from common import CHANNEL, DECISION, LABEL, NEXT_MIN, PLEVEL, PNAME, QUEUE, REQUEST, REVIEW_ON, SPEC_KEYS, log, plevel, short  # noqa: E402,F401
from docs import load_features, load_stages, load_team, suggest_due  # noqa: E402,F401
from slack import api, mood, person  # noqa: E402,F401
from store import STATE, add_log, ann_snap, chan, decision_snap, log_line, open_cards, plan_result, progress, ref, save, tag  # noqa: E402,F401


async def tell_assigned(s, c, by=None):
    """**맡은 사람에게 모아 DM 으로 알린다** (2026-09-22 사장님: 「이게 dm이 기본이 되어야 하는데」).

    모아의 「메시지」 탭은 **사람마다 따로인 1:1 DM** 이다 — 개인에게 할 말은 거기가 자리다.
    예전에는 카드 스레드의 멘션이 전부였다. 그러면 **그 방에 안 들어온 사람은 아무것도 모른다.**

    **자기가 가져간 일은 안 알린다** (`uid == by`) — 방금 자기가 누른 것을 다시 알리면 시끄럽다.
    DM 이 막혀도 일을 멈추지 않는다: 카드는 이미 만들어졌고, 못 보낸 것만 로그에 남긴다.
    """
    uid = c.get("assignee")
    if not uid or uid == by:
        return
    d = await api(s, "conversations.open", body={"users": uid})
    ch = (d.get("channel") or {}).get("id")
    if not ch:
        log(f"담당 DM 못 엶 {uid}: {d.get('error')}")
        return
    link = c.get("permalink") or ""
    if not link and c.get("card_ts"):
        link = (await api(s, "chat.getPermalink", channel=chan(c),
                          message_ts=c["card_ts"])).get("permalink", "")
    from docs import due_text, load_team as _team
    team = _team()
    await api(s, "chat.postMessage", body={"channel": ch, "unfurl_links": False, **mood("부탁"),
              "text": say("dm_assigned", ref=ref(c["no"], 30), channel=chan(c),
                          due=due_text(c) or "📅 언제까지는 아직 안 정했어요",
                          who=team.get(by, {}).get("name") or "누군가",
                          link=link or "")})
    log(f"담당 DM → {uid} ({ref(c['no'], 16)})")


async def take_card(s, c, who, how="pull_card"):
    """✋ 내가 할게요 — 누른 사람이 맡는다 = 확정 (pull)."""
    snap, asnap = decision_snap(), ann_snap(c)
    if c.get("assign_src") == "ai":                   # 지표: AI 배정 수락 / 다른 사람이 가져감
        c["ai_outcome"] = "accepted" if who == c.get("assignee") else "changed"
    c["assignee"], c["assign_src"] = who, "human"
    c.pop("no_auto", None)
    for moved_no, _ in balance():
        m = next((x for x in STATE["cards"].values() if x["no"] == moved_no), None)
        if m and m is not c:
            await api(s, "chat.update", body={"channel": chan(m), "ts": m["card_ts"],
                      "text": f"🎫 #{m['no']} {m['title']}", "blocks": card_blocks(m)})
    await redraw(s, c)
    await note_decisions(s, snap, who, c["no"])
    await announce(s, c, asnap, who)
    log(f"담당 확정 #{c['no']} ← {who} ({how})")


async def check_criteria(s, c, options, who, why=None):
    """완료 조건 체크 — 카드에 2/4, 한 줄 기록. 다 체크하면 확인 대기(확인할 사람이 있으면) 또는 닫을지 묻기."""
    dc = (c.get("spec") or {}).get("done_criteria") or []
    before_k, _ = progress(c)
    c["checked"] = [dc[int(o["value"])] for o in options if int(o["value"]) < len(dc)]
    k, n = progress(c)
    if k != before_k:                                  # 한 줄 기록 — 채널에는 올리지 않는다
        await post_log(s, c, add_log(c, f"완료 조건 {k}/{n}", load_team().get(who, {}).get("name", who or "누군가"), why, icon="☑"))
    if n and k == n > before_k and c["status"] not in ("done", "review", "cancelled"):
        if resolve(c, "done", who) == "review":                     # 확인할 사람이 있으면 → 확인 대기
            await apply_change(s, c, "set_status", "done", who)
        else:                                                       # 혼자 맡고 요청한 일 — 닫을지 묻는다 (실수로 닫히지 않게)
            text = say("all_checked", uid=who)
            await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"], "text": text,
                      "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text},
                                  "accessory": {"type": "button", "text": {"type": "plain_text", "text": "✅ 완료로 닫기"},
                                                "style": "primary", "action_id": "close_done", "value": c["card_ts"]}}]})
            await api(s, "chat.update", body={"channel": chan(c), "ts": c["card_ts"],
                      "text": f"🎫 #{c['no']} {c['title']}", "blocks": card_blocks(c)})
            await ensure_ctl(s, c)
    else:
        await api(s, "chat.update", body={"channel": chan(c), "ts": c["card_ts"],
                  "text": f"🎫 #{c['no']} {c['title']}", "blocks": card_blocks(c)})
        await ensure_ctl(s, c)
    save()
    asyncio.create_task(render_canvas(s))
    log(f"완료 조건 체크 #{c['no']} {k}/{n}")


async def close_done(s, c, who, msg_ts=None):
    """[✅ 완료로 닫기] — 혼자 맡고 요청한 일을 닫는다. 누른 버튼 자리는 결과로 바꾼다."""
    await apply_change(s, c, "set_status", "done", who)
    if msg_ts:
        await api(s, "chat.update", body={"channel": chan(c), "ts": msg_ts, "text": "완료로 닫았어요",
                  "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": "✅ 완료로 닫았어요"}]}]})


def resolve(c, want, user):
    """바꾸려는 상태 → 실제 상태. 담당자가 완료하면 「확인 대기」 — 규칙은 core.next_status."""
    return core.next_status(c, want, user, load_team(), REVIEW_ON)


async def ensure_ctl(s, c):
    """**더 이상 ⚙️ 를 따로 올리지 않는다** (2026-09-22 사장님: 「알림은 하나가 가도록 해줘」).

    설정은 이제 카드 안에 있다 (`views.card.card_blocks`). 할 일 하나에 글이 둘이면
    알림도 둘이다. 예전에 올려 둔 ⚙️ 가 남아 있으면 **치운다** — 두 벌이 보이면
    어느 것이 지금인지 사람이 모른다.
    """
    if c.get("ctl_ts"):
        await api(s, "chat.delete", body={"channel": chan(c), "ts": c.pop("ctl_ts")})
        log(f"옛 ⚙️ 설정 치움 #{c['no']}")


async def redraw(s, c):
    await api(s, "chat.update", body={"channel": chan(c), "ts": c["card_ts"],
              "text": f"🎫 #{c['no']} {c['title']}", "blocks": card_blocks(c)})
    if c.get("ctl_ts") or c["status"] not in ("done", "cancelled"):
        await ensure_ctl(s, c)
    await render_canvas(s)
    save()


async def open_editor(s, payload, c):
    """상태·담당을 창에서 고른다. 이모지를 몰라도 된다.
    작업자에게는 2칸만. 우선순위·단계·기능은 AI가 붙이고, PM이 열 때만 보인다 (틀을 작업자에게 요구하지 않는다)."""
    me = payload.get("user", {}).get("id")
    pm = True            # 누구나 모든 칸을 고친다 (Jira·Linear 처럼). 사람이 고친 값은 AI가 덮어쓰지 않는다
    opt = lambda k: {"text": {"type": "plain_text", "text": LABEL[k], "emoji": True}, "value": k}
    status_el = {"type": "static_select", "action_id": "status",
                 "options": [opt(k) for k in LABEL], "initial_option": opt(c["status"])}
    who_el = {"type": "users_select", "action_id": "assignee", "placeholder":
              {"type": "plain_text", "text": "담당자 고르기"}}
    if c.get("assignee"):
        who_el["initial_user"] = c["assignee"]
    popt = lambda k: {"text": {"type": "plain_text", "text": f"{PLEVEL[k]} {PNAME[k]}", "emoji": True}, "value": str(k)}
    prio_el = {"type": "static_select", "action_id": "priority", "options": [popt(k) for k in PLEVEL],
               "initial_option": popt(plevel(c))}
    due_el = {"type": "datepicker", "action_id": "due", "placeholder": {"type": "plain_text", "text": "완료 목표일"}}
    if c.get("due"):
        due_el["initial_date"] = c["due"]
    sopt = lambda k: {"text": {"type": "plain_text", "text": k[:75]}, "value": k}
    feat_el = {"type": "static_select", "action_id": "feature", "options": [sopt(f) for f in load_features()]}
    if c.get("feature") in load_features():
        feat_el["initial_option"] = sopt(c["feature"])
    stage_el = {"type": "static_select", "action_id": "stage",
                "options": [sopt(x["name"]) for x in load_stages()] + [sopt("나중 (단계 밖)")]}
    if c.get("stage") in {x["name"] for x in load_stages()}:
        stage_el["initial_option"] = sopt(c["stage"])
    await api(s, "views.open", body={"trigger_id": payload["trigger_id"], "view": {
        "type": "modal", "callback_id": "edit_card_submit", "private_metadata": c["card_ts"],
        "title": {"type": "plain_text", "text": f"이슈 #{c['no']}"},
        "submit": {"type": "plain_text", "text": "저장"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{c['title']}*"}]},
            {"type": "input", "block_id": "s", "label": {"type": "plain_text", "text": "상태"},
             "element": status_el},
            {"type": "input", "block_id": "a", "optional": True,
             "label": {"type": "plain_text", "text": "담당"}, "element": who_el},
            {"type": "input", "block_id": "d", "optional": True,
             "label": {"type": "plain_text", "text": "완료 목표일"}, "element": due_el},
        ] + ([{"type": "context", "elements": [{"type": "mrkdwn",
                "text": "우선순위·단계·기능은 AI가 붙여 둬요. 바꾸려면 PM에게 말해 주세요."}]}] if not pm else [
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "*PM만 보이는 칸* — AI가 붙인 것을 고칠 때만"}]},
            {"type": "input", "block_id": "p", "optional": True,
             "label": {"type": "plain_text", "text": "우선순위 (P1~P4)"}, "element": prio_el,
             "hint": {"type": "plain_text", "text": "여기서 고르면 사람이 정한 것으로 고정돼요. AI가 바꾸지 않습니다."}},
            {"type": "input", "block_id": "g", "optional": True,
             "label": {"type": "plain_text", "text": "로드맵 단계"}, "element": stage_el,
             "hint": {"type": "plain_text", "text": "단계 밖으로 두면 💤 나중으로 가요."}},
            {"type": "input", "block_id": "f", "optional": True,
             "label": {"type": "plain_text", "text": "기능 (상위 이슈)"}, "element": feat_el},
        ])}})


async def open_take_editor(s, trigger, c, who):
    """✋ 내가 할게요 — 맡기 전에 **언제까지를 본인이 정한다** (#52).

    예전에는 누르면 바로 맡고 목표일은 AI 가 찍어 줬다 (📅 9/23 💡). 본인이 그 날짜를
    본 적이 없으니 지키자고 할 수가 없고, 「3/5」 진척도 기준이 없는 숫자가 된다.

    **창을 띄우되 AI 추측을 미리 채워 둔다** — 채우기보다 고치기가 쉽다(우리가 쓰는 원칙).
    그냥 저장만 눌러도 되고, 그래도 **본인이 한 번은 본다.**
    """
    due = c.get("due") or (suggest_due(c) if c.get("hours") else None)
    picker = {"type": "datepicker", "action_id": "v",
              "placeholder": {"type": "plain_text", "text": "언제까지"}}
    if due:
        picker["initial_date"] = due
    await api(s, "views.open", body={"trigger_id": trigger, "view": {
        "type": "modal", "callback_id": "take_submit", "private_metadata": c["card_ts"],
        "title": {"type": "plain_text", "text": "내가 할게요"},
        "submit": {"type": "plain_text", "text": "맡기"}, "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{tag(c['no'], c)} {c['title']}*"}]},
            {"type": "input", "block_id": "due", "element": picker,
             "label": {"type": "plain_text", "text": "언제까지 하실 수 있나요?"},
             "hint": {"type": "plain_text", "text": "AI 가 예상 시간으로 잡아 둔 날짜예요 — 맞지 않으면 바꿔 주세요"}},
            {"type": "input", "block_id": "how", "optional": True,
             "label": {"type": "plain_text", "text": "어떻게 하실 건가요?"},
             "element": {"type": "plain_text_input", "action_id": "v"},
             "hint": {"type": "plain_text", "text": "한 줄이면 돼요. 안 쓰셔도 됩니다"}},
        ]}})


async def save_take(s, payload):
    """[맡기] — 담당 확정 + **사람이 정한 목표일**. 어떻게 할 건지는 스레드에 남긴다 (#52)."""
    v = payload["view"]
    c = STATE["cards"].get(v.get("private_metadata"))
    if not c:
        return
    vals = v["state"]["values"]
    due = ((vals.get("due") or {}).get("v") or {}).get("selected_date")
    how = (((vals.get("how") or {}).get("v") or {}).get("value") or "").strip()
    who = payload.get("user", {}).get("id")
    if due:
        c["due"], c["due_src"] = due, "human"      # 사람이 정했으므로 AI 가 덮어쓰지 않는다
    await take_card(s, c, who, "pull_card")
    if how:
        await post_log(s, c, add_log(c, "이렇게 할게요", load_team().get(who, {}).get("name", "누군가"),
                                     how[:200], icon="🗺️"))
        save()


async def open_content_editor(s, trigger, c, push=False):
    """사람이 제목·이슈 정의를 직접 고친다. 비워 두면 그 칸은 없는 것으로."""
    sp = c.get("spec") or {}
    field = lambda bid, label, val, multi=True, hint=None: {
        "type": "input", "block_id": bid, "optional": bid != "title",
        "label": {"type": "plain_text", "text": label},
        "element": {"type": "plain_text_input", "action_id": "v", "multiline": multi, "initial_value": val or ""},
        **({"hint": {"type": "plain_text", "text": hint}} if hint else {})}
    view = {"type": "modal", "callback_id": "edit_content_submit", "private_metadata": c["card_ts"],
            "title": {"type": "plain_text", "text": f"#{c['no']} 내용 수정"},
            "submit": {"type": "plain_text", "text": "저장"}, "close": {"type": "plain_text", "text": "닫기"},
            "blocks": [field("title", "제목", c["title"], False)]
            + [field(k, label, sp.get(k)) for k, label in SPEC_KEYS]
            + [field("done_criteria", "완료 조건", "\n".join(sp.get("done_criteria") or []), hint="한 줄에 하나씩"),
               # 선행은 여기서 받는다 — 새 창을 띄우면 창이 3층이 된다 (상세도 이미 창이다).
               # 비우면 「기다리는 게 없다」 는 뜻이고, 고치면 after_src=human 이 붙어 AI 가 못 지운다 (#68)
               field("after", "먼저 끝나야 할 일", " ".join(f"#{x}" for x in (c.get("after") or [])), False,
                     hint="이슈 번호를 띄어쓰기로. 예: #52 #57 · 비우면 기다리는 것 없음"),
               field("reason", "바꾼 이유", "", False, hint="한 줄이면 돼요. 비워 두면 스레드에서 한 번 여쭤볼게요")]}
    await api(s, "views.push" if push else "views.open", body={"trigger_id": trigger, "view": view})


async def save_content(s, payload):
    v = payload["view"]
    c = STATE["cards"].get(v.get("private_metadata"))
    if not c:
        return
    vals = {k: (x.get("v") or {}).get("value") or "" for k, x in v["state"]["values"].items()}
    old_title, old_spec = c["title"], dict(c.get("spec") or {})
    c["title"] = vals.get("title", c["title"]).strip()[:80] or c["title"]
    sp = dict(c.get("spec") or {})
    for k, _ in SPEC_KEYS:
        sp[k] = vals.get(k, "").strip()
    sp["done_criteria"] = [x.strip("-☐ ").strip() for x in vals.get("done_criteria", "").splitlines() if x.strip()]
    c["spec"] = sp if any(sp.values()) else None
    c["spec_src"], c["coach"] = "human", "done"
    c.pop("spec_draft", None)
    # 선행 — **바뀌었을 때만** 사람 것으로 잠근다. 제목만 고치고 저장한 사람까지 잠그면
    # 「앞선 일 찾기」 가 그 이슈를 영영 못 채운다
    want = core.clean_after(re.findall(r"\d{1,4}", vals.get("after", "")), c["no"], STATE["cards"])
    if want != (c.get("after") or []):
        c["after"], c["after_src"] = want, "human"
    await record_change(s, c, payload.get("user", {}).get("id"), old_title, old_spec, vals.get("reason", "").strip())
    await redraw(s, c)
    log(f"내용 수정 #{c['no']}")


def ai_reason(c, k, after):
    """AI 가 바꾼 이유 — 배정 계산이 남긴 사유를 칸마다 읽기 쉽게."""
    if k == "assignee":
        return "자리가 나서 우선순위 순으로 배정" if after else (c.get("prio_reason") or "자리 조정")
    if k == "due":
        return "예상 시간으로 계산한 제안" if after else "배정이 풀려 제안 날짜도 뺌"
    return c.get("prio_reason") or "점수 다시 계산"


async def post_log(s, c, e, ask_uid=None):
    """한 줄로 스레드에 남긴다. 이유가 없으면 끝에 한 번 묻고, 다음 답글이 오면 이 줄을 고쳐 이유를 넣는다."""
    text = log_line(e) + (say("ask_reason", uid=ask_uid) if ask_uid else "")
    d = await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"], "text": text})
    e["ts"] = d.get("ts")
    if ask_uid:
        c["await_reason"] = len(c["edits"]) - 1


async def note_decisions(s, snap, actor=None, target=None, how=None, why=None, skip=()):
    """결정이 바뀐 카드마다 스레드에 한 댓글 — 누가(사람/AI) · 무엇이 · 왜.
    actor+target: 그 카드는 사람이 바꾼 것. how+why: 사람이 AI 추천을 적용한 것 (전부). 나머지는 AI."""
    team = load_team()
    now = decision_snap()
    for c in STATE["cards"].values():
        no = c["no"]
        if no not in snap or no not in now or no in skip:
            continue
        diff = core.decision_diff(snap[no], now[no], DECISION)
        if not diff:
            continue
        src = {"assignee": c.get("assign_src") == "human", "plevel": c.get("plevel_src") == "human",
               "due": c.get("due_src") == "human", "stage": True}      # 칸마다 누가 정한 값인가
        mine = [d for d in diff if (how and (target is None or no == target)) or (actor and no == target and src[d[0]])]
        ai = [d for d in diff if d not in mine]
        human = bool(mine)
        who = team.get(actor, {}).get("name", "누군가") if human else "AI"
        tag = (how or "직접 바꿈") if human else "자동 조정"
        reasons = [why] if human and how and why else list(dict.fromkeys(ai_reason(c, k, b) for k, _, _, b in ai))
        what = ", ".join(f"{label} {decision_value(k, a, team)}→{decision_value(k, b, team)}"
                         + ("(AI)" if human and (k, label, a, b) in ai else "") for k, label, a, b in diff)
        by_ = f"{how}·{who}" if human and how else who if human else "AI 자동 조정"
        e = add_log(c, what, by_, " · ".join(reasons) or None, kind="decision", icon="🔄",
                    fields=[label for _, label, _, _ in diff], by=actor if human else "ai")
        ask = human and not how and any(k in ("plevel", "due", "stage") for k, _, _, _ in mine)
        await post_log(s, c, e, actor if ask else None)          # 우선순위·목표일·단계는 이유를 한 번 묻는다


async def announce(s, c, before, user=None):
    """중요한 변화 5가지(시작·완료·보류·P1/P2로 올라감·담당 확정)만 채널에도 한 줄 (reply_broadcast).
    채널은 시간순이라 맨 아래가 곧 최근에 움직인 일이 된다. 나머지 안내는 스레드 안에서만."""
    team = load_team()
    nm = lambda u: team.get(u, {}).get("name", "누군가")
    f = dict(ref=ref(c["no"], 24))
    day = lambda x: f"{int(x['due'][5:7])}/{int(x['due'][8:])}" if x.get("due") else ""
    post = lambda text, cast=False, ts=None, face=None: api(s, "chat.postMessage", body={
        "channel": chan(c), "thread_ts": ts or c["card_ts"], "text": text, "reply_broadcast": cast,
        "unfurl_links": False, **mood(face)})
    st, was = c["status"], before["status"]
    if st in ("review", "done") and was not in ("review", "done"):
        c["finished"] = datetime.date.today().isoformat()          # 계획대로였나는 끝낸 날로 잰다
    if st == "review" and was != "review":
        return await request_review(s, c, user)
    what = MSG["requester_what"].get(st)
    if what and st != was and was != "review" and c.get("origin_ts") and c.get("origin_channel") not in (None, chan(c)):
        await api(s, "chat.postMessage", body={"channel": c["origin_channel"], "thread_ts": c["origin_ts"],   # 요청한 자리에
                  "unfurl_links": False, "text": say("requester", ref=ref(c["no"], 30), what=what)})
    if st == "doing" and was not in ("doing", "review"):          # 확인 대기에서 돌아온 건 review_answer 가 알린다
        await post(say("start_line", **f, who=nm(c.get("assignee") or user)), True, face="기본")
        add_log(c, "시작", nm(c.get("assignee") or user), icon="👀")
        dc = (c.get("spec") or {}).get("done_criteria") or []
        await post(say("start_guide" if dc else "start_guide_empty", uid=c.get("assignee") or user,
                       due=day(c) or "아직 없어요", dc=f"{len(dc)}개예요 (📄 상세)"))
    elif st == "done" and was != "done":
        waiting = [x for x in open_cards() if c["no"] in (x.get("after") or [])]
        freed = [x for x in waiting if all(a == c["no"] or a in {y["no"] for y in STATE["cards"].values()
                                           if y["status"] in ("done", "cancelled")} for a in x.get("after") or [])]
        add_log(c, f"완료 — {plan_result(c)}", nm(c.get("assignee") or user), icon="✅")
        await post(say("done_line", **f, plan=plan_result(c),
                       freed=f" · 기다리던 일도 풀렸어요: {', '.join(ref(x['no'], 12) for x in freed)}" if freed else ""), True, face="완료")
        for x in freed:
            if x.get("assignee") and x.get("card_ts"):
                await post(say("freed", uid=x["assignee"], **f), ts=x["card_ts"])
        who = c.get("assignee") or user
        nxt = sorted([x for x in open_cards() if x.get("assignee") == who and x["status"] == "todo"],
                     key=lambda x: (core.due_urgency(x), plevel(x), x.get("rank", 99)))   # 날짜를 먼저 본다
        if who and nxt:
            n = nxt[0]
            await post(say("next", uid=who, next=ref(n["no"], 20),
                       why=" · ".join(x for x in [PLEVEL[plevel(n)].split()[1], day(n) and f"목표일 {day(n)}"] if x)))
    elif st == "blocked" and was != "blocked":
        await post(say("blocked_line", **f), True, face="막힘")
        add_log(c, "보류", nm(user), icon="⛔")
    if st not in ("done", "cancelled"):
        if plevel(c) <= 2 < before["plevel"]:
            await post(say("up_line", **f, p=PLEVEL[plevel(c)].split()[1],
                       nobody=", 담당이 아직 없어요" if not c.get("assignee") else ""), True)
        if st == was and c.get("assign_src") == "human" and c.get("assignee") \
                and (before["assign_src"] != "human" or before["assignee"] != c["assignee"]):
            await post(say("took_line", **f, who=nm(c["assignee"])), True)


async def record_change(s, c, user, old_title, old_spec, reason, how="직접 고침"):
    """요구사항이 바뀌면 무엇이 · 왜 바뀌었는지 카드 스레드에 남긴다. 정본 md 「변경 이력」 에도 들어간다.
    user=None 이면 AI 가 바꾼 것 — 이유를 묻지 않는다."""
    diff = core.spec_diff(old_title, old_spec, c["title"], c.get("spec"), SPEC_KEYS)
    if not diff:
        return
    who = load_team().get(user, {}).get("name", "누군가") if user else "AI"
    what = ", ".join(f"완료 조건 " + " ".join(x for x in [a and f"−{short(a, 20)}", b and f"+{short(b, 20)}"] if x)
                     if k == "완료 조건" else f"{k} 수정" for k, a, b in diff)
    by_ = who if how == "직접 고침" else f"{how}·{who}" if user else how
    e = add_log(c, what, by_, reason or None, kind="spec", icon="📝", fields=[d[0] for d in diff], by=user)
    await post_log(s, c, e, user if not reason and user else None)


def balance():
    """상태·담당이 바뀐 뒤 자리를 다시 맞춘다 — 넘치면 뒤로, 비면 당긴다. 계산은 place() 와 같다. 바뀐 것을 돌려준다."""
    before = {c["no"]: c.get("priority") for c in STATE["cards"].values()}
    place()
    return [(c["no"], c["priority"]) for c in STATE["cards"].values()
            if c["status"] not in ("done", "cancelled") and before.get(c["no"]) != c.get("priority")]


def place():
    """배정 계산은 core.place — 여기서는 상태와 설정만 넘긴다. 규칙은 priority.md."""
    core.place(STATE["cards"], load_team(), {x["name"] for x in load_stages()}, QUEUE, NEXT_MIN,
               STATE.get("capcut"), suggest_due)


async def apply_change(s, c, kind, val, user, how=None, why=None):
    """카드에서 바로 바꾼 값 하나를 반영한다 — 사람이 정한 값은 AI가 덮어쓰지 않는다."""
    if not val:
        return
    snap, asnap = decision_snap(), ann_snap(c)
    before = c["status"]
    if kind == "set_status":
        val = resolve(c, val, user)
    if kind == "set_status" and val != c["status"]:
        c["status"], c["since"], c["at"] = val, datetime.date.today().isoformat(), time.time()
        if val == "doing" and (not c.get("assignee") or c.get("assign_src") == "ai"):
            if c.get("assign_src") == "ai":
                c["ai_outcome"] = "accepted" if user == c.get("assignee") else "changed"
            c["assignee"], c["assign_src"] = user, "human"             # 진행 중으로 바꾼 사람이 맡는다
    elif kind == "set_prio" and val.isdigit():
        c["plevel"], c["plevel_src"], c["prio_reason"] = int(val), "human", "사람이 정함"
    elif kind == "set_due":
        c["due"], c["due_src"] = val, "human"
    elif kind == "set_stage":
        c["stage"] = val if val in {x["name"] for x in load_stages()} else None
    elif kind == "set_feature":
        c["feature"] = val
    elif kind == "set_assignee" and val != c.get("assignee"):
        if c.get("assign_src") == "ai":
            c["ai_outcome"] = "changed"
        c["assignee"], c["assign_src"] = val, "human"
        c.pop("no_auto", None)
        await tell_assigned(s, c, user)          # 남이 맡겼으면 그 사람 DM 으로
    else:
        return
    for moved_no, _ in balance():                                    # 넘치면 뒤로, 비면 당긴다
        m = next((x for x in STATE["cards"].values() if x["no"] == moved_no), None)
        if m and m is not c:
            await api(s, "chat.update", body={"channel": chan(m), "ts": m["card_ts"],
                      "text": f"#{m['no']} {m['title']}", "blocks": card_blocks(m)})
            if m.get("ctl_ts"):
                await ensure_ctl(s, m)
    await redraw(s, c)
    await note_decisions(s, snap, user, c["no"], how=how, why=why)
    await announce(s, c, asnap, user)
    log(f"카드에서 바꿈 #{c['no']} {kind}={val}")


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from views.canvas import render_canvas  # noqa: E402,F401
from views.card import card_blocks, ctl_blocks, decision_value, review_blocks  # noqa: E402,F401
from flows.review import remind_reviews, request_review, review_answer  # noqa: E402,F401
