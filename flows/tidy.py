"""이슈 정리 도우미 (#57) — 안 해도 될 일을 이유와 함께 골라 보여 준다.

**봇은 고르기만 한다. 지우는 건 사람이다** — 「AI는 제안까지, 누르는 건 사람」 (#11).
왜 골랐는지를 함께 보여 주는 게 핵심이다. 이유 없이 「정리하세요」 하면 판단할 수가 없다.

프로젝트 방에 올린다. 취소는 되돌리기 번거로워서 팀이 같이 보는 편이 낫고,
나에게만 보이는 글은 버튼을 누른 뒤 결과로 바꿔 주기가 어렵다.
"""
import core
from messages import say
from common import CHANNEL, log
from docs import load_stages, load_team
from slack import Step, api, mood
from store import STATE, chan, add_log, open_cards, ref, save


def rows(cards, stage_names):
    return core.tidy_candidates(cards, stage_names)


def blocks(found, cards=()):
    """한 줄에 하나씩 — 제목 · 왜 골랐나 · 버튼.

    버튼 뜻을 머리말에 한 줄로 적는다. 「취소·나중·그대로」 만 보면 무슨 차이인지 모른다 (사장님 지적 9/20).
    비슷한 이슈가 있으면 **합치기 버튼까지** 준다 — 골라 놓고 어떻게 하라는 말이 없으면 소용없다.
    """
    from views.card import link_of, state_line
    open_ = [x for x in cards if x.get("status") not in ("done", "cancelled")]
    out = [{"type": "section", "text": {"type": "mrkdwn", "text": say("tidy_head", n=len(found))}},
           {"type": "context", "elements": [{"type": "mrkdwn", "text": say("tidy_legend")}]}]
    for c, why in found[:10]:
        out.append({"type": "section",
                    "text": {"type": "mrkdwn", "text": f"{link_of(c, True)}\n{state_line(c)}\n_{' · '.join(why)}_"}})
        el = [{"type": "button", "text": {"type": "plain_text", "text": "📄 상세"},   # 안 보고 정하지 않게
               "action_id": "show_md", "value": c["card_ts"]}]
        if not (c.get("spec") or {}).get("done_criteria"):     # 내용이 없으면 판단 자체가 안 된다
            el.append({"type": "button", "text": {"type": "plain_text", "text": "✨ 정리해 줘"},
                       "action_id": "fill_spec", "value": c["card_ts"]})
        for o, _ in core.similar(c, open_)[:1]:        # 합치기는 가장 닮은 하나만 — 고르기 어려워지지 않게
            el.append({"type": "button", "text": {"type": "plain_text", "text": f"🔗 #{o['no']} 과 합치기"},
                       "action_id": "same_as", "value": f"{c['card_ts']}|{o['no']}"})
        el += [{"type": "button", "text": {"type": "plain_text", "text": "✖ 안 하기로"},
                "action_id": "tidy_drop", "value": c["card_ts"], "style": "danger"},
               {"type": "button", "text": {"type": "plain_text", "text": "💤 나중에"},
                "action_id": "tidy_later", "value": c["card_ts"]},
               {"type": "button", "text": {"type": "plain_text", "text": "✓ 계속 할 일"},
                "action_id": "tidy_keep", "value": c["card_ts"]}]
        out.append({"type": "actions", "block_id": f"tidy:{c['card_ts']}", "elements": el})
    if len(found) > 10:
        out.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"…외 {len(found) - 10}건"}]})
    return out[:48]


