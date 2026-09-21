# 모아

**프로젝트 팀의 비서.** 팀에 흩어진 대화를 할 일로 바꾸고, 담당·일정·회의록·지난 의사결정까지 챙깁니다.
Jira·Notion 없이 **Slack 하나로**.
사람은 Slack만 보고, 기록은 GitHub에 쌓입니다.

```
"@모아 내 할 일"          "@모아 현황 알려줘"          "@모아 지난번 그 결정 뭐였지"
```

**설치 10분** — 사람이 하는 일은 **클릭 두 번 · 붙여넣기 두 번**입니다 → `docs/install.md`

## 필요한 것

| | |
| --- | --- |
| **Claude Code 로그인** | **봇의 AI는 설치한 사람의 구독으로 돕니다** — 따로 딸려 오는 열쇠도, 거쳐 가는 서버도 없습니다 |
| 맥 (또는 늘 켜 둔 컴퓨터) | Python 3.11+ · `pip install aiohttp pyyaml` |
| GitHub (선택) | 안 쓰면 할 일 정본을 `md` 로만 씁니다 |

**팀 대화가 남의 서버를 지나가지 않습니다.** 팀이 자기 Slack 앱·자기 토큰·자기 컴퓨터로 돌립니다.

## 하는 일

```
#팀-대화 🎫 한 줄  →  #프로젝트-○○ 에 카드  →  모아가 스레드에서 빠진 것만 묻고 정리 (👍 로 확정)
카드: 우선순위 색 · 상태 · 담당 · 목표일 + [✋ 내가 할게요] [📄 상세 → ✏️ 내용 수정]
스레드 ⚙️ 설정: 담당 · 우선순위 · 목표일 · 상태 · 단계 · 기능 — AI가 먼저 채우고 사람이 고친다
@모아 현황 (평일 9시 자동): 진행률 · 누가 뭘 · 위험 → 추천 [적용]
캔버스: Pain point → 목표 → 기능·할 일 → 로드맵 → 누가 뭘 → 회의록
```

**할 일 관리는 그중 하나입니다** — 회의록·결정 기록·지난 맥락 찾기까지 합니다.

## 파일
| 파일 | 역할 |
| --- | --- |
| `bot.py` · `handlers.py` | 실행 · 이벤트·버튼 받기 (#30 에서 나눔 — 구조는 `docs/architecture.md`) |
| `common.py` · `store.py` · `docs.py` · `slack.py` · `ai.py` | 바탕 · 상태 저장 · 팀 문서 읽기 · Slack 호출 · AI 호출 |
| `views/` · `flows/` | 화면(카드·홈·캔버스·현황) · 흐름(요청·상태·회의·일정 위험·GitHub) |
| `core.py` | 배정·우선순위 계산 (순수 로직, 테스트 대상) |
| `config.py` · `config.json` | 팀마다 다른 값 — 채널·캔버스 ID, 봇 이름, GitHub. 데이터 폴더는 `MOA_DATA` (#50) |
| `setup.py` · `manifest.yaml` | 설치 — 앱 매니페스트 · 채널·캔버스 찾기/만들기 · 새 팀 문서(`templates/starter`) · LaunchAgent 파일 |
| `messages.py` | 모아가 사람에게 하는 말 전부 + 말투 규칙 (`tests/test_messages.py`). 데이터 폴더의 `messages.json` 으로 덮어쓴다 (#48) |
| `gh_link.py` | md 정본 쓰기 · GitHub 이슈 · Projects 보드 |
| `tests/` | 시험 267개 (10파일) + 골든 — 화면·동작이 한 글자도 안 바뀌는지 |
| `project.md` · `roadmap.md` · `team.md` · `priority.md` | 목표 · 단계 · 역할 · 우선순위 규칙 (사람이 고치면 모아가 따른다) |
| `canvas_head.md` | 캔버스 맨 위 안내 · 앱 홈 ❓ 사용법 |
| `research/` | 조사 — 벤치마킹(`benchmark.md`) · 어디서 돌릴까(`hosting.md`) · 말 정하기(`words.md`) |
| `docs/` | 설계 — 구조 · 데이터 위치 · GitHub 동기화 · 팀의 기억(`team-memory.md`) · 설치 |
| `templates/` | 새 팀이 받는 문서(`starter/`) · 목표 틀 |
| `example/` | 가상 팀 데이터 — 시험이 이것을 보고 돈다 |
| `issues/` · `meetings/` · `notes/` | 정본 |

## 실행

**맥의 LaunchAgent 가 띄운다** — 죽어도 스스로 살아나고, 껐다 켜도 뜬다 (#10).
`kill` 로는 못 끈다(30초 안에 돌아온다). 자세한 것은 `research/hosting.md`.

`setup.py --launchagent` 가 데이터 폴더에 plist 를 써 주고, 설치 명령도 알려 준다.

```bash
launchctl kickstart -k gui/$(id -u)/<라벨>   # 다시 띄우기 (새 코드 반영)
launchctl bootout   gui/$(id -u)/<라벨>      # 끄기
```

토큰은 `~/.config/moa.env` (저장소에 두지 않는다). 봇은 **늘 하나만** 뜬다 (`bot.pid` 파일 잠금).

> 만든 사람 맥에서는 **둘이 각자 돈다** — 옛 워크스페이스(`com.dongwon.slack-sandbox-bot`,
> 토큰 `~/.config/slack-sandbox.env`)와 모아(`com.moa.t0b2mskm7n0`, 토큰 `~/.config/moa.env`).
> 팀마다 데이터 폴더·토큰·Slack 앱이 따로라 서로 안 부딪힌다 (`bot.pid` 잠금도 데이터 폴더 안).

## 테스트
고친 뒤 **반드시** 돌린다 — 267개가 1초면 끝난다. **토큰도 설정도 필요 없다.**

```bash
python3 -m unittest discover -s tests -t .
```

`-t .` 가 있어야 `tests/__init__.py` 가 먼저 불려 **예시 데이터**(`example/`)를 가리킨다.
빼면 데이터 폴더가 코드 폴더가 되어 `config.json` 이 없다고 터진다.

**`example/`** 는 가상 팀의 데이터다 — 김하나(PM)·박두리(개발), 할 일 71건. 데이터 폴더가
어떻게 생겼는지 보고 싶을 때도 여기를 열면 된다.

**골든**이 화면과 동작을 통째로 고정한다 — `tests/golden/expected.json`(화면) ·
`expected_actions.json`(버튼·반응·창을 누른 뒤 무엇을 하는지). 바뀌면 **의도한 것인지 diff 로 본다.**
말을 바꾸는 판에서는 이 diff 가 **어디를 놓쳤는지 알려 주는 지도**가 된다.

## 이름

**모아** — 팀에 흩어진 것을 *모아* 준다는 뜻입니다 (2026-09-21).
예전 이름 `sandbox` 는 개발자 말로 「모래놀이터 = 마음껏 부숴도 되는 시험판」인데, 이제 시험판이 아니고
받는 사람이 알아들을 수도 없어서 버렸습니다.

목표·단계는 `project.md` · `roadmap.md`, 지나온 길과 결정은 `HISTORY.md`.
