---
kind: guide
updated: "2026-09-20"
---

# 봇 프로필 이미지 (앱 아이콘)

이름: **프로덕트 에이전트** · 부를 때 **PA** (`@PA`). 사람 프로필 사진과 한눈에 구별되게 도형 위주로.

## Slack 이 요구하는 것
| 항목 | 값 |
| --- | --- |
| 크기 | 512×512 이상 정사각형 (PNG) |
| 실제로 보이는 크기 | 메시지 옆 36px · 앱 목록 48px — **작게 보인다** |
| 모서리 | Slack 이 알아서 둥글게 깎는다. 직접 둥글릴 필요 없다 |
| 배경 | 투명 대신 **꽉 찬 단색** (밝은 테마·어두운 테마 모두에서 떠 보이게) |
| 글자 | 넣지 않는다. 36px 에서 안 읽힌다 (넣는다면 두 글자까지) |

## 잘 되는 그림 (Slack 앱 아이콘들을 보고)
- 도형 **하나**만 크게. 배경 여백 넉넉히 (가장자리 12% 는 비움)
- 납작한 벡터. 그림자·그라데이션·입체 효과 없음
- 색은 두세 가지. 배경 진한 색 + 도형 흰색 + 포인트 한 색
- 사람 얼굴·사진·아이소메트릭 일러스트는 피한다 (사람 프로필과 헷갈림)

## 봇 이름·얼굴 — 설정 없이 (2026-09-20 확정)
메시지를 보낼 때 **이름과 그림 주소를 같이 보낸다**(`chat:write.customize`). 그래서 앱 설정을 바꾸거나
워크스페이스에 이모지를 올리지 않아도 `PA` 이름과 얼굴이 나온다. **새 워크스페이스는 설치만 하면 끝.**
- 표시 이름: `config.json` 의 `bot_name` (기본 `프로덕트 에이전트 - PA`) — 메시지에 보이는 이름
- 부르는 이름: `config.json` 의 `bot_handle` — `@` 로 부를 때 쓰는 짧은 이름. 앱 설정의 봇 이름과 같아야 한다
  (이 워크스페이스는 아직 옛 이름. 앱 설정에서 `모아` 로 바꾸면 여기도 같이 바꾼다)
- 그림: 공개 저장소 github.com/webn77/pa-icons — 배경 투명. 배경 있는 버전은 `bg-pa-*.png`
- 앱 아이콘(앱 목록·앱 홈에 보이는 것)만 API 가 없어서 앱 설정에 한 번 올려야 한다. 안 올려도 메시지에는 얼굴이 나온다

## (참고) 파일 목록
그림은 공개 저장소 **github.com/webn77/pa-icons** 에 있고, 봇이 메시지를 보낼 때마다 그 주소를 함께 보낸다.
그래서 **새 워크스페이스는 아무 설정도 필요 없다** — 앱만 설치하면 얼굴이 나온다. 사용자 지정 이모지를 올릴 필요도 없다.

| 파일 (공개) | 언제 |
| --- | --- |
| `pa-base.png` | 평소 · 시작 |
| `pa-think.png` | 정리하는 중 |
| `pa-done.png` | 완료 |
| `pa-ask.png` | 확인·부탁 |
| `pa-stuck.png` | 보류·막힘 |
| `pa-app-icon.png` | 앱 아이콘 (앱 설정에 한 번 올리면 앱 목록·홈에도 얼굴이 나온다. 안 올려도 메시지에는 나온다) |

팀이 다른 그림을 쓰려면 `config.json` 에 `"icons": "https://…/"` (주소 앞부분) 또는 `"moods": {"완료": "이모지이름"}`.
Slack 이 같은 사람의 연속 메시지를 묶기 때문에, 한 번에 여러 줄을 보내면 첫 줄의 얼굴만 보인다.

## 이전: 512px 아이콘 (2026-09-20)
**`pa-icon-512.png`** — 둥근 사각 몸체에 눈 두 개, 무광 3D, 민트색 + 진한 남색 배경. 꼬리·입 없음.
36px 로 줄여도 형태가 남는 것 확인 (`pa-icon-36.png` · `pa-icon-48.png`).

올리는 곳: api.slack.com/apps → 앱 선택 → Basic Information → Display Information → **App icon** 에 `pa-icon-512.png` 업로드.

