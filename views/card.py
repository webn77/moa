"""카드 · ⚙️ 설정 · 체크리스트 체크박스 · 📄 상세 — 화면만 만든다 (#30 에서 bot.py 를 나눔)."""
import re
import core
from messages import say
from common import AI_LABEL, HANDLE, LABEL, PLEVEL, PNAME, PROJECTS, SICON, SPEC_KEYS, mday, plevel  # noqa: E402,F401
from docs import due_text, load_team, need_hours, okr_of  # noqa: E402,F401
from store import STATE, bar, progress, ref, tag, timeline  # noqa: E402,F401


# 번호 매긴 항목 앞에서 줄을 바꾼다. 앞의 공백을 **먹어서** 옮긴다 — 뒤돌아보기만 쓰면
# 빈 너비 매치가 같은 자리에서 두 번 걸려 빈 줄이 하나 더 생긴다 (2026-09-20 실측)
STEP = re.compile(r"[ \t]*([①②③④⑤⑥⑦⑧⑨])")


def readable(text):
    """긴 정의를 읽을 수 있게 편다 (2026-09-20 사장님 지적: 「너무 어려워」).

    글자 수보다 **한 덩어리로 붙어 있는 것**이 문제였다 — ①②③ 이 한 줄에 쭉 이어지면
    눈이 쉴 자리가 없다. 항목마다 줄을 바꾼다.

    별 두 개(`**굵게**`)도 하나로 고친다. Slack 은 별 하나가 굵게이고, 두 개를 쓰면
    별이 글자로 남는 자리가 있다 — 우리가 md 습관으로 쓴 것이 그대로 새어 나왔다.
    """
    t = (text or "").replace("**", "*")
    return STEP.sub(r"\n  \1", t).strip()


def state_line(c, team=None):
    """카드·홈·팝업이 같이 쓰는 한 줄 — 상태(🕒 대기 · 🔄 진행 중 · ⏸️ 보류 · ✅ 완료 · ❌ 취소) · P · 담당 · 📅 목표일.
    우선순위 색은 제목 앞에 따로 붙는다."""
    team = team if team is not None else load_team()
    nm = lambda u: team.get(u, {}).get("name", "미정")
    st = f"{SICON[c['status']]} {LABEL[c['status']]}"
    if c["status"] == "cancelled":
        return f"{st} — {c.get('cancel_reason') or '사유 없음'}"
    if c["status"] == "done":
        return st + (f" · {nm(c['assignee'])}" if c.get("assignee") in team else "")
    k, n = progress(c)
    parts = [st + (f" {k}/{n}" if n and (k or c["status"] == "doing") else ""), PLEVEL[plevel(c)].split()[1]]
    if c.get("assignee"):
        parts.append(f"담당 {nm(c['assignee'])}" + (" (AI 추천 — 확정 전)" if c.get("assign_src") == "ai" else ""))
    else:
        parts.append("담당 없음" + (f" (추천 {nm(c['suggested'])})" if c.get("suggested") else ""))
    parts.append(due_text(c))
    return " · ".join(x for x in parts if x)


