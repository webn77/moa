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
import ai as _ai  # noqa: E402

# **진짜 함수를 미리 붙잡아 둔다** — Base 가 `ai.checklists` 를 가짜로 갈아 끼운 뒤에는
# `ai.checklists` 라고 써도 가짜가 나온다. 진짜를 돌려 보는 시험은 이 이름을 쓴다
REAL_CHECKLISTS = _ai.checklists

ME = "U0EXAMPLEPM"
DM = {"user": ME, "channel": "D0TEST", "ts": "1.0"}


def run(coro):
    return asyncio.run(coro)


class Fake:
    def __init__(self, **answers):
        self.sent, self.answers = [], answers

    async def api(self, s, method, body=None, **params):
        self.sent.append((method, body or params))
        if method == "conversations.open":      # 진짜 Slack 처럼 DM 방 id 를 준다
            return {"ok": True, "channel": {"id": "D0" + (body or {}).get("users", "")[-4:]}}
        return self.answers.get(method, {"ok": True, "ts": "9.9", "permalink": "https://x/p"})

    def texts(self):
        return [b.get("text", "") for m, b in self.sent if m == "chat.postMessage"]


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.made = []
        STATE.pop("new_task", None)

        async def fake_add(s, title, user, project=None, assignee=None, due=None, ask=True, score=True, spec=None, suggest=True):
            """카드 만들기는 이미 다른 시험이 본다 — 여기서는 **무엇을 넘겼는지**만 본다."""
            c = {"no": 90 + len(self.made), "title": title, "card_ts": f"8.{len(self.made)}",
                 "project": project, "assignee": assignee, "due": due, "ask": ask, "score": score,
                 "spec": spec, "suggest": suggest}
            self.made.append(c)
            return c

        async def fake_lists(titles):
            """**진짜 AI 를 부르지 않는다.** 안 갈아 끼우면 시험이 네트워크를 타고 4초씩 걸린다
            (2026-09-23 실측). 체크리스트를 무엇으로 채우는지는 `checklists` 가 따로 본다."""
            return {t: [f"{t} 준비하기"] for t in titles}
        async def fake_reco(s, cards):
            """담당 추천도 갈아 끼운다 — 안 그러면 시험이 네트워크를 타고 **69초** 걸린다
            (2026-09-23 실측: 묶어 부르게 고치자마자 1.1초가 69초가 됐다)."""
            self.reco = list(cards)
        self.lists, self.reco = fake_lists, None
        self.patches = [mock.patch.object(task, "api", self.fake.api),
                        mock.patch("ai.checklists", fake_lists),
                        mock.patch("ai.recommend", fake_reco),
                        mock.patch.object(task, "save", lambda: None),
                        # 담당 DM 은 `flows.status` 가 보낸다 — 거기도 갈아 끼워야 밖으로 안 나간다
                        mock.patch("flows.status.api", self.fake.api),
                        mock.patch("flows.intake.add_issue", fake_add)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        STATE.pop("new_task", None)

    def say(self, q):
        """**스레드 안에서 답하는 사람**을 흉내 낸다 (2026-09-23 사장님: 「스레드 안에서
        시작한 건 거기에서 이야기가 맞어」). 등록이 열려 있으면 그 스레드에 쓴 것으로 본다 —
        밖에 쓴 말은 등록과 무관한 말이고, 그건 `ThreadTest` 가 따로 본다."""
        st = (STATE.get("new_task") or {}).get(ME)
        e = dict(DM, **({"thread_ts": st["th"]} if st else {}))
        return run(task.maybe(None, e, q))

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
        self.assertNotIn("titles", STATE["new_task"][ME])

    def test_a_title_said_clearly_up_front_is_kept(self):
        self.say("할 일 등록 제목은 충전 실패 알림이 두 번 와요")
        self.assertEqual(STATE["new_task"][ME]["titles"], ["충전 실패 알림이 두 번 와요"])


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
        self.assertNotIn("titles", STATE["new_task"][ME])

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
        self.assertEqual(STATE["new_task"][ME]["titles"], before["titles"])
        self.assertEqual(self.made, [])


class ManyTest(Base):
    """**여러 개를 한 번에** (2026-09-22 사장님: 「여러 할일을 한번에 등록하는 경우도 있자나」).

    새 명령을 만들지 않는다 — 1️⃣ 칸에 여러 줄을 붙이면 여러 개로 본다.
    담당·언제까지는 **전부에 한 번**만 묻는다 (사장님이 정함) — 개당 물으면 세 개에 아홉 번이다.
    """

    def test_splits_on_lines_bullets_and_numbers(self):
        got, dropped = task._titles_of("충전 실패 알림\n- 상태가 안 바뀜\n2. 로그가 안 남음")
        self.assertEqual(got, ["충전 실패 알림", "상태가 안 바뀜", "로그가 안 남음"])
        self.assertEqual(dropped, 0)

    def test_a_comma_is_not_a_separator(self):
        """「A, B를 고쳐요」 는 **한 일**이다 — 쉼표로 쪼개면 반쪽 할 일이 생긴다."""
        got, _ = task._titles_of("알림과 로그, 둘 다 고쳐요")
        self.assertEqual(got, ["알림과 로그, 둘 다 고쳐요"])

    def test_empty_lines_are_not_counted_but_junk_lines_are_reported(self):
        got, dropped = task._titles_of("알림이 두 번 와요\n\n🎉\n로그가 안 남아요")
        self.assertEqual(got, ["알림이 두 번 와요", "로그가 안 남아요"])
        self.assertEqual(dropped, 1)              # 빈 줄은 안 세고, 이모지 줄만 센다

    def test_it_says_why_a_line_was_dropped(self):
        """「글자가 없는 줄」 이라고 했는데 「둘」 은 글자가 있다 — **거짓말을 하면 안 된다**."""
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("알림 고치기\n둘\n로그 남기기")
        self.assertIn("두 자 안 되는", self.last())

    def test_the_questions_do_not_multiply(self):
        """개수와 무관하게 묻는 횟수가 같다 — 그게 이 방식의 이유다."""
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("알림 고치기\n상태 고치기\n로그 남기기")
        self.assertIn("3개로 봤어요", self.last())
        self.say("제가")
        self.say("이번 주")
        self.assertIn("3개를 올릴까요", self.last())
        self.say("네")
        self.assertEqual([c["title"] for c in self.made], ["알림 고치기", "상태 고치기", "로그 남기기"])
        self.assertEqual({c["assignee"] for c in self.made}, {ME})

    def test_only_the_last_one_scores(self):
        """점수는 열린 것 전부를 같이 본다 — 카드마다 부르면 세 개에 AI 를 세 번 쓴다."""
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림 고치기\n상태 고치기\n로그 남기기")
        self.say("나중에"); self.say("나중에"); self.say("네")
        self.assertEqual([c["score"] for c in self.made], [False, False, True])

    def test_it_stops_at_ten(self):
        """**상한이 있다** (사장님이 정함) — 회의록을 통째로 붙이면 카드 40개가 생긴다."""
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("\n".join(f"할 일 {i}" for i in range(1, 13)))
        self.assertEqual(len(STATE["new_task"][ME]["titles"]), task.MAX)
        self.assertIn("나머지 2개", self.last())
        self.say("나중에"); self.say("나중에"); self.say("네")
        self.assertEqual(len(self.made), task.MAX)

    def test_it_only_reports_what_actually_landed(self):
        """번호를 받다 막히면 **거기까지만** 올라간다 — 안 한 일을 했다고 말하지 않는다."""
        calls = []

        async def flaky(s, title, user, project=None, assignee=None, due=None, ask=True, score=True, spec=None, suggest=True):
            calls.append(title)
            if len(calls) > 2:
                return None                    # 세 번째에서 번호를 못 받았다
            c = {"no": 90 + len(calls), "title": title, "card_ts": f"7.{len(calls)}"}
            self.made.append(c)
            return c
        with mock.patch("flows.intake.add_issue", flaky):
            self.say("두 번째 프로젝트에 할일 등록"); self.say("알림 고치기\n상태 고치기\n로그 남기기")
            self.say("나중에"); self.say("나중에"); self.say("네")
        self.assertIn("3개 가운데 2개만", self.last())
        self.assertEqual(len(self.made), 2)


class NumberTest(Base):
    """**고를 것이 정해진 칸은 번호로** (2026-09-22 사장님 승낙).

    다른 분을 맡기는 건 `@이름` 이 필요하니 번호로 안 만든다 — 번호로 만들 수 없는 것을
    번호로 만들면 「3번이 누구지?」 가 된다.
    """

    def test_who_by_number(self):
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
        self.assertIn("*1.* 제가 할게요", self.last())
        self.say("1")
        self.assertIn("언제까지", self.last())
        self.assertEqual(STATE["new_task"][ME]["who"], ME)

    def test_who_two_means_later(self):
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요"); self.say("2")
        self.assertIsNone(STATE["new_task"][ME]["who"])

    def test_due_by_number(self):
        import datetime
        want = {"1": 0, "2": 1}
        for pick, days in want.items():
            STATE.pop("new_task", None)
            self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요"); self.say("1")
            self.assertIn("*3.* 이번 주", self.last())
            self.say(pick)
            self.assertEqual(STATE["new_task"][ME]["due"],
                             (datetime.date.today() + datetime.timedelta(days=days)).isoformat())

    def test_due_five_means_later(self):
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요"); self.say("1"); self.say("5")
        self.assertIsNone(STATE["new_task"][ME]["due"])

    def test_a_date_still_works(self):
        import datetime
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요"); self.say("1")
        self.say("9/30")
        self.assertEqual(STATE["new_task"][ME]["due"], f"{datetime.date.today().year}-09-30")

    def test_numbers_are_only_read_where_they_are_offered(self):
        """확인 단계에서 「3」 은 3일을 뜻할 수도 있다 — **묻는 자리에서만** 번호로 읽는다."""
        self.assertIs(task._due_of("3"), False)          # 그냥 「3」 은 날짜가 아니다
        self.assertEqual(task._due_pick("3"), task._due_of("이번 주"))


class YesTest(Base):
    """**「네」 하나만 딱 쓰지 않는다** (2026-09-22 사장님 실측: 「네! 그러자고」 로 등록이 안 됐다).

    목록과 똑같아야 통과였다 — 「네」 는 되고 「네! 그러자고」 는 고치자는 말로 떨어져서,
    답을 했는데 되묻는 봇이 됐다. 이 저장소가 같은 모양으로 세 번 넘어졌다.
    """

    def test_a_yes_with_words_after_it_still_means_yes(self):
        for word in ("네", "네! 그러자고", "ㅇㅇ", "오케이 진행", "좋아요~", "그대로 올려주세요", "네네"):
            STATE.pop("new_task", None)
            self.made.clear()
            self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
            self.say("나중에"); self.say("나중에")
            self.say(word)
            self.assertEqual(len(self.made), 1, f"「{word}」 로 안 올라갔다")

    def test_words_that_only_look_like_yes_do_not_pass(self):
        for word in ("그래서 뭐?", "아니 그게 아니고", "제가", "음?"):
            STATE.pop("new_task", None)
            self.made.clear()
            self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
            self.say("나중에"); self.say("나중에")
            self.say(word)
            self.assertEqual(self.made, [], f"「{word}」 로 올라가 버렸다")

    def test_a_fix_in_the_same_breath_wins(self):
        """「네 근데 담당은 나중에」 — **고치자는 말을 먼저 본다.** 옛 값으로 올리면 안 된다."""
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
        self.say("제가"); self.say("9/30")
        self.say("네 근데 담당은 나중에")
        self.assertEqual(self.made, [], "고치자는 말인데 올려 버렸다")
        self.assertIsNone(STATE["new_task"][ME]["who"])


class DmTest(Base):
    """**개인에게 할 말은 모아 DM 으로** (2026-09-22 사장님: 「이게 dm이 기본이 되어야 하는데」).

    모아의 「메시지」 탭은 사람마다 따로인 1:1 자리다. 예전에는 카드 스레드의 멘션이
    전부였는데, 그러면 **그 방에 안 들어온 사람은 아무것도 모른다.**
    """

    def dms(self):
        """**보낸 글로 가른다** — 방 id 로 가르면 이 흐름 자신의 DM 까지 센다 (시험이 잡았다)."""
        return [b for m, b in self.fake.sent
                if m == "chat.postMessage" and "맡으실 일이 생겼어요" in (b.get("text") or "")]

    def test_giving_it_to_someone_else_dms_them(self):
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
        self.say("<@U0OTHER1>"); self.say("나중에"); self.say("네")
        got = self.dms()
        self.assertTrue(got, "맡은 사람에게 DM 을 안 보냈다")
        self.assertIn("맡으실 일이 생겼어요", got[0]["text"])

    def test_taking_it_yourself_does_not_dm_you(self):
        """방금 자기가 고른 것을 다시 알리면 시끄럽다."""
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
        self.say("1"); self.say("나중에"); self.say("네")
        self.assertEqual(self.dms(), [])

    def test_nobody_assigned_means_no_dm(self):
        self.say("두 번째 프로젝트에 할일 등록"); self.say("알림이 두 번 와요")
        self.say("2"); self.say("나중에"); self.say("네")
        self.assertEqual(self.dms(), [])


class AskingBackTest(Base):
    """**묻는 말은 제목이 아니다** (2026-09-23 사장님 실사용).

        모아: ① 무슨 일인가요?
        사장님: 이번주에 할일 한번에 만들려고 하는데 가능해?
        모아: 「이번주에…가능해?」 — 적어 뒀어요.      ← 질문이 할 일 이름이 됐다
        사장님: 그게 아니라 …
        모아: 그 프로젝트를 못 찾았어요.               ← 정정까지 답으로 받았다

    봇이 방금 물었으니 무엇이 와도 답으로 받던 탓이다. 사람은 **물어 놓고 되물을 수 있다.**
    """

    def test_a_question_is_not_a_title(self):
        self.say("할 일 등록")
        self.say("이번주에 할일 한번에 만들려고 하는데 가능해?")
        self.assertNotIn("적어 뒀어요", self.last())
        self.assertIn("한 줄에 하나씩", self.last())            # 물으신 것에 답을 한다
        self.assertIsNone(STATE["new_task"][ME].get("titles"))  # 제목으로 안 받는다

    def test_other_ways_of_asking(self):
        for q in ("여러 개 한 번에 되나요?", "이거 어떻게 해?", "담당도 같이 정할 수 있어?"):
            STATE.pop("new_task", None)
            self.say("할 일 등록")
            self.say(q)
            self.assertIsNone(STATE["new_task"][ME].get("titles"), q)

    def test_a_real_title_still_goes_through(self):
        """되묻기를 넓히다 진짜 제목까지 막으면 안 된다."""
        for q in ("충전 실패 알림이 두 번 와요", "결제 화면 문구 고치기", "로그인 실패 안내 방법 정리"):
            STATE.pop("new_task", None)
            self.say("할 일 등록")
            self.say(q)
            self.assertEqual(STATE["new_task"][ME].get("titles"), [q], q)

    def test_a_question_at_any_step_gets_an_answer(self):
        """갇히는 자리는 **네 칸 전부**다 — 제목 칸만 가리면 나머지 셋에서 같은 일이 난다
        (2026-09-23 사장님: 「질문을 하는 경우에 대한 예외 처리 하면 될 거 같아」)."""
        self.say("할 일 등록")
        self.say("알림이 두 번 와요")
        for q in ("프로젝트가 뭐야?", "담당은 나중에 정해도 되나요?", "언제까지가 뭐야?"):
            self.say(q)
            self.assertIn("물어보신 것부터", self.last(), q)

    def test_a_real_answer_still_wins(self):
        """되묻기를 넓히다 진짜 답까지 막으면 안 된다 — 답이 되면 답이 먼저다."""
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("알림이 두 번 와요")
        self.say("제가")
        self.assertNotIn("물어보신 것부터", self.last())
        self.assertEqual(STATE["new_task"][ME]["who"], ME)

    def test_not_that_rewinds_the_step(self):
        """「그게 아니라」 는 답이 아니라 되돌리자는 말이다."""
        self.say("할 일 등록")
        self.say("알림이 두 번 와요")
        self.say("그게 아니라")
        self.assertIn("다시 여쭤볼게요", self.last())

    def test_not_that_plus_the_real_answer(self):
        self.say("할 일 등록")
        self.say("알림이 두 번 와요")
        self.say("그게 아니라 결제 화면 문구 고치기")
        self.assertEqual(STATE["new_task"][ME].get("titles"), ["결제 화면 문구 고치기"])


class ChecklistAtConfirmTest(Base):
    """확인 화면에서 체크리스트를 그 자리에서 더한다 (2026-09-23 사장님: 「체크리스트 추가
    하거나 수정 할 수 있는거지 등록 할때 너가 추천 해주는거고」).

    AI 가 낸 것은 **추천**이다 — 사람이 손댈 자리가 없으면 추천이 아니라 통보다.
    """

    def ready(self):
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("알림이 두 번 와요")
        self.say("제가")
        self.say("9/30")

    def dc(self):
        return (STATE["new_task"][ME].get("dc") or {}).get("알림이 두 번 와요")

    def test_the_ai_suggestion_is_there_to_start_with(self):
        self.ready()
        self.assertEqual(self.dc(), ["알림이 두 번 와요 준비하기"])      # setUp 의 가짜 AI
        self.assertIn("☐ 알림이 두 번 와요 준비하기", self.last())

    def test_you_can_add_one(self):
        self.ready()
        self.say("체크리스트에 스테이징에서 확인하기")
        self.assertEqual(self.dc(), ["알림이 두 번 와요 준비하기", "스테이징에서 확인하기"])
        self.assertIn("☐ 스테이징에서 확인하기", self.last())

    def test_you_can_add_several_at_once(self):
        """항목은 짧은 할 거리라 한 줄에 「a, b」 로 쓰는 것이 자연스럽다."""
        self.ready()
        self.say("체크리스트에 로그 남기기, 배포 확인하기")
        self.assertEqual(self.dc()[-2:], ["로그 남기기", "배포 확인하기"])

    def test_spaced_spelling_works_too(self):
        """「체크 리스트」 라고 띄어 쓰셨다 (2026-09-23 실사용) — 한국어는 띄어쓰기가 흔들린다."""
        self.ready()
        self.say("체크 리스트에 스테이징에서 확인하기")
        self.assertIn("스테이징에서 확인하기", self.dc())

    def test_fill_it_for_me_asks_the_ai_again(self):
        """「채워줘」 는 더하자는 말이 아니라 **다시 만들자**는 말이다 (2026-09-23 실사용:
        「체크 리스트도 채워줘봐 테스트로」 를 못 알아들었다)."""
        self.ready()
        calls = []

        async def counted(titles):
            calls.append(list(titles))
            return {t: ["다시 만든 것"] for t in titles}
        with mock.patch("ai.checklists", counted):
            self.say("체크리스트 채워줘")
        self.assertEqual(len(calls), 1, "AI 를 한 번 불러야 한다")
        self.assertEqual(self.dc(), ["다시 만든 것"])

    def test_when_the_ai_has_nothing_it_says_so(self):
        """제목만으로 모르면 **지어내지 않는다** — 대신 어디서 쓰면 되는지 알려 준다."""
        self.ready()

        async def empty(titles):
            return {}
        with mock.patch("ai.checklists", empty):
            self.say("체크리스트 채워줘")
        self.assertIn("지어내고 싶지 않아서", self.last())

    def test_you_can_throw_it_away(self):
        self.ready()
        self.say("체크리스트 지워")
        self.assertEqual(self.dc(), [])
        self.say("네")
        self.assertIsNone(self.made[0]["spec"])          # 빈 것은 안 붙인다

    def test_what_you_added_is_what_gets_saved(self):
        self.ready()
        self.say("체크리스트에 스테이징에서 확인하기")
        self.say("네")
        self.assertEqual(self.made[0]["spec"]["done_criteria"][-1], "스테이징에서 확인하기")

    def test_adding_does_not_call_the_ai_again(self):
        """한 번 받아 둔 추천을 다시 받지 않는다 — 고칠 때마다 부르면 한 줄에 한 번씩이다."""
        import ai
        self.ready()
        calls = []

        async def counted(system, prompt, tries=3):
            calls.append(prompt)
            return '{"lists":[]}'
        with mock.patch.object(ai, "ask_ai", counted), mock.patch("ai.checklists", REAL_CHECKLISTS):
            self.say("체크리스트에 하나 더 넣기")
        self.assertEqual(calls, [])


class AiBudgetTest(Base):
    """**묻는 대화는 AI 를 안 쓴다. 체크리스트만 한 번** (2026-09-23 사장님: 「체크리스트
    만드는건 할일만들때」).

    9/22 에는 이 흐름이 AI 를 **한 번도** 안 불렀다. 이제 확인 직전에 딱 한 번 부른다 —
    제목 열 개를 올려도 **한 번**이다. 묻는 칸(제목·프로젝트·담당·기한)은 여전히 AI 없이 돈다:
    구독 한도가 인사말에 녹으면 안 되고, AI 가 죽어도 등록은 돼야 한다.
    """

    def calls_for(self, *says):
        import ai
        calls = []

        async def counted(system, prompt, tries=3):
            calls.append(prompt)
            return '{"lists":[]}'
        with mock.patch.object(ai, "ask_ai", counted), mock.patch("ai.checklists", REAL_CHECKLISTS):
            for q in says:
                self.say(q)
        return calls

    def test_the_questions_call_the_ai_zero_times(self):
        calls = self.calls_for("두 번째 프로젝트에 할일 등록", "알림이 두 번 와요", "제가")
        self.assertEqual(calls, [], "묻는 칸에서 AI 를 불렀다")

    def test_the_checklist_costs_exactly_one_call(self):
        calls = self.calls_for("두 번째 프로젝트에 할일 등록", "알림이 두 번 와요", "제가", "이번 주", "네")
        self.assertEqual(len(self.made), 1)
        self.assertEqual(len(calls), 1, "체크리스트는 한 번이어야 한다")

    def test_ten_at_once_is_still_one_call(self):
        """열 개를 올리면 열 번 부르면 안 된다."""
        calls = self.calls_for("두 번째 프로젝트에 할일 등록",
                               "\n".join(f"{i}번 일 고치기" for i in range(1, 6)),
                               "제가", "이번 주", "네")
        self.assertEqual(len(self.made), 5)
        self.assertEqual(len(calls), 1)

    def test_a_dead_ai_does_not_stop_the_registration(self):
        """AI 가 죽어도 카드는 올라간다 — 체크리스트만 비어 있고 카드가 그렇게 말해 준다."""
        import ai

        async def dead(system, prompt, tries=3):
            raise RuntimeError("Anthropic 500")
        with mock.patch.object(ai, "ask_ai", dead), mock.patch("ai.checklists", REAL_CHECKLISTS):
            for q in ("두 번째 프로젝트에 할일 등록", "알림이 두 번 와요", "제가", "이번 주", "네"):
                self.say(q)
        self.assertEqual(len(self.made), 1)
        self.assertIsNone(self.made[0]["spec"])


class ThreadTest(Base):
    """**답은 사람이 쓴 자리로 간다** (2026-09-23 사장님: 「스레드에 안 적고 그냥 채팅에
    적었는데 스레드 답변으로 들어가네」).

    9/22 에는 「대화가 한 스레드에 모인다」 가 기본이었다. 맞는 말이지만, **사람이 스레드
    밖에 썼을 때**까지 옛 스레드로 답하면 쓴 사람 눈에는 아무 말이 없는 것과 같다.
    스레드 안에 쓰면 그 스레드로 — 이건 그대로다.
    """

    def test_an_answer_in_the_thread_stays_in_the_thread(self):
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "111.1"}, "할 일 등록"))
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "222.2",
                              "thread_ts": "111.1"}, "알림이 두 번 와요"))
        posts = [b for m, b in self.fake.sent if m == "chat.postMessage"]
        self.assertEqual({b.get("thread_ts") for b in posts}, {"111.1"})

    def test_the_dm_talk_stays_in_the_thread_only(self):
        """**DM 은 스레드만** (2026-09-23 사장님: 「노노 dm 은 스레드만」).

        하루 전에는 첫 물음을 `reply_broadcast` 로 채팅창에도 띄웠다 — 스레드 답글이
        「답글 1개」 로 접혀서 반응이 없는 것처럼 보였기 때문이다. 실제로 써 보니
        **같은 말이 두 곳에 나오는 것**이 더 거슬렸다 (「왜 2곳에 둘다 나와」).
        """
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "111.1"}, "할 일 등록"))
        first = [b for m, b in self.fake.sent if m == "chat.postMessage"][0]
        self.assertEqual(first.get("thread_ts"), "111.1")
        self.assertFalse(first.get("reply_broadcast"), "첫 물음이 채팅창에도 나왔다")

    def test_the_later_steps_stay_in_the_thread_only(self):
        self.say("할 일 등록")
        self.fake.sent.clear()
        self.say("알림이 두 번 와요")
        posts = [b for m, b in self.fake.sent if m == "chat.postMessage"]
        self.assertTrue(posts)
        self.assertFalse(any(b.get("reply_broadcast") for b in posts), "걸음마다 채팅창에 띄웠다")

    def test_a_word_outside_the_thread_is_not_an_answer(self):
        """밖에 쓴 말은 **등록과 무관한 말**이다 — 조용히 스레드로 빨려 들어가면 헷갈린다.
        평소 갈래(찾기·현황)로 가도록 `False` 를 돌려준다."""
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "111.1"}, "할 일 등록"))
        self.fake.sent.clear()
        got = run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "222.2"}, "알림이 두 번 와요"))
        self.assertFalse(got, "밖에 쓴 말을 등록 답으로 받았다")
        self.assertEqual(self.fake.texts(), [], "밖에 쓴 말에 스레드로 답했다")
        self.assertIsNone(STATE["new_task"][ME].get("titles"))

    def test_starting_again_outside_starts_there(self):
        """밖에서 다시 부르면 **거기서 새로** 시작한다 — 옛 스레드에 갇히지 않는다."""
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "111.1"}, "할 일 등록"))
        self.fake.sent.clear()
        run(task.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "333.3"}, "할 일 등록"))
        self.assertEqual(STATE["new_task"][ME]["th"], "333.3")
        posts = [b for m, b in self.fake.sent if m == "chat.postMessage"]
        self.assertEqual({b.get("thread_ts") for b in posts}, {"333.3"})


