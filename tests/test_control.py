"""작업 보고 통로 (#56) — 도구(Claude·스크립트)가 봇에게 맡기는 한 줄 명령."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
import pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import flows.control as control  # noqa: E402
import store  # noqa: E402


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.card = {"no": 900, "title": "시험 이슈", "status": "todo", "card_ts": "T900",
                     "spec": {"why": "w", "done_criteria": ["가", "나", "다"]}}
        store.STATE["cards"]["T900"] = self.card
        self.calls = []

        async def fake_check(s, c, options, who, why=None):
            c["checked"] = [c["spec"]["done_criteria"][int(o["value"])] for o in options]
            self.calls.append(("check", who, why, list(c["checked"])))

        async def fake_log(s, c, e, ask_uid=None):
            self.calls.append(("log", e["what"], e.get("reason")))

        async def fake_redraw(s, c):
            pass
        async def no_export(s, c, thread_ts=None):
            pass
        # save 는 import 될 때 각 모듈 이름으로 복사된다 — store 것만 막으면 시험 카드가 진짜 파일에 남는다
        self.old = (control.check_criteria, control.post_log, control.redraw,
                    store.save, control.save, control.export_github)
        control.check_criteria, control.post_log, control.redraw = fake_check, fake_log, fake_redraw
        store.save = control.save = lambda: None
        control.export_github = no_export

    def tearDown(self):
        (control.check_criteria, control.post_log, control.redraw,
         store.save, control.save, control.export_github) = self.old
        store.STATE["cards"].pop("T900", None)

    def test_check_one_criterion_with_evidence(self):
        """근거(커밋)를 함께 남기고, 이미 체크한 것은 그대로 둔다."""
        out = asyncio.run(control.do_check(None, {"no": 900, "item": 2, "why": "커밋 abc1234"}))
        self.assertIn("2번 체크", out)
        self.assertEqual(self.card["checked"], ["나"])
        self.assertEqual(self.calls[0][2], "커밋 abc1234")
        asyncio.run(control.do_check(None, {"no": 900, "item": 1}))
        self.assertEqual(self.card["checked"], ["가", "나"])                       # 앞서 체크한 것 유지

    def test_check_twice_is_noop(self):
        asyncio.run(control.do_check(None, {"no": 900, "item": 1}))
        out = asyncio.run(control.do_check(None, {"no": 900, "item": 1}))
        self.assertIn("이미", out)

    def test_unknown_issue_or_item(self):
        self.assertIn("못 찾음", asyncio.run(control.do_check(None, {"no": 999, "item": 1})))
        self.assertIn("못 찾음", asyncio.run(control.do_check(None, {"no": 900, "item": 9})))

    def test_note(self):
        out = asyncio.run(control.do_note(None, {"no": 900, "text": "중간 보고"}))
        self.assertIn("메모", out)
        self.assertEqual(self.calls[-1][1], "중간 보고")

    def test_spec_keeps_human_written(self):
        """사람이 쓴 정의는 도구가 덮어쓰지 않는다."""
        self.card["spec_src"] = "human"
        out = asyncio.run(control.do_spec(None, {"no": 900, "why": "새 정의", "done": ["x"]}))
        self.assertIn("두었음", out)
        self.assertEqual(self.card["spec"]["done_criteria"], ["가", "나", "다"])

    def test_bad_line_does_not_stop_the_queue(self):
        """한 줄이 잘못돼도 다음 줄은 처리된다 — ask.done.jsonl 에 결과가 남는다."""
        with tempfile.TemporaryDirectory() as d:
            control.ASK = pathlib.Path(d) / "ask.jsonl"
            control.ASK.write_text('{"do": "없는명령"}\n{"do": "note", "no": 900, "text": "둘째 줄"}\n', encoding="utf-8")

            async def once():
                task = asyncio.create_task(control.watch_asks(None, every=0.05))
                await asyncio.sleep(0.3)
                task.cancel()
            asyncio.run(once())
            done = [json.loads(x) for x in (pathlib.Path(d) / "ask.done.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(done), 2)
            self.assertIn("실패", done[0]["result"])
            self.assertIn("메모", done[1]["result"])
            self.assertEqual(control.ASK.read_text(encoding="utf-8"), "")       # 처리한 줄은 남지 않는다



class CancelTest(unittest.TestCase):
    """취소는 AI 가 스스로 하지 않는다 — 사람이 시켰을 때만, 사유와 함께 (#11)."""

    def setUp(self):
        self.card = {"no": 901, "title": "시험", "status": "todo", "card_ts": "T901", "spec": None}
        store.STATE["cards"]["T901"] = self.card
        self.changed = []

        async def fake_apply(s, c, kind, val, user, how=None, why=None):
            c["status"] = val
            self.changed.append((c["no"], val, why))
        self.old = (control.apply_change, control.save, store.save)
        control.apply_change = fake_apply
        control.save = store.save = lambda: None

    def tearDown(self):
        control.apply_change, control.save, store.save = self.old
        store.STATE["cards"].pop("T901", None)

    def test_needs_a_reason(self):
        out = asyncio.run(control.do_cancel(None, {"no": 901}))
        self.assertIn("사유가 없어요", out)
        self.assertEqual(self.card["status"], "todo")

    def test_cancels_with_reason(self):
        out = asyncio.run(control.do_cancel(None, {"no": 901, "why": "확인용으로 만든 카드"}))
        self.assertIn("취소", out)
        self.assertEqual(self.card["status"], "cancelled")
        self.assertEqual(self.card["cancel_reason"], "확인용으로 만든 카드")

    def test_unknown_issue(self):
        self.assertIn("못 찾음", asyncio.run(control.do_cancel(None, {"no": 9999, "why": "x"})))

if __name__ == "__main__":
    unittest.main()


class AfterEditTest(unittest.TestCase):
    """✏️ 내용 수정의 「먼저 끝나야 할 일」 칸 (#68) — 사람이 정한 선행에 after_src 가 붙는가."""

    def setUp(self):
        import flows.status as status
        self.status = status
        self.cards = {"T1": {"no": 1, "title": "앞", "status": "todo", "card_ts": "T1"},
                      "T2": {"no": 2, "title": "뒤", "status": "todo", "card_ts": "T2",
                             "spec": {"why": "w", "done_criteria": ["가"]}}}
        self.old_cards = dict(store.STATE["cards"])
        store.STATE["cards"].clear()
        store.STATE["cards"].update(self.cards)

        async def nop(*a, **k):
            pass
        self.old = (status.record_change, status.redraw, store.save, status.save)
        status.record_change, status.redraw = nop, nop
        store.save = status.save = lambda: None

    def tearDown(self):
        self.status.record_change, self.status.redraw, store.save, self.status.save = self.old
        store.STATE["cards"].clear()
        store.STATE["cards"].update(self.old_cards)

    def save(self, after, card="T2"):
        vals = {k: {"v": {"value": v}} for k, v in
                {"title": "뒤", "why": "w", "change": "", "expect": "", "not_doing": "",
                 "done_criteria": "가", "after": after, "reason": ""}.items()}
        asyncio.run(self.status.save_content(None, {"user": {"id": "U1"},
                    "view": {"private_metadata": card, "state": {"values": vals}}}))
        return store.STATE["cards"][card]

    def test_human_after_is_marked(self):
        c = self.save("#1")
        self.assertEqual(c["after"], [1])
        self.assertEqual(c["after_src"], "human")          # 이게 붙어야 AI 가 못 지운다

    def test_unknown_number_dropped(self):
        self.assertEqual(self.save("#1 #999")["after"], [1])

    def test_clearing_is_also_a_human_decision(self):
        store.STATE["cards"]["T2"]["after"] = [1]
        c = self.save("")
        self.assertEqual(c["after"], [])
        self.assertEqual(c["after_src"], "human")          # 일부러 비운 것도 사람 결정이다

    def test_untouched_stays_ai(self):
        """선행을 안 건드리고 저장하면 잠기지 않는다 — 잠그면 「앞선 일 찾기」 가 영영 못 채운다."""
        store.STATE["cards"]["T2"]["after"] = [1]
        c = self.save("#1")
        self.assertNotIn("after_src", c)


class TakeCardTest(unittest.TestCase):
    """✋ 내가 할게요 — 맡기 전에 언제까지를 본인이 정한다 (#52)."""

    def setUp(self):
        import flows.status as status
        self.status = status
        self.card = {"no": 900, "title": "시험", "status": "todo", "card_ts": "T900",
                     "due": "2026-09-30", "due_src": "ai", "spec": {"why": "w", "done_criteria": ["가"]}}
        self.old_cards = dict(store.STATE["cards"])
        store.STATE["cards"].clear()
        store.STATE["cards"]["T900"] = self.card
        self.logged = []

        async def fake_take(s, c, who, how="pull_card"):
            c["assignee"], c["assign_src"] = who, "human"

        async def fake_post(s, c, e, ask_uid=None):
            self.logged.append(e)
        self.old = (status.take_card, status.post_log, store.save, status.save)
        status.take_card, status.post_log = fake_take, fake_post
        store.save = status.save = lambda: None

    def tearDown(self):
        self.status.take_card, self.status.post_log, store.save, self.status.save = self.old
        store.STATE["cards"].clear()
        store.STATE["cards"].update(self.old_cards)

    def submit(self, due=None, how=""):
        vals = {}
        if due is not None:
            vals["due"] = {"v": {"selected_date": due}}
        vals["how"] = {"v": {"value": how}}
        asyncio.run(self.status.save_take(None, {"user": {"id": "U1"},
                    "view": {"private_metadata": "T900", "state": {"values": vals}}}))
        return self.card

    def test_human_date_wins_over_ai_guess(self):
        """본인이 고른 날짜는 사람 것으로 잠긴다 — AI 가 덮어쓰지 않는다."""
        c = self.submit(due="2026-09-25")
        self.assertEqual(c["due"], "2026-09-25")
        self.assertEqual(c["due_src"], "human")
        self.assertEqual(c["assignee"], "U1")

    def test_how_goes_to_the_thread(self):
        self.submit(due="2026-09-25", how="먼저 재현부터")
        self.assertTrue(any("먼저 재현부터" in (e.get("reason") or "") for e in self.logged), self.logged)

    def test_no_how_leaves_no_note(self):
        """안 써도 된다 — 빈 줄을 기록에 남기지 않는다."""
        self.submit(due="2026-09-25")
        self.assertEqual(self.logged, [])

    def test_missing_date_keeps_the_old_one(self):
        c = self.submit(due=None)
        self.assertEqual(c["due"], "2026-09-30")
        self.assertEqual(c["assignee"], "U1")        # 날짜가 없어도 맡는 것은 된다
