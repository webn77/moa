"""배정·우선순위 계산 — Slack·GitHub·파일을 모르는 순수 로직 (#35).

bot.py 가 상태와 설정을 넘겨 부르고, tests/test_core.py 가 같은 함수를 시험한다.
규칙 설명은 priority.md.
"""


def score_of(c):
    """점수 = (가치 + 긴급 + 목표 적합 × 2) ÷ 노력. 입력은 AI 추정, 계산은 코드."""
    s = c.get("scores") or {}
    v, u, g, e = (float(s.get(k, 3)) for k in ("value", "urgency", "goal_fit", "effort"))
    return round((v + u + g * 2) / max(e, 1), 2)


def plevel(c):
    """우선순위 P1~P4. 사람이 정하면 그대로. P1 긴급은 사람만 붙인다 (AI는 P2~P4)."""
    if c.get("plevel_src") == "human" and c.get("plevel") in (1, 2, 3, 4):
        return c["plevel"]
    if c.get("priority") == "later" or c.get("duplicate_of"):
        return 4
    s = c.get("score")
    if s is None:
        return 3
    return 2 if s >= 7 else 3 if s >= 4 else 4


def spec_diff(old_title, old, new_title, new, labels):
    """요구사항이 어떻게 바뀌었나 — [(칸 이름, 전, 후)]. 안 바뀐 칸은 뺀다.
    labels = [(키, 이름)] — 제목·완료 조건은 따로 본다."""
    old, new = old or {}, new or {}
    out = []
    if (old_title or "") != (new_title or ""):
        out.append(("제목", old_title or "", new_title or ""))
    for k, label in labels:
        a, b = (old.get(k) or "").strip(), (new.get(k) or "").strip()
        if a != b:
            out.append((label, a, b))
    a, b = old.get("done_criteria") or [], new.get("done_criteria") or []
    if a != b:
        gone = [x for x in a if x not in b]
        added = [x for x in b if x not in a]
        out.append(("완료 조건", " / ".join(gone), " / ".join(added)) if gone or added
                   else ("완료 조건", "순서", "바뀜"))
    return out


def decision_diff(before, after, labels):
    """결정(담당·우선순위·목표일·단계)이 어떻게 바뀌었나 — [(키, 이름, 전, 후)]."""
    return [(k, label, before.get(k), after.get(k)) for k, label in labels if before.get(k) != after.get(k)]


def approvers(c, team):
    """완료를 확인할 사람 — 요청자(담당자 본인이 아니면) + PM 들. 담당자 혼자면 빈 집합."""
    pms = {u for u, t in team.items() if "PM" in (t.get("role") or "")}
    who = ({c["by_id"]} if c.get("by_id") else set()) | pms
    return who - {c.get("assignee")}


def confirmer(c, team):
    """태그해서 확인을 부탁할 한 사람 — 요청자가 먼저, 없으면 PM."""
    if c.get("by_id") and c["by_id"] != c.get("assignee"):
        return c["by_id"]
    return next((u for u, t in team.items() if "PM" in (t.get("role") or "") and u != c.get("assignee")), None)


def next_status(c, want, user, team, review=True):
    """바꾸려는 상태 → 실제로 갈 상태. 완료는 확인할 사람이 있으면 먼저 「확인 대기」 (Jira·Linear 의 In Review).
    확인할 사람(요청자·PM)이 직접 완료하면 바로 완료. 확인할 사람이 없으면(혼자 요청·담당) 바로 완료."""
    if want != "done" or not review or c["status"] == "done":
        return want
    if user in approvers(c, team) or not confirmer(c, team):
        return "done"
    return "review"


