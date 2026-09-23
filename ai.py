"""AI 호출 — claude -p · 구체화 질문 · 정리 · 답 · 추천 담당 · 우선순위 숫자 (#30 에서 bot.py 를 나눔)."""
import asyncio, json, os, re, pathlib
import core
from messages import say
from common import AI_FACTOR, CHANNEL, HERE, SPEC_KEYS, log  # noqa: E402,F401
from docs import load_features, load_team  # noqa: E402,F401
from slack import api, mood, name_of  # noqa: E402,F401
from store import STATE, chan, save  # noqa: E402,F401


AI_CWD = pathlib.Path.home() / ".cache/moa-ai"


def model():
    """어느 모델을 부를까. **무슨 일이 나든 "sonnet" 으로 물러난다** (2026-09-21 실측).

    예전에는 `~/projects/config.sh` 를 그냥 읽었는데 **그건 만든 사람 맥에만 있는 파일**이다.
    `HOME` 을 바꿔치기해 남의 맥을 흉내 내 보니 `FileNotFoundError` 로 터졌고, 그러면
    `ask_ai` 를 부르는 아홉 군데가 전부 죽는다 — 그것도 **조용히** 죽는다. 부르는 쪽이 잡아서
    「정리 실패」 만 띄우기 때문에, 받은 사람 눈에는 **Slack 에 붙고 카드도 생기는데 생각만 안 하는**
    봇으로 보인다. 설치한 사람이 제일 알아채기 어려운 고장 모양이라 여기서 막는다.

    **별칭(`sonnet`)을 쓴다** (2026-09-23 사장님: 「소네트로 해서 두자고」).
    예전에는 `~/projects/config.sh` 의 `MODEL_SONNET` 에 박힌 **전체 이름**(`claude-sonnet-5`)을
    읽었다. 그러면 새 판이 나와도 **안 따라간다** — 9/22 에 Opus 5.5 가 나왔고 Sonnet 5.5 도
    곧 나온다는데, 그날 이 봇만 옛 판에 묶여 있게 된다.

    전체 이름은 **API 를 직접 부르는 쪽**에 필요하다 (별칭을 안 받는다). 우리는 `claude` CLI 를
    부르고, CLI 의 별칭은 **늘 그 계열의 최신**을 가리킨다. 그래서 여기서는 별칭이 맞고,
    `config.sh` 를 안 읽는 편이 **받는 사람 맥에서도 안전하다** (그 파일은 만든 사람 것이다).

    `MOA_MODEL` 로 덮어쓸 수 있다 — 다른 모델을 쓰고 싶은 팀을 위해 (`opus`·`fable` 도 별칭이다).
    """
    return os.environ.get("MOA_MODEL") or "sonnet"


# 잠깐 뒤 되는 오류들 — 여기 걸리면 다시 해 본다 (2026-09-22 실측: 500 이 몇 분 오락가락했다)
# **「한도」 는 여기 없다** (2026-09-23 감사). 한도에 걸린 때가 바로 물러설 때인데 2초·4초 쉬고
# 두 번 더 두드리고 있었다 — 벽을 세 번 치는 셈이고, 여러 카드를 도는 자리에서는 N×3 이 된다.
AGAIN = ("500", "502", "503", "504", "overloaded", "Connection", "TimeoutError")
# 물러설 오류 — 걸리면 **그 자리에서 끝낸다**
STOP = ("rate limit", "usage limit", "quota")


async def _once(system, prompt):
    AI_CWD.mkdir(parents=True, exist_ok=True)
    p = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", model(), "--setting-sources", "project", "--strict-mcp-config",
        "--tools", "", "--no-session-persistence", "--output-format", "json", "--system-prompt", system,
        cwd=AI_CWD, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(p.communicate(prompt.encode()), timeout=180)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # **끊으면 죽인다.** `wait_for` 는 기다리기를 그만둘 뿐 자식 프로세스는 계속 돈다 —
        # 화면에서만 끝나고 한도는 계속 녹는다 (2026-09-23 감사)
        try:
            p.kill()
        except ProcessLookupError:
            pass
        raise
    d = json.loads(out or b"{}")
    if d.get("is_error"):
        raise RuntimeError(d.get("result"))
    return d.get("result", "")


