"""요청 → 이슈 카드 (#30 에서 bot.py 를 나눔)."""
import asyncio, json, re
import core
import gh_link
from messages import say
from common import CHANNEL, PROJECTS, BOT, HANDLE, HERE, log  # noqa: E402,F401
from docs import current_stage, load_team  # noqa: E402,F401
from slack import Step, api, mood, name_of  # noqa: E402,F401
from store import STATE, chan, project_for, add_log, decision_snap, ref, save  # noqa: E402,F401


async def add_issue(s, title, user, project=None, assignee=None, due=None, ask=True):
    """요청 메시지 없이 할 일 카드만 만든다 — DM 등록·관리자·봇이 직접 올릴 때.

    `project` 를 주면 그 프로젝트로 (#70). 안 주면 첫 프로젝트 — 프로젝트가 하나면 늘 그것이다.
    `assignee`·`due` 는 **사람이 이미 말한 것**이다 (DM 등록의 ③④번 칸). 주면 AI 에게 다시
    묻지 않는다 — 물어서 받은 답을 두고 AI 에게 추천을 시키면 그게 더 이상하다.
    `ask=False` 면 카드를 만든 뒤 AI 가 이어서 캐묻지 않는다 (완료 조건은 카드 버튼으로 —
    2026-09-22 사장님이 정함).
    만든 카드를 돌려준다 (못 만들었으면 None).
    """
    room = next((p.get("channel") for p in PROJECTS if p.get("key") == project), CHANNEL) if project else CHANNEL
    return await new_card(s, {"text": title, "user": user}, room, assignee=assignee, due=due, ask=ask)


async def new_card(s, m, origin_channel=None, spec=None, number=None, assignee=None, due=None, ask=True):
    """요청을 이슈 카드로 만든다. 카드는 언제나 프로젝트 방에, 링크는 요청이 온 자리에.
    spec 을 주면 정의를 붙인 채로 만든다 — 그러면 봇이 다시 묻지 않는다 (초안 → 버튼 경로, #54).
    만든 카드를 돌려준다.

    **번호는 GitHub 에서 받아 온다** (#60). 못 받으면 카드를 만들지 않는다 — 로컬 번호로 넘어가면
    다음에 받아 온 번호와 겹치고, 그게 오늘 아침 59개를 손으로 맞추게 한 원인이다.
    """
    origin_channel = origin_channel or CHANNEL
    proj = project_for(origin_channel)            # 요청이 온 방이 어느 프로젝트인가 (#70)
    title = re.sub(r"^(🎫|:ticket:)\s*", "", m.get("text", "")).strip().splitlines()[0][:60] or "(제목 없음)"
    no, url = number or STATE["next"], None
    if gh_link.REPO and not number:                    # number 를 주면 이미 GitHub 에 있는 이슈다 (되가져오기)
        try:
            no, url = await asyncio.get_running_loop().run_in_executor(
                None, gh_link.reserve, title, proj.get("repo"))
        except Exception as e:
            log(f"번호 받기 실패: {e}")
            if m.get("ts"):
                await api(s, "chat.postMessage", body={"channel": origin_channel, "thread_ts": m["ts"],
                          "text": say("no_number", err=str(e)[:120])})
            return None
    c = {"no": no, "title": title, "by": await name_of(s, m), "origin_ts": m.get("ts"),
         "origin_channel": origin_channel, "status": "todo", "assignee": assignee, "spec": spec,
         # 사람이 말한 것은 `human` 이다 — AI 가 정한 것과 섞으면 「AI 배정 수락률」 이 거짓이 된다
         "assign_src": "human" if assignee else None,
         **({"due": due, "due_src": "human"} if due else {}),
         "spec_src": "ai" if spec else None, "coach": "done" if spec else None,
         "request": m.get("text", ""), "stage": current_stage(), "project": proj.get("key"),
         "by_id": m.get("user")}
    if url:                                            # GitHub 이 준 번호 — 짝을 바로 적어 둔다
        c["gh_no"], c["issue_url"], c["tracker"] = no, url, f'{proj.get("repo") or gh_link.REPO}#{no}'
    STATE["next"] = max(STATE["next"], no + 1)
    d = await api(s, "chat.postMessage", body={"channel": chan(c), "text": f"🎫 #{c['no']} {title}",
                                               "blocks": card_blocks(c)})
    c["card_ts"] = d["ts"]
    STATE["cards"][d["ts"]] = c
    await api(s, "chat.update", body={"channel": chan(c), "ts": c["card_ts"],   # 버튼에 카드 ts 를 채운다
              "text": f"🎫 #{c['no']} {title}", "blocks": card_blocks(c)})
    link = (await api(s, "chat.getPermalink", channel=chan(c), message_ts=d["ts"])).get("permalink", "")
    if m.get("ts"):                          # 요청 메시지가 있을 때만 그 자리에 링크 (직접 등록은 카드만)
        await api(s, "chat.postMessage", body={"channel": origin_channel, "thread_ts": m["ts"], "unfurl_links": False,
                  "text": say("card_made", link=link, no=c["no"])})
    snap = decision_snap()
    try:                                   # 담당 없음을 남기지 않는다 — 만들자마자 추천·순서까지
        if not assignee:                   # **물어서 받은 답을 두고 다시 추천하지 않는다**
            await recommend(s, [c])
        await prioritize(s)
        await api(s, "chat.update", body={"channel": chan(c), "ts": c["card_ts"],
                  "text": f"🎫 #{c['no']} {c['title']}", "blocks": card_blocks(c)})
        await note_decisions(s, snap, skip={c["no"]})     # 새 카드 때문에 다른 카드가 밀렸으면 그 카드에 남긴다
    except Exception as e:
        log(f"담당 추천 실패 #{c['no']}: {e}")
    await ask_similar(s, c)                   # 혹시 같은 일인가요? (#57 — 만들 때 막는다)
    await ensure_ctl(s, c)                    # 스레드 첫 메시지 — ⚙️ 담당·우선순위·목표일
    if ask:                                   # 이어서 빠진 것만 묻는다 (#31).
        # **한 칸씩 물어 올린 할 일에는 캐묻지 않는다** (2026-09-22 사장님이 정함) — 방금
        # 세 가지를 답하셨는데 AI 가 또 물으면 같은 대화를 두 번 하는 셈이다.
        # 완료 조건은 그 할 일의 [✨ 정리해 줘] 를 누를 때 채운다
        asyncio.create_task(coach(s, c, "new"))
    await render_canvas(s)
    save()
    log(f"카드 #{c['no']} 생성" + (f" (GitHub {c['tracker']})" if url else ""))
    return c