def place(cards, team, stage_names, queue=2, next_min=2.0, capcut=None, due_fn=None):
    """사람마다 적정량만 배정한다 — 진행(team 의 max) + 대기(queue). 나머지는 담당 없이.

    cards       {key: card}  — 제자리에서 고친다
    team        {uid: {"max": n, ...}}
    stage_names 로드맵 단계 이름들 (비어 있으면 단계 검사 안 함)
    capcut      {uid: n} 과부하로 줄인 개수 — 대기 칸부터, 모자라면 진행 칸
    due_fn      card → 'YYYY-MM-DD' 목표일 제안 (없으면 제안 안 함)

    사람이 맡은 것(✋·👍·👀)은 담당을 빼지 않는다. AI 배정(💡)은 자리가 없으면 풀린다.
    """
    capcut = capcut or {}
    open_ = [c for c in cards.values() if c["status"] not in ("done", "cancelled")]
    finished = {c["no"] for c in cards.values() if c["status"] in ("done", "cancelled")}
    for c in open_:
        c["score"] = score_of(c)
    ranked = sorted(open_, key=lambda c: (plevel(c), -c["score"]))     # 사람이 정한 P가 먼저, 같으면 점수
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
    now_slots = {u: t["max"] for u, t in team.items()}
    next_slots = {u: max(queue - capcut.get(u, 0), 0) for u in team}
    now_slots = {u: max(n - max(capcut.get(u, 0) - queue, 0), 0) for u, n in now_slots.items()}

    def take(c, uid, bucket, why=None):
        c["assignee"], c["priority"] = uid, bucket
        if due_fn and not c.get("due") and c.get("hours"):           # 목표일이 없으면 예상 시간으로 제안
            c["due"], c["due_src"] = due_fn(c), "ai"
        if c.get("assign_src") != "human":
            c["assign_src"] = "ai"
        c["prio_reason"] = why or ""      # 사유 없이 배정되면 **옛 사유를 지운다** — 안 지우면 지난 이유가 계속 붙어 있다
        slots = now_slots if bucket == "now" else next_slots
        slots[uid] = slots.get(uid, 0) - 1

    def release(c, bucket, why):
        if c.get("assign_src") != "human":            # 사람이 맡은 건 빼지 않는다
            c["assignee"] = None
            c.pop("assign_src", None)
            if c.get("due_src") == "ai":              # 배정이 풀리면 AI 제안 날짜도 뺀다
                c.pop("due", None), c.pop("due_src", None)
        c["priority"], c["prio_reason"] = bucket, why

    fixed = set()
    for c in ranked:                                   # ① 진행 중·보류는 늘 진행 칸
        if c["status"] in ("doing", "blocked", "review") and c.get("assignee"):
            take(c, c["assignee"], "now")
            fixed.add(c["no"])
    for c in ranked:                                   # ② 사람이 맡은 것 — 사람이 정한 칸을 따른다
        if c["no"] in fixed or c.get("assign_src") != "human" or not c.get("assignee"):
            continue
        want = c.get("priority") if c.get("prio_src") == "human" else None
        u = c["assignee"]
        waiting_on = [x for x in c.get("after") or [] if x not in finished]
        why = "사람이 맡음"          # 선행은 「🔗 관계」 칸이 혼자 갖는다 — 여기 문자열로 박으면 두 곳에 같은 말이 뜬다
        if want in ("now", "next"):
            take(c, u, want, why)
        elif now_slots.get(u, 0) > 0:
            take(c, u, "now", why)
        else:
            take(c, u, "next", why)
        fixed.add(c["no"])
    for c in ranked:                                   # ③ 나머지 — 우선순위 순으로 추천 담당의 자리에
        if c["no"] in fixed:
            continue
        c["prio_src"] = "ai"
        waiting_on = [x for x in c.get("after") or [] if x not in finished]
        goal = float((c.get("scores") or {}).get("goal_fit", 3))
        if stage_names and c.get("stage") not in stage_names:     # 로드맵 단계에 없는 일은 나중
            release(c, "later", "로드맵 단계 밖")
            continue
        if c.get("duplicate_of") or c["score"] < next_min or goal <= 1:
            release(c, "later", "중복" if c.get("duplicate_of") else
                    "목표와 무관(목표 적합 1)" if goal <= 1 else "점수가 낮음")
            continue
        u = c.get("suggested") or c.get("assignee")
        if c.get("no_auto"):                                      # 과부하로 내려놓은 일
            release(c, "backlog", "과부하로 내려놓음 — 직접 가져가면 다시 배정")
            continue
        if not waiting_on and now_slots.get(u, 0) > 0:
            take(c, u, "now")
        elif next_slots.get(u, 0) > 0:
            take(c, u, "next", "먼저 끝나야 할 일이 있어요" if waiting_on else None)
        else:
            release(c, "backlog", "지금·다음 자리가 차서 백로그")


