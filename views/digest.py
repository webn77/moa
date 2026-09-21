"""/상태 현황 · 일정 위험 · 추천 (#30 에서 bot.py 를 나눔)."""
import datetime
from common import LABEL, PLEVEL, WEEK, mday, plevel  # noqa: E402,F401
from docs import current_stage, deadline, load_hours, load_stages, load_team, need_hours  # noqa: E402,F401
from store import STATE, day_meta, open_cards, ref, timeline  # noqa: E402,F401


def risks():
    """병목·위험 신호 — 지금 데이터로만 센다. [(짧은 한 줄, 자세한 줄들)] 걸린 것만."""
    team, hours = load_team(), load_hours()
    nm = lambda u: team.get(u, {}).get("name", "미정")
    today = datetime.date.today()
    oc = open_cards()
    out = []
    # 1 단계 넘침 — 지금 단계 남은 일의 사람 시간 vs 단계 마감까지 팀 가능 시간 (평일 기준)
    cur = current_stage()
    st = next((x for x in load_stages() if x["name"] == cur), None)
    if st and st["end"]:
        left = [c for c in oc if c.get("stage") == cur]
        need = round(sum(need_hours(c) for c in left), 1)
        days = sum(1 for i in range(1, (st["end"] - today).days + 1) if (today + datetime.timedelta(days=i)).weekday() < 5)
        have = round(sum(hours.get(t_["name"], 0) for t_ in team.values()) / 5 * days, 1)
        if need > have:
            names = [x["name"] for x in load_stages()]
            nxt = names[names.index(cur) + 1] if names.index(cur) + 1 < len(names) else None
            # 사람이 고정하지 않았고 진행 중이 아닌 일 — 우선순위 낮은 것부터, 시간이 맞을 때까지
            free = sorted([c for c in left if c["status"] == "todo" and c.get("plevel_src") != "human"
                           and c.get("assign_src") != "human"], key=lambda c: (-plevel(c), c.get("score") or 0))
            move, rest = [], need
            for c in free:
                if rest <= have:
                    break
                move.append(c); rest -= need_hours(c)
            fix = None
            if move and nxt:
                fix = (f"우선순위가 낮은 {len(move)}건을 {nxt} 단계로 옮기면 좋겠어요", f"{nxt}로 옮기기", "stage:" + ",".join(str(c["no"]) for c in move))
            out.append((f"{cur} 단계가 {st['end'].month}/{st['end'].day}에 끝나요 — 남은 일은 {round(need)}시간, 쓸 수 있는 시간은 {round(have)}시간이에요",
                        [f"평일 {days}일 남음"] + [f"옮길 후보: {link_of(c, True, ref(c['no'], 16, link=False))} ({PLEVEL[plevel(c)].split()[1]})" for c in move], fix))
    # 이하 일정만 — 「언제까지 해야 하는데 안 된 것」 (9/19 사용자: 위험·추천은 일정 관련만)
    iso = today.isoformat()
    md_ = lambda d: f"{int(d[5:7])}/{int(d[8:])}"
    workdays = lambda end: sum(1 for i in range(0, (end - today).days + 1) if (today + datetime.timedelta(days=i)).weekday() < 5)
    # 2 지남
    for c in sorted([c for c in oc if c.get("due") and c["due"] < iso], key=lambda c: c["due"]):
        late_days = (today - datetime.date.fromisoformat(c["due"])).days
        out.append((f"{ref(c['no'], 16)} — 목표일({md_(c['due'])})이 {late_days}일 지났어요", [],
                    (f"{nm(c.get('assignee'))} 님, 새 목표일을 정해 주시면 좋겠어요", "3일 미루기", f"delay:{c['no']}:3")))
    # 3 곧 마감인데 시작 전
    for c in sorted([c for c in oc if c.get("due") and iso <= c["due"] <= (today + datetime.timedelta(days=2)).isoformat()
                     and c["status"] == "todo" and c.get("assignee")], key=lambda c: c["due"]):
        out.append((f"{ref(c['no'], 16)} — {md_(c['due'])} 마감인데 아직 시작 전이에요", [],
                    (f"{nm(c['assignee'])} 님, 오늘 시작해 주시면 좋겠어요", "알림 보내기", f"start:{c['no']}")))
    # 4 못 맞출 것 같음 — 사람마다 목표일 순으로 쌓아서 가능 시간을 넘는 첫 일
    for uid, t_ in team.items():
        per_day = hours.get(t_["name"], 0) / 5
        mine = [c for c in oc if c.get("assignee") == uid and c.get("due") and c["due"] >= iso]
        hr = lambda x: f"{round(x, 1):g}" if x < 10 else f"{round(x)}"
        for d_ in sorted({c["due"] for c in mine}):                   # 마감일마다 — 그날까지 끝낼 일을 한꺼번에 본다
            upto = [c for c in mine if c["due"] <= d_]
            need = sum(need_hours(c) for c in upto)
            have = per_day * workdays(datetime.date.fromisoformat(d_))
            movable = [c for c in upto if c["status"] == "todo"]
            if need > have and movable:
                pick = max(movable, key=lambda x: (plevel(x), x["due"]))    # 그중 우선순위가 가장 낮은 일을 미룬다
                later = max(1, round((need - have) / per_day + 0.49)) if per_day else 3
                out.append((f"{t_['name']} 님 — {md_(d_)}까지 {hr(need)}시간이 필요한데, 쓸 수 있는 시간은 {hr(have)}시간이에요", [],
                            (f"{ref(pick['no'], 14)}(P{plevel(pick)})의 목표일을 {later}일 미루면 여유가 생겨요", f"{later}일 미루기", f"delay:{pick['no']}:{later}")))
                break
    # 단계 마감은 맨 뒤로 — 개별 일정이 먼저 보이게
    out.sort(key=lambda r: " 단계가 " in r[0])                  # 단계 마감은 맨 뒤
    return out


