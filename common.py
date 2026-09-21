"""바탕 — 채널 ID · 용어(상태·우선순위) · 로그 · 작은 도우미. 다른 우리 모듈을 모른다 (#30 에서 bot.py 를 나눔)."""
import re, logging, asyncio
import config
import core


HERE = config.DATA                                   # 팀 데이터 폴더 — 코드와 분리 (#50). 코드 폴더와 같아도 된다
CHANNEL, CANVAS = config.need("issue_channel"), config.need("canvas")   # 프로젝트 방 — 이슈 카드·작업판
# 팀 대화방 — 사람과 AI 가 이야기하는 곳. **없을 수 있다** (2026-09-21 사장님 지적):
# 채널 하나로 쓰는 팀도 있고, 프로젝트마다 따로 두지 않기도 한다. 없다고 봇이 안 뜨면 안 되므로
# 프로젝트 방으로 떨어뜨린다 — 예전에는 need() 라 설정에 없으면 **봇이 아예 안 떴다.**
REQUEST = config.CFG.get("request_channel") or CHANNEL
BOT = config.CFG.get("bot_name", "프로덕트 에이전트 - PA")     # 메시지에 보이는 이름
HANDLE = config.CFG.get("bot_handle") or "PA"                 # @ 로 부를 때 쓰는 이름 (앱 설정의 봇 이름과 같아야 한다)
ISSUE_NAME, REQUEST_NAME = config.CFG.get("issue_name", "프로젝트 방"), config.CFG.get("request_name", "팀 대화방")
# 번호 앞말 (#66) — `PA-40` 의 `PA`. **레포**(번호를 주는 단위)의 약칭이고 프로젝트가 아니다.
# 프로젝트는 앞말이 아니라 카드의 칸이다 — 그래야 한 프로젝트가 레포 둘로 갈라질 수 있다 (research/benchmark.md).
# 없으면 예전처럼 맨 `#40`. Jira 처럼 사람이 정한다 — 레포 이름에서 뽑으면 읽기 어려운 약칭이 나온다.
PROJECTS = config.projects()                 # 하나여도 목록이다 — 설정에 없으면 평평한 키로 하나 (#66)
PREFIX = PROJECTS[0].get("key") or ""        # 프로젝트를 못 고를 때 쓰는 앞말 (프로젝트가 하나면 늘 이것)
LABEL = {"todo": "대기", "doing": "진행 중", "blocked": "보류", "review": "확인 대기", "done": "완료", "cancelled": "취소"}
# 우선순위 — Jira·Linear 처럼 P1~P4 + 색. 사람이 정하면 그대로, 아니면 점수로 (priority.md)
PLEVEL = {1: "🔴 P1", 2: "🟠 P2", 3: "🟡 P3", 4: "⚪ P4"}
SICON = {"todo": "🕒", "doing": "👀", "blocked": "⛔", "review": "🔍", "done": "✅", "cancelled": "❌"}   # 상태 — 카드 반응(👀⛔✅)과 같은 뜻. 동그라미는 우선순위 몫
PNAME = {1: "긴급", 2: "높음", 3: "보통", 4: "낮음"}


plevel = core.plevel          # 계산은 core.py (#35)
BOARD = ["todo", "doing", "blocked", "review", "done"]        # 칸반에 세우는 열. 취소는 목록에만 남는다
REACT = {"eyes": "doing", "white_check_mark": "done", "no_entry": "blocked"}
REVIEW_ON = config.CFG.get("review", True)           # 완료 전에 요청자·PM 확인 (팀 설정으로 끌 수 있다)
SPEC_KEYS = [("why", "왜"), ("change", "바뀌는 것"), ("expect", "기대와 확인"), ("not_doing", "하지 않는 것")]

logging.basicConfig(filename=HERE / "bot.log", level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info


AI_FACTOR = {"ai": 0.3, "assist": 0.6, "human": 1.0}
AI_LABEL = {"ai": "🤖 AI가 대부분", "assist": "🤝 AI 보조", "human": "🧑 사람만"}


def fill(text):
    """안내 문서의 자리표시 — {request} 팀 대화방 이름 · {issues} 프로젝트 방 이름 · {bot} 봇 이름."""
    return text.replace("{request}", REQUEST_NAME).replace("{issues}", ISSUE_NAME).replace("{bot}", HANDLE)


MARK = {"todo": "", "doing": "👀", "blocked": "⛔", "done": "✅", "cancelled": "❌"}


WEEK = "월화수목금토일"


def short(t, n=40):
    t = re.sub(r"\s+", " ", t or "").strip()
    return (t[:n] + "…") if len(t) > n else (t or "(비어 있음)")


DECISION = [("assignee", "담당"), ("plevel", "우선순위"), ("due", "목표일"), ("stage", "단계")]


def mday(iso):
    return f"{int(iso[5:7])}/{int(iso[8:10])}" if iso else ""


QUEUE = 2          # 사람당 「다음」 대기 줄. 지금(team.md 최대 동시) + 다음 = 배정되는 최대치


NEXT_MIN = 2.0      # 이 점수 미만은 나중


score_of = core.score_of


async def guard(coro):
    try:
        await coro
    except Exception as e:
        log(f"처리 실패: {type(e).__name__}: {e}")


async def drain(skip=(), timeout=30):
    """끄기 전에 하던 일(버튼 처리 · 내보내기 · 캔버스)을 마칠 때까지 기다린다 — skip 은 끝나지 않는 반복 일 (#30)."""
    me = asyncio.current_task()
    left = [t for t in asyncio.all_tasks() if t is not me and t not in skip
            and "render_canvas" not in repr(t.get_coro())]          # 10분 기다리는 캔버스 쓰기는 켤 때 다시 한다
    if left:
        done, pending = await asyncio.wait(left, timeout=timeout)
        return len(done), len(pending)
    return 0, 0
