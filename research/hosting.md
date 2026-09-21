---
kind: research
updated: "2026-09-21"
---

# 🖥️ 봇을 어디서 돌릴까 — 조사 (2026-09-21)

조사 계기: 「여러 명이 쓴다고 가정하면 개인 컴퓨터에 있는 게 문제 아니냐」 (사장님).

## 한 줄 결론

**Mac mini 로 간다 — 돈이 들지 않고, 이미 상시 켜져 있는 데스크탑이다.**
클라우드는 「봇이 멈추면 곤란한」 단계가 됐을 때. 옮기는 비용은 그때도 지금과 같다.

## 먼저 걸린 제약 — 서버리스는 아예 안 된다

우리 봇은 **Socket Mode** 다. Slack 쪽으로 **연결을 붙들고** 이벤트를 기다린다.

> "Socket Mode is at the cost of a persistent connection needing to be made (so no serverless approaches)"
> — [Slack Socket Mode 문서](https://api.slack.com/socket-mode) 및 커뮤니티 정리

그래서 **Vercel · AWS Lambda 처럼 「부를 때만 깨는」 곳은 후보가 아니다.** 24시간 깨어 있는
프로세스가 필요하다. 이 한 줄이 후보의 절반을 잘라낸다.

**대신 좋은 점도 여기서 온다** — 공인 IP · 열린 포트 · 도메인이 **필요 없다.** 봇이 밖으로
연결을 건다. 그래서 집 안의 기계에서도 그대로 돌아간다.

## 비교표

| | 월 비용 | Socket Mode | 관리 부담 | 비고 |
| --- | --- | --- | --- | --- |
| **Mac mini (지금)** | **0원** | ✅ | 낮음 | 이미 있다. 정전·인터넷에 같이 묶인다 |
| Fly.io | 약 $2 (256MB) | ✅ | 낮음 | 2024/10 이후 만든 조직은 무료 없음 |
| Railway | $5 (크레딧 $5 포함) | ✅ | 낮음 | 1GB 상시는 수십 달러로 뜀 |
| Hetzner | 약 €4.35 (2코어/4GB) | ✅ | **높음** — OS·보안 직접 | 2026/4 가격 인상, 플랜 교체 중 |
| DigitalOcean | $4 (1코어/512MB) | ✅ | **높음** | |
| Oracle 무료 | 0원 | ✅ | 높음 | 2026/6 한도 반토막(2 OCPU/12GB)이나 **우리에겐 여전히 과분** |
| ~~Render 무료~~ | 0원 | ❌ | — | **15분 놀면 꺼진다** → 연결이 끊겨 후보가 아니다 |

우리 봇이 실제로 쓰는 것: 파이썬 프로세스 하나, 카드 68장, CPU 거의 0. **256MB 면 충분하다.**

## Mac mini 로 정한 이유

처음에는 「개인 컴퓨터라 문제」 라고 적었는데 **기종을 보지 않고 한 말이었다.** 노트북이면
들고 나가서 문제지만, Mac mini 는 책상에 놓인 데스크탑이고 늘 전원에 꽂혀 있다.

필요한 것은 셋뿐이고 전부 공짜다:

1. **맥이 자지 않게** — 지금은 Claude 세션이 막고 있을 뿐이라 세션이 끝나면 잔다 (`pmset -g` 로 확인)
2. **LaunchAgent** — 껐다 켜도 봇이 스스로 살아나게 (#10)
3. **잠금 파일** — 봇이 두 대 뜨는 것을 막는다. **LaunchAgent 로는 안 된다** (아래)

## 옮기면 필요 없어지는 것들 (나중에 클라우드로 갈 때)

Fly.io·Railway 같은 곳은 **코드를 올리면 자동으로 다시 뜬다.** 그래서:

| 맥에서 필요한 것 | 클라우드에서는 |
| --- | --- |
| LaunchAgent (#10) | 필요 없음 — 플랫폼이 다시 띄운다 |
| 잠금 파일 | 필요 없음 — 컨테이너 하나만 뜬다 |
| `update` 명령 (코드 받고 갈아타기) | 필요 없음 — 배포가 곧 재시작 |

**즉 이 셋은 「맥에서 돌리기 때문에」 생기는 일이다.** 옮기면 버려진다 — 그래서 지금 크게
만들지 않는다.

⚠️ 다만 그런 곳은 파일이 임시라 재시작하면 사라진다. 우리 봇은 켤 때 `git pull`, 바뀌면
push 라 **GitHub 에서 복구되지만**, 마지막 5분 창 안의 변경은 날아갈 수 있다. 옮길 때
영구 디스크를 붙이거나 창을 줄인다.

## 알아 둘 것 — LaunchAgent 는 「봇 하나」 를 보장하지 않는다

`#10` 완료 조건에 「봇 프로세스는 늘 1개」 가 있는데 **LaunchAgent 로는 안 채워진다.**
LaunchAgent 는 **자기가 띄운 것** 하나만 관리한다. 손으로 `nohup` 으로 또 띄우면 그냥 지나간다 —
2026-09-21 에 실제로 그렇게 봇이 두 대 떴고, 25초 동안 같은 Slack 연결에 둘이 붙어 있었다.

봇이 둘이면 **각자 자기 메모리의 카드를 들고 있다가 나중에 저장한 쪽이 이긴다** (`store.py:7`
이 `cards.json` 을 켤 때 한 번만 읽는다). 그래서 **잠금 파일이 따로 필요하다.**

## ⚠️ 등록한 뒤에는 봇을 이렇게 다룬다 (2026-09-21 등록 완료)

`KeepAlive` 를 켰으므로 **봇이 어떤 이유로든 꺼지면 launchd 가 다시 띄운다.** 실측: 죽이고
**14초 뒤 스스로 살아났다.** 그래서 예전처럼 `kill` → `nohup` 하면 **launchd 와 손이 각각
띄워 둘이 된다** (잠금 파일이 막아 주긴 하지만, 애초에 그러지 않는다).

| 하고 싶은 것 | 명령 |
| --- | --- |
| 다시 띄우기 (새 코드 반영) | `launchctl kickstart -k gui/$(id -u)/com.dongwon.slack-sandbox-bot` |
| 잠시 끄기 | `launchctl bootout gui/$(id -u)/com.dongwon.slack-sandbox-bot` |
| 다시 켜기 | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dongwon.slack-sandbox-bot.plist` |
| 상태 보기 | `launchctl print gui/$(id -u)/com.dongwon.slack-sandbox-bot` |

**`kill` 로는 봇을 끌 수 없다** — 30초 안에 돌아온다. 끄려면 `bootout` 을 써야 한다.

## 언제 클라우드로 옮기나

| | Mac mini | 클라우드 |
| --- | --- | --- |
| 평소 | ✅ | ✅ |
| 정전 · 인터넷 끊김 | ❌ 같이 멈춘다 | ✅ |
| 맥을 껐을 때 | ❌ | ✅ |
| 비용 | 0원 | 월 3천~7천 원 |

**PoC 에는 Mac mini 로 충분하다.** 「봇이 멈추면 팀이 곤란한」 단계가 되면 그때 옮긴다.
가져갈 것은 코드(GitHub) · 토큰 2개 · `config.json` 뿐이고, 다른 컴퓨터에서 이어받기는
`#59` 에서 이미 해 봤다.

## 출처

- [Slack Socket Mode](https://api.slack.com/socket-mode)
- [Render vs Railway vs Fly.io 가격 비교 (2026)](https://dev.to/pavel-hostim/render-vs-railway-vs-flyio-pricing-compared-2026-2e5p)
- [Fly.io 무료 티어 2026](https://www.saaspricepulse.com/blog/flyio-free-tier-2026)
- [Oracle 무료 한도 축소 (2026-07)](https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)
- [Hetzner CX22 가격 (2026)](https://vpsfor.dev/posts/hetzner-cx22-pricing-2026/)
- [DigitalOcean Droplet 가격](https://docs.digitalocean.com/products/droplets/details/pricing/)
