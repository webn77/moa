"""DM 에서 회의 잡기 (flows/meeting.py) — 할 일 올리기와 **같은 틀**인지, 갇히지 않는지.

Slack·GitHub·AI 를 부르지 않는다. 회의록 정리(`finish_meeting`)는 AI 를 부르므로
여기서 건드리지 않는다 — 부르면 시험이 네트워크를 타고 몇십 초씩 걸린다
(2026-09-23 에 `ai.recommend` 로 1.1초가 69초가 된 적이 있다).
"""
import asyncio
import json
import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core  # noqa: E402
from flows import meeting  # noqa: E402
from store import STATE  # noqa: E402

ME = "U0EXAMPLEPM"
DM = {"user": ME, "channel": "D0TEST", "ts": "1.0"}


def run(coro):
    return asyncio.run(coro)


class Fake:
    def __init__(self):
        self.sent = []
        self.n = 0

    async def api(self, s, method, body=None, **params):
        self.sent.append((method, body or params))
        if method == "chat.postMessage":
            self.n += 1
            return {"ok": True, "ts": f"7.{self.n}"}
        return {"ok": True, "ts": "9.9", "permalink": "https://x/p"}

    def texts(self):
        return [b.get("text", "") for m, b in self.sent if m == "chat.postMessage"]

    def cards(self):
        """회의 카드로 올라간 글 — 카드는 팀 대화방에 가고 대화는 DM 에 남는다."""
        return [b for m, b in self.sent if m == "chat.postMessage" and not b.get("thread_ts")
                and str(b.get("text", "")).startswith("🗓️ M")]


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        STATE.pop("new_meeting", None)
        STATE.pop("meetings", None)
        STATE.pop("next_m", None)
        self.patches = [mock.patch.object(meeting, "api", self.fake.api),
                        mock.patch.object(meeting, "save", lambda: None),
                        mock.patch.object(meeting, "render_canvas", self._nothing),
                        mock.patch.object(meeting, "name_of", self._name)]
        for p in self.patches:
            p.start()

    async def _nothing(self, *a, **k):
        return None

    async def _name(self, s, m):
        return "이동원"

    def tearDown(self):
        for p in self.patches:
            p.stop()
        STATE.pop("new_meeting", None)
        STATE.pop("meetings", None)

    def say(self, q):
        """**시작한 스레드 안에서 답하는 사람**을 흉내 낸다."""
        st = (STATE.get("new_meeting") or {}).get(ME)
        e = dict(DM, **({"thread_ts": st["th"]} if st else {}))
        return run(meeting.maybe(None, e, q))

    def last(self):
        return self.fake.texts()[-1]

    def made(self):
        return list(STATE.get("meetings", {}).values())