def digest():
    """현황 — 본문 3줄 + 스레드용 자세한 내용. @모아 현황 · 평일 아침 9시."""
    team = load_team()
    nm = lambda u: team.get(u, {}).get("name", "미정")
    today = datetime.date.today()
    oc = open_cards()
    cnt = {k: sum(c["status"] == k for c in oc) for k in ("todo", "doing", "blocked")}
    late = [c for c in oc if c.get("due") and c["due"] < today.isoformat()]
    urgent = sorted([c for c in oc if c.get("assignee")], key=lambda c: (plevel(c), c.get("due") or "9", c.get("rank", 99)))[:3]
    # 프로젝트 — 로드맵 단계에 속한 일(나중 제외)의 완료율 · 지금 단계 · 남은 날
    staged = [c for c in STATE["cards"].values() if c.get("stage") and c["status"] != "cancelled"]
    fin_all = sum(c["status"] == "done" for c in staged)
    pct = round(100 * fin_all / len(staged)) if staged else 0
    cur = current_stage()
    st = next((x for x in load_stages() if x["name"] == cur), None)
    cs = [c for c in staged if c.get("stage") == cur]
    end = deadline()
    # 사람마다 — 지금 하는 일(진행 중 먼저, 없으면 가장 급한 일) + 몇 건. 한 사람 한 줄
    who = []
    for uid in team:
        mine = sorted([c for c in oc if c.get("assignee") == uid], key=lambda c: ({"doing": 0, "blocked": 1}.get(c["status"], 2), plevel(c)))
        doing = sum(c["status"] == "doing" for c in mine)
        if mine:
            top = mine[0]
            who.append(f"• *{nm(uid)}*  {link_of(top, True, ref(top['no'], 18, link=False))}"
                       + (f"  외 {len(mine) - 1}건" if len(mine) > 1 else "") + (f"  (진행 {doing})" if doing else ""))
        else:
            who.append(f"• *{nm(uid)}*  맡은 일 없음")
    unassigned = sum(1 for c in oc if not c.get("assignee"))
    field = lambda k, v: {"type": "mrkdwn", "text": f"*{k}*\n{v}"}
    fields = [field("프로젝트", f"{pct}% 끝남 · 남은 일 {len(staged) - fin_all}건"),
              field("지금 단계", f"{cur} {sum(c['status'] == 'done' for c in cs)}/{len(cs)}"
                    + (f" · D-{(st['end'] - today).days}" if st and st["end"] else "")),
              field("판단일", f"{end.month}/{end.day} · D-{(end - today).days}" if end else "미정"),
              field("일", f"진행 {cnt['doing']} · 대기 {cnt['todo']} · 담당 없음 {unassigned}")]
    if late or cnt["blocked"]:
        fields.append(field("⚠️ 챙길 것", f"목표일 지남 {len(late)} · 보류 {cnt['blocked']}"))
    rk = risks()
    main = [{"type": "header", "text": {"type": "plain_text", "text": f"📋 {today.month}/{today.day}({WEEK[today.weekday()]}) 현황", "emoji": True}},
            {"type": "section", "fields": fields},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*누가 뭘*\n" + "\n".join(who)}}] + (
           [{"type": "section", "text": {"type": "mrkdwn", "text": "*⚠️ 일정 위험 → 추천*\n" + "\n".join(
               f"• {r[0]}  → {r[2][0]}" if r[2] else f"• {r[0]}" for r in rk[:3])}}]
           if rk else []) + [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "먼저 볼 것 · 사람별 전체 목록은 스레드에"}]}]
    # 스레드 — 이모지 없이 글자로. 우선순위는 P1, 상태는 대기가 아닐 때만, 날짜는 숫자만
    def row(c, who_=False):
        st_ = "" if c["status"] == "todo" else f" · {LABEL[c['status']]}"
        due_ = f" · {int(c['due'][5:7])}/{int(c['due'][8:])}{' 지남' if c['due'] < today.isoformat() else ''}" if c.get("due") else ""
        return (f"P{plevel(c)}  {link_of(c, True, ref(c['no'], 22, link=False))}"
                + (f" — {nm(c.get('assignee'))}" if who_ else "") + st_ + due_)
    past = [(d, es) for d, es in timeline(STATE["cards"].values()).items() if d < datetime.date.today().isoformat()]
    lines = ([f"*지난 기록 {mday(past[0][0])}*  {day_meta(past[0][1])} — 날마다 보기는 앱 홈 📆 날짜별 진행", ""] if past else []) \
        + ([f"*🔍 확인 기다리는 일*  " + " · ".join(f"{ref(c['no'], 16)} ({team[c['review_by']]['name'] if c.get('review_by') in team else '요청자'} 확인)" for c in oc if c["status"] == "review"), ""]
           if any(c["status"] == "review" for c in oc) else []) \
        + ["*먼저 볼 것*"] + [f"{i}. {row(c, True)}" for i, c in enumerate(urgent, 1)] + [""]
    for uid in team:
        mine = sorted([c for c in oc if c.get("assignee") == uid], key=lambda c: ({"doing": 0, "blocked": 1}.get(c["status"], 2), plevel(c)))
        doing = sum(c["status"] == "doing" for c in mine)
        lines.append(f"*{nm(uid)}*  {len(mine)}건" + (f" (진행 {doing})" if doing else ""))
        lines += [f"• {row(c)}" for c in mine] or ["• 없음"]
        lines.append("")
    cur = current_stage()
    for st in load_stages():
        if st["name"] == cur:
            cs2 = [c for c in STATE["cards"].values() if c.get("stage") == cur and c["status"] != "cancelled"]
            fin = sum(c["status"] == "done" for c in cs2)
            lines.append(f"*지금 단계*  {cur} — {fin}/{len(cs2)} 끝남" + (f" · {st['end'].month}/{st['end'].day} 마감" if st["end"] else ""))
    return main, "\n".join(lines)