### 표정 세트 (만듦 · 2026-09-20)
`pa-기본.png` · `pa-생각.png` · `pa-완료.png` · `pa-부탁.png` · `pa-막힘.png` — 512×512, 배경 투명. 한 장짜리 시트(`faces-src.webp`)를 잘라 만들었다.

**올리기**: Slack 왼쪽 위 워크스페이스 이름 → 도구 및 설정 → 사용자 지정 이모지 관리 → 추가. 이름은 파일 이름 그대로(`pa-기본` …).

**켜기**: 이모지를 **먼저 올린 뒤** 데이터 폴더 `config.json` 에 아래를 넣는다. 없으면 늘 기본 아이콘.
봇은 켤 때 그 이모지가 워크스페이스에 정말 있는지 확인하고, 없는 것은 자동으로 끈다 (없는 이모지를 쓰면 메시지 옆에 `:pa-완료:` 글자가 그대로 보인다 — 9/20 실측).
```json
"moods": {"기본": "pa-기본", "생각": "pa-생각", "완료": "pa-완료", "부탁": "pa-부탁", "막힘": "pa-막힘"}
```
| 언제 | 표정 |
| --- | --- |
| 시작했어요 👀 | 기본 |
| 완료됐어요 ✅ | 완료 |
| 보류됐어요 ⛔ | 막힘 |
| 확인해 주세요 (팀 대화방) | 부탁 |
| 정리하고 있어요… | 생각 |

### 원래 계획
같은 캐릭터로 눈만 바꾼다. 워크스페이스 사용자 지정 이모지로 올리면 봇이 상황에 따라 아이콘을 바꿔 쓴다.
| 이모지 이름 | 언제 | 눈 |
| --- | --- | --- |
| `pa-기본` | 평소 안내 | 동그란 눈 |
| `pa-생각` | 정리 중 · 회의록 정리 중 | 위를 보는 눈 + 작은 점 하나 |
| `pa-완료` | 완료 · 확인됨 | 웃는 눈 (^ ^) |
| `pa-부탁` | 확인해 주세요 · 시작해 주세요 | 감은 눈 + 살짝 숙임 |
| `pa-막힘` | 보류 · 일정 위험 | 반쯤 감긴 눈 + 땀 한 방울 |

## 방향 세 가지 (사람과 구별되는 「에이전트」 느낌)
| 방향 | 무엇을 그리나 | 왜 |
| --- | --- | --- |
| **A 코어** (`agent-core.png`) | 가운데 점 + 둘레를 도는 고리 | 「스스로 도는 무언가」. 36px 로 줄여도 안 뭉개짐. 가장 안전 |
| **B 노드** (`agent-node.png`) | 점 세 개를 선으로 이음 | 에이전트가 하는 일 — 사람·일·기록을 잇는다 |
| **C 모노그램** (`agent-pa.png`) | 두 글자 PA | 이름과 아이콘이 한 번에. 두 글자라 작아도 읽힘 |

체크리스트·반짝임(`agent-check` · `agent-spark`)은 「할 일 앱」 느낌이라 에이전트 느낌과는 다르다. 로봇 얼굴(`agent-face`)은 직관적이지만 장난감처럼 보일 수 있다.

## GPT 에 넣을 문장 (이미지 생성)

### A 코어 (추천)
```
Flat vector app icon, 1:1 square, 512x512, for an AI agent named "Product Agent (PA)".
A single glowing core: one solid circle in the center with one thin ring orbiting it,
the ring broken into an arc so it reads as motion. Very dark navy background (#1B1F2A),
mint-green core and arc, one muted slate ring. No text, no letters, no gradients, no shadows,
no 3D, no human faces, no robots. Geometric, calm, symmetrical, generous padding,
high contrast, legible at 36 pixels. Solid background. Modern AI product icon style.
```

### B 노드
```
Flat vector app icon, 1:1 square, 512x512, for an AI agent named "Product Agent (PA)".
Three circular nodes connected by straight thick lines forming a triangle; the top node is
larger and white (the agent), the two lower nodes are blue (people and work). Very dark navy
background (#161B2E). No text, no gradients, no shadows, no 3D, no faces. Geometric, minimal,
thick strokes, generous padding, legible at 36 pixels. Solid background.
```

