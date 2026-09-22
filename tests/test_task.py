"""DM 에서 할 일 올리기 (flows/task.py) — 프로젝트 등록과 **같은 틀**인지, 갇히지 않는지.

Slack·GitHub·AI 를 부르지 않는다. 시뮬레이션이 잡은 고장을 여기 다 적어 둔다 —
새 모양이 나오면 여기에 한 줄 더한다.
"""
import asyncio
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common  # noqa: E402
from flows import task  # noqa: E402
from store import STATE  # noqa: E402

ME = "U0EXAMPLEPM"
DM = {"user": ME, "channel": "D0TEST", "ts": "1.0"}


def run(coro):
    return asyncio.run(coro)


class Fake:
    def __init__(self, **answers):
        self.sent, self.answers = [], answers

    async def api(self, s, method, body=None, **params):
        self.sent.append((method, body or params))
        return self.answers.get(method, {"ok": True, "ts": "9.9", "permalink": "https://x/p"})

    def texts(self):
        return [b.get("text", "") for m, b in self.sent if m == "chat.postMessage"]


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.made = []
        STATE.pop("new_task", None)

        async def fake_add(s, title, user, project=None, assignee=None, due=None, ask=True):
            """카드 만들기는 이미 다른 시험이 본다 — 여기서는 **무엇을 넘겼는지**만 본다."""
            c = {"no": 99, "title": title, "card_ts": "8.8", "project": project,
                 "assignee": assignee, "due": due, "ask": ask}
            self.made.append(c)
            return c
        self.patches = [mock.patch.object(task, "api", self.fake.api),
                        mock.patch.object(task, "save", lambda: None),
                        mock.patch("flows.intake.add_issue", fake_add)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        STATE.pop("new_task", None)

    def say(self, q):
        return run(task.maybe(None, DM, q))

    def last(self):
        return self.fake.texts()[-1]


class StartTest(Base):
    def test_only_starts_on_a_making_phrase(self):
        """「내 할 일」·「할 일 목록」 은 **찾는 말**이다 — 삼키면 목록을 못 본다."""
        for q in ("할 일 등록", "할일 등록 하려고", "이슈 만들어줘", "충전성공에 할일 추가해줘",
                  "작업 하나 올려줘"):
            STATE.pop("new_task", None)
            self.assertTrue(self.say(q), q)
        for q in ("내 할 일", "할 일 목록", "현황", "홍길동", "안녕"):
            STATE.pop("new_task", None)
            self.assertFalse(self.say(q), q)

    def test_the_opening_sentence_is_not_the_title(self):
        """**시작하는 말은 제목이 아니다** (2026-09-22 시뮬레이션이 잡았다).

        「두 번째 프로젝트에 할일 등록 하려고!」 에서 제목이 「등록 하려고」 가 됐다 —
        제목 규칙에 「일」 을 넣었더니 「할**일**」 에 걸렸다. 짧은 낱말은 우연히 걸린다.
        """
        self.say("두 번째 프로젝트에 할일 등록 하려고!")
        self.assertIn("무슨 일인가요", self.last())
        self.assertNotIn("title", STATE["new_task"][ME])

    def test_a_title_said_clearly_up_front_is_kept(self):
        self.say("할 일 등록 제목은 충전 실패 알림이 두 번 와요")
        self.assertEqual(STATE["new_task"][ME]["title"], "충전 실패 알림이 두 번 와요")


class ThreeQuestionsTest(Base):
    """**아는 것은 묻지 않는다** (사장님: 「이미 알고 있다면 넘어가고」)."""

    def test_a_project_in_the_words_skips_that_question(self):
        self.say("두 번째 프로젝트에 할일 등록 하려고")
        self.say("알림이 두 번 와요")
        self.assertIn("누가 할까요", self.last())          # 프로젝트를 안 묻는다
        self.assertEqual(STATE["new_task"][ME]["pkey"], "XX")

    def test_the_numbers_match_the_number_of_questions(self):
        """프로젝트 칸을 건너뛰면 **번호도 다시 센다** — 「2/3 단계」 인데 「3️⃣」 이면 헷갈린다."""
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("알림이 두 번 와요")
        self.assertIn("2️⃣", self.last())        # 2️⃣ 누가 할까요
        self.say("제가")
        self.assertIn("3️⃣", self.last())        # 3️⃣ 언제까지
        self.assertNotIn("4️⃣", self.last())

    def test_without_a_project_it_asks_and_counts_four(self):
        self.say("할 일 등록")
        self.say("알림이 두 번 와요")
        self.assertIn("어느 프로젝트", self.last())
        self.say("아무 말")
        self.assertIn("2/4 단계", self.last())

    def test_one_project_needs_no_question(self):
        saved = [dict(p) for p in common.PROJECTS]
        try:
            common.PROJECTS[:] = saved[:1]
            self.say("할 일 등록")
            self.say("알림이 두 번 와요")
            self.assertIn("누가 할까요", self.last())
            self.assertEqual(STATE["new_task"][ME]["pkey"], saved[0]["key"])
        finally:
            common.PROJECTS[:] = saved


class PickTest(Base):
    def test_a_project_by_name_number_or_key(self):
        self.assertEqual(task._pick_project("두 번째 프로젝트")["key"], "XX")
        self.assertEqual(task._pick_project("두번째 프로젝트")["key"], "XX")    # 붙여 써도
        self.assertEqual(task._pick_project("2")["key"], "XX")
        self.assertEqual(task._pick_project("2번")["key"], "XX")
        self.assertEqual(task._pick_project("XX")["key"], "XX")
        self.assertIsNone(task._pick_project("음?"))
        self.assertIsNone(task._pick_project("99"))

    def test_who(self):
        self.assertEqual(task._who_of("제가", ME), ME)
        self.assertEqual(task._who_of("제가 할게요", ME), ME)
        self.assertEqual(task._who_of("<@U0OTHER1> 이요", ME), "U0OTHER1")
        self.assertIsNone(task._who_of("나중에", ME))
        self.assertIs(task._who_of("음?", ME), False)

    def test_due(self):
        today = datetime.date.today()
        self.assertEqual(task._due_of("오늘"), today.isoformat())
        self.assertEqual(task._due_of("내일"), (today + datetime.timedelta(days=1)).isoformat())
        self.assertEqual(task._due_of("9/30"), f"{today.year}-09-30")
        self.assertEqual(task._due_of("9월 30일"), f"{today.year}-09-30")
        self.assertEqual(task._due_of("2027-01-05"), "2027-01-05")
        self.assertIsNone(task._due_of("나중에"))
        self.assertIs(task._due_of("언젠가는"), False)
        self.assertIs(task._due_of("2/31"), False)        # 없는 날짜

    def test_this_week_is_that_friday(self):
        """「이번 주」 라고 하면 사람은 **주말 전**을 뜻한다."""
        got = datetime.date.fromisoformat(task._due_of("이번 주"))
        self.assertEqual(got.weekday(), 4)
        self.assertGreaterEqual(got, datetime.date.today())


class NotStuckTest(Base):
    """갇히지 않는다 — 프로젝트 등록에서 배운 것을 여기서도 지킨다."""

    def test_a_command_is_not_taken_as_the_title(self):
        self.say("할 일 등록")
        self.say("현황")
        self.assertIn("잠깐 미뤄", self.last())
        self.assertIn("할 일 등록", self.last())
        self.assertNotIn("title", STATE["new_task"][ME])

    def test_every_re_ask_says_where_you_are_and_how_to_get_out(self):
        for setup, bad in (
            (["할 일 등록"], "🎉"),
            (["할 일 등록", "알림이 두 번 와요"], "음?"),
            (["할 일 등록", "알림이 두 번 와요", "2"], "음?"),
            (["할 일 등록", "알림이 두 번 와요", "2", "제가"], "언젠가는"),
        ):
            STATE.pop("new_task", None)
            for q in setup:
                self.say(q)
            self.say(bad)
            self.assertIn("할 일 등록", self.last(), bad)
            self.assertIn("취소", self.last(), bad)

    def test_it_stops_repeating_the_same_line(self):
        """**같은 말을 세 번 되풀이하지 않는다** (시뮬레이션에서 똑같은 줄이 여덟 번 나왔다)."""
        self.say("할 일 등록"); self.say("알림이 두 번 와요")
        self.say("음?"); self.say("제가")
        first = self.last()
        self.say("언젠가는")
        self.assertNotEqual(self.last(), first, "같은 말을 또 했다")
        self.assertIn("숫자 하나만", self.last())

    def test_cancel_stops_and_makes_nothing(self):
        self.say("할 일 등록")
        self.say("취소")
        self.assertNotIn(ME, STATE.get("new_task", {}))
        self.assertEqual(self.made, [])


class BuildTest(Base):
    def ready(self):
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("알림이 두 번 와요")
        self.say("제가")
        self.say("9/30")

    def test_nothing_is_made_before_you_say_yes(self):
        self.ready()
        self.assertEqual(self.made, [])
        self.say("네")
        self.assertEqual(len(self.made), 1)

    def test_what_the_person_said_is_what_gets_saved(self):
        """물어서 받은 답을 두고 AI 에게 다시 추천시키지 않는다 — `ask=False`."""
        self.ready()
        self.say("네")
        c = self.made[0]
        self.assertEqual(c["title"], "알림이 두 번 와요")
        self.assertEqual(c["project"], "XX")
        self.assertEqual(c["assignee"], ME)
        self.assertEqual(c["due"], f"{datetime.date.today().year}-09-30")
        self.assertFalse(c["ask"], "카드를 만든 뒤 AI 가 또 캐묻는다")

    def test_later_leaves_the_blanks_empty(self):
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
        self.say("나중에"); self.say("나중에"); self.say("네")
        c = self.made[0]
        self.assertIsNone(c["assignee"])
        self.assertIsNone(c["due"])

    def test_fixing_at_the_confirm_step(self):
        self.ready()
        self.say("제목은 알림이 세 번 와요")
        self.assertIn("알림이 세 번 와요", self.last())
        self.say("언제까지는 내일")
        self.assertIn(str((datetime.date.today() + datetime.timedelta(days=1)).day), self.last())
        self.assertEqual(self.made, [])          # 아직 안 만든다

    def test_words_it_cannot_place_do_not_change_anything(self):
        self.ready()
        before = dict(STATE["new_task"][ME])
        self.say("아니 그게 아니고요")
        self.assertIn("어느 걸 고칠까요", self.last())
        self.assertEqual(STATE["new_task"][ME]["title"], before["title"])
        self.assertEqual(self.made, [])


class AiBudgetTest(Base):
    """**이 흐름도 AI 를 한 번도 안 부른다** — 프로젝트 등록과 같은 약속이다."""

    def test_the_conversation_calls_the_ai_zero_times(self):
        import ai
        calls = []

        async def counted(system, prompt, tries=3):
            calls.append(prompt)
            return "{}"
        with mock.patch.object(ai, "ask_ai", counted):
            self.say("두 번째 프로젝트에 할일 등록")
            self.say("알림이 두 번 와요")
            self.say("제가")
            self.say("이번 주")
            self.say("네")
        self.assertEqual(len(self.made), 1)
        self.assertEqual(calls, [], "AI 를 불렀다 — 이 흐름은 AI 없이 돌아야 한다")


class ThreadTest(Base):
    """등록 대화는 **한 스레드에 모인다** — 사장님이 정한 기본값."""

    def test_the_whole_talk_lands_in_one_thread(self):
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "111.1"}, "할 일 등록"))
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "222.2"}, "알림이 두 번 와요"))
        posts = [b for m, b in self.fake.sent if m == "chat.postMessage"]
        self.assertEqual({b.get("thread_ts") for b in posts}, {"111.1"})


if __name__ == "__main__":
    unittest.main()