# 하루 묶음에 남길 것 — 기록이 남을 만한 것만.
#
# 🔄 는 **동기화가 아니라 결정**이다 (담당·우선순위·목표일·단계). 예전 주석이 「동기화」 라고
# 잘못 적혀 있어서 통째로 빠져 있었고, 그래서 **「그날 팀이 무엇을 정했나」 가 안 남았다** — 그게
# `PA-22` 가 풀려던 바로 그 문제다 (2026-09-21 발견).
#
# **사람이 정한 것만 올린다.** 30건 중 28건이 AI 자동 배정(「자리가 나서 우선순위 순으로」)이라
# 다 넣으면 사람 결정 2건이 그 속에 묻힌다. AI 가 한 것은 아래 「그 밖에」 줄에서 수만 센다.
# 체크(☑)도 수만 센다 — 근거(커밋)가 달려 있지만 그건 **진행**이지 **결정**이 아니고,
# 이슈별 기록에 그대로 있다. 하루가 200줄이 되면 아무도 안 읽는다.
DAILY = {"✅": "끝낸 일", "👀": "시작한 일", "🔄": "사람이 정한 것", "📝": "내용을 고친 것",
         "💤": "뒤로 보낸 것", "🔗": "합친 것", "🔒": "순서를 정한 것", "📌": "남긴 기록"}


