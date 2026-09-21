---
kind: research
updated: "2026-09-21"
---

# 🗣️ 「이슈」 라고 부르지 않는다 — 말 정하기

조사 계기: 「이슈란 단어 어려운 거 같아. 투두 리스트 만들어서 도와주는, 계획 잡아주는 계획표
느낌 어때? 번호는 문서번호 같은 거고」 (2026-09-21 사장님).

## 한 줄 결론

**「이슈」 는 버그 추적기 시대의 말이고, 업계 1위가 이미 버렸다.** 우리는 **트래커가 없는 팀**을
위해 만드는데 남의 옛말을 빌려 쓰고 있었다. → **「할 일」**

## 1. Atlassian 이 먼저 버렸다

2024년 11월 발표, 2025년 3월 적용 — Jira 의 **「Issue」 → 「Work item(작업 항목)」**.
공식 이유가 사장님 말씀과 같다:

> 「issue」 는 **문제·결함** 을 떠올리게 하는데, 이제 Jira 가 다루는 것은 **모든 종류의 일**이다.
> 개발자가 아닌 팀들이 쓰게 되면서 맞지 않는 말이 됐다.

Jira 는 **원래 버그 추적기**였다. 「이슈」 는 그때의 말이다. API·필터는 호환을 위해 `issue` 를
그대로 받지만, **사람에게 보이는 말은 바꿨다** — 우리가 할 일과 같은 모양이다
(안은 그대로, 보이는 말만).

## 2. 다른 도구들은 뭐라고 부르나

| 도구 | 부르는 말 |
| --- | --- |
| Jira | **작업 항목** (2025-03 부터. 그 전엔 이슈) |
| Linear · GitHub | Issue (개발자 대상이라 그대로) |
| Asana · Todoist · Height | **Task (할 일)** |
| Trello | Card |
| Monday | Item |
| **우리** | 이슈 → **할 일** |

개발자만 쓰는 도구는 Issue 를 남기고, **여러 직군이 쓰는 도구는 Task/Work 로 갔다.**
우리는 「Jira 없는 팀」 이 대상이므로 뒤쪽이다.

## 3. 외부 팀이 실제로 막히는 곳

| 무엇 | 숫자 | 출처 |
| --- | --- | --- |
| 일하는 시간 중 **소통** | **57%** (만드는 건 43%) | Microsoft |
| 방해 주기 · 복귀 시간 | **2분마다** · **25분** | Microsoft · UC Irvine |
| 결함이 지원 채널로 들어와 흩어짐 | **30%** | Atlassian |
| 백로그가 부푼 예시 | 847건 → 312건 (AI 정리) | 사례 |

그리고 작은 팀 조사에서 나온 한 문장:

> **이슈 만들기는 「대화 중에」 할 수 있을 만큼 빨라야 한다** — 버그 하나 올리는 데 1분 걸리고
> 필수 칸이 넷이면, **사람들은 그냥 안 올린다.**

**우리 데이터가 그 말을 그대로 증명한다** (2026-09-21 실측):

```
카드 71개 중 이동원이 직접 만든 것 28개
열린 29개 중 담당 없음 19개 · 정의(왜·무엇)가 빈 카드 24개
```

만들다 만 것이 24개다. **「만들기가 무겁다」 의 증거**다.

## 4. 바꾸는 말

| 지금 | 바꾼 뒤 | 비고 |
| --- | --- | --- |
| 이슈 | **할 일** | 상태 라벨(대기·진행 중·보류·완료)과 겹치지 않는다 |
| 이슈 목록 | 할 일 목록 | |
| 이슈로 만들어줘 | 할 일로 만들어줘 | |
| 작업판 (캔버스) | **계획표** | 사장님: 「계획 잡아주는 계획표 느낌」 |
| `PA-73` | 그대로 | **문서번호처럼** 가리킬 때만 쓴다 |

### 바꾸지 않는 것

- **GitHub 이슈** — 그쪽 도구의 이름이다. 「GitHub 이슈로 올렸어요」 는 그대로
- `issues/` 폴더 이름 · `issue_channel` 같은 **설정 키와 코드 이름** — 바꾸면 옛 설정이 깨진다
  (Atlassian 도 API 는 `issue` 를 그대로 뒀다)
- `HISTORY.md` 의 과거 기록 — 그때는 그렇게 불렀다

## 5. 왜 지금인가

**PoC 에 사람이 들어오기 전이 가장 싸다.** 사람이 그 말에 익숙해진 뒤에 바꾸면 두 번 가르쳐야
한다. 지금은 쓰는 사람이 둘뿐이다.

## 출처

- [Atlassian — Work 가 새 용어](https://community.atlassian.com/forums/Jira-articles/It-s-here-Work-is-the-new-collective-term-for-all-items-you/ba-p/2954892)
- [이름 변경 배경 정리](https://jimiwikman.se/writings/my-articles/atlassian/the-renaming-of-issues-and-making-work-as-the-new-collective-term-for-all-items-tracked-in-jira-r253/)
- [작은 팀의 이슈 관리 (Linear·Jira·Height)](https://pickuma.com/for-dev/linear-vs-jira-vs-height-2026-issue-tracking-small-teams/)
- [팀 작업 추적 통계](https://www.thisandthat.chat/blog/engineering-team-task-tracking-statistics)