async def ask_ai(system, prompt, tries=3):
    """구독으로 Claude Code 를 부른다. 훅·MCP·도구 없이, 세션을 남기지 않는다.

    **잠깐 뒤 되는 오류는 다시 해 본다** (2026-09-22 실측). Anthropic 쪽 500 이 몇 분간
    오락가락했는데, 우리는 한 번 실패하면 그대로 포기했다 — 그러면 사람 눈에는
    **말이 안 통하는 봇**으로 보인다. 2초 · 4초 쉬고 두 번 더 해 본다.
    """
    last = None
    for i in range(tries):
        try:
            return await _once(system, prompt)
        except Exception as e:
            last = e
            # **예외 이름도 본다** — `str(asyncio.TimeoutError())` 는 **빈 문자열**이라
            # 예전의 `"timeout"` 항목은 한 번도 안 걸렸다 (2026-09-23 실측)
            says = f"{type(e).__name__} {e}".lower()
            if any(x in says for x in STOP):
                log(f"AI 한도에 걸렸어요 — 다시 안 두드립니다: {str(e)[:80]}")
                raise
            if i + 1 >= tries or not any(x.lower() in says for x in AGAIN):
                raise
            log(f"AI 다시 해 봅니다 ({i + 1}/{tries - 1}): {str(e)[:80]}")
            await asyncio.sleep(2 * (i + 1))
    raise last


# ─── 뜻 고르기 (2026-09-22 사장님 결정) ──────────────────────────────────────
# **AI 는 갈림길에서 한 번만 부른다.** 하루 전까지는 프로젝트를 만드는 대화마다 AI 에게
# 칸을 채우게 했다 — 세 마디면 세 번이고, **구독 한도는 그렇게 녹는다**
# (사장님: 「우리는 구독으로 동작하는데 차라리 하나씩 받아서 하는 건 어때?」).
#
# 그래서 일을 나눴다: **무엇을 하려는 말인지**는 AI 가 고르고(한 번),
# **무엇을 물을지**는 흐름이 정한다(AI 없이). 낱말 목록으로는 「새로 시작하는 일 하나
# 올려줘」 를 못 잡는데, 그걸 잡으려고 낱말을 늘리면 「프로젝트 현황」 까지 삼킨다.
INTENT_SYS = (
    "너는 Slack 에서 팀의 프로젝트를 돕는 비서다. 사람이 한 말이 **무엇을 하려는 것인지**만 고른다. "
    "셋 중 하나만 그대로 출력한다 — 다른 말은 한 글자도 쓰지 않는다.\n"
    "project = 프로젝트를 새로 만들거나 올리려는 말\n"
    "task = 할 일 하나를 만들거나 올리려는 말\n"
    "other = 그 밖의 모든 것 — 찾기 · 목록 · 현황 · 질문 · 인사 · 이미 있는 것에 대한 이야기"
)


async def intent(q):
    """이 말이 프로젝트 등록인지 할 일 등록인지 고른다. 못 고르거나 AI 가 안 되면 "other".

    **막히지 않는다** — 못 고르면 찾기로 흘러가고, 거기서도 못 알아들으면 무엇을 할 수 있는지
    알려 준다. 프로젝트를 잘못 시작하는 것보다 그게 낫다 (되돌릴 수 없는 것을 만드는 길이다).
    """
    try:
        raw = (await ask_ai(INTENT_SYS, (q or "")[:500], tries=2)).strip().lower()
    except Exception as ex:
        log(f"뜻 고르기 실패: {type(ex).__name__}: {str(ex)[:80]}")
        return "other"
    got = next((k for k in ("project", "task") if k in raw), "other")
    log(f"뜻: {got} ← {(q or '')[:40]}")
    return got


async def transcript(s, c):
    msgs = (await api(s, "conversations.replies", channel=chan(c), ts=c["card_ts"], limit=200)).get("messages", [])
    lines = [f"[처음 요청] {c['by']}: {c['request']}"]
    for m in msgs[1:]:
        if m.get("bot_id") and not m.get("username"):
            continue
        lines.append(f"{await name_of(s, m)}: {re.sub(r'<@U[A-Z0-9]+>', '@봇', m.get('text', ''))}")
    return "\n".join(lines)