# ── 계획 — 「내가 언제 뭘 하면 되나」 (2026-09-22 사장님 지시) ──
#
# `due` 와 다른 것이다. **`due` 는 언제까지, `start` 는 언제 손을 대나**다.
# 목표일만 있으면 「이번 주에 여섯 개가 금요일」 이 되고, 그건 계획이 아니라 벽이다.
#
# 사장님: 「todo리스트 자동생성 하고 수정도 가능하게 해줘서 내가 언제 뭘 하면 되는지를 계획할 수 있게」
# 그래서 **묻지 않는다.** 언제나 자동으로 채워 두고, 사람은 이미 있는 계획을 옮기기만 한다
# (「사용자에게 많은 걸 요청 하지 말자고」).
def _workday(d):
    import datetime                             # 이 파일은 부를 때 불러온다 (아래 `due_urgency` 와 같다)
    while d.weekday() >= 5:                     # 토·일은 건너뛴다
        d += datetime.timedelta(days=1)
    return d


def plan_order(mine, finished=(), today=None):
    """계획에 놓을 순서 — **늦은 것** → 진행 중 → 우선순위 → 목표일 → 순위.
    **선행은 앞으로 당긴다.**

    목표일이 지났거나 오늘인 것이 맨 앞이다. 안 그러면 「9/21 이 목표일인데 계획은 9/24」
    같은 줄이 나온다 — 사람은 그걸 계획으로 안 읽는다 (2026-09-22 실측).

    내 목록 안에 선행이 있으면 그것을 먼저 놓는다 — 아니면 「#57 을 오늘, 그게 기다리는
    #52 를 목요일」 같은 계획이 나온다. 고리가 있어도 멈춘다 (`seen` · 깊이 제한).
    """
    rank = {"doing": 0, "blocked": 1}
    end = today.isoformat() if today else None
    late = lambda c: 0 if end and (c.get("due") or "9999-99-99") <= end else 1
    todo = sorted(mine, key=lambda c: (late(c), rank.get(c["status"], 2), plevel(c),
                                       c.get("due") or "9999-99-99", c.get("rank", 99)))
    here, out, seen = {c["no"]: c for c in todo}, [], set()

    def add(c, depth=0):
        if c["no"] in seen or depth > 10:
            return
        seen.add(c["no"])
        for a in c.get("after") or []:
            if a in here and a not in finished:
                add(here[a], depth + 1)
        out.append(c)
    for c in todo:
        add(c)
    return out


def plan(cards, per_day, today=None, hours_fn=None):
    """열린 내 일을 **날짜에 놓는다** — `start` 를 채운다. 제자리에서 고친다.

    per_day   {uid: 하루에 쓸 수 있는 시간}  (team.md 「가용 시간」 ÷ 5)
    hours_fn  card → 걸릴 시간 (없으면 하나에 한 시간으로 본다)

    **사람이 옮긴 날(`start_src="human"`)은 건드리지 않는다** — 이 저장소의 다른 칸과 같은
    약속이다 (`due_src` · `assign_src`). 사람이 잡아 둔 날도 그날의 자리를 먼저 차지한다.
    하루보다 큰 일도 **시작하는 날**만 잡는다 — 언제 끝나는지는 `due` 가 말한다.
    """
    import datetime
    today = today or datetime.date.today()
    hours_fn = hours_fn or (lambda c: 1.0)
    finished = {c["no"] for c in cards.values() if c["status"] in ("done", "cancelled")}
    open_ = [c for c in cards.values()
             if c["status"] not in ("done", "cancelled") and c.get("assignee")]
    for c in cards.values():                     # 끝난 일에 계획이 남아 있으면 지운다
        if c["status"] in ("done", "cancelled") or not c.get("assignee"):
            c.pop("start", None), c.pop("start_src", None)
    for uid in sorted({c["assignee"] for c in open_}):
        mine = [c for c in open_ if c["assignee"] == uid]
        cap = max(per_day.get(uid) or 1.0, 0.5)
        fixed = [c for c in mine if c.get("start_src") == "human" and c.get("start")]
        used = {}
        for c in fixed:
            used[c["start"]] = used.get(c["start"], 0) + min(hours_fn(c), cap)
        d = _workday(today)
        for c in plan_order([c for c in mine if c not in fixed], finished, today):
            need = min(max(hours_fn(c), 0.1), cap)
            # 자리가 모자라면 다음 일하는 날로. **빈 날은 아무리 큰 일이라도 받는다** —
            # 안 그러면 하루치보다 큰 일이 영영 놓일 자리를 못 찾는다
            while used.get(d.isoformat(), 0) and used.get(d.isoformat(), 0) + need > cap:
                d = _workday(d + datetime.timedelta(days=1))
            c["start"], c["start_src"] = d.isoformat(), "ai"
            used[d.isoformat()] = used.get(d.isoformat(), 0) + need
    return open_


