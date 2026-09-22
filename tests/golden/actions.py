"""동작 골든 (#30 3·4단계) — 버튼 · 반응 · 명령 · 창 제출을 차례로 누르고, 봇이 Slack 에 보낸 것과 최종 상태를 JSON 으로 낸다.

  MOA_DATA=tests/golden/data python3 tests/golden/actions.py > out.json

화면 골든(render.py)은 그리는 것만 본다. 이건 누른 뒤 무엇을 하는지 본다. 고친 뒤에도 한 글자도 같아야 한다.
Slack·GitHub·AI 는 부르지 않는다. 날짜·시각도 고정.
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import render  # noqa: E402  날짜 고정 · 이름 찾기 도우미를 같이 쓴다

PM, DEV, REQ = "U0EXAMPLEPM", "U0EXAMPLEDEV", "UREQUESTER"


def main():
    import bot as _bot  # noqa: F401
    try:
        import handlers  # noqa: F401
    except ImportError:
        pass
    bot = render.Bot()
    render.freeze()
    render.freeze_clock(render.Clock())
    sent = []

    async def fake_api(s, method, body=None, **kw):
        sent.append({"method": method, **({"body": body} if body else {}), **kw})
        if method == "conversations.replies":
            return {"messages": [{"ts": "x"}, {"user": PM, "text": "#10 은 9/26 으로 미루자"}]}
        if method == "users.info":
            return {"user": {"real_name": "요청자", "profile": {}}}
        if method == "users.list":
            return {"members": []}
        if method == "conversations.open":       # 담당 DM — 진짜 Slack 처럼 방 id 를 준다.
            return {"ok": True, "channel": {"id": "D0" + (body or {}).get("users", "")[-6:]}}
        return {"ok": True, "ts": f"{len(sent)}.5", "permalink": "https://x"}

    async def fake_ai(system, prompt):
        if "회의록" in system:
            return json.dumps({"decisions": ["일정 조정"], "action_items": [{"what": "재부팅 시험", "owner": "이동원", "due": "9/25", "issue": 10}],
                               "issue_changes": [{"issue": 10, "field": "목표일", "to": "2026-09-26", "reason": "이번 주 다른 일"}]}, ensure_ascii=False)
        if "일을 나누는" in system:
            return '[{"no": 99, "uid": "%s", "reason": "PM 영역"}]' % PM
        if "1~5 점수" in system:
            return '[{"no": 99, "value": 4, "urgency": 4, "goal_fit": 4, "effort": 2, "hours_min": 2, "hours_max": 3, "ai": "assist", "reason": "새 일"}]'
        return '{"reply": "끝났다고 볼 조건이 뭘까요?", "ready": false, "stop": false, "spec": null}'

    async def noop(*a, **k):
        pass

    trail = []

    async def record(name, *a, **k):
        trail.append(name)

    bot.api, bot.ask_ai, bot.save = fake_api, fake_ai, (lambda: None)
    bot.render_canvas = noop
    bot.export_github = lambda s, c, t=None: record("export", c["no"])
    import gh_link
    gh_link.save_meeting = lambda m, b, l: (m.__setitem__("file", "t.md"), "meetings/t.md")[1]
    gh_link.link_meeting_to_issue = lambda *a: None
    gh_link.add_actions_to_issue = lambda *a: None
    gh_link.commit = lambda *a: "시험"
    C = bot.STATE["cards"]
    by = {c["no"]: c for c in C.values()}
    ts = lambda n: by[n]["card_ts"]
    act = lambda aid, user=PM, **kw: bot.on_action(None, {"actions": [{"action_id": aid, **kw}], "user": {"id": user},
                                                           "trigger_id": "T", "message": {"ts": "M"}, "container": {}})
    react = lambda emoji, n, user: bot.on_event(None, {"type": "reaction_added", "reaction": emoji, "user": user,
                                                       "item": {"ts": ts(n)}}, "UBOT")
    submit = lambda cb, meta, values, user=PM: bot.on_view_submit(None, {"view": {"callback_id": cb, "private_metadata": meta,
                                                                                  "state": {"values": values}}, "user": {"id": user}})
    # 시험용 무대 — 요청자·담당이 다른 일 둘, 정리안이 있는 일 하나, 회의 하나
    by[10].update(assignee=DEV, assign_src="human", by_id=REQ, origin_channel=bot.REQUEST, origin_ts="777.1")
    by[11].update(assignee=DEV, assign_src="human", by_id=PM)
    by[12]["spec_draft"] = {"title": "권한 정리", "why": "w", "change": "c", "expect": "e", "not_doing": "n", "done_criteria": ["a", "b"]}
    by[12]["spec"] = {"why": "옛 왜", "done_criteria": ["옛"]}
    bot.STATE.setdefault("meetings", {})["MTS"] = {"id": "M9", "title": "주간 점검", "issue": "10", "by": "이동원",
                                                  "date": "2026-09-21", "card_ts": "MTS", "summary": None, "file": None}

    steps = [
        ("pull #5", lambda: act("pull_card", DEV, value=ts(5))),          # 창만 연다 (#52)
        # 맡기 전에 **언제까지**를 본인이 정한다 — 창을 띄우고 제출해야 담당이 확정된다
        ("pull #5 맡기", lambda: submit("take_submit", ts(5),
                                      {"due": {"v": {"selected_date": "2026-09-25"}},
                                       "how": {"v": {"value": "먼저 재현부터 해 볼게요"}}}, DEV)),
        ("⚙️ 우선순위 #5", lambda: act("set_prio", PM, block_id=f"card:{ts(5)}", selected_option={"value": "2"})),
        ("⚙️ 목표일 #5", lambda: act("set_due", PM, block_id=f"card:{ts(5)}", selected_date="2026-09-30")),
        ("이유 답글 #5", lambda: bot.on_event(None, {"type": "message", "channel": bot.CHANNEL, "thread_ts": ts(5),
                                                    "user": PM, "text": "고객 일정 때문"}, "UBOT")),
        ("⚙️ 담당 #6", lambda: act("set_assignee", PM, block_id=f"card:{ts(6)}", selected_user=DEV)),
        ("⚙️ 단계 #6", lambda: act("set_stage", PM, block_id=f"card:{ts(6)}", selected_option={"value": "PoC"})),
        ("👀 #10", lambda: react("eyes", 10, DEV)),
        ("체크 1개 #10", lambda: act("check_dc", DEV, block_id=f"dc:{ts(10)}", selected_options=[{"value": "0"}])),
        ("✅ 담당자 #10 → 확인 대기", lambda: react("white_check_mark", 10, DEV)),
        ("모르는 사람 확인 #10", lambda: act("review_ok", "UOTHER", value=ts(10))),
        ("더 필요해요 창 #10", lambda: act("review_back", REQ, value=ts(10))),
        ("더 필요해요 제출 #10", lambda: submit("review_back_submit", ts(10), {"why": {"v": {"value": "엑셀도"}}}, REQ)),
        ("다 체크 #10 → 확인 대기", lambda: act("check_dc", DEV, block_id=f"dc:{ts(10)}",
                                               selected_options=[{"value": str(i)} for i in range(len(by[10]["spec"]["done_criteria"]))])),
        ("요청자 확인 #10", lambda: act("review_ok", REQ, value=ts(10))),
        ("⛔ #11", lambda: react("no_entry", 11, DEV)),
        ("✅ PM #11 (바로 완료)", lambda: react("white_check_mark", 11, PM)),
        ("정리안 👍 #12", lambda: act("spec_ok", PM, value=ts(12))),
        ("✏️ 내용 수정 창 #12", lambda: act("edit_content", PM, value=ts(12))),
        ("✏️ 저장 #12", lambda: submit("edit_content_submit", ts(12), {"title": {"v": {"value": "권한 정리 2"}}, "why": {"v": {"value": "새 왜"}},
                                                                       "change": {"v": {"value": ""}}, "expect": {"v": {"value": ""}},
                                                                       "not_doing": {"v": {"value": ""}}, "done_criteria": {"v": {"value": "a\nc"}},
                                                                       "after": {"v": {"value": ""}},
                                                                       "reason": {"v": {"value": ""}}})),
        # 선행을 사람이 넣는다 (#68) — after_src=human 이 붙어야 AI 가 못 지운다.
        # #12 는 원래 after=[11] 이라 여기서 #14 로 바꾸는 것이 「사람이 고쳤다」 의 실제 모습이다
        ("✏️ 선행 넣기 #12", lambda: submit("edit_content_submit", ts(12), {"title": {"v": {"value": "권한 정리 2"}},
                                                                       "why": {"v": {"value": "새 왜"}}, "change": {"v": {"value": ""}},
                                                                       "expect": {"v": {"value": ""}}, "not_doing": {"v": {"value": ""}},
                                                                       "done_criteria": {"v": {"value": "a\nc"}},
                                                                       "after": {"v": {"value": "#14 #999"}},   # 없는 번호는 걸러진다
                                                                       "reason": {"v": {"value": "손으로 정함"}}})),
        ("📄 선행 넣은 뒤 상세 #12", lambda: act("show_md", PM, value=ts(12))),
        ("홈 ✏️ 창 #13", lambda: act("edit_card", PM, value=ts(13))),
        ("홈 ✏️ 저장 #13", lambda: submit("edit_card_submit", ts(13), {"s": {"status": {"selected_option": {"value": "doing"}}}})),
        ("위험 [미루기] #13", lambda: act("risk_fix", PM, value=f"delay:13:3")),
        ("위험 [알림] #5", lambda: act("risk_fix", PM, value="ask:5")),
        ("회의록 확정", lambda: act("finish_meeting", PM, value="MTS")),
        ("회의 [적용]", lambda: act("mtg_apply", PM, value="MTS|10|0")),
        ("회의록 보기", lambda: act("show_meeting", PM, value="MTS")),
        ("팝업 이번 주 진행", lambda: act("detail_timeline", PM, value="timeline")),
        ("📄 상세 #5", lambda: act("show_md", PM, value=ts(5))),
        ("앱 홈 열기", lambda: bot.on_event(None, {"type": "app_home_opened", "tab": "home", "user": REQ}, "UBOT")),
        ("🔄 작업판 새로고침", lambda: act("canvas_now", PM, value="now")),
        ("🔄 한 번 더 (같으면 안 씀)", lambda: act("canvas_now", PM, value="now")),
    ]

    out = {}

    async def run():
        for name, f in steps:
            sent.clear()
            trail.clear()
            await f()
            for _ in range(5):
                await asyncio.sleep(0)
            out[name] = {"sent": list(sent), "trail": list(trail)}
    asyncio.run(run())
    keep = ("status", "assignee", "assign_src", "plevel", "plevel_src", "due", "due_src", "stage", "checked", "spec", "title",
            "review_by", "review_msg", "edits", "finished")
    out["최종 상태"] = {f"#{c['no']}": {k: c.get(k) for k in keep} for c in sorted(C.values(), key=lambda c: c["no"])}
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
