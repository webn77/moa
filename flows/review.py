"""확인 대기 — 담당자가 끝내면 팀 대화방에서 요청자·PM 을 태그하고, [확인했어요] · [더 필요해요] 로 닫거나 되돌린다 (#30 에서 status 에서 나눔)."""
import datetime
import core
from messages import say
from common import CHANNEL, REQUEST  # noqa: E402,F401
from docs import load_team  # noqa: E402,F401
from slack import api, mood, person  # noqa: E402,F401
from store import STATE, chan, add_log, plan_result, progress, ref, req, save  # noqa: E402,F401


async def request_review(s, c, user, who=None):
    """확인 대기 — **확인할 사람에게 DM 으로** 부탁한다 (2026-09-21 사장님 결정).

    예전에는 팀 대화방에 올렸다. 「봐 주세요」 는 **한 사람에게 하는 부탁**인데 팀 전체가 봤고,
    누른 뒤 그 자리가 결과로 바뀌면서 팀 방이 「확인했어요」 로 줄줄이 찼다.
    **끝났다는 소식은 팀 방에 그대로 남는다** — 아래 `review_line` 이 그 몫이다.

    who 를 주면 그 사람에게 — AI가 일하고 사람이 검증할 때는 담당자가 곧 확인자일 수 있다.
    """
    team = load_team()
    who = who or core.confirmer(c, team)
    c["review_by"], c["review_day"] = who, datetime.date.today().isoformat()
    k, n = progress(c)
    detail = " · ".join(x for x in [f"완료 조건 {k}/{n}" if n else "", plan_result(c)] if x)
    text = say("review_ask", uid=who, ref=ref(c["no"], 30), detail=detail)
    body = {"channel": who, "text": text, "blocks": review_blocks(c, text), "unfurl_links": False}
    d = await api(s, "chat.postMessage", body={**body, **mood("부탁")})
    if not d.get("ok"):          # DM 을 못 열면 **부탁이 통째로 사라진다** — 팀 대화방으로 떨어뜨린다
        body["channel"] = req(c)
        d = await api(s, "chat.postMessage", body={**body, **mood("부탁")})
    # **어디에 올렸는지 같이 남긴다** — 누른 뒤 그 자리를 결과로 바꿔야 하는데, 채널이 없으면 못 찾는다
    c["review_msg"] = {"ts": d.get("ts"), "thread": None, "channel": d.get("channel") or body["channel"]}
    add_log(c, "확인 대기", team.get(user, {}).get("name", "누군가"), icon="🔍")
    await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"], "reply_broadcast": True,
              "text": say("review_line", ref=ref(c["no"], 24), who=await person(s, who))})
    await ensure_ctl(s, c)


async def review_answer(s, c, user, ok, why=None, trigger=None):
    """[확인했어요] → 완료 · [더 필요해요] → 진행 중 + 무엇이 더 필요한지. 팀 대화방 글의 버튼 자리를 결과로 바꾼다."""
    team = load_team()
    if c["status"] != "review":
        return
    if user not in core.approvers(c, team) and user != c.get("review_by"):   # 지정된 확인자는 누를 수 있다
        # 부탁이 DM 으로 가면 남이 누를 일이 없다 — 팀 대화방으로 떨어진 경우에만 걸린다
        await api(s, "chat.postEphemeral", body={"channel": (c.get("review_msg") or {}).get("channel") or req(c),
                  "user": user, "text": say("review_not_you", uid=c.get("review_by") or "")})
        return
    name = await person(s, user)
    if ok:
        await apply_change(s, c, "set_status", "done", user)
        done = say("review_done", who=name)
    else:
        await apply_change(s, c, "set_status", "doing", user)
        add_log(c, "더 필요 — 다시 진행 중", name, why or None, icon="↩️")
        done = say("review_back_done", who=name, why=why or "스레드에서 이야기해요")
        if c.get("assignee"):
            await api(s, "chat.postMessage", body={"channel": chan(c), "thread_ts": c["card_ts"],
                      "text": say("review_back", uid=c["assignee"], who=name, why=why or "자세한 건 팀 대화방 스레드에")})
    rm = c.get("review_msg") or {}
    if rm.get("ts"):
        # 옛 카드에는 채널이 안 적혀 있다 — 그때는 팀 대화방이었다
        await api(s, "chat.update", body={"channel": rm.get("channel") or req(c), "ts": rm["ts"], "text": done,
                  "blocks": [{"type": "context", "elements": [{"type": "mrkdwn", "text": f"{ref(c['no'], 30)} — {done}"}]}]})
    c.pop("review_by", None)
    save()


async def remind_reviews(s):
    """아침 — 하루 넘게 확인을 기다리는 일은 팀 대화방에서 한 번 더 부드럽게."""
    today = datetime.date.today().isoformat()
    for c in STATE["cards"].values():
        rm = c.get("review_msg") or {}
        if c["status"] == "review" and c.get("review_by") and (c.get("review_day") or today) < today \
                and c.get("review_pinged") != today and rm.get("ts"):
            await api(s, "chat.postMessage", body={"channel": req(c), "thread_ts": rm.get("thread") or rm["ts"],
                      "text": say("review_remind", uid=c["review_by"], ref=ref(c["no"], 30))})
            c["review_pinged"] = today
    save()


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from flows.status import apply_change, ensure_ctl  # noqa: E402,F401
from views.card import review_blocks  # noqa: E402,F401