class StartTest(Base):
    def test_the_words_people_actually_use(self):
        """**사장님이 실제로 쓰신 말이 먹혀야 한다** (2026-09-23: 「회의만들기?」).

        예전에는 이름이 빈 것을 그냥 버려서 이 말들이 전부 **아무 반응도 없었다**.
        """
        for q in ("회의 만들자", "회의만들기", "회의 만들어줘", "미팅 잡아줘", "회의 잡자",
                  "회의 하나 열자", "미팅 시작하자", "회의 등록할래"):
            STATE.pop("new_meeting", None)
            self.assertTrue(self.say(q), q)

    def test_it_does_not_lose_to_the_task_flow(self):
        """**할 일 등록이 가로채면 안 된다** — `handlers.py` 는 `new_task` 를 먼저 부른다.

        2026-09-23: 「회의 등록할래」 가 할 일 등록으로 샜다. `START` 의 뒷가지
        `등록(할래|하려|…)` 에는 「할 일」 이 안 붙어 있어서였다. 위 시험들은
        `meeting.maybe` 를 **바로** 불러서 이 자리를 못 본다 — 갈래를 가르는 규칙끼리
        맞대어 본다. 순서를 바꿔 막지 않는다: 순서는 나중에 누가 또 바꾼다
        """
        from flows.task import START, NOT_MINE

        def route(q):
            if START.search(q) and not NOT_MINE.search(q):
                return "할 일"
            if meeting.MEET_START.search(q) and not meeting.MEET_NOT.search(q):
                return "회의"
            return "검색"

        # **회의라는 말은 할 일 이름에도 흔하다.** 처음에 「회의」 를 통째로 막았더니
        # 「회의 준비 할 일 등록해줘」 가 검색으로 샜고, 두 자까지 봐줬더니 「회의 **자료**
        # 만들기 할일 추가」 가 걸렸다. 두 줄이 서로를 붙잡게 여기 한 표로 못 박아 둔다
        for q, want in {"회의 등록할래": "회의", "회의 등록하자": "회의", "미팅 등록해줘": "회의",
                        "회의 만들자": "회의", "회의만들기": "회의", "미팅 잡아줘": "회의",
                        "회의 잡자": "회의", "회의를 만들자": "회의",
                        "회의 준비 할 일 등록해줘": "할 일", "회의록 정리하는 할일 등록": "할 일",
                        "다음 회의 자료 만들기 할일 추가": "할 일", "회의실 예약 할일 만들어줘": "할 일",
                        "할 일 등록": "할 일", "깃허브 등록 하려고": "검색",
                        "내 할 일": "검색", "회의록 보여줘": "검색"}.items():
            self.assertEqual(route(q), want, f"「{q}」")

    def test_it_does_not_swallow_other_talk(self):
        """회의록을 보자는 말·평범한 말은 삼키지 않는다 — 삼키면 그 갈래가 죽는다."""
        for q in ("회의록 보여줘", "회의록 다시", "현황", "안녕", "내 할 일", "회의 언제였지"):
            STATE.pop("new_meeting", None)
            self.assertFalse(self.say(q), q)

    def test_the_name_is_not_chewed_up(self):
        """**옵션 뒤에 맨 `\\w*` 를 두면 아무 낱말이나 먹는다** — 2026-09-23 실사용에서
        「회의 주간 점검」 의 제목이 「점검」 이 됐고, 「만들자」 가 회의 이름이 됐다."""
        for q, want in {"회의 주간 점검": "주간 점검", "회의 결제 논의": "결제 논의",
                        "회의 잡담방 개선": "잡담방 개선", "회의 열린 회고": "열린 회고",
                        "회의 만들자": "", "회의만들자": "", "미팅 잡자": "",
                        "회의 시작하자": "", "만들자": "", "잡아줘": ""}.items():
            self.assertEqual(meeting._title_after(q), want, f"「{q}」")

    def test_a_making_word_is_not_a_name(self):
        """① 칸에서 「만들자」 라고 답하시면 **되풀이하신 것**이지 이름을 주신 게 아니다."""
        self.say("회의 만들자")
        self.say("만들자")
        self.assertIn("회의 이름으로 쓰기 어려워요", self.last())
        self.assertFalse(self.made())

    def test_a_name_in_the_first_line_is_the_name(self):
        """「회의 만들자 주간 점검」 이면 이름을 다시 묻지 않는다 — **아는 것은 묻지 않는다.**"""
        self.assertTrue(self.say("회의 만들자 주간 점검"))
        self.assertIn("주간 점검", self.last())
        self.assertIn("프로젝트", self.last())       # 이름을 건너뛰고 다음 칸으로 갔다

    def test_the_channel_path_can_start_it_too(self):
        """채널에서 `@모아 회의` 라고만 불러도 — `handlers.py` 가 `force` 로 넘긴다."""
        self.assertTrue(run(meeting.maybe(None, DM, "회의", force=True)))
        self.assertIn("무슨 회의", self.last())