def card_blocks(c):
    """채널 카드 = 제목 + 상태 줄 + 버튼 + **설정**. 사람 이름은 멘션하지 않는다 — 알림이 눈에 걸린다.

    **설정을 카드 안에 넣는다** (2026-09-22 사장님: 「알림은 하나가 가도록 해줘」).
    예전에는 스레드에 ⚙️ 메시지를 따로 올렸다 — 할 일 하나에 **글이 둘**이었고, 둘 다 알림을 울렸다.
    Slack 블록 한도는 50 인데 합쳐도 7 이라 나눌 이유가 없었다.
    끝난 일에는 설정을 안 붙인다 — 고칠 게 없다.
    """
    ts = c.get("card_ts") or "-"                                       # 첫 게시 땐 아직 ts 가 없다
    open_ = c["status"] not in ("done", "cancelled")
    title = f"~{tag(c['no'])} {c['title']}~" if c["status"] in ("done", "cancelled") else f"{tag(c['no'])} {c['title']}"   # 끝난 일은 취소선
    f, _, _ = okr_of(c)
    line = "  ·  ".join(x for x in [state_line(c), f"🧩 {f}" if f else "",
                                    "GitHub 에서 온 일" if c.get("by") == "GitHub" else ""] if x)
    btns = []
    if open_ and (not c.get("assignee") or c.get("assign_src") == "ai"):   # 아직 사람이 확정하지 않았을 때만
        btns.append({"type": "button", "text": {"type": "plain_text", "text": "✋ 내가 할게요"},
                     "action_id": "pull_card", "value": ts})
    if open_:
        # **설명을 쓰는 자리가 카드에 있어야 한다** (2026-09-22 사장님). 전에는 📄 상세 →
        # ✏️ 내용 수정 으로 **창이 두 겹**이라 아무도 못 찾았다. Jira·Linear 도 제목 다음이 본문이다.
        # 메시지 안에 직접 타자 치는 칸은 두지 않는다 — 카드가 다시 그려지면(우선순위 재계산 ·
        # 담당 배정) 치던 글자가 날아간다. 그래서 단추 → 창이다
        wrote = any((c.get("spec") or {}).get(k) for k, _ in SPEC_KEYS)
        btns.append({"type": "button", "text": {"type": "plain_text", "text": "✏️ 설명 고치기" if wrote else "📝 설명 쓰기"},
                     "action_id": "edit_content", "value": ts})
    btns.append({"type": "button", "text": {"type": "plain_text", "text": "📄 상세"}, "action_id": "show_md", "value": ts})
    out = [{"type": "section", "text": {"type": "mrkdwn", "text": f"{PLEVEL[plevel(c)].split()[0]} *{title}*"}},
           {"type": "context", "elements": [{"type": "mrkdwn", "text": line or " "}]}]
    # **남은 칸을 보여 준다** (2026-09-23 사장님: 「회원가입처럼 남은 거를 누가 더 만들지」).
    # 한 사람이 다 만들지 않아도 된다 — 제목만 던져 두면 다음 사람이 이어 채운다.
    # 안 보이면 아무도 이어 쓰지 않는다. 다 찼으면 **안 쓴다** — 칭찬은 자리를 먹는다
    k, n, left = core.filled(c)
    if open_ and left:
        out.append({"type": "context", "elements": [{"type": "mrkdwn",
                    "text": f"✍️ *{k}/{n} 채워짐* — 남은 것: {' · '.join(left)}  (아무나 이어서 채우셔도 돼요)"}]})
    out.append({"type": "actions", "elements": btns})
    return out + (ctl_blocks(c) if open_ else [])


def similar_blocks(c, hits):
    """「혹시 같은 일인가요?」 — 만들 때 한 번 물어본다 (#57).

    쌓인 것을 치우는 것보다 안 쌓이게 하는 게 싸다. 넘겨짚지 않고 **묻기만** 한다 —
    낱말 겹침으로 고른 후보라 틀릴 수 있고, 정하는 건 사람이다.
    """
    rows = []
    for o, ratio in hits:
        rows.append({"type": "section",
                     "text": {"type": "mrkdwn", "text": f"{link_of(o, True)}\n{state_line(o)}"}})
        rows.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": f"📄 #{o['no']} 상세"},
             "action_id": "show_md", "value": o["card_ts"]},                     # 보고 나서 정하게
            {"type": "button", "text": {"type": "plain_text", "text": "같은 일이에요"},
             "action_id": "same_as", "value": f"{c['card_ts']}|{o['no']}"}]})
    return [{"type": "section", "text": {"type": "mrkdwn", "text": say("similar_ask", n=len(hits))}}] + rows + [
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "다른 일이에요"},
             "action_id": "not_same", "value": c["card_ts"]}]}]