if __name__ == "__main__":
    unittest.main()


class AiCountTest(Base):
    """**AI 를 몇 번 부르나** (2026-09-23 감사). 열 개를 올리면 열두 번이었다.

        담당 추천 10 + 점수 1 + 체크리스트 1 = 12   →   1 + 1 + 1 = 3
    """

    def many(self, who="2"):
        self.say("두 번째 프로젝트에 할일 등록")
        self.say("\n".join(f"{i}번 일 고치기" for i in range(1, 6)))
        self.say(who)
        self.say("이번 주")
        self.say("네")

    def test_five_at_once_asks_for_one_recommendation(self):
        self.many()
        self.assertEqual(len(self.made), 5)
        self.assertEqual(len(self.reco or []), 5, "다섯을 한 번에 묶어 물어야 한다")
        self.assertTrue(all(c["suggest"] is False for c in self.made), "카드마다 추천을 불렀다")

    def test_no_recommendation_when_you_said_who(self):
        """물어서 받은 답을 두고 다시 추천하지 않는다 — 「제가」 면 아예 안 부른다."""
        self.many(who="1")
        self.assertIsNone(self.reco)

    def test_the_score_is_still_only_on_the_last_one(self):
        self.many()
        self.assertEqual([c["score"] for c in self.made], [False, False, False, False, True])