DRAFT_SYS = ("너는 Slack 대화를 이슈 정의로 정리하는 PM 보조다. 대화에 있는 사실만 쓴다. "
             "추측은 쓰지 않고 모르면 '미정'이라고 쓴다. "
             "**서로 다른 일이 섞여 있으면 나눈다** — 억지로 하나로 합치지 않는다. "
             "사람이 「N개로 나눠줘」 라고 하면 그대로 나눈다. "
             "반드시 JSON 한 개만 출력한다: {\"issues\": [ {…}, … ]}. "
             "각 항목의 키: title(40자 이내), why, change, expect, not_doing, done_criteria(문자열 배열, 1~4개). "
             "모든 값은 한국어 한두 문장.")


def _specs(raw):
    """AI 답에서 초안 목록을 뽑는다. 예전처럼 하나만 준 경우도 받는다."""
    d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    got = d.get("issues") if isinstance(d.get("issues"), list) else [d]
    return [x for x in got if isinstance(x, dict)][:5] or [{}]      # 한 번에 5건까지


def _keys(ch, ts):
    """그 스레드의 초안 키들. 옛 모양(`채널:시각`, 번호 없음)도 받는다 —
    키 모양을 바꾸면서 이미 떠 있던 초안이 미아가 됐다 (2026-09-20)."""
    want = f"{ch}:{ts}"
    return [k for k in (STATE.get("drafts") or {}) if k == want or k.startswith(want + "#")]


async def _clear(s, ch, ts):
    """그 스레드의 옛 초안을 지운다 — 다시 정리하면 옛 단추가 남아 있으면 안 된다."""
    for k in _keys(ch, ts):
        d = STATE["drafts"].pop(k)
        if d.get("msg"):
            await api(s, "chat.update", body={"channel": ch, "ts": d["msg"], "text": say("draft_redone"),
                      "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": say("draft_redone")}]}]})