class StepTest(Base):
    def test_once_takes_four_questions(self):
        """한 번만 하는 회의 — 이름 · 프로젝트 · 한 번/정기 · 언제(+몇 시).

        **날짜와 시간은 한 칸이다** — 「내일 2시」 라고 한 번에 말씀하시면 거기서 끝난다
        (2026-09-23 사장님: 「몇시에 잡는지 이게 중요해」 · 「쉽게 가야해」).
        """
        self.say("회의 만들자")
        self.assertIn("무슨 회의", self.last())
        self.say("결제 점검")
        self.assertIn("프로젝트", self.last())
        self.say("1")
        self.assertIn("정기", self.last())
        self.say("한 번만")
        self.assertIn("언제", self.last())
        self.say("내일 오후 2시")
        self.assertEqual(len(self.made()), 1)
        m = self.made()[0]
        self.assertEqual(m["title"], "결제 점검")
        self.assertEqual(m["every"], "once")
        self.assertEqual(m["date"], (datetime.date.today() + datetime.timedelta(days=1)).isoformat())
        self.assertEqual(m["time"], [14, 0])

    def test_recurring_asks_for_a_weekday(self):
        """정기면 ③ 이 날짜가 아니라 **요일**이다 — 매주 회의에 날짜를 물으면 한 번짜리가 된다."""
        self.say("회의 만들자")
        self.say("주간 점검")
        self.say("1")
        self.say("매주")
        self.assertIn("요일", self.last())
        self.say("화요일 10시")
        m = self.made()[0]
        self.assertEqual((m["every"], m["weekday"]), ("week", 1))
        self.assertEqual(datetime.date.fromisoformat(m["date"]).weekday(), 1)
        self.assertGreater(m["date"], datetime.date.today().isoformat())   # 지난 날로 잡지 않는다

    def test_numbers_work_for_the_second_question(self):
        """고를 것이 정해진 칸은 번호가 빠르다 — 1~4 가 그대로 먹혀야 한다."""
        for n, want in (("1", "once"), ("2", "week"), ("3", "2week"), ("4", "month")):
            STATE.pop("new_meeting", None); STATE.pop("meetings", None)
            self.say("회의 만들자")
            self.say("점검")
            self.say("1")
            self.say(n)
            self.say("화요일 10시" if want != "once" else "내일 10시")
            self.assertEqual(self.made()[0]["every"], want, n)

    def test_numbers_work_for_the_weekday_too(self):
        """앞 칸에서 번호로 답한 분은 여기서도 번호를 친다 — 안 받으면 한 번 더 되묻게 된다.

        **번호만 왔을 때 받는다.** 「2 10시」 처럼 시간과 함께 오면 어느 숫자가 요일인지
        알 수 없다 — 그때는 시간을 따로 여쭙는 쪽이 안전하다.
        """
        for n, wd in (("1", 0), ("2", 1), ("5", 4), ("7", 6)):
            STATE.pop("new_meeting", None); STATE.pop("meetings", None)
            self.say("회의 만들자")
            self.say("점검")
            self.say("1")
            self.say("매주")
            self.say(n)                       # 요일만 — 시간은 봇이 한 번 더 묻는다
            self.assertIn("몇 시", self.last())
            self.say("10시")
            self.assertEqual(self.made()[0]["weekday"], wd, n)

    def test_the_card_goes_to_the_team_room_not_the_dm(self):
        """**카드는 팀 대화방에** (2026-09-20 결정) — 대화만 DM 에 남는다."""
        self.say("회의 만들자")
        self.say("주간 점검")
        self.say("1")
        self.say("한 번만")
        self.say("내일 10시")
        self.assertTrue(self.fake.cards(), "회의 카드가 안 올라갔다")
        self.assertNotEqual(self.fake.cards()[0]["channel"], DM["channel"])