# ── 선행 (#68) — 「무엇이 끝나야 이 일을 시작하나」 ──
def _by_no(cards):
    return {c.get("no"): c for c in (cards.values() if isinstance(cards, dict) else cards)}


def waits_on(no, cards):
    """#no 가 건너건너 기다리는 번호 전부. 고리가 있어도 멈춘다."""
    by, out = _by_no(cards), set()
    stack = list((by.get(no) or {}).get("after") or [])
    while stack:
        x = stack.pop()
        if x in out:
            continue
        out.add(x)
        stack += (by.get(x) or {}).get("after") or []
    return out


def relations(c, cards):
    """(대기, 막고 있음, 끝난 선행) — 그리는 쪽이 쓰는 한 곳.

    **끝난 선행은 「대기」 에서 뺀다.** `after` 값은 선행이 끝나도 그대로 남아서,
    그냥 그리면 이미 풀린 카드에 자물쇠가 붙는다. `place()` 는 이미 끝난 것을 빼고
    순서를 매기므로 화면도 같은 눈으로 봐야 어긋나지 않는다.

    Linear 도 같은 방식이다 — 막던 이슈가 끝나면 관계가 「관련」 으로 내려간다 (2026-09-20 조사).
    """
    by = _by_no(cards)
    fin = lambda x: (by.get(x) or {}).get("status") in ("done", "cancelled")
    mine = [x for x in (c.get("after") or []) if x in by]
    blocking = sorted((x for x in by.values()
                       if c.get("no") in (x.get("after") or []) and x.get("status") not in ("done", "cancelled")),
                      key=lambda x: x.get("no") or 0)
    return ([by[x] for x in mine if not fin(x)], blocking, [by[x] for x in mine if fin(x)])


def clean_after(raw, no, cards):
    """AI 가 준 선행 번호를 다듬는다 — 없는 번호 · 자기 자신 · 이미 끝난 일 · 고리를 뺀다.

    고리(A 가 B 를, B 가 A 를 기다림)를 넣으면 둘 다 영영 「지금」 칸에 못 온다. 넣기 전에 막는다.
    """
    by, out = _by_no(cards), []
    for x in raw or []:
        try:
            x = int(x)
        except (TypeError, ValueError):
            continue
        t = by.get(x)
        if x == no or x in out or not t or t.get("status") in ("done", "cancelled"):
            continue
        if no in waits_on(x, by):
            continue
        out.append(x)
    return out


# ── 검증 (#58) — 모두플로2 checks.py 의 규칙만 가져왔다. 상태 모델은 우리 것 그대로 ──
# 「모자란 것은 막지 않는다. 화면을 거짓말하게 만드는 것만 막는다」
# 「모르는 값은 통과가 아니라 걸린다」 — 확인 못 한 것은 통과로 세지 않는다
SPEC_KEYS = ("why", "change", "expect")          # 이것들이 다 비면 정의가 없는 것으로 본다


