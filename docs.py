"""팀 문서 읽기 — project.md · team.md · roadmap.md (md_table · load_*) · 일정 계산 (#30 에서 bot.py 를 나눔)."""
import re, datetime
from common import AI_FACTOR, HERE  # noqa: E402,F401
from store import STATE  # noqa: E402,F401


def due_text(c):
    """📅 완료 목표일 — 넘기면 ⚠️."""
    if not c.get("due") or c["status"] in ("done", "cancelled"):
        return ""
    d = c["due"]
    late = d < datetime.date.today().isoformat()
    return f"{'⚠️ ' if late else '📅 '}{int(d[5:7])}/{int(d[8:])}{' 지남' if late else ''}{' 💡' if c.get('due_src') == 'ai' else ''}"


def load_team():
    """team.md 첫 표를 읽는다. {Slack ID: {name, role, areas, max}}"""
    p = HERE / "team.md"
    team = {}
    if not p.exists():
        return team
    for line in p.read_text(encoding="utf-8").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells) == 5 and re.fullmatch(r"U[A-Z0-9]+", cells[1]):
            team[cells[1]] = {"name": cells[0], "role": cells[2], "areas": cells[3],
                              "max": int(cells[4]) if cells[4].isdigit() else 3}
    return team


def load_hours():
    """team.md 「가용 시간」 표. {이름: 주당 시간}"""
    p = HERE / "team.md"
    out, on = {}, False
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        if line.startswith("## "):
            on = line.strip() == "## 가용 시간"
        elif on:
            cells = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and re.fullmatch(r"\d+(\.\d+)?", cells[1]):
                out[cells[0]] = float(cells[1])
    return out


def deadline():
    """project.md 의 판단일 (YYYY-MM-DD 판단)."""
    p = HERE / "project.md"
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*판단", p.read_text(encoding="utf-8")) if p.exists() else None
    return datetime.date.fromisoformat(m.group(1)) if m else None


def need_hours(c):
    """이 이슈에 사람이 쓸 시간 — 예상 시간 가운데 × AI 할인."""
    h = c.get("hours") or {}
    mid = (float(h.get("min", 2)) + float(h.get("max", 4))) / 2
    return round(mid * AI_FACTOR.get(c.get("ai"), 1.0), 1)


def schedule():
    """사람마다 판단일까지 필요 시간과 가능 시간. [(이름, 필요, 가능, 넘치면 미룰 후보)]"""
    team, hours, end = load_team(), load_hours(), deadline()
    if not end:
        return []
    weeks = max((end - datetime.date.today()).days, 0) / 7
    out = []
    for uid, t in team.items():
        mine = [c for c in STATE["cards"].values() if c.get("assignee") == uid
                and c["status"] not in ("done", "cancelled") and c.get("priority") in ("now", "next")]
        need = round(sum(need_hours(c) for c in mine), 1)
        have = round(hours.get(t["name"], 0) * weeks, 1)
        cut = sorted([c for c in mine if c.get("priority") == "next"], key=lambda c: -c.get("rank", 0))
        out.append((t["name"], need, have, cut[:2] if need > have else []))
    return out


def suggest_due(c):
    """완료 예정일 제안 — 사람 시간 ÷ 하루 가능 시간(주당 ÷ 5), 주말 건너뜀. 사람이 정하면 그걸 쓴다."""
    team, hours = load_team(), load_hours()
    per_day = max(hours.get(team.get(c.get("assignee"), {}).get("name"), 5) / 5, 0.5)
    days = max(1, -(-need_hours(c) // per_day))          # 올림
    d = datetime.date.fromisoformat(c.get("start") or datetime.date.today().isoformat())
    while days > 0:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d.isoformat()


BASE_KPI = "밑바탕 — 2배 목표에 직접 닿지 않아요"


def okr_of(c):
    """카드가 목표에 어떻게 이어지나 — 기능 → 할 일 → 성공 기준.

    **「할 일」 표에 없는 할 일은 재지 않는다** (2026-09-20 사장님 결정). project.md 의 할 일은
    Pain point 와 짝이라 「2배 목표에 직접 닿는 것」 넷뿐인데, 기능 표는 그 넷을 만들려면 있어야 하는
    「기반」 도 가리킨다 — 층이 다르다. 열린 이슈의 3분의 1이 여기 있으므로 `-` 로 두면
    화면이 아무 말도 안 하게 된다. 나중에 재기로 하면 할 일 표에 행을 넣으면 된다.
    """
    f = c.get("feature")
    ini = load_features().get(f)
    inis = load_initiatives()
    kpi = next((i["kpi"] for i in inis if i["name"] == ini), None)
    if kpi is None and ini and ini not in {i["name"] for i in inis}:
        kpi = BASE_KPI
    return f, ini, kpi


def md_table(path, section=None, cols=None):
    """md 파일의 표를 행 목록으로. section 을 주면 그 ## 제목 아래 표만.

    **제목은 앞부분만 맞으면 된다** (2026-09-22). 예전에는 글자가 똑같아야 했는데,
    `## 기능 (상위 이슈)` 를 `## 기능 (여러 할 일을 묶는 칸)` 으로 고치면서 **읽는 쪽 둘이
    안 따라왔다.** 그래서 `load_features()` 가 **어디서나 빈 값**이었다 —
    ⚙️ 설정의 「기능」 칸은 고를 것이 0개가 되고, Slack 은 그런 메시지를 통째로 막는다
    (`invalid_blocks`). 아무도 안 알아챘다: 실패가 로그 한 줄로만 남았다.
    제목 뒤 괄호는 사람이 읽으라고 붙이는 말이라 자주 바뀐다 — 거기에 매달리지 않는다.
    """
    p = HERE / path
    out, on = [], section is None
    head = (section or "").split(" (")[0].strip()
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        if re.match(r"#{2,3} ", line):                # ## · ### 제목에서 구역이 바뀐다
            on = section is None or line.strip() == section or line.strip().startswith(head + " ")
        elif on and line.startswith("|"):
            cells = [x.strip() for x in line.strip().strip("|").split("|")]
            if (cols is None or len(cells) == cols) and not cells[0].startswith("---"):
                out.append(cells)
    return out[1:]                                   # 머리줄 빼고


def project_info():
    """project.md 에서 목표 한 줄과 초안 여부."""
    p = HERE / "project.md"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    m = re.search(r"## 목표\s*\n+\*\*(.+?)\*\*", txt)
    draft = "초안" in txt.split("---")[1] if txt.startswith("---") else False
    return (m.group(1) if m else "목표 미정 — project.md 에 적어 주세요"), draft


def load_why():
    """project.md 「## 왜 (문제)」 의 목록 줄."""
    p = HERE / "project.md"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    sec = re.search(r"## (?:Pain point|왜)[^\n]*\n(.*?)(?:\n## |\Z)", txt, re.S)
    return [x[2:].strip() for x in (sec.group(1).splitlines() if sec else []) if x.startswith("- ")]


def load_stages():
    """roadmap.md 단계 표. [{name, end, what, gate}]"""
    out = []
    for name, end, state, what, gate in md_table("roadmap.md", cols=5):
        try:
            day = datetime.date.fromisoformat(end)
        except ValueError:
            day = None                               # 「미정」
        out.append({"name": name, "end": day, "state": state, "what": what, "gate": gate})
    return out


def current_stage():
    """「진행」 단계 — 새 이슈는 여기에 붙는다. 표시가 없으면 날짜로."""
    st = load_stages()
    today = datetime.date.today()
    return next((x["name"] for x in st if x["state"] == "진행"),
                next((x["name"] for x in st if x["end"] and x["end"] >= today), st[-1]["name"] if st else None))


def load_features():
    """project.md 「기능」 표. {기능: 할 일}"""
    return {r[0]: r[1] for r in md_table("project.md", "## 기능 (상위 이슈)", 3)}


def feature_state():
    """{기능: 있음|만드는 중|예정}"""
    return {r[0]: r[2] for r in md_table("project.md", "## 기능 (상위 이슈)", 3)}


def load_initiatives():
    """project.md 「할 일」 표. [{name, before, after, kpi, target}]"""
    out = []
    for name, better, kpi, target in md_table("project.md", "## 할 일", 4):
        before, _, after = better.partition("→")
        out.append({"name": name, "before": before.strip(), "after": (after or better).strip(), "kpi": kpi, "target": target})
    return out


def lead_times():
    """끝난 이슈의 요청 → 완료 일수. 완료 시각(at)이 없던 예전 카드는 완료 날짜(since)로."""
    out = []
    for c in STATE["cards"].values():
        if c["status"] != "done" or not c.get("card_ts"):
            continue
        made = float(c["card_ts"])
        end = c.get("at") or (datetime.datetime.fromisoformat(c["since"]).timestamp() + 86399 if c.get("since") else None)
        if end:
            out.append(round(max(0, end - made) / 86400, 1))
    return out


def metrics():
    """성공 기준의 지금 값. {성공 기준: (지금, 달성 여부)}"""
    cs = list(STATE["cards"].values())
    live = [c for c in cs if c["status"] != "cancelled"]
    spec = sum(1 for c in live if (c.get("spec") or {}).get("done_criteria"))
    ok = sum(c.get("ai_outcome") == "accepted" for c in cs)
    no = sum(c.get("ai_outcome") == "changed" for c in cs)
    handoff = sum(1 for c in cs if c["no"] == 25 and c["status"] == "done")
    pct = lambda a, b: round(100 * a / b) if b else 0
    today = datetime.date.today()
    stalled = sum(1 for c in cs if c["status"] in ("doing", "blocked") and c.get("since")
                  and (today - datetime.date.fromisoformat(c["since"])).days >= 3)
    lt = lead_times()
    med = sorted(lt)[len(lt) // 2] if lt else None
    return {"리드타임": (f"중앙값 {med}일 ({len(lt)}건)" if lt else "아직 없음", False),
            "PM 조율 시간": ("아직 없음 (매주 PM에게 물어요)", False),
            "3일 넘게 멈춘 일": (f"{stalled}건", stalled == 0),
            "완료 조건을 갖춘 이슈 비율": (f"{pct(spec, len(live))}% ({spec}/{len(live)})", pct(spec, len(live)) >= 80),
            "AI 배정 수락률": (f"{pct(ok, ok + no)}% ({ok}/{ok + no})" if ok + no else "아직 없음",
                           bool(ok + no) and pct(ok, ok + no) >= 70),
            "인수인계 성공 (#25)": (f"{handoff}건", handoff >= 1)}


def vacant_roles():
    """team.md 「빈 역할과 대행」 표. [(역할, 대행, 상태)]"""
    p = HERE / "team.md"
    out, on = [], False
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        if line.startswith("## "):
            on = line.strip() == "## 빈 역할과 대행"
        elif on and line.startswith("|"):
            cells = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0] != "빈 역할" and not cells[0].startswith("---"):
                out.append((cells[0], cells[2], cells[3]))
    return out