class LostTest(Base):
    """**갇히지 않는지** — 봇이 물었다고 무엇이든 답으로 받으면 사람 눈에는 고장이다."""

    def test_a_question_gets_an_answer_not_the_same_prompt(self):
        """묻는 말에는 답을 한다 (2026-09-23 사장님: 「질문을 하는 경우에 대한 예외 처리」)."""
        self.say("회의 만들자")
        self.say("회의 만들면 뭐가 좋아?")
        self.assertIn("물어보신 것부터", self.last())
        self.assertNotIn("회의 만들면 뭐가 좋아", str(self.made()))

    def test_a_question_at_the_middle_step_does_not_become_an_answer(self):
        """②·③ 에서 물어도 마찬가지다 — 갇히는 자리는 세 칸 전부다.

        **물어보신 것에 답을 해야 한다.** 「정기가 뭐야?」 에는 「정기」 가 들어 있어서 ② 의
        답으로 잘 읽히고, 그대로 매주 회의가 잡혔다 (2026-09-23 시뮬레이션이 잡았다).
        """
        self.say("회의 만들자")
        self.say("주간 점검")
        self.say("1")
        self.say("정기가 뭐야?")
        self.assertIn("정기", self.last())
        self.assertIn("매주·격주·매달", self.last(), "묻는 칸에 맞는 답이 아니다")
        self.assertFalse(self.made())

    def test_no_particle_right_after_a_date(self):
        """자리표시 바로 뒤에 조사를 붙이지 않는다 — 「화요일 **으로**」 가 나갔다."""
        self.say("회의 만들자")
        self.say("주간 점검")
        self.say("1")
        self.say("매주")
        self.say("화요일 10시")
        self.assertNotIn(" 으로", self.last())
        self.assertIn("화요일", self.last())

    def test_it_says_where_we_are_when_it_cannot_read(self):
        """못 알아들으면 **어디에 있는지와 나가는 길**을 늘 함께."""
        self.say("회의 만들자")
        self.say("주간 점검")
        self.say("1")
        self.say("음")
        self.assertIn("3/4", self.last())
        self.assertIn("취소", self.last())

    def test_that_is_not_it_rewinds(self):
        """「그게 아니라」 는 답이 아니라 되돌리자는 말이다."""
        self.say("회의 만들자")
        self.say("주간 점검")
        self.say("그게 아니라 결제 점검")
        self.assertIn("결제 점검", self.last())
        self.say("1")
        self.say("한 번만")
        self.say("내일 10시")
        self.assertEqual(self.made()[0]["title"], "결제 점검")

    def test_cancel_leaves_nothing_behind(self):
        self.say("회의 만들자")
        self.say("주간 점검")
        self.say("취소")
        self.assertFalse(self.made())
        self.assertNotIn(ME, STATE.get("new_meeting") or {})

    def test_talk_outside_the_thread_is_not_an_answer(self):
        """**시작한 스레드 안에서만 이어 간다** (사장님이 못 박은 규칙)."""
        self.say("회의 만들자")
        self.assertFalse(run(meeting.maybe(None, dict(DM, ts="2.0"), "주간 점검")))
        self.assertFalse(self.made())


class EveryTest(Base):
    """정기 회의가 **실제로 다음 회를 여는지** — 안 열리면 「정기」 는 카드에 적힌 글자일 뿐이다."""

    def _recurring(self, every="week", weekday=1, date="2026-09-29"):
        run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM",
                                every=every, date=date, weekday=weekday))
        return self.made()[0]

    def test_it_opens_the_next_one_on_the_day(self):
        m = self._recurring()
        made = run(meeting.due_meetings(None, datetime.date(2026, 10, 6)))
        self.assertEqual(len(made), 1)
        nxt = [x for x in self.made() if x["id"] != m["id"]][0]
        self.assertEqual(nxt["date"], "2026-10-06")
        self.assertEqual(nxt["every"], "week")
        self.assertEqual(m["every"], "once", "정기 표시는 맨 마지막 회에만 남아야 한다")

    def test_it_does_not_open_early(self):
        self._recurring()
        self.assertFalse(run(meeting.due_meetings(None, datetime.date(2026, 10, 5))))
        self.assertEqual(len(self.made()), 1)

    def test_it_skips_meetings_that_already_passed(self):
        """봇이 며칠 꺼져 있었어도 **지난 회는 건너뛴다** — 이제 와 열어도 아무도 안 모인다."""
        self._recurring()
        run(meeting.due_meetings(None, datetime.date(2026, 10, 20)))
        self.assertEqual([x["date"] for x in self.made() if x["date"] != "2026-09-29"], ["2026-10-06"])

    def test_one_at_a_time_not_one_per_card(self):
        """카드가 여러 장이어도 **마지막 것에서만** 다음을 잰다 — 아니면 방이 찬다."""
        self._recurring()
        for day in ("2026-10-06", "2026-10-13", "2026-10-20"):
            run(meeting.due_meetings(None, datetime.date.fromisoformat(day)))
        self.assertEqual(len(self.made()), 4)
        self.assertEqual(sum(1 for x in self.made() if x["every"] != "once"), 1)

    def test_a_one_off_never_repeats(self):
        run(meeting.new_meeting(None, "한 번", None, "이동원", "C0TEAM", date="2026-09-29"))
        self.assertFalse(run(meeting.due_meetings(None, datetime.date(2027, 1, 1))))

    def test_stopping_keeps_the_meeting(self):
        """정기 끄기는 **다음 회만** 안 연다 — 이 회의는 그대로 있어야 한다."""
        m = self._recurring()
        run(meeting.stop_every(None, m["card_ts"], ME))
        self.assertIn(m["card_ts"], STATE["meetings"])
        self.assertFalse(run(meeting.due_meetings(None, datetime.date(2026, 11, 1))))