def rough_blocks(spec):
    """아직 모자란 초안 — **단추 없이** 보여 준다 (2026-09-20).

    빈 질문(「누가 무엇 때문에 곤란한가요?」)은 답하기 어렵다. 추측한 것을 보여 주고
    고치게 하는 편이 훨씬 쉽다 — 채우기보다 고치기가 쉽다.
    """
    fill = lambda v: "❓" if (not v or "미정" in str(v)) else v    # 「미정」 도 빈 칸이다 — 고칠 자리가 보여야 한다
    done = [x for x in (spec.get("done_criteria") or []) if "미정" not in x]
    rows = [f"*{label}*  {fill(spec.get(k))}" for k, label in
            (("why", "왜"), ("change", "바뀌는 것"), ("expect", "기대와 확인"))]
    rows.append("*체크리스트*  " + (" / ".join(done) if done else "❓"))
    return [{"type": "section", "text": {"type": "mrkdwn", "text": say("rough_head")}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(rows)[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": say("rough_tail")}]}]


def draft_blocks(key, spec):
    """이슈 초안 — 아직 카드가 아니다. 번호도 없다. 사람이 눌러야 생긴다 (#54, #11)."""
    done = spec.get("done_criteria") or []
    body = "\n".join(x for x in [
        f"*{spec.get('title', '(제목 없음)')}*",
        f"*왜* {spec.get('why', '-')}", f"*바뀌는 것* {spec.get('change', '-')}",
        f"*기대와 확인* {spec.get('expect', '-')}",
        f"*하지 않는 것* {spec.get('not_doing', '-')}",
        "*체크리스트*\n" + "\n".join(f"☐ {x}" for x in done) if done else ""] if x)
    # 프로젝트가 여럿이면 **만들기 전에** 고르게 한다 (#70, 사장님 지적 9/21).
    # 팀 대화방은 프로젝트들이 함께 쓰는 자리라 방만 보고는 어느 프로젝트인지 알 수 없다.
    # 만든 뒤에 바꾸는 길은 두지 않는다 — 카드 메시지가 이미 그 방에 올라가 있어서 옮길 수가 없다.
    if len(PROJECTS) > 1:
        make = [{"type": "button", "text": {"type": "plain_text", "text": f"✅ {p['name']} 로"},
                 "style": "primary" if i == 0 else None, "action_id": f"draft_make_{p['key']}",
                 "value": f"{key}|{p['key']}"} for i, p in enumerate(PROJECTS[:4])]
        make = [{k: v for k, v in b.items() if v is not None} for b in make]
        tail = "어느 프로젝트의 일인지 골라 주세요 — 만들고 나면 옮길 수 없어요"
    else:
        make = [{"type": "button", "text": {"type": "plain_text", "text": "✅ 할 일로 만들기"}, "style": "primary",
                 "action_id": "draft_make", "value": key}]
        tail = "아직 번호가 없어요 — 만들기를 누르시면 그때 생겨요"
    return [{"type": "section", "text": {"type": "mrkdwn", "text": body[:2900]}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": tail}]},
            {"type": "actions", "elements": make + [
                {"type": "button", "text": {"type": "plain_text", "text": "아니요"}, "action_id": "drop_draft", "value": key}]}]


def peek_blocks(c):
    """어느 채널에서든 `#40` 이라고 쓰면 펼쳐지는 작은 카드 (#54).

    채널 카드와 같은 모양·같은 버튼이다 — 자리마다 다르게 보이면 배워야 할 것이 늘어난다.
    맨 아래에 원래 카드로 가는 링크만 더한다."""
    out = list(card_blocks(c))
    if c.get("permalink"):
        out.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{c['permalink']}|프로젝트 방에서 보기>"}]})
    return out


def ctl_blocks(c):
    """카드 안의 「⚙️ 이 이슈 설정」 — AI가 먼저 채우고, 사람은 담당을 정하고 필요하면 고친다.

    **단계·기능은 여기 없다** (2026-09-22 사장님: 「이건 지금하고 안 맞는 거 같은데」).
    둘 다 **사람이 고를 칸이 아니다** — 단계는 만들 때 지금 단계로 자동, 기능은 AI가 붙인다.
    새 팀에서는 고를 것이 하나뿐이라(로드맵 세 줄 · 기능 표 한 줄) 칸만 차지했다.
    값은 그대로 살아 있고 — 현황판 로드맵 진행률 · 🧩 기능 표 · 자동 배정 문지기가 쓴다 —
    고치는 길은 앱 홈 ✏️ 창과 회의록 적용에 남겨 뒀다. Jira·Linear 도 Epic·Project 를 접어 둔다.

    고를 것이 없는 칸은 애초에 넣지 않는다 — Slack 은 options 가 빈 static_select 를 보면
    **메시지 전체를** `invalid_blocks` 로 막는다 (2026-09-22 실측: mju 는 기능 표가 비어서
    ⚙️ 설정이 한 번도 안 올라갔다 — 조용히).
    """
    team = load_team()
    nm = lambda u: team.get(u, {}).get("name", "미정")
    ts = c.get("card_ts") or "-"
    sopt = lambda k: {"text": {"type": "plain_text", "text": f"{SICON[k]} {LABEL[k]}", "emoji": True}, "value": k}
    popt = lambda k: {"text": {"type": "plain_text", "text": f"{PLEVEL[k]} {PNAME[k]}", "emoji": True}, "value": str(k)}
    who_el = {"type": "users_select", "action_id": "set_assignee", "placeholder": {"type": "plain_text", "text": "누가 맡을까요?"}}
    if c.get("assignee"):
        who_el["initial_user"] = c["assignee"]
    due_el = {"type": "datepicker", "action_id": "set_due", "placeholder": {"type": "plain_text", "text": "목표일"}}
    if c.get("due"):
        due_el["initial_date"] = c["due"]
    els = [who_el,
           {"type": "static_select", "action_id": "set_prio", "options": [popt(k) for k in PLEVEL], "initial_option": popt(plevel(c))},
           due_el,
           {"type": "static_select", "action_id": "set_status", "options": [sopt(k) for k in LABEL], "initial_option": sopt(c["status"])}]
    ai = []
    if c.get("assign_src") == "ai" and c.get("assignee"):
        ai.append(f"담당 {nm(c['assignee'])} (AI 추천 — {c.get('assign_reason') or '역할 기준'})")
    elif not c.get("assignee") and c.get("suggested"):
        ai.append(f"추천 담당 {nm(c['suggested'])} — 자리가 나면 자동으로 가요")
    if c.get("plevel_src") != "human":
        ai.append(f"우선순위 {PLEVEL[plevel(c)]} (AI — {c.get('prio_reason') or '점수 기준'})")
    if c.get("due_src") == "ai":
        ai.append("목표일은 예상 시간으로 계산한 제안")
    out = [{"type": "section", "text": {"type": "mrkdwn", "text": "⚙️ *이 할 일 설정* — AI가 먼저 정해 뒀어요. 담당 · 우선순위 · 목표일 · 상태, 다르면 고쳐 주세요"}},
           {"type": "actions", "block_id": f"card:{ts}", "elements": els},
           {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(ai) or "사람이 정한 값이에요"}]}]
    if c["status"] == "review" and c.get("review_by"):
        out.insert(0, {"type": "section", "text": {"type": "mrkdwn", "text": say("review_ctl", uid=c["review_by"])}})
    return out + checklist_blocks(c)


def checklist_blocks(c):
    """체크리스트 체크박스 — 스레드 ⚙️ 설정과 📄 상세가 같이 쓴다. 체크하면 카드에 2/4, 다 체크하면 확인 대기."""
    done = ((c.get("spec") or {}).get("done_criteria") or [])[:10]
    if not done:
        return [{"type": "context", "elements": [{"type": "mrkdwn", "text": "*체크리스트* 아직 없어요 — 뭘 해야 하는지 스레드에 한두 줄 알려 주시면 정리해 둘게요"}]}]
    k, n = progress(c)
    opts = [{"text": {"type": "mrkdwn", "text": x[:150]}, "value": str(i)} for i, x in enumerate(done)]
    el = {"type": "checkboxes", "action_id": "check_dc", "options": opts}
    got = [o for o, x in zip(opts, done) if x in (c.get("checked") or [])]
    if got:
        el["initial_options"] = got
    return [{"type": "section", "text": {"type": "mrkdwn",
             "text": f"*{bar(k, n)} 체크리스트 {k}/{n}* — 한 것을 체크해 주세요. 다 체크하면 요청하신 분께 확인을 부탁드려요"}},
            {"type": "actions", "block_id": f"dc:{c.get('card_ts') or '-'}", "elements": [el]}]


def rel_blocks(c):
    """🔗 관계 — 「무엇 때문에 못 하나」 와 「무엇을 막고 있나」 를 **둘 다** (#68).

    상태를 같이 적는다 — Jira 가 링크마다 「키·요약·상태」 를 보여 주는 것과 같은 이유로,
    「#52 가 진행 중인가 아직 할 일인가」 가 곧 풀리는지를 알려 주는 유일한 정보다 (2026-09-20 조사).

    말은 상태의 ⛔ 막힘(사람이 「나 막혔어요」 로 바꾼 것)과 갈라 놓는다 — 🔒 는 선행이 안 끝난 것이다.
    """
    waiting, blocking, done_before = core.relations(c, STATE["cards"])
    if not (waiting or blocking or done_before):
        return []
    # **한 줄에 하나씩 세로로.** 옆으로 이으면 가운뎃점이 「항목 사이」 와 「제목·상태 사이」 에
    # 같이 쓰여서 어디가 끊기는지 안 보인다 (2026-09-20 사장님 지적)
    one = lambda x: f"    {link_of(x, True, ref(x['no'], 26, link=False))} · {SICON[x['status']]} {LABEL[x['status']]}"
    rows = []
    if waiting:
        rows.append("🔒 *이게 끝나야 시작해요*\n" + "\n".join(one(x) for x in waiting))
    if blocking:
        rows.append("🚧 *이 일이 막고 있어요*\n" + "\n".join(one(x) for x in blocking[:5])
                    + (f"\n    …외 {len(blocking) - 5}건" if len(blocking) > 5 else ""))
    if done_before:
        rows.append("✅ *끝난 선행*  " + ", ".join(ref(x["no"], 20, link=False) for x in done_before))
    return [{"type": "header", "text": {"type": "plain_text", "text": "🔗 관계", "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(rows)[:2900]}}]


def card_detail_blocks(c):
    """📄 상세 팝업 — 카드에서 뺀 정보를 전부 여기에. 누를 때마다 최신."""
    team = load_team()
    nm = lambda u: team.get(u, {}).get("name", "미정")
    f = c.get("feature")
    ctx = lambda s_: {"type": "context", "elements": [{"type": "mrkdwn", "text": s_[:2900]}]}
    sec = lambda s_: {"type": "section", "text": {"type": "mrkdwn", "text": s_[:2900]}}
    head = lambda s_: {"type": "header", "text": {"type": "plain_text", "text": s_[:150], "emoji": True}}
    b = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{tag(c['no'])} {c['title']}*"},
          "accessory": {"type": "button", "text": {"type": "plain_text", "text": "✏️ 내용 수정"},
                        "action_id": "edit_content", "value": c.get("card_ts") or "-"}},
         # 분류는 작은 글씨 두 줄로 — 칸을 쓰지 않는다. 「할 일·성공 기준」 은 뺐다 (사장님 결정 9/20):
         # 성공 기준은 할 일 전체의 지표라 이 이슈 것이 아니고, 아래 「기대와 확인」·「체크리스트」 와
         # 같은 물음에 답해서 세 번 읽힌다. 할 일 단위 지표는 캔버스 맨 위 표가 이미 보여 준다
         ctx(f"{PLEVEL[plevel(c)]} {PNAME[plevel(c)]} · {LABEL[c['status']]} · 담당 {nm(c.get('assignee'))}"
             + (f" · {due_text(c)}" if due_text(c) else "")
             + (f" (추천 {nm(c.get('suggested'))})" if not c.get("assignee") and c.get("suggested") else "")
             + f" · 올린 사람 {c.get('by', '-')}"
             + f"\n🗺️ {c.get('stage') or '나중 (단계 밖)'} · 🧩 {f or '기능 미정'}")]
    b += rel_blocks(c)
    b.append(head("📝 할 일 정의"))
    spec = c.get("spec")
    if spec:
        lines = [f"*{label}*\n{readable(spec.get(key) or '-')}" for key, label in SPEC_KEYS]
        b.append(sec("\n\n".join(lines)))
        b += checklist_blocks(c)
    else:
        b.append({"type": "section",
                  "text": {"type": "mrkdwn", "text": f"아직 정리 전이에요. 스레드에서 이야기하고 `@{HANDLE} 정리` "
                                                     "라고 하시거나, 오른쪽 단추를 눌러 주시면 채워 볼게요."},
                  "accessory": {"type": "button", "text": {"type": "plain_text", "text": "✨ 정리해 줘"},
                                "action_id": "fill_spec", "value": c.get("card_ts") or "-"}})
    b.append(head("📆 날짜별 기록"))
    rows = [f"{mday(d)}  {e.get('icon', '•')} {e['what']} · {e['who']}" + (f" ({e['reason']})" if e.get("reason") else "")
            for d, es in timeline([c]).items() for _, e in es][:25]
    if c["status"] == "done":
        rows.insert(0, f"결과: {next((e['what'] for e in reversed(c.get('edits') or []) if e.get('icon') == '✅'), '완료')}")
    elif c.get("due"):
        rows.insert(0, f"계획: 목표일 {mday(c['due'])}" + (f" · 체크리스트 {progress(c)[0]}/{progress(c)[1]}" if progress(c)[1] else ""))
    b.append(sec("\n".join(rows)[:2900]))
    b.append(head("🧮 순서와 담당 — 왜 이렇게 됐나"))
    why = [f"{'💡 AI' if c.get('prio_src') == 'ai' else '✋ 사람'}: {c.get('prio_reason') or '-'}"
           + (f" · {c.get('rank', '-')}위 · 점수 {c['score']}" if c.get("score") is not None else "")]
    if c.get("assign_reason"):
        why.append(f"담당 추천 이유: {c['assign_reason']}")
    if c.get("hours"):
        why.append(f"⏱ {c['hours']['min']}~{c['hours']['max']}시간 · {AI_LABEL.get(c.get('ai'), '')} · 사람 시간 약 {need_hours(c)}시간")
    if c.get("design"):
        why.append("🎨 디자인 검수 필요")
    if c.get("duplicate_of"):
        why.append(f"중복 의심: {ref(c['duplicate_of'])}")
    if c.get("cancel_reason"):
        why.append(f"취소 사유: {c['cancel_reason']}")
    b.append(sec("\n".join(why)))
    if c.get("tracker"):
        b.append(ctx(f"정본 `issues/{c.get('file', '')}` · <{c.get('issue_url', '')}|GitHub {c['tracker']}>"))
    return b


def link_of(c, slack=False, text=None):
    """카드로 뛰는 링크. 캔버스는 마크다운, 팝업·홈은 Slack 표기.

    **취소선은 링크 밖에 붙인다.** 안쪽에 넣으면 Slack 은 물결을 글자로 찍고 캔버스는 링크가 깨져서,
    끝난 이슈를 눌러도 아무 데도 안 간다 (2026-09-20 사용자 지적 — 「완료하면 링크가 안 먹는다」).
    """
    t = text or f"#{c['no']} {c['title']}"
    if t.startswith("<http") or t.startswith("["):      # 이미 링크다 — 두 번 감싸면 주소가 글자로 튀어나온다
        return t                                        # (2026-09-20 현황의 「누가 뭘」 칸에 http 가 그대로 보였다)
    link = (f"<{c['permalink']}|{t}>" if slack else f"[{t}]({c['permalink']})") if c.get("permalink") else t
    if c["status"] in ("done", "cancelled"):                  # 끝난 일은 어디서나 취소선 — 팝업·홈·캔버스
        return f"~{link}~" if slack else f"~~{link}~~"
    return link


def md_for_slack(path):
    """md 를 Slack 표기로 바꾼다 — 제목은 굵게, 체크박스는 네모."""
    body = path.read_text(encoding="utf-8").split("---", 2)[-1].strip()[:2800]
    body = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", body, flags=re.M)
    body = re.sub(r"^- \[ \]\s*", "☐ ", body, flags=re.M)
    return re.sub(r"^- \[x\]\s*", "☑ ", body, flags=re.M)


def decision_value(k, v, team):
    if k == "assignee":
        return team.get(v, {}).get("name", "없음") if v else "없음"
    if k == "plevel":
        return PLEVEL[v].split()[1]
    if k == "due":
        return f"{int(v[5:7])}/{int(v[8:])}" if v else "없음"
    return v or "단계 밖"


def review_blocks(c, text):
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ 확인했어요"}, "style": "primary",
                 "action_id": "review_ok", "value": c["card_ts"]},
                {"type": "button", "text": {"type": "plain_text", "text": "↩️ 더 필요해요"},
                 "action_id": "review_back", "value": c["card_ts"]}]}]



def review_back_view(c):
    """[↩️ 더 필요해요] 창 — 무엇이 더 필요한지 한 줄 (비워도 된다)."""
    return {"type": "modal", "callback_id": "review_back_submit", "private_metadata": c["card_ts"],
            "title": {"type": "plain_text", "text": "더 필요해요"}, "submit": {"type": "plain_text", "text": "보내기"},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": [{"type": "input", "block_id": "why", "optional": True,
                        "label": {"type": "plain_text", "text": f"#{c['no']} 에 무엇이 더 필요한가요?"},
                        "element": {"type": "plain_text_input", "action_id": "v", "multiline": True}}]}