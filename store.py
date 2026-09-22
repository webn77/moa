"""상태 저장 — STATE · save · 카드 찾기 · 날짜별 기록(add_log · timeline) (#30 에서 bot.py 를 나눔)."""
import time, json, os, re, datetime
from common import HERE, PROJECTS, mday, plevel  # noqa: E402,F401


CARDS = HERE / "cards.json"
STATE = json.loads(CARDS.read_text()) if CARDS.exists() else {"next": 4, "cards": {}}


def save():
    """임시 파일에 다 쓴 뒤 바꿔 끼운다 — 쓰는 도중에 꺼져도 cards.json 이 반쪽이 되지 않는다 (#30)."""
    tmp = CARDS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=1))
    os.replace(tmp, CARDS)


def progress(c):
    """체크리스트 몇 개 중 몇 개 체크했나 — (k, n). 조건 문구가 바뀌면 없어진 체크는 세지 않는다."""
    dc = (c.get("spec") or {}).get("done_criteria") or []
    return sum(1 for x in dc if x in (c.get("checked") or [])), len(dc)


def bar(k, n, width=10):
    """▓░ 막대 — 캔버스가 쓰던 모양 그대로 (2026-09-20 사장님 요청: 「시각화가 없다」).

    Slack 에는 그림도 접기도 없어서 글자로 그리는 수밖에 없다. 화면마다 다르게 그리면
    같은 것이 달라 보이므로 한 곳에서 만든다.
    """
    if not n:
        return "░" * width
    filled = round(width * k / n)
    return "▓" * filled + "░" * (width - filled)


def open_cards():
    return [c for c in STATE["cards"].values() if c["status"] not in ("done", "cancelled")]


def created_day(c):
    try:
        return datetime.date.fromtimestamp(float(c.get("card_ts") or 0)).isoformat()
    except ValueError:
        return None


def day_meta(es):
    """하루 요약 — 완료 N (계획대로 · 늦음) · 시작 · 변경 · 새 이슈. 0 인 칸은 뺀다."""
    done = [e for _, e in es if e.get("icon") == "✅"]
    late = sum("늦게" in e["what"] for e in done)
    return " · ".join(x for x in [
        f"완료 {len(done)}" + (" (" + " · ".join(y for y in [f"계획대로 {len(done) - late}", f"늦음 {late}"]
                                                   if not y.endswith(" 0")) + ")" if done else ""),
        f"시작 {sum(e.get('icon') == '👀' for _, e in es)}",
        f"변경 {sum(e.get('kind') in ('decision', 'spec') for _, e in es)}",
        f"새 이슈 {sum(e.get('what') == '만듦' for _, e in es)}"] if not x.endswith(" 0")) or "움직임 없음"


def timeline(cards):
    """날짜 → [(카드, 기록)] — 만든 날 + 기록된 일(시작·완료·보류·결정·요구사항·회의). 최근 날짜가 위."""
    days = {}
    for c in cards:
        if created_day(c):
            days.setdefault(created_day(c), []).append((c, {"icon": "🎫", "what": "만듦", "who": c.get("by", ""),
                                                             "at": float(c["card_ts"])}))
        for e in c.get("edits") or []:
            if e.get("what") and e.get("date"):
                days.setdefault(e["date"], []).append((c, e))
    return {d: sorted(es, key=lambda x: -x[1].get("at", 0)) for d, es in sorted(days.items(), reverse=True)}


def decision_snap():
    """결정 값 스냅샷 — 바뀐 뒤와 비교해 이력을 남긴다."""
    return {c["no"]: {"assignee": c.get("assignee"), "plevel": plevel(c), "due": c.get("due"), "stage": c.get("stage")}
            for c in STATE["cards"].values() if c["status"] not in ("done", "cancelled")}


def add_log(c, what, who, reason=None, kind="event", icon="•", **kw):
    """이슈의 날짜별 기록 한 줄 — 무엇이 · 누가/무엇에 의해 · 왜. 스레드 댓글·상세·정본 md·날짜별 진행이 같이 쓴다."""
    e = {"date": datetime.date.today().isoformat(), "at": time.time(), "kind": kind, "icon": icon,
         "what": what, "who": who, "reason": reason, **kw}
    c.setdefault("edits", []).append(e)
    return e


def log_line(e):
    return f"{e.get('icon', '•')} {mday(e['date'])} {e['what']} · {e['who']}" + (f" ({e['reason']})" if e.get("reason") else "")


def plan_result(c):
    """계획대로 끝났나 — 완료일과 목표일 비교, 목표일을 몇 번 바꿨나."""
    moved = sum("목표일" in (e.get("fields") or []) for e in c.get("edits") or [] if e.get("kind") == "decision")
    tail = f" · 목표일 {moved}번 바뀜" if moved else ""
    if not c.get("due"):
        return "목표일 없이" + tail
    end = datetime.date.fromisoformat(c["finished"]) if c.get("finished") else datetime.date.today()
    gap = (end - datetime.date.fromisoformat(c["due"])).days
    return (f"목표일 {mday(c['due'])}보다 {gap}일 늦게" if gap > 0 else
            f"계획대로 (목표일 {mday(c['due'])}" + (f", {-gap}일 일찍)" if gap < 0 else ")")) + tail


def ann_snap(c):
    return {"status": c["status"], "assignee": c.get("assignee"), "assign_src": c.get("assign_src"), "plevel": plevel(c)}


def card_of(no):
    return next((x for x in STATE["cards"].values() if x["no"] == int(no)), None)


def project_of(c):
    """카드가 어느 프로젝트인가 — 안 적혀 있으면 첫 프로젝트 (하나뿐이면 늘 그것) (#66).

    옛 카드에는 이 칸이 없다. 없는 것을 「모름」 으로 두면 화면마다 예외가 생기므로 기본값을 준다.
    """
    key = (c or {}).get("project")
    return next((p for p in PROJECTS if p.get("key") == key), PROJECTS[0])


def project_for(channel):
    """이 방은 어느 프로젝트인가 (#70) — 프로젝트 방이든 팀 대화방이든 본다.

    새 카드가 **어느 프로젝트로 갈지**를 정하는 자리다. 요청이 온 방을 보고 정한다 —
    사람에게 「어느 프로젝트예요?」 를 묻지 않는다. 모르는 방(DM·다른 채널)이면 첫 프로젝트.
    """
    return next((p for p in PROJECTS if channel and channel in (p.get("channel"), p.get("request"))), PROJECTS[0])


def chan(c=None):
    """그 카드가 사는 방 (#70) — 카드가 없거나 안 적혀 있으면 첫 프로젝트 방.

    예전에는 방이 모듈 로드 시점 상수여서 코드 88곳에 박혀 있었다. 그래서 프로젝트를 바꾸려면
    봇을 다시 띄워야 했고, 같은 앱으로 둘을 띄우면 둘 다 같은 앱 홈을 덮어썼다.

    **프로젝트가 하나면 예전 값과 같다** — 그래서 옮기는 동안 골든 화면이 한 글자도 안 바뀌어야 한다.
    """
    return project_of(c).get("channel") or PROJECTS[0].get("channel")


def req(c=None):
    """그 카드의 팀 대화방 — **없으면 그 프로젝트 방** (#52·#70).

    확인 요청·회의처럼 「사람이 같이 보는 자리」 로 가는 글이 여기로 간다. 팀 대화방을 안 둔 팀에서는
    그 프로젝트 방이 곧 팀 대화방이다 — 사라지게 두지 않는다.
    """
    p = project_of(c)
    return p.get("request") or p.get("channel") or PROJECTS[0].get("channel")


def canvas_of(c=None):
    """그 카드가 사는 작업판 (#70)."""
    return project_of(c).get("canvas") or PROJECTS[0].get("canvas")


def tag(no, c=None):
    """사람이 보는 번호 — 그 카드의 프로젝트 앞말을 붙여 `PA-40`, 앞말이 없으면 예전처럼 `#40` (#66).

    **보이는 표시일 뿐이다.** 저장·파일 이름·GitHub 이슈 제목은 번호만 쓴다 —
    앞말은 나중에 바뀔 수 있고, 키로 쓰면 이름을 바꿀 때 전부 깨진다 (research/benchmark.md 교훈 4).
    Slack 은 원래 여러 프로젝트가 섞이는 화면이라 맨 `#40` 을 그리지 않는다 (교훈 3).
    """
    key = project_of(c if c is not None else card_of(no)).get("key") or ""
    return f"{key}-{no}" if key else f"#{no}"


def ref(no, width=14, link=True):
    """번호만 쓰지 않는다 — 「#11 권한 기준…」 처럼 무엇인지 함께.

    Slack 메시지에서는 **눌러서 카드로 갈 수 있게** 링크를 단다 — 예전에는 글자만 찍혀서
    번호를 보고도 카드를 찾아 스크롤해야 했다 (2026-09-20 사용자 지적).
    캔버스는 마크다운이라 Slack 표기(<url|글자>)가 그대로 글자로 찍힌다 — 거기서는 link=False.
    """
    c = card_of(no)
    if not c:
        return tag(no)
    t = re.sub(r"\s*[(—].*$", "", c["title"]).strip() or c["title"]
    text = f"{tag(no, c)} {t[:width]}{'…' if len(t) > width else ''}"
    return f"<{c['permalink']}|{text}>" if link and c.get("permalink") else text