class RoomTest(Base):
    """**회의는 항상 그 프로젝트 방에** (2026-09-24 사장님: 「프로젝트 방이 기본적으로 있잖아」)."""

    def test_it_follows_the_project(self):
        from common import PROJECTS
        self.assertEqual(meeting.meet_room({"pkey": PROJECTS[1].get("key")}),
                         PROJECTS[1].get("channel"), "두 번째 프로젝트의 방으로 안 갔다")

    def test_it_never_falls_to_the_team_room(self):
        """팀 대화방(요청 방)으로 떨어지지 않는다 — 예전에는 그랬다 (2026-09-24)."""
        from common import PROJECTS
        p = PROJECTS[1]
        self.assertNotEqual(meeting.meet_room({"pkey": p.get("key")}, dm="D0TEST"), p.get("request"))

    def test_a_broken_project_keeps_it_in_the_dm(self):
        """프로젝트 방을 못 찾을 때만(설정이 깨졌을 때) 대화하던 DM 에 둔다."""
        from common import PROJECTS
        p = PROJECTS[1]
        with mock.patch.dict(p, {"channel": ""}, clear=False):
            self.assertEqual(meeting.meet_room({"pkey": p.get("key")}, dm="D0TEST"), "D0TEST")

    def test_a_dm_meeting_still_knows_its_room(self):
        """DM 에 남은 회의도 `channel` 이 차 있어야 한다 — 비면 회의록 확정·정기
        끄기가 어디에 말해야 할지 모른다."""
        from common import PROJECTS
        with mock.patch.dict(PROJECTS[0], {"channel": ""}, clear=False):
            self.say("회의 만들자")
            self.say("주간 점검")
            self.say("1")
            self.say("한 번만")
            self.say("내일 10시")
        m = self.made()[0]
        self.assertEqual(m["channel"], DM["channel"])
        self.assertTrue(meeting.mch(m))


class SoonTest(Base):
    """회의 **10분 전 알림** — 슬랙 안에서 끝낸다 (사장님: 「슬랙으로 바로」)."""

    def _at(self, date, hm):
        return run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM",
                                       date=date, time=list(hm)))

    def test_it_tells_ten_minutes_before(self):
        import datetime as dt
        self._at("2026-09-29", (10, 0))
        told = run(meeting.soon_meetings(None, dt.datetime(2026, 9, 29, 9, 50)))
        self.assertEqual(len(told), 1)
        self.assertIn("10분 뒤", self.fake.texts()[-1])

    def test_it_is_quiet_when_it_is_not_close(self):
        import datetime as dt
        self._at("2026-09-29", (10, 0))
        self.assertFalse(run(meeting.soon_meetings(None, dt.datetime(2026, 9, 29, 8, 0))))
        self.assertFalse(run(meeting.soon_meetings(None, dt.datetime(2026, 9, 29, 10, 30))))

    def test_it_tells_only_once(self):
        """매분 도는 루프가 부른다 — 한 회의에 알림이 열 번 가면 안 된다."""
        import datetime as dt
        self._at("2026-09-29", (10, 0))
        run(meeting.soon_meetings(None, dt.datetime(2026, 9, 29, 9, 50)))
        self.assertFalse(run(meeting.soon_meetings(None, dt.datetime(2026, 9, 29, 9, 51))))

    def test_a_meeting_without_a_time_is_skipped(self):
        """옛 회의에는 시간이 없다 — 터지지 말고 조용히 넘어가야 한다."""
        import datetime as dt
        run(meeting.new_meeting(None, "옛 회의", None, "이동원", "C0TEAM", date="2026-09-29"))
        self.assertFalse(run(meeting.soon_meetings(None, dt.datetime(2026, 9, 29, 9, 50))))


