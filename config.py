"""팀마다 다른 값 (#50) — 코드는 같고, 데이터 폴더와 그 안의 config.json 만 팀마다 다르다.

  MOA_DATA       데이터 폴더 (없으면 코드 폴더). project.md · team.md · cards.json · issues/ … 가 여기 있다
  config.json    Slack 채널·캔버스 ID, 봇 이름, GitHub 레포(없어도 됨). setup.py 가 만든다
  토큰           config.json 의 env_file (기본 ~/.config/moa.env). 레포에 두지 않는다

「모아」 는 이 봇의 이름이다 (2026-09-21 사장님이 정함). 예전 이름 `sandbox` 는 「모래놀이터 =
마음껏 부숴도 되는 시험판」 이라는 개발자 말인데, 이제 시험판이 아니고 받는 사람이 알아들을 수도 없다.
"""
import json, os, pathlib

CODE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get("MOA_DATA") or CODE).expanduser().resolve()
PATH = DATA / "config.json"
CFG = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
ENV_FILE = pathlib.Path(CFG.get("env_file") or "~/.config/moa.env").expanduser()
GITHUB = CFG.get("github") or {}          # {"repo": "owner/name", "project_owner": "...", "project_number": "1"}


def projects():
    """프로젝트 목록 — **하나여도 같은 모양**이다 (#66).

    설정에 `projects` 가 없으면 지금까지 쓰던 평평한 키로 하나를 지어낸다 — 이미 설치한 팀과
    새로 설치하는 팀(`setup.py` 는 평평한 키를 쓴다)이 둘 다 그대로 돈다.

      {"key": "PA", "name": "…", "channel": "C…", "canvas": "F…", "request": "C…", "repo": "owner/name"}

    `key` 는 번호 앞말이고 **레포**(번호를 주는 단위)를 가리킨다 — 프로젝트 이름이 아니다.
    """
    out = CFG.get("projects")
    if out:
        return [dict(p) for p in out]
    return [{"key": (CFG.get("prefix") or "").strip(), "name": CFG.get("issue_name") or "프로젝트",
             "channel": CFG.get("issue_channel"), "canvas": CFG.get("canvas"),
             "request": CFG.get("request_channel"), "repo": GITHUB.get("repo")}]


def need(key):
    if key not in CFG:
        raise SystemExit(f"{PATH} 에 {key} 가 없어요 — 먼저 `python3 setup.py` 로 설치해 주세요 (docs/install.md)")
    return CFG[key]