def check_card(c, cards=(), when="number", today=None, stage_names=()):
    """올려도 되는가 — (막는 것, 알려주는 것). 둘 다 사람이 읽는 한 줄씩.

    c           카드 하나
    cards       같은 판의 카드 전부 (번호 중복·선행 번호 확인용). 없으면 그 검사는 건너뛴다
    when        'number' 번호를 받을 때 · 'doing' 진행 중으로 갈 때 · 'done' 완료로 갈 때
    today       'YYYY-MM-DD' (늦음 계산용). 없으면 오늘
    stage_names 로드맵 단계 이름들. 비어 있으면 단계 검사 안 함

    막는 것이 하나라도 있으면 GitHub 에 올리지 않는다. 알려주는 것은 올리되 스레드에 남긴다.
    완료는 막지 않는다 — 팀이 쓰는 곳이라 막으면 일이 멈춘다. 확인하는 사람에게 빠진 것만 보여 준다.
    """
    block, warn = [], []
    spec = c.get("spec") or {}
    done = spec.get("done_criteria") or []
    checked = [x for x in (c.get("checked") or []) if x in done]

    if when == "number":
        if not (c.get("title") or "").strip():
            block.append("제목이 없어요")
        if not any((spec.get(k) or "").strip() for k in SPEC_KEYS):
            block.append("이슈 정의가 비어 있어요 — 왜 · 바뀌는 것 · 기대와 확인 중 하나는 있어야 해요")
        if not done:
            block.append("완료 조건이 없어요 — 끝났다고 볼 조건을 한두 줄 적어 주세요")
        # cards 는 이 카드를 포함한 판 전체다. 같은 번호가 둘 이상이면 화면이 어느 것을 가리키는지 모른다
        if sum(1 for x in cards if x.get("no") == c.get("no")) > 1:
            block.append(f"#{c.get('no')} 번을 다른 이슈가 쓰고 있어요")
        known = {x.get("no") for x in cards} | {c.get("no")}
        for a in c.get("after") or []:
            if a == c.get("no"):
                block.append("제 자신을 기다리고 있어요")
            elif cards and a not in known:
                block.append(f"#{a} 을 기다리는데 그런 이슈가 없어요")
            elif cards and c.get("no") in waits_on(a, cards):   # 고리 — 둘 다 영영 시작 못 한다
                block.append(f"#{a} 도 이 이슈를 기다리고 있어요 — 서로 기다리면 둘 다 시작할 수 없어요")
        if not c.get("due"):
            warn.append("목표일이 없어요")
        if stage_names and c.get("stage") not in stage_names:
            warn.append("로드맵 단계가 없어요")
        if not c.get("feature"):
            warn.append("기능이 없어요")

    elif when == "doing":
        if not done:
            warn.append("끝났다고 볼 조건을 한두 줄 알려 주세요")

    elif when == "done":
        if done and len(checked) < len(done):
            warn.append(f"완료 조건이 {len(checked)}/{len(done)} 만 체크돼 있어요")
        late = days_late(c, today)
        if late:
            warn.append(f"목표일보다 {late}일 늦었어요")

    return block, warn


def due_urgency(c, today=None):
    """급한 순서 — 0 목표일 지남 · 1 오늘·내일 · 2 그 밖(목표일 없음 포함).

    「다음 일」 추천이 우선순위(P)로만 줄을 세워서, P2 인데 내일 마감인 일이 P1 모레 마감보다
    뒤에 섰다 (2026-09-20 사용자 지적 — #40 9/21 이 #41 9/22 보다 뒤). 날짜를 먼저 본다.
    """
    import datetime
    try:
        left = (datetime.date.fromisoformat(c.get("due") or "")
                - (datetime.date.fromisoformat(today) if today else datetime.date.today())).days
    except ValueError:
        return 2                                   # 모르는 날짜는 급하다고 하지 않는다
    return 0 if left < 0 else 1 if left <= 1 else 2


def days_late(c, today=None):
    """목표일보다 며칠 늦었나. 목표일이 없거나 안 늦었으면 0. 날짜가 이상하면 0 (모르는 값은 안 센다)."""
    import datetime
    try:
        due = datetime.date.fromisoformat(c.get("due") or "")
        now = datetime.date.fromisoformat(today) if today else datetime.date.today()
    except ValueError:
        return 0
    return max((now - due).days, 0)