async def refine(s, c, thread_ts):
    await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": thread_ts, "text": say("refining"), **mood("생각")})
    system = ("너는 Slack 이슈를 정리하는 PM 보조다. 대화에 있는 사실만 쓴다. 추측은 쓰지 않고 모르면 '미정'이라고 쓴다. "
              "반드시 JSON 한 개만 출력한다. 키: title(40자 이내), why, change, expect, not_doing, done_criteria. "
              # **체크리스트는 「할 거리」 다** (2026-09-22 사장님: 「이거 안에 체크리스트 뭐 해야 하나」).
              # 예전에는 「끝났다고 볼 조건」 이라고만 시켜서 「알림이 1회만 발송된다」 같은 **통과 조건**이
              # 섞여 들어왔다. 읽는 사람은 「그래서 뭘 하지」 를 알 수 없다. 그리고 「미정」 은 항목이 아니다 —
              # 체크할 수 없는 것을 체크 목록에 넣으면 진행률이 거짓말을 한다 (실제로 5건이 그랬다)
              "done_criteria 는 **해야 할 일**을 2~5개, 한 줄에 하나씩, 「…하기」 로 끝나는 짧은 동사구로 쓴다 "
              "(예: 「권한 단계 표 쓰기」 · 「안 쓰는 항목 빼기」). 「…된다」 같은 상태 서술이나 「미정」 은 넣지 않는다 — "
              "쓸 것이 없으면 빈 배열로 둔다. 모든 값은 한국어 한두 문장.")
    try:
        raw = await ask_ai(system, "다음 이슈 스레드를 이슈 정의로 정리해줘.\n\n" + await transcript(s, c))
        spec = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception as e:
        log(f"정리 실패: {e}")
        await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": thread_ts,
                  "text": say("refine_fail", err=str(e)[:120])})
        return
    old_title, old_spec = c["title"], dict(c.get("spec") or {})
    c["title"] = spec.get("title") or c["title"]
    # 「미정」·글머리표를 받는 쪽에서 거른다 — 시키는 것만으로는 못 막는다 (core.clean_items)
    spec["done_criteria"] = core.clean_items(spec.get("done_criteria"))
    c["spec"] = spec
    if old_spec:                                           # 처음 정리는 이력이 아니다
        await record_change(s, c, None, old_title, old_spec, "스레드 대화를 다시 정리 (@정리)", how="AI 정리")
    await redraw(s, c)
    await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": thread_ts,
              "text": say("refined", n=len(spec.get("done_criteria") or []))})
    await redraw(s, c)


COACH = ("너는 팀의 프로젝트 비서다. 카드 스레드에서 사람과 짧게 대화하며 이슈를 구체화한다. "
         "이슈 정의는 5칸: why(누가 무엇 때문에 곤란한가), change(무엇이 바뀌나), expect(기대와 확인 방법), "
         "not_doing(이번에 하지 않을 것), done_criteria(**해야 할 일** 2~5개 — 「…하기」 로 끝나는 짧은 동사구. "
         "「…된다」 같은 상태 서술이나 「미정」 은 넣지 않고, 쓸 것이 없으면 빈 배열). "
         "규칙: 대화나 지금 정의에 이미 있는 것은 묻지 않는다. 한 번에 최대 2개만, 한 줄씩 짧게 묻는다. "
         "사람이 질문하면 먼저 답한다 (모르면 모른다고). 양식을 채우라고 하지 않는다 — 말로 묻는다. "
         "5칸을 쓸 만큼 모였으면 ready=true 로 spec 을 채운다. 대화에 없는 사실은 지어내지 않고 모르는 칸은 '미정'. "
         "사람이 '됐어·그만·나중에' 라고 하면 stop=true. "
         "JSON 한 개만 출력: {\"reply\": \"사람에게 할 말(한국어, 3줄 이내)\", \"ready\": false, \"stop\": false, "
         "\"spec\": {\"title\":\"40자 이내\",\"why\":\"\",\"change\":\"\",\"expect\":\"\",\"not_doing\":\"\",\"done_criteria\":[]} 또는 null}")
COACH_MAX = 5           # 구체화 대화를 주고받는 상한 — 넘으면 멈춘다 (한도가 녹지 않게)
_COACHING = set()