class SpacingAndTyposTest(unittest.TestCase):
    """**띄어쓰기·오타를 낱말마다 고치지 않는다** (2026-09-23 사장님: 「띄어쓰기 문제들도
    지속적으로 나오는데 해결방법은」).

    같은 모양으로 네 번 고쳤다 — 「내 할일」 · 「체크 리스트」 · 「두번째 프로젝트」 · 「할일등록」.
    맞춰 보는 자리에 한 겹(`flows/ask.py` 의 `norm`·`loose`·`near`)을 깔아 한 번에 끝낸다.
    """

    def test_spacing_never_matters(self):
        from flows.ask import norm
        self.assertEqual(norm("내 할 일"), norm("내할일"))
        self.assertEqual(norm("체크 리스트"), norm("체크리스트"))
        self.assertEqual(norm("GitHub"), norm("git hub"))

    def test_a_word_can_be_written_apart(self):
        from flows.task import LIST_IN, TITLE_IN, DUE_WORD, WHO_IN
        self.assertTrue(LIST_IN.search("체크 리스트에 하나 더"))
        self.assertTrue(TITLE_IN.search("제 목은 알림 고치기"))
        self.assertTrue(DUE_WORD.search("목표 일은 금요일"))
        self.assertTrue(WHO_IN.search("담 당은 제가"))

    def test_a_typo_in_a_command_word_still_lands(self):
        from flows.ask import command
        self.assertTrue(command("도움마"))          # 도움말
        self.assertTrue(command("캔버수"))          # 캔버스

    def test_a_real_name_that_starts_like_a_command_is_not_swallowed(self):
        """**길이가 비슷한 것만 본다** — 이게 없으면 「도움말 개선」 이 명령으로 막힌다."""
        from flows.ask import command
        for q in ("도움말 개선", "현황판 개편", "캔버스 다시 그리기", "정리 자동화"):
            self.assertFalse(command(q), q)

    def test_cancelling_never_guesses(self):
        """취소는 되돌릴 수 없다 — 닮은 말로 짐작하면 사람이 쓴 것을 잃는다.
        빈칸만 봐주고 오타는 안 봐준다 (「안 할래요」 는 프로젝트 등록의 **답**이다)."""
        from flows.ask import cancelled
        self.assertTrue(cancelled("안할래"))
        self.assertTrue(cancelled("안 할래"))
        self.assertFalse(cancelled("안 할래요"))
        self.assertFalse(cancelled("안할레"))