def _mine(icon, e):
    """그날 묶음에 올릴 기록인가 — 🔄 는 사람이 정한 것만 (AI 자동 배정은 수만 센다).

    `by` 가 없는 옛 기록은 **올리지 않는다** — 누가 정했는지 모르는 것을 「사람이 정했다」 고
    단언하면 안 된다. 셈에는 들어가므로 사라지지는 않는다.
    """
    return e.get("icon") == icon and (icon != "🔄" or e.get("by") not in (None, "ai"))


def daily_md(day=None):
    """그날 있었던 일 — **새로 쌓지 않는다.** 카드마다 이미 쌓이는 `edits` 를 날짜로 모을 뿐이다 (#59).

    기록을 따로 만들면 사람이 기억해서 써야 하고, 바쁜 날 건너뛴다 (삼성 기술블로그 사례).
    그래서 하던 일 안에서 이미 쌓인 것만 본다. 그날 아무 일도 없었으면 `None` — 빈 파일을 남기지 않는다.
    """
    day = day or datetime.date.today().isoformat()
    rows = [(c, e) for c in STATE["cards"].values() for e in (c.get("edits") or []) if e.get("date") == day]
    if not rows:
        return None
    made = sorted(c["no"] for c, e in rows if e.get("icon") == "🎫")
    out = [f"# {day}", ""]
    for icon, label in DAILY.items():
        got = [(c, e) for c, e in rows if _mine(icon, e)]
        if not got:
            continue
        out.append(f"## {icon} {label} ({len(got)})")
        for c, e in sorted(got, key=lambda t: t[0]["no"]):
            # 「무엇을 어떻게 고쳤나」 는 카드 스레드에 있다 — 여기서는 왜 했는지만. 안 그러면 하루가 200줄이 된다
            tail = (e.get("reason") or e.get("what") or "").strip()
            out.append(f"- #{c['no']} {c['title'][:40]}" + (f" — {tail[:60]}" if tail else "") + f" ({e.get('who', '-')})")
        out.append("")
    if made:
        out += [f"## 🎫 새로 만든 할 일 ({len(made)})", ", ".join(f"#{n}" for n in made), ""]
    # 위에 올린 「사람이 정한 것」 은 여기서 두 번 세지 않는다
    quiet = sum(1 for _, e in rows if e.get("icon") == "☑"
                or (e.get("icon") == "🔄" and e.get("by") in (None, "ai")))
    if quiet:
        out.append(f"_그 밖에 완료 조건 체크·AI 자동 조정 {quiet}건_")
    return "\n".join(out).rstrip() + "\n"


def risk_blocks():
    """스레드용 — 위험마다 무엇이 문제인지 · 추천 · [적용] 버튼. 버튼을 누르는 게 사람의 승인이다."""
    blocks = []
    for head_, det, fix in risks():
        b = {"type": "section", "text": {"type": "mrkdwn", "text": (f"*{head_}*" + (f"\n→ {fix[0]}" if fix else ""))[:2900]}}
        if fix and fix[1]:
            b["accessory"] = {"type": "button", "text": {"type": "plain_text", "text": fix[1]}, "action_id": "risk_fix", "value": fix[2]}
        blocks.append(b)
    return blocks


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from views.card import link_of  # noqa: E402,F401