### C 모노그램
```
Flat vector app icon, 1:1 square, 512x512. Bold geometric monogram "PA" in white, centered,
heavy sans-serif, tight letter spacing, on a deep indigo background (#2F3B8C), with one small
cyan dot beneath the letters. No other elements, no gradients, no shadows, no 3D.
Legible at 36 pixels. Solid background.
```

## 이전 안 (참고)
```
Flat vector app icon, 1:1 square, 512x512, for a Slack bot called "Product Agent (PA)".
Single bold centered glyph: a checklist inside a speech bubble, with a small four-point spark
at the top-right corner. Deep indigo background (#2A3245), white glyph, one warm yellow accent.
No text, no letters, no gradients, no shadows, no 3D, no human faces, no photo realism.
Minimal geometric shapes, thick strokes, generous padding, high contrast, legible at 36 pixels.
Solid background (not transparent). Flat design in the style of modern productivity app icons.
```
뜻: 말풍선(대화) 안에 체크리스트(할 일) + 반짝임(AI). 대화 안에서 일을 챙기는 봇이라는 뜻이 한 장에 들어간다.

**다른 개념도 같은 문장에서 이 부분만 바꾸면 된다**
- 방패 + 체크 → 지킴이 느낌: `a shield with a check mark inside`
- 나침반 + 체크 → 방향을 잡아 주는 느낌: `a compass rose with a check mark in the center`
- 동그란 봇 얼굴 → 친근한 느낌: `a simple rounded robot head with two dot eyes and an antenna`

## 여기 들어 있는 임시 아이콘
에이전트 느낌: `agent-core.png` · `agent-node.png` · `agent-pa.png`
할 일 앱 느낌(이전 안): `agent-check.png` · `agent-face.png` · `agent-spark.png`
SVG 원본도 함께 있다. 정식 이미지가 나오면 지워도 된다.

## 올리는 곳
api.slack.com/apps → 앱 선택 → **Basic Information → Display Information → App icon** 에 업로드.
(봇 표시 이름은 같은 화면, 또는 Features → App Home 에서 바꾼다.)

## 앱 아이콘 (2026-09-20)
`pa-icon-white-1024.png` — 흰 배경 · 1024×1024 · 불투명. Slack 앱 설정에 손으로 올린다.
- **512 는 거절당한다.** 안내문은 512~2000px 이라고 하지만 딱 512 로는 안 올라갔다
- **불투명이 맞다.** 앱 아이콘은 라이트·다크 양쪽에 나오므로 배경이 있는 편이 안전하다
- **얼굴이 꽉 차야 한다.** 여백이 있으면 작은 칸에서 더 작아 보인다 (`pa-기본.png` 를 940px 로 얹었다)
- 메시지마다 나오는 얼굴(`pa-기본` 등)은 반대로 **투명** — 작은 동그라미로 나와서 배경이 있으면 네모가 도드라진다
- **배경색은 `#0A3A42`** (짙은 청록). 매니페스트의 `background_color` 는 앱 이름이 흰 글씨로 얹히는 자리라
  어두워야 한다 — 민트(`#02c2d3`)는 대비 2.2:1 이라 Slack 이 거절한다 (권장 4.5:1 이상)

## 앱 아이콘

`moa-icon-1024.png` — **투명 배경 1024×1024.** 앱 설정에 올리는 것은 이것 하나다
(Basic Information → Display Information → App icon).

**투명이라야 한다** (2026-09-22 사장님 지적). 예전 `pa-icon-white-1024.png` 는 흰 배경이
구워져 있어서, Slack 의 어두운 테마에서 **흰 네모**가 그대로 보인다.

**512 는 Slack 이 거절한다** — 안내문에는 512~2000px 이라고 나오지만 딱 512 는 튕긴다 (9/20 실측).
**API 로는 못 올린다** — 매니페스트에 아이콘 항목이 없다 (display_information 은 name ·
description · long_description · background_color 넷뿐, 9/22 확인). 사람이 한 번 올려야 한다.

메시지에 보이는 얼굴은 이것과 **다른 것**이다 — 봇이 `icon_url` 로 매번 실어 보낸다
(`slack.py` 의 `ICON_BASE`, webn77/pa-icons). 그쪽은 올릴 필요가 없다.