def reconcile(cards, issues):
    """카드와 GitHub 이슈를 맞대어 어긋난 것만 돌려준다 — [(번호, 무엇이 어긋났나)] (#59·#60).

    cards   [{no, title, status, tracker}]
    issues  {번호: {"state": "OPEN"|"CLOSED", "title": …}}

    동기화는 조용히 깨진다. 2026-09-20 에 시험이 진짜 이슈 #57·#58 을 「not planned」 로 닫았는데
    카드는 그대로 열려 있었고, 사람이 알 방법이 없었다. 그래서 매일 맞대어 본다.
    순수 계산이라 시험할 수 있다 — 실제로 GitHub 을 부르는 쪽은 flows/github.py.
    """
    SHUT = ("done", "cancelled")
    out = []
    for c in sorted(cards, key=lambda x: x["no"]):
        no, g = c["no"], issues.get(c["no"])
        if not c.get("tracker"):
            out.append((no, "GitHub 짝이 없어요"))
            continue
        if not g:
            out.append((no, "GitHub 에 그 번호가 없어요"))
            continue
        want = "CLOSED" if c["status"] in SHUT else "OPEN"
        if g["state"] != want:
            out.append((no, f"카드는 {'끝남' if want == 'CLOSED' else '열려 있음'} 인데 GitHub 은 "
                            f"{'닫힘' if g['state'] == 'CLOSED' else '열림'} 이에요"))
        elif g["title"] != f"#{no} {c['title']}":
            # 제목은 Slack 이 주인이라 30초마다 맞춰진다. 하루가 지나도 다르면 GitHub 에서 고친 것이다
            out.append((no, "제목이 GitHub 에서 달라요 — 여기서 고친 건 다음 갱신 때 덮어써져요"))
    mine = {c["no"] for c in cards}
    for no, g in sorted(issues.items()):
        if no not in mine and g["state"] == "OPEN" and "(빈 번호)" not in g["title"]:
            out.append((no, "GitHub 에만 있고 카드가 없어요"))
    return out


# ── 비슷한 이슈 찾기 (#57) ────────────────────────────────────────────────
# AI 로 판정하지 않는다. 낱말 겹침으로 **후보만** 보여 주고 사람이 정한다 —
# 빠르고 공짜고, 틀려도 사람이 본다. AI 는 「같은 일인가」 를 자신 있게 틀린다.
STOP = {"이슈", "하기", "되기", "있는", "없는", "위한", "대한", "그리고", "해서", "하는", "되는",
        "것을", "것이", "한다", "한번", "하고", "에서", "으로", "에게", "부터", "까지", "만들기"}


def words(text):
    """견줄 낱말만 남긴다 — 두 글자 미만·흔한 말·번호는 뺀다."""
    import re
    out = set()
    for w in re.split(r"[^\w가-힣]+", (text or "").lower()):
        w = w.strip("#")
        if len(w) >= 2 and not w.isdigit() and w not in STOP:
            out.add(w)
    return out


def similar(c, others, floor=0.34, top=3):
    """c 와 비슷한 이슈 — [(카드, 닮은 정도)] 높은 순. 없으면 빈 목록.

    닮은 정도 = 겹친 낱말 ÷ 적은 쪽 낱말 수. 짧은 제목이 긴 제목에 묻히지 않게 적은 쪽으로 나눈다
    (「로그인 오류」 와 「로그인 오류가 가끔 나요 재현 조건 정리」 는 같은 일일 수 있다).
    floor 는 넘겨짚지 않을 만큼만 — 낮추면 엉뚱한 것까지 물어보게 되고, 그러면 사람이 안 본다.
    """
    def bag(x):
        sp = x.get("spec") or {}
        return words(f"{x.get('title', '')} {sp.get('why', '')} {sp.get('change', '')}")
    mine = bag(c)
    if len(mine) < 2:
        return []
    out = []
    for o in others:
        if o.get("no") == c.get("no") or o.get("status") in ("done", "cancelled"):
            continue
        his = bag(o)
        if len(his) < 2:
            continue
        hit = len(mine & his) / min(len(mine), len(his))
        if hit >= floor:
            out.append((o, round(hit, 2)))
    return sorted(out, key=lambda x: -x[1])[:top]