async def convo(s, c):
    """스레드 대화 — 봇의 질문도 포함 (⚙️ 설정·안내 메시지는 뺀다)."""
    msgs = (await api(s, "conversations.replies", channel=chan(c), ts=c["card_ts"], limit=200)).get("messages", [])
    lines = [f"[처음 요청] {c.get('by', '')}: {c.get('request') or c['title']}"]
    for m in msgs[1:]:
        if m.get("ts") == c.get("ctl_ts") or m.get("ts") == c.get("draft_ts"):
            continue
        who = "비서" if (m.get("bot_id") and not m.get("username")) else await name_of(s, m)
        lines.append(f"{who}: {re.sub(r'<@U[A-Z0-9]+>', '@봇', m.get('text', ''))}")
    return "\n".join(lines[-40:])


async def coach(s, c, trigger):
    """#31 질문으로 구체화 — 카드가 생기면 먼저 묻고, 사람이 스레드에 답하면 이어서 대화한다."""
    if c["no"] in _COACHING or c.get("coach") in ("done", "stopped"):
        return
    # **다섯 번 주고받으면 멈춘다** (2026-09-23 감사). 멈추는 길이 둘뿐이었다 — 정리안에 👍 를
    # 누르거나 AI 가 스스로 「그만」 이라고 하거나. 그래서 정리안이 떴는데 👍 를 안 누르고
    # 그 스레드에서 **딴 얘기**를 하면(「이거 언제까지죠?」·「ㅇㅋ」) 한 줄마다 AI 를 불렀다.
    # `coach_rounds >= 3` 은 **정리안을 낼지**만 가르고 대화를 안 멈춘다 — 그건 그대로 둔다.
    if c.get("coach_rounds", 0) >= COACH_MAX:
        c["coach"] = "stopped"
        log(f"구체화 그만 #{c['no']} — {COACH_MAX}번 주고받음")
        return
    if (c.get("spec") or {}).get("done_criteria") and trigger == "new":
        c["coach"] = "done"                                       # 이미 정의가 있으면 묻지 않는다
        return
    _COACHING.add(c["no"])
    try:
        head = (HERE / "project.md").read_text(encoding="utf-8")[:1200]
        prompt = (f"프로젝트 요약:\n{head}\n\n이슈 #{c['no']} {c['title']}\n지금 정의: "
                  f"{json.dumps(c.get('spec') or {}, ensure_ascii=False)}\n\n대화:\n{await convo(s, c)}\n\n"
                  + ("카드가 방금 만들어졌다. **바로 정리안을 내라** — 제목과 프로젝트 요약만으로 "
                     "채울 수 있는 만큼 채우고, 모르는 칸은 '미정'으로 둔다. ready 는 true 로 하고, "
                     "꼭 물어야 할 것이 있으면 reply 에 한 가지만 묻는다."
                     if trigger == "new" else "사람이 방금 답했다. 이어서 대화해라."))
        raw = await ask_ai(COACH, prompt)
        r = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception as e:
        log(f"구체화 실패 #{c['no']}: {e}")
        _COACHING.discard(c["no"])
        return
    c["coach_rounds"] = c.get("coach_rounds", 0) + 1
    if r.get("stop"):
        c["coach"] = "stopped"
    if r.get("reply"):
        await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"], "text": r["reply"]})
    spec = r.get("spec") if isinstance(r.get("spec"), dict) else None
    # 내용 없는 이슈를 만들지 않는다 (사장님 지시 9/20) — 만들자마자 정리안을 내고 사람이 👍 한다.
    # **미정투성이 정리안은 내놓지 않는다.** 내놓으면 사람이 👍 를 눌러 버리고, 그러면
    # 「채워졌지만 뭔지 모를 카드」 가 남는다 — 오늘 19건을 채웠더니 절반이 그랬다.
    if spec and core.vague({"spec": spec}) >= 3:
        spec = None
        if not r.get("reply"):                                    # 물어볼 말이 없으면 우리가 묻는다
            await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"],
                      "text": say("too_vague"), **mood("생각")})
    if spec and (r.get("ready") or trigger == "new" or c["coach_rounds"] >= 3):
        c["spec_draft"] = spec
        lines = [f"*{label}*  {spec.get(k) or '미정'}" for k, label in SPEC_KEYS]
        lines.append("*체크리스트*\n" + ("\n".join(f"☐ {x}" for x in spec.get("done_criteria") or []) or "아직 없어요"))
        d = await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"], "text": "정리안",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "📝 *이렇게 정리했어요* — 맞으면 👍, 다르면 고쳐 주세요"}},
                       {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]}},
                       {"type": "actions", "elements": [
                           {"type": "button", "text": {"type": "plain_text", "text": "👍 이대로"}, "style": "primary",
                            "action_id": "spec_ok", "value": c["card_ts"]},
                           {"type": "button", "text": {"type": "plain_text", "text": "✏️ 고치기"},
                            "action_id": "edit_content", "value": c["card_ts"]}]}]})
        c["draft_ts"] = d.get("ts")
        c["coach"] = "proposed"
    else:
        c.setdefault("coach", "asking")
    save()
    _COACHING.discard(c["no"])
    log(f"구체화 #{c['no']} {trigger} → {c.get('coach')}")