async def propose(s, channel=None, user=None):
    """`@PA 정리` — 정리할 것과 내용 없는 것을 **따로** 보여 준다.

    섞어 놓으면 목록만 길어지고 정작 정리할 것이 묻힌다 (2026-09-20: 17건 중 13건이 내용 없음이었다).
    정리는 「없앨까요」, 내용 없음은 「채울까요」 — 다른 질문이다.
    """
    step = Step(s, CHANNEL)
    await step.say(say("tidy_working"), **mood("생각"))
    cards = list(STATE["cards"].values())
    found = rows(cards, {x["name"] for x in load_stages()})
    empty = core.needs_spec(cards)
    # 순서가 빈 것 — 내용이 없는 이슈는 빼고 본다. 정의가 없으면 앞선 일도 찾을 수 없다 (실측: 제목만으로는 0건)
    no_after = [c for c in cards if c.get("status") not in ("done", "cancelled")
                and not c.get("after") and c not in empty]
    if not found and not empty and not no_after:
        await step.say(say("tidy_none"))
        return found
    back = [c for c in cards if c.get("status") not in ("done", "cancelled") and c.get("priority") == "later"]
    body = blocks(found, cards) if found else [
        {"type": "section", "text": {"type": "mrkdwn", "text": say("tidy_clean")}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": say("tidy_basis") + (say("tidy_skipped", n=len(back)) if back else "")}]}]
    if empty:
        body += [{"type": "divider"},
                 {"type": "section", "text": {"type": "mrkdwn", "text": say("empty_head", n=len(empty))},
                  "accessory": {"type": "button", "text": {"type": "plain_text", "text": "✨ 한 번에 채우기"},
                                "style": "primary", "action_id": "fill_all", "value": "go"}},
                 {"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(
                  ref(c["no"], 30) for c in empty[:12])
                  + (f"\n…외 {len(empty) - 12}건" if len(empty) > 12 else "")}]}]
    if no_after:
        body += [{"type": "divider"},
                 {"type": "section", "text": {"type": "mrkdwn", "text": say("after_head", n=len(no_after))},
                  "accessory": {"type": "button", "text": {"type": "plain_text", "text": "🔒 앞선 일 찾기"},
                                "action_id": "fill_after", "value": "go"}},
                 # 번호만 늘어놓으면 무슨 일인지 알 수가 없다 — 제목을 붙이고 한 줄에 하나씩 (사장님 지적 9/20)
                 {"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(
                  ref(c["no"], 30) for c in sorted(no_after, key=lambda c: c["no"])[:12])
                  + (f"\n…외 {len(no_after) - 12}건" if len(no_after) > 12 else "")}]}]
    await step.say(say("tidy_head", n=len(found)), blocks=body[:48])
    if channel and channel != CHANNEL and user:
        await api(s, "chat.postEphemeral", body={"channel": channel, "user": user,
                  "text": say("tidy_posted", n=len(found))})
    log(f"정리 제안 {len(found)}건")
    return found


async def fill_all(s, p, a):
    """[✨ 한 번에 채우기] — 내용 없는 이슈를 차례로 채운다. 하나씩 눌러야 하면 16건을 아무도 안 채운다."""
    empty = core.needs_spec(list(STATE["cards"].values()))
    step = Step(s, CHANNEL)
    await step.say(say("fill_all_start", n=len(empty)), **mood("생각"))
    ok = 0
    # 여기는 한 건씩 도니까 k/n 으로 진행을 보여 준다 — 경과 시간보다 낫다
    for c in empty:
        try:
            await refine(s, c, c["card_ts"])
            ok += 1
        except Exception as e:
            log(f"채우기 실패 #{c['no']}: {e}")
        await step.say(say("fill_all_at", k=ok, n=len(empty)))     # 몇 번째인지 보여 준다
    await step.say(say("fill_all_done", n=ok))
    log(f"한 번에 채우기 {ok}/{len(empty)}")


async def fill_after_all(s, p, a):
    """[🔒 앞선 일 찾기] — 선행이 빈 이슈에 「무엇이 먼저 끝나야 하나」 를 채운다 (#68).

    점수는 건드리지 않는다 — 다시 매기면 순서가 흔들린다. 없는 번호·끝난 일·고리는 core 가 거른다.
    """
    todo = [c for c in STATE["cards"].values() if c["status"] not in ("done", "cancelled") and not c.get("after")]
    step = Step(s, CHANNEL)
    async with step.waiting(say("after_all_start", n=len(todo)), say("after_all_step2"),
                            say("after_all_step3"), say("after_all_step4")):   # 단계를 바꿔 보여 준다
        filled = await fill_after()
    if not filled:
        await step.say(say("after_all_none"))
        return
    await step.say(say("after_all_done", n=len(filled)), blocks=[
        {"type": "section", "text": {"type": "mrkdwn", "text": say("after_all_done", n=len(filled))}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(
            f"{ref(no, 16)} ← {' '.join('#%d' % x for x in aft)}" for no, aft in filled[:12])}]}])
    for no, _ in filled:
        c = next((x for x in STATE["cards"].values() if x["no"] == no), None)
        if c:
            await redraw(s, c)