class NoteTest(Base):
    """**적을 길을 먼저 준다** (2026-09-23 사장님: 「확정 버튼이 먼저 나오면 안될거 같고
    작성 할 수 있게 해줘야 할거 같은데」)."""

    def _buttons(self, m):
        from flows.meeting import meeting_blocks
        return [el.get("text", {}).get("text")
                for b in meeting_blocks(m) if b.get("type") == "actions"
                for el in b.get("elements", [])]

    def test_confirm_is_hidden_until_there_is_discussion(self):
        m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM"))
        self.assertIn("✍️ 논의 적기", self._buttons(m))
        self.assertNotIn("📝 회의록 확정", self._buttons(m), "논의도 없는데 확정 단추가 보인다")

    def test_confirm_appears_once_someone_talks(self):
        m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM"))
        run(meeting.mark_talked(None, m["card_ts"]))
        self.assertIn("📝 회의록 확정", self._buttons(m))
        self.assertIn("✍️ 논의 적기", self._buttons(m), "적는 길은 계속 있어야 한다")

    def test_the_popup_posts_into_the_thread_and_opens_the_gate(self):
        m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM"))
        run(meeting.save_note(None, m["card_ts"], "다음 주에 배포하기로 했어요", ME))
        posted = [b for mm, b in self.fake.sent
                  if mm == "chat.postMessage" and b.get("thread_ts") == m["card_ts"]]
        self.assertTrue(posted, "창에 적은 것이 스레드에 안 올라갔다")
        self.assertIn("다음 주에 배포", posted[-1]["text"])
        self.assertTrue(m.get("talked"))

    def test_an_empty_note_does_nothing(self):
        m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM"))
        run(meeting.save_note(None, m["card_ts"], "   ", ME))
        self.assertFalse(m.get("talked"))


class MinutesTest(Base):
    """**빈 표만 든 회의록을 레포에 남기지 않는다** (2026-09-23 사장님 실사용:
    「회의록 작성 누르면 이상한게 나오는거같은데」).

    스레드가 비었을 때는 예전부터 막았다. 그런데 실제로 걸린 건 **차 있는데 건질 게
    없는** 경우였다 — AI 가 「정한 것 없음」·「액션 아이템 -」 만 든 문서를 돌려주고,
    그게 `meetings/` 에 파일로 남았다.
    """

    def _finish(self, answer):
        import ai
        m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM"))
        self.saved = []

        async def fake_ai(system, prompt):
            return json.dumps(answer, ensure_ascii=False)

        def fake_save(mm, body, link):
            """진짜 `gh_link.save_meeting` 처럼 `m["file"]` 을 채운다 — 카드가 그걸로
            「회의록 보기」 단추를 그린다."""
            self.saved.append(body)
            mm["file"] = f"{mm['date']}-x.md"
            return "meetings/x.md"

        async def replies(s, method, body=None, **p):
            if method == "conversations.replies":
                return {"messages": [{"text": "카드"}, {"user": "U1", "text": "회의합시다"}]}
            return await self.fake.api(s, method, body, **p)

        with mock.patch.object(meeting, "ask_ai", fake_ai), \
             mock.patch.object(meeting, "api", replies), \
             mock.patch.object(meeting.gh_link, "save_meeting", fake_save):
            run(meeting.finish_meeting(None, m, ME))
        return m

    def test_it_refuses_to_save_an_empty_one(self):
        m = self._finish({"agenda_results": [], "decisions": [], "action_items": [],
                          "issue_changes": [], "new_issues": [], "next_meeting": ""})
        self.assertFalse(self.saved, "건질 게 없는데 회의록을 저장했다")
        self.assertIsNone(m.get("file"))
        self.assertIn("남길 게 없어요", self.fake.texts()[-1])

    def test_it_saves_when_something_was_decided(self):
        m = self._finish({"agenda_results": [], "decisions": ["다음 주에 배포한다"],
                          "action_items": [], "issue_changes": [], "new_issues": [], "next_meeting": ""})
        self.assertTrue(self.saved, "정한 것이 있는데 저장하지 않았다")
        self.assertTrue(m.get("file"), "회의록을 저장했는데 카드가 모른다")