def tidy_candidates(cards, stage_names=(), today=None, stale=14):
    """정리해 볼 만한 이슈 — [(카드, [왜 골랐나])]. 이유가 많은 것부터 (#57).

    **고르기만 한다. 지우지 않는다.** 왜 골랐는지를 함께 돌려주는 게 핵심이다 —
    이유 없이 「정리하세요」 하면 사람이 판단할 수가 없다.
    끝난 일·사람이 맡은 일은 후보로 보지 않는다 — 맡은 사람이 있으면 그건 하기로 한 일이다.
    """
    import datetime
    now = datetime.date.fromisoformat(today) if today else datetime.date.today()
    open_ = [c for c in cards if c.get("status") not in ("done", "cancelled")]
    out = []
    for c in open_:
        if c.get("assign_src") == "human" and c.get("status") in ("doing", "blocked", "review"):
            continue                                   # 사람이 맡고 굴러가는 일은 건드리지 않는다
        # **사람이 이미 정한 것은 다시 묻지 않는다** — 묻고 또 물으면 아무도 안 보게 된다.
        # 2026-09-20: [계속 할 일]·[나중에] 를 눌러도 다음 정리에 또 나왔다 (표시만 남기고 읽지 않았다)
        if c.get("tidy_keep"):
            continue
        if c.get("priority") == "later":                # 이미 뒤로 보낸 일 — 사람이든 AI 든 한 번 내려놓았다
            continue
        why = []
        if c.get("duplicate_of"):
            why.append(f"#{c['duplicate_of']} 과 겹쳐 보여요")
        elif similar(c, open_):
            why.append("비슷한 이슈가 있어요 — " + " · ".join(f"#{o['no']}" for o, _ in similar(c, open_)))
        if stage_names and c.get("stage") not in stage_names:
            why.append("로드맵 단계 밖이에요")
        if float((c.get("scores") or {}).get("goal_fit", 3)) <= 1:
            why.append("목표와 이어지지 않아요")
        try:
            days = (now - datetime.date.fromisoformat(c.get("since") or "")).days
        except ValueError:
            days = 0
        if days >= stale and not c.get("assignee"):
            why.append(f"{days}일째 담당 없이 멈춰 있어요")
        if (c.get("spec") or {}).get("done_criteria") and vague(c) >= 3:
            why.append(f"채워 봤지만 5칸 중 {vague(c)}칸이 미정이에요 — 뭔지 알 수 없어요")
        if why and not (c.get("spec") or {}).get("done_criteria"):
            # 내용 없음은 **혼자서는 취소 사유가 아니다.** 붙여서 보여 주되, 이것만으로 후보가 되지는 않는다
            why.append("무슨 일인지도 적혀 있지 않아요")
        if why:
            out.append((c, why))
    return sorted(out, key=lambda x: (-len(x[1]), x[0]["no"]))


def vague(c):
    """정의 5칸 중 「미정」 이거나 빈 칸이 몇 개인가 (#57).

    AI 가 채워도 미정투성이면 **애초에 기록이 없다는 뜻**이다 — 「채워졌다」 고 넘어가면
    판단할 수 없는 이슈가 판단된 것처럼 남는다 (2026-09-20: 19건을 채웠더니 절반이 미정이었다).
    """
    sp = c.get("spec") or {}
    if not sp:
        return 0
    bad = 0
    for k in ("why", "change", "expect", "not_doing"):
        v = (sp.get(k) or "").strip()
        if not v or "미정" in v:
            bad += 1
    dc = sp.get("done_criteria") or []
    if not dc or all("미정" in x for x in dc):
        bad += 1
    return bad


def needs_spec(cards):
    """내용이 없는 열린 이슈 — 취소 후보가 아니라 **채울 대상**이다 (#57).

    이유가 「내용 없음」 하나뿐인 것을 정리 목록에 섞으면, 목록이 길어지기만 하고
    정작 정리할 것이 묻힌다 (2026-09-20: 17건 중 13건이 그랬다). 따로 묶어 한 번에 채운다.
    """
    return [c for c in cards
            if c.get("status") not in ("done", "cancelled")
            and not (c.get("spec") or {}).get("done_criteria")]