async def answer(s, c, thread_ts, question):
    system = "너는 Slack 이슈 스레드의 AI 동료다. 스레드 내용만 근거로 한국어로 5줄 이내로 답한다. 모르면 모른다고 한다."
    try:
        text = await ask_ai(system, f"이슈 #{c['no']} {c['title']}\n\n{await transcript(s, c)}\n\n질문: {question}")
    except Exception as e:
        text = f"답을 만들지 못했어요: {str(e)[:120]}"
    await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": thread_ts, "text": text})


async def recommend(s, cards):
    """team.md 를 근거로 담당을 정한다. 담당 없음을 남기지 않는다. 사람은 수락하거나 바꾼다."""
    team = load_team()
    if not team or not cards:
        return
    load = {u: sum(1 for c in STATE["cards"].values() if c.get("assignee") == u
                   and c["status"] in ("doing", "blocked")) for u in team}
    roster = "\n".join(f"- {uid} | {t['name']} | {t['role']} | 영역: {t['areas']} | 진행 중 {load[uid]}/{t['max']}"
                       for uid, t in team.items())
    items = "\n".join(f"- #{c['no']} {c['title']}" + (f" — {c['spec'].get('why','')}" if c.get("spec") else "")
                      for c in cards)
    system = ("너는 팀의 일을 나누는 PM 보조다. 각 이슈를 역할 영역이 가장 잘 맞는 사람에게 한 명씩 배정한다. "
              "영역이 비슷하면 진행 중인 일이 적은 사람에게 준다. 반드시 모든 이슈를 배정한다. "
              "JSON 배열 하나만 출력한다: [{\"no\": 번호, \"uid\": \"Slack ID\", \"reason\": \"20자 이내 한국어\"}]")
    raw = await ask_ai(system, f"팀:\n{roster}\n\n{(HERE / 'team.md').read_text(encoding='utf-8')[:2500]}\n\n이슈:\n{items}")
    picks = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
    by_no = {c["no"]: c for c in cards}
    # 담당을 바로 박지 않는다 — 「추천 담당」 만 적고, 실제 배정은 place() 가 자리를 보고 한다
    for p in picks:
        c = by_no.get(int(p.get("no", 0)))
        if c and p.get("uid") in team:
            c["suggested"], c["assign_reason"] = p["uid"], p.get("reason", "")
    for c in cards:                       # AI 가 빠뜨린 것은 진행 중이 가장 적은 사람에게
        if not c.get("suggested"):
            c["suggested"], c["assign_reason"] = min(team, key=lambda u: load[u]), "여유가 가장 많음"
    log(f"담당 추천 {len(cards)}건")