class DmIsThreadOnlyTest(unittest.TestCase):
    """**DM 은 스레드만** (2026-09-23 사장님: 「노노 dm 은 스레드만」).

    한 칸씩 묻는 흐름이 넷이다 (할 일·프로젝트·회의·GitHub). 넷 다 `_say` 를 저마다
    몇 줄씩 두고 있어서, 하루 전에 넷 모두에 `reply_broadcast` 를 켰다가 오늘 넷 모두에서
    껐다. **한 곳만 고치면 조용히 갈라질 자리**라 여기서 넷을 한꺼번에 본다.

    `flows/review.py`·`flows/status.py` 는 **채널** 카드 스레드에서 채널로 띄우는 것이라
    여기 해당하지 않는다 — 그건 팀이 같이 보라고 일부러 띄운다.
    """

    def test_no_dm_flow_broadcasts_to_the_channel(self):
        import pathlib
        here = pathlib.Path(__file__).resolve().parent.parent / "flows"
        for name in ("task.py", "project.py", "meeting.py", "repo.py"):
            body = (here / name).read_text(encoding="utf-8")
            code = "\n".join(l for l in body.splitlines()
                             if not l.lstrip().startswith("#") and "`reply_broadcast`" not in l)
            self.assertNotIn("reply_broadcast", code, f"{name} 가 DM 에서 채팅창에도 띄운다")


class DateTest(unittest.TestCase):
    """`core` 의 순수 셈 — Slack 을 안 탄다."""

    WED = datetime.date(2026, 9, 23)

    def test_it_reads_the_day_people_mean(self):
        self.assertEqual(core.meet_when("내일", self.WED), "2026-09-24")
        self.assertEqual(core.meet_when("9/30", self.WED), "2026-09-30")
        self.assertEqual(core.meet_when("2026-10-05", self.WED), "2026-10-05")
        self.assertEqual(core.meet_when("목요일에 하자", self.WED), "2026-09-24")
        self.assertFalse(core.meet_when("다다음에", self.WED))

    def test_the_same_weekday_means_next_week(self):
        """「수요일에 하자」 를 수요일에 말하면 보통 다음 주 이야기다."""
        self.assertEqual(core.meet_when("수요일", self.WED), "2026-09-30")

    def test_a_weekday_letter_alone_is_not_a_weekday(self):
        """「일」·「토」 는 다른 말에도 흔하다 — 글자 하나만 보고 집으면 엉뚱한 날이 잡힌다."""
        self.assertIsNone(core.weekday_of("일정을 보자"))
        self.assertIsNone(core.weekday_of("토의합시다"))
        self.assertEqual(core.weekday_of("금요일"), 4)

    def test_every_other_week_is_not_every_week(self):
        """「격주」 에도 「주」 가 들어 있다 — 순서를 잘못 두면 매주로 먹힌다."""
        self.assertEqual(core.meet_every("격주"), "2week")
        self.assertEqual(core.meet_every("2주에 한 번"), "2week")
        self.assertEqual(core.meet_every("매주"), "week")
        self.assertEqual(core.meet_every("매달"), "month")
        self.assertIsNone(core.meet_every("몰라"))

    def test_weekly_means_seven_days_even_off_cycle(self):
        """**요일을 먼저 맞추고 주를 더한다** — 거꾸로 하면 매주인데 2주 뒤가 나온다
        (2026-09-23 에 만들자마자 잡은 고장)."""
        self.assertEqual(core.next_meet("week", 1, datetime.date(2026, 9, 29)), "2026-10-06")
        self.assertEqual(core.next_meet("week", 1, self.WED), "2026-09-29")

    def test_monthly_stays_on_the_same_weekday(self):
        """매달은 **같은 요일**로 — 날짜로 매기면 「31일」 이 없는 달이 생긴다."""
        d = datetime.date(2026, 9, 29)
        for _ in range(4):
            d = datetime.date.fromisoformat(core.next_meet("month", 1, d))
            self.assertEqual(d.weekday(), 1)

    def test_it_never_returns_the_same_day(self):
        for every in ("week", "2week", "month"):
            self.assertGreater(core.next_meet(every, 1, datetime.date(2026, 9, 29)), "2026-09-29")


if __name__ == "__main__":
    unittest.main()