async def order(s, channel=None, user=None):
    """`@PA 순서` — 선행을 한 화면에 모으고 **그 자리에서 고치게** 한다 (#68).

    AI 가 넣은 선행도 그대로 쓰이지만, 틀린 것을 고치려면 이슈마다 상세 → 수정으로
    들어가야 했다 — 15건이면 45번을 눌러야 한다. 한 줄에 하나씩 놓고 옆에 고치기를 단다.
    """
    step = Step(s, CHANNEL)
    await step.say(say("order_working"), **mood("생각"))
    rows = sorted(((c, core.relations(c, STATE["cards"])[0]) for c in open_cards()),
                  key=lambda t: t[0]["no"])
    has = [(c, w) for c, w in rows if w]
    if not has:
        await step.say(say("order_none"))
        return
    from views.card import link_of
    body = [{"type": "section", "text": {"type": "mrkdwn", "text": say("order_head", n=len(has))}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": say("order_legend")}]}]
    for c, w in has[:14]:                      # 칸이 50개까지라 넉넉히 남겨 둔다
        mark = " ✋" if c.get("after_src") == "human" else ""
        body.append({"type": "section",
                     "text": {"type": "mrkdwn",
                              "text": f"{link_of(c, True, ref(c['no'], 30, link=False))}{mark}\n"
                                      + "🔒 " + "   ".join(f"{ref(x['no'], 20)}" for x in w)},
                     "accessory": {"type": "button", "text": {"type": "plain_text", "text": "✏️ 고치기"},
                                   "action_id": "edit_content", "value": c["card_ts"]}})
    if len(has) > 14:
        body.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"…외 {len(has) - 14}건"}]})
    await step.say(say("order_head", n=len(has)), blocks=body[:48])
    if channel and channel != CHANNEL and user:
        await api(s, "chat.postEphemeral", body={"channel": channel, "user": user, "text": say("order_posted")})
    log(f"순서 보기 {len(has)}건")


async def decide(s, p, a):
    """[✖ 취소] [💤 나중] [✓ 그대로] — 누른 줄만 결과로 바꾼다. 다른 줄은 그대로 남는다."""
    c = STATE["cards"].get(a.get("value"))
    if not c:
        return
    kind = a.get("action_id")
    who = load_team().get(p["user"]["id"], {}).get("name", "누군가")
    if kind == "tidy_drop":
        c["cancel_reason"] = f"정리에서 취소 · {who}"
        await apply_change(s, c, "set_status", "cancelled", p["user"]["id"], why="정리 제안에서 취소")
        done = say("tidy_dropped", ref=ref(c["no"], 24), who=who)
    elif kind == "tidy_later":
        c["priority"], c["prio_src"], c["prio_reason"] = "later", "human", f"정리에서 나중으로 · {who}"
        c["no_auto"] = True                            # 사람이 직접 가져갈 때까지 자동 배정하지 않는다
        await post_log(s, c, add_log(c, "나중으로", who, "정리 제안", icon="💤"))
        await redraw(s, c)
        done = say("tidy_later_done", ref=ref(c["no"], 24), who=who)
    else:
        c["tidy_keep"] = True                          # 다음 정리에서 다시 묻지 않는다
        await post_log(s, c, add_log(c, "그대로 두기로", who, "정리 제안", icon="✓"))
        done = say("tidy_kept", ref=ref(c["no"], 24), who=who)
    save()
    await _mark(s, p, a.get("value"), done)
    log(f"정리 {kind} #{c['no']} ← {who}")


async def _mark(s, p, ts, done):
    """누른 줄을 결과로 바꾼다 — 나머지 줄은 손대지 않는다."""
    out, skip = [], False
    for b in p.get("message", {}).get("blocks") or []:
        if b.get("type") == "actions" and (b.get("block_id") or "") == f"tidy:{ts}":
            out[-1] = {"type": "context", "elements": [{"type": "mrkdwn", "text": done}]}
            skip = True
            continue
        out.append(b)
    if skip:
        await api(s, "chat.update", body={"channel": chan(c), "ts": p["message"]["ts"],
                                          "text": done, "blocks": out[:48]})


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from ai import fill_after, refine  # noqa: E402,F401
from flows.status import apply_change, post_log, redraw  # noqa: E402,F401