async def _show(s, ch, ts, specs, by):
    """초안들을 보여 준다. 모자란 것은 단추 없이, 갖춰진 것만 만들기 단추를 준다."""
    if len(specs) == 1 and core.vague({"spec": specs[0]}) >= 5:
        # 아는 게 하나도 없으면 빈 칸(❓ 네 개)을 보여 주지 않는다 — 무엇을 써야 하는지도 안 알려 준다
        STATE.setdefault("drafts", {})[f"{ch}:{ts}#0"] = {"spec": specs[0], "channel": ch, "ts": ts, "by": by, "msg": None}
        save()
        await api(s, "chat.postMessage", body={"channel": ch, "thread_ts": ts, "text": say("what_is_it"), **mood("생각")})
        log("초안 만들 거리가 없음")
        return
    if len(specs) > 1:
        await api(s, "chat.postMessage", body={"channel": ch, "thread_ts": ts,
                  "text": say("draft_split", n=len(specs)), **mood("생각")})
    for i, spec in enumerate(specs):
        key = f"{ch}:{ts}#{i}"
        rough = core.vague({"spec": spec}) >= 3            # 모자라면 단추를 주지 않는다 — 빈 이슈가 GitHub 에 남는다
        body = {"channel": ch, "thread_ts": ts, "unfurl_links": False,
                **({"text": say("rough_head"), "blocks": rough_blocks(spec)} if rough else
                   {"text": say("draft_ready", title=spec.get("title", "")), "blocks": draft_blocks(key, spec)}),
                **mood("생각" if rough else "부탁")}
        d = await api(s, "chat.postMessage", body=body)
        STATE.setdefault("drafts", {})[key] = {"spec": spec, "channel": ch, "ts": ts, "by": by,
                                               "msg": None if rough else d.get("ts")}
    save()
    log(f"이슈 초안 {len(specs)}건 제안 {ch}:{ts}")


async def propose_issue(s, e, q):
    """`@PA 이슈로 만들어줘` — **바로 만들지 않는다.** 초안을 보여 주고 사람이 눌러야 번호가 나온다.

    말만으로 이슈가 생기면 잡담이 이슈가 되고, 다른 팀이 쓰면 더 심해진다.
    「AI는 제안까지, 누르는 건 사람」 (#11). 초안 만들기는 읽기라서 AI가 바로 한다.
    **서로 다른 일이면 나눠서 낸다** — 억지로 하나로 합치면 사람이 다시 쪼개야 한다 (사장님 지적 9/20).
    """
    ch, ts = e["channel"], e.get("thread_ts") or e["ts"]
    step = Step(s, ch, ts)
    await step.say(say("drafting"), **mood("생각"))
    try:
        talk = await thread_text(s, ch, ts) or q
        raw = await ask_ai(DRAFT_SYS, "다음 Slack 대화를 이슈 정의로 정리해줘. 요청한 말은: "
                           + q[:200] + "\n\n대화:\n" + talk[:4000])
        specs = _specs(raw)
    except Exception as err:
        log(f"초안 실패: {err}")
        await step.say(say("draft_fail", err=str(err)[:120]))
        return
    await step.drop()                                  # 「읽는 중」 을 치우고 정리안으로
    await _clear(s, ch, ts)
    await _show(s, ch, ts, specs, e.get("user"))


async def refresh_draft(s, e):
    """초안 스레드에 사람이 더 쓰면 **다시 정리한다** (#54).

    초안을 내놓고 답글을 받지 않으면 대화가 끊긴다 (2026-09-20). 「2개로 나눠줘」 처럼
    나누라는 말도 여기로 온다 — 그래서 고쳐 쓰는 대신 다시 정리하고 옛 초안을 접는다.
    """
    ch, ts = e["channel"], e.get("thread_ts")
    if not _keys(ch, ts):
        return False
    by = next(iter(STATE["drafts"][k].get("by") for k in _keys(ch, ts)), None)
    step = Step(s, ch, ts)
    await step.say(say("drafting"), **mood("생각"))
    try:
        talk = await thread_text(s, ch, ts)
        raw = await ask_ai(DRAFT_SYS, "다음 Slack 대화를 이슈 정의로 정리해줘. 마지막 말을 특히 따른다.\n\n대화:\n" + talk[:4000])
        specs = _specs(raw)
    except Exception as err:
        log(f"초안 고치기 실패: {err}")
        await step.say(say("draft_fail", err=str(err)[:120]))
        return True
    await step.drop()
    await _clear(s, ch, ts)
    await _show(s, ch, ts, specs, by)
    return True