async def prioritize(s):
    """AI 는 숫자(가치·긴급·목표 적합·노력)와 선행·중복·디자인 필요만 추정한다. 순서는 place() 가 계산한다."""
    team = load_team()
    # 이미 숫자가 있는 이슈는 다시 매기지 않는다 — 매번 매기면 순서가 흔들린다 (2026-09-19 실측)
    open_ = [c for c in STATE["cards"].values() if c["status"] not in ("done", "cancelled") and not c.get("scores")]
    if not open_:
        place()
        await asyncio.get_running_loop().run_in_executor(None, write_backlog)
        return []
    items = "\n".join(f"- #{c['no']} [{team.get(c.get('assignee'), {}).get('role', '?')}] {c['title']}"
                      for c in sorted(open_, key=lambda c: c["no"]))
    # 이미 점수가 있는 열린 이슈도 **보여만 준다** — 안 보여 주면 선행을 찾을 수가 없다.
    # 2026-09-20 확인: 카드 하나가 혼자 매겨진 것이 로그상 34회인데, 그때 AI 는 다른 이슈를 못 봤다.
    # 그래서 열린 31건 중 after 가 6건뿐이었고, 그 6건도 전부 「대량 배치」·「본문에 번호가 적힘」 으로 설명된다.
    others = "\n".join(f"- #{c['no']} {c['title']}"
                       for c in sorted((x for x in STATE["cards"].values()
                                        if x["status"] not in ("done", "cancelled") and x.get("scores")),
                                       key=lambda c: c["no"]))
    system = ("너는 PM 보조다. 각 이슈에 1~5 점수를 매긴다: value(가치), urgency(긴급), goal_fit(프로젝트 목표·성공 기준에 "
              "직접 닿는 정도), effort(노력, 클수록 큼). **먼저 끝나야 이 일을 시작할 수 있는 이슈**가 있으면 after 에 "
              "번호를 넣는다 — 「이미 있는 이슈」 목록과 매길 이슈 목록의 번호만 쓸 수 있고, 확실하지 않으면 비워 둔다. "
              "그냥 관련 있는 정도는 선행이 아니다. 다른 이슈와 중복이면 duplicate_of 에 "
              "번호를 넣는다. 화면 구성·문구·흐름이 바뀌면 design=true. 사람이 쓰는 시간이 아니라 일 자체의 예상 시간을 "
              "hours_min·hours_max(시간)로, AI(코딩 에이전트·LLM)가 얼마나 대신할 수 있는지 ai 에 "
              "ai(대부분 AI가 가능)|assist(AI가 돕고 사람이 판단)|human(정책·합의 등 사람만) 로 쓴다. "
              "project.md 「기능」 표에서 이 이슈가 속하는 기능 이름 하나를 feature 에 그대로 쓴다. JSON 배열 하나만 출력: "
              "[{\"no\":번호,\"value\":n,\"urgency\":n,\"goal_fit\":n,\"effort\":n,\"after\":[],\"duplicate_of\":null,"
              "\"design\":false,\"hours_min\":n,\"hours_max\":n,\"ai\":\"assist\",\"feature\":\"기능 이름\","
              "\"reason\":\"20자 이내\"}]")
    raw = await ask_ai(system, f"{(HERE / 'project.md').read_text(encoding='utf-8')}\n\n"
                               + (f"이미 있는 이슈 (점수는 매기지 말고 선행·중복을 찾을 때만 본다):\n{others}\n\n" if others else "")
                               + f"점수를 매길 이슈:\n{items}")
    picks = {int(p["no"]): p for p in json.loads(re.search(r"\[.*\]", raw, re.S).group(0)) if "no" in p}
    for c in open_:
        p = picks.get(c["no"])
        if not p:
            continue
        c["scores"] = {k: p.get(k, 3) for k in ("value", "urgency", "goal_fit", "effort")}
        if c.get("after_src") != "human":          # 사람이 정한 선행은 AI 가 지우지 않는다 (assign_src 와 같은 규칙)
            c["after"] = core.clean_after(p.get("after"), c["no"], STATE["cards"])
        c["duplicate_of"] = p.get("duplicate_of")
        c["design"], c["prio_reason"] = bool(p.get("design")), p.get("reason", "")
        c["hours"] = {"min": p.get("hours_min", 2), "max": p.get("hours_max", 4)}
        c["ai"] = p.get("ai") if p.get("ai") in AI_FACTOR else "assist"
        if p.get("feature") in load_features() and not c.get("feature"):
            c["feature"] = p["feature"]
    place()
    await asyncio.get_running_loop().run_in_executor(None, write_backlog)
    log(f"우선순위 {len(open_)}건 계산")
    return []


