"""시험은 **예시 데이터**(`example/`)를 보고 돈다 — 만든 사람 팀 데이터가 아니다 (2026-09-21).

예전에는 `MOA_DATA` 가 없으면 데이터 폴더가 **코드 폴더**가 되어, 시험이 만든 사람의 진짜
`config.json` · `cards.json` · `team.md` 를 읽고 돌았다. 그 맥에서는 잘 돌지만 **저장소를 받은
사람 맥에서는 `config.json` 이 없어 불러오기부터 터진다** — 처음 치는 명령이 파이썬 traceback 이었다.

여기서 `MOA_DATA` 를 미리 박아 두면 `unittest discover` 가 이 꾸러미를 먼저 불러오므로
어떤 시험 파일이 `common` 을 불러오든 이미 예시 데이터를 보게 된다.

`setdefault` 인 것은 일부러다 — 밖에서 `MOA_DATA` 를 주면 그쪽을 따른다 (골든이 그렇게 쓴다).
"""
import atexit
import os
import pathlib
import shutil
import tempfile

# **예시 데이터를 그대로 가리키면 안 된다.** 시험 중에 `store.save()` 가 한 번이라도 돌면
# `example/cards.json` 을 덮어쓴다 — 실제로 2026-09-22 에 51건이 5건으로 줄었고, 그걸 모르고
# 커밋했다. 골든이 그 파일을 기준으로 삼으므로 **기준 자체가 조용히 바뀌는** 최악의 고장이다.
# 그래서 **베껴서** 쓴다. 시험이 무엇을 쓰든 원본은 그대로다.
_SRC = pathlib.Path(__file__).resolve().parent.parent / "example"
if not os.environ.get("MOA_DATA"):
    _TMP = pathlib.Path(tempfile.mkdtemp(prefix="moa-test-"))
    shutil.copytree(_SRC, _TMP / "example")
    os.environ["MOA_DATA"] = str(_TMP / "example")
    atexit.register(shutil.rmtree, _TMP, True)

# 시험은 Slack 을 부르지 않는다 — 가짜 토큰이면 충분하다. 진짜 토큰 파일이 없는 맥에서도 돌아야 한다.
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-example-not-a-real-token")
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-example-not-a-real-token")