async def thread_text(s, ch, ts):
    """그 스레드의 사람 말만 모은다. 스레드가 아니면 그 메시지 하나."""
    msgs = (await api(s, "conversations.replies", channel=ch, ts=ts, limit=100)).get("messages", [])
    out = []
    for m in msgs:
        if m.get("bot_id"):
            continue
        out.append(f"{await name_of(s, m)}: {re.sub(r'<@U[A-Z0-9]+>', '@봇', m.get('text', ''))}")
    return "\n".join(out)


async def make_from_draft(s, p, a):
    """[이슈로 만들기] — 여기서 처음으로 번호가 나온다. 초안은 한 번만 쓴다.

    프로젝트가 여럿이면 단추 값이 `초안키|프로젝트` 다 — 팀 대화방은 함께 쓰는 자리라
    방만 보고는 알 수 없어서 **누르는 사람이 고른다** (#70).
    """
    val = a.get("value") or ""
    key, _, pick = val.partition("|")
    d = (STATE.get("drafts") or {}).pop(key, None)
    msg = p.get("message", {})
    if not d:
        await api(s, "chat.postEphemeral", body={"channel": p["channel"]["id"], "user": p["user"]["id"],
                  "text": say("draft_gone")})
        return
    spec = d["spec"]
    who = p["user"]["id"]
    room = next((x.get("channel") for x in PROJECTS if x.get("key") == pick), None) if pick else None
    c = await new_card(s, {"text": spec.get("title") or "(제목 없음)", "user": who, "ts": d["ts"]},
                       room or d["channel"], spec=spec)
    if not c:                                   # 번호를 못 받았다 — 초안은 되돌려 둔다
        STATE.setdefault("drafts", {})[key] = d
        save()
        return
    save()
    await api(s, "chat.update", body={"channel": d["channel"], "ts": msg.get("ts"),
              "text": say("draft_made", no=c["no"]),
              "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": say("draft_made", no=c["no"])}]}]})
    log(f"초안 → 카드 #{c['no']} ({who})")


async def drop_draft(s, p, a):
    """[아니요] — 초안을 버린다. 아무것도 안 만든다."""
    (STATE.get("drafts") or {}).pop(a.get("value"), None)
    save()
    await api(s, "chat.update", body={"channel": p["channel"]["id"], "ts": p.get("message", {}).get("ts"),
              "text": say("draft_dropped"),
              "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": say("draft_dropped")}]}]})


async def ask_similar(s, c):
    """비슷한 열린 이슈가 있으면 스레드에 한 번 물어본다. 없으면 아무 말도 하지 않는다 (#57)."""
    hits = core.similar(c, list(STATE["cards"].values()))
    if not hits:
        return
    await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"], "unfurl_links": False,
              "text": say("similar_ask", n=len(hits)), "blocks": similar_blocks(c, hits), **mood("생각")})
    log(f"비슷한 이슈 물음 #{c['no']} ↔ {[o['no'] for o, _ in hits]}")


async def same_as(s, p, a):
    """[같은 일이에요] — 새로 만든 쪽을 취소하고 두 이슈를 서로 잇는다. 사유가 기록에 남는다."""
    ts, _, other = a.get("value", "").partition("|")
    c = STATE["cards"].get(ts)
    o = next((x for x in STATE["cards"].values() if x["no"] == int(other or 0)), None)
    if not c or not o:
        return
    who = load_team().get(p["user"]["id"], {}).get("name", "누군가")
    c["duplicate_of"] = o["no"]
    c["cancel_reason"] = f"#{o['no']} 과 같은 일"
    await apply_change(s, c, "set_status", "cancelled", p["user"]["id"], why=f"#{o['no']} 과 같은 일")
    await post_log(s, o, add_log(o, f"{ref(c['no'], 20)} 을 같은 일로 합침", who, icon="🔗"))
    await api(s, "chat.update", body={"channel": CHANNEL, "ts": p["message"]["ts"],
              "text": say("same_done", ref=ref(o["no"], 24), who=who),
              "blocks": [{"type": "context", "elements": [{"type": "mrkdwn",
                          "text": say("same_done", ref=ref(o["no"], 24), who=who)}]}]})
    save()
    log(f"합침 #{c['no']} → #{o['no']}")