async def fill_after():
    """선행만 메운다 — 점수는 건드리지 않는다 (#68).

    점수가 있는 이슈는 `prioritize` 가 다시 보지 않는다(매번 매기면 순서가 흔들려서, 2026-09-19 실측).
    그래서 선행이 빈 채로 굳은 것들은 이 길로만 메운다. 사람이 정한 것(`after_src`)은 건드리지 않는다.
    """
    open_ = sorted((c for c in STATE["cards"].values() if c["status"] not in ("done", "cancelled")),
                   key=lambda c: c["no"])
    todo = [c for c in open_ if not c.get("after") and c.get("after_src") != "human"]
    if not todo:
        return []
    # **제목만 주면 못 찾는다** — 2026-09-20 실측: 제목만 26건 → 0건, 「왜·바뀌는 것」 을 붙이니 15건.
    # 「#54 가 #52 위에 올라타는가」 는 제목으로는 판단할 수 없는 물음이다.
    def blurb(c):
        sp = c.get("spec") or {}
        return (f"- #{c['no']} {c['title']}\n    왜: {(sp.get('why') or '')[:160]}"
                f"\n    바뀌는 것: {(sp.get('change') or '')[:200]}")
    system = ("너는 PM 보조다. 아래 열린 이슈들 사이에서 **먼저 끝나야 그 일을 시작할 수 있는** 관계를 찾는다. "
              "한 이슈가 다른 이슈가 만드는 것(화면·저장 구조·규칙) 위에 올라타면 그것이 선행이다. "
              "주제가 비슷한 정도는 선행이 아니다. JSON 배열 하나만: [{\"no\":번호,\"after\":[번호,…],\"why\":\"15자\"}] "
              "— 선행이 없는 이슈는 배열에서 빼라.")
    raw = await ask_ai(system, f"{(HERE / 'project.md').read_text(encoding='utf-8')}\n\n"
                               f"열린 이슈:\n" + "\n".join(blurb(c) for c in open_))
    picks = {int(p["no"]): p.get("after") for p in json.loads(re.search(r"\[.*\]", raw, re.S).group(0)) if "no" in p}
    filled = []
    for c in todo:               # **한 건씩 쓰고 다음 건을 거른다** — 몰아서 거르면 방금 생긴 고리를 놓친다
        got = core.clean_after(picks.get(c["no"]), c["no"], STATE["cards"])
        if got:
            c["after"] = got
            filled.append((c["no"], got))
    if filled:
        place()
        save()
    log(f"선행 채움 {len(filled)}/{len(todo)}건")
    return filled


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from flows.github import write_backlog  # noqa: E402,F401
from flows.status import place, record_change, redraw  # noqa: E402,F401


# ── 등록할 때 체크리스트를 미리 만들어 둔다 (2026-09-23 사장님: 「체크리스트 만드는건 할일만들때」) ──
CHECK_SYS = ("너는 팀의 프로젝트 비서다. 할 일 제목들을 받아 **각각 무엇을 해야 하는지** 체크리스트를 쓴다. "
             "항목은 「…하기」 로 끝나는 짧은 동사구로 2~4개. 제목에 없는 사실은 지어내지 않는다 — "
             "제목만으로 모르겠으면 그 제목의 items 를 빈 배열로 둔다. 「미정」 같은 못 체크할 말은 쓰지 않는다. "
             "반드시 JSON 한 개만 출력한다: {\"lists\":[{\"title\":\"받은 제목 그대로\",\"items\":[\"…하기\"]}]}")


async def checklists(titles):
    """제목들 → {제목: [할 거리]}. **한 번만 부른다** — 열 개를 올리면 열 번 부르면 안 된다.

    실패하면 빈 사전을 돌려준다. 등록이 AI 때문에 막히면 안 된다 (이 저장소의 약속) —
    체크리스트가 없어도 카드는 올라가고, 카드에 「✍️ 남은 것: 체크리스트」 라고 뜬다.
    """
    want = [t for t in (titles or []) if (t or "").strip()][:10]
    if not want:
        return {}
    try:
        raw = await ask_ai(CHECK_SYS, "다음 할 일마다 체크리스트를 써 줘.\n" + "\n".join(f"- {t}" for t in want))
        got = json.loads(re.search(r"\{.*\}", raw, re.S).group(0)).get("lists") or []
    except Exception as e:
        log(f"체크리스트 만들기 실패: {type(e).__name__}: {e}")
        return {}
    out, left = {}, list(want)
    for row in got:
        t = (row.get("title") or "").strip()
        hit = t if t in left else next((x for x in left if x.startswith(t[:12]) or t.startswith(x[:12])), None)
        items = core.clean_items(row.get("items"))[:4]     # 「미정」·글머리표는 여기서도 거른다
        if hit and items:
            out[hit] = items
            left.remove(hit)
    return out