async def merge_into(s, c, other_no, e):
    """`@PA 합치기 #57` — 이 카드를 저 이슈로 합친다 (#57).

    낱말 겹침은 사람이 아는 것을 못 잡는다 — #7 「정리할 때 비슷한 이슈를 연결하기」 와
    #57 「이슈 정리 도우미」 는 겹치는 낱말이 하나도 없었다 (2026-09-20). 그래서 손으로 잇는 길을 둔다.
    """
    o = next((x for x in STATE["cards"].values() if x["no"] == other_no), None)
    if not o or o["no"] == c["no"]:
        await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"],
                  "text": say("merge_no", no=other_no)})
        return
    who = await name_of(s, e)
    c["duplicate_of"], c["cancel_reason"] = o["no"], f"#{o['no']} 과 같은 일"
    await apply_change(s, c, "set_status", "cancelled", e.get("user"), why=f"#{o['no']} 과 같은 일")
    await post_log(s, o, add_log(o, f"{ref(c['no'], 20)} 을 같은 일로 합침", who, icon="🔗"))
    await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"],
              "text": say("same_done", ref=ref(o["no"], 24), who=who)})
    save()
    log(f"합침(손으로) #{c['no']} → #{o['no']} ← {who}")


async def not_same(s, p, a):
    """[다른 일이에요] — 묻는 말을 지운다. 아무것도 바꾸지 않는다."""
    await api(s, "chat.update", body={"channel": CHANNEL, "ts": p["message"]["ts"],
              "text": say("not_same_done"),
              "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": say("not_same_done")}]}]})


async def show_md(s, c, thread_ts):
    """정본 md 파일을 Slack 안에서 그대로 보여 준다. GitHub 로그인이 필요 없다."""
    path = HERE / "issues" / (c.get("file") or "")
    if not path.exists():
        await api(s, "chat.postMessage", body={"channel": CHANNEL, "thread_ts": thread_ts,
                  "text": say("no_md", bot=HANDLE)})
        return
    body = md_for_slack(path)
    await api(s, "chat.postMessage", body={"channel": CHANNEL, "thread_ts": thread_ts,
              "text": f"📄 issues/{c['file']}", "unfurl_links": False, "unfurl_media": False,
              "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": f"📄 `issues/{c['file']}` · 정본"}]},
                         {"type": "section", "text": {"type": "mrkdwn", "text": body}}]})



async def confirm_spec(s, c, user):
    """정리안 [👍 이대로] — 이슈 정의로 확정. 있던 정의가 바뀌면 변경 이력."""
    sp = c.pop("spec_draft")
    old_title, old_spec = c["title"], dict(c.get("spec") or {})
    c["title"] = (sp.get("title") or c["title"])[:80]
    if old_spec:                                   # 처음 정리는 이력이 아니다 — 있던 정의가 바뀔 때만
        c["spec"] = sp
        await record_change(s, c, user, old_title, old_spec, "스레드 대화 참고", how="대화로 정리 → 👍")
    c["spec"], c["spec_src"], c["coach"] = sp, "coach", "done"
    if c.get("draft_ts"):
        await api(s, "chat.update", body={"channel": CHANNEL, "ts": c["draft_ts"], "text": "확정",
                  "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": say("spec_ok")}]}]})
    await redraw(s, c)

# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from ai import ask_ai, coach, prioritize, recommend  # noqa: E402,F401
from flows.status import apply_change, post_log, ensure_ctl, note_decisions, record_change, redraw  # noqa: E402,F401
from views.canvas import render_canvas  # noqa: E402,F401
from views.card import draft_blocks, rough_blocks, similar_blocks, card_blocks, md_for_slack  # noqa: E402,F401
