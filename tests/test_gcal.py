"""구글 캘린더 연동 (`gcal.py`, PA #45 v1) — 가짜 HTTP 로 돈다. 진짜 구글·Slack 은 부르지 않는다.

두 층을 본다.
  · `gcal.py` 자체 — RRULE·시간 없는 회의·`ready()`·`stop_series` (`_http` 를 가짜로 바꾼다)
  · `flows/meeting.py` 연결 — 멱등·다음 회차가 다시 안 만드는지·실패해도 회의는 남는지
    (`meeting.gcal` 을 통째로 가짜로 바꾼다 — Slack 은 `test_meeting.py` 와 같은 `Fake`)
"""
import asyncio
import datetime
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gcal  # noqa: E402
from flows import meeting  # noqa: E402
from store import STATE  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def make_meeting(**kw):
    base = {"id": "M1", "title": "주간 점검", "date": "2026-09-29", "time": [10, 0],
            "every": "once", "weekday": None}
    base.update(kw)
    return base


class FakeHttp:
    """`gcal._http` 를 대신한다 — 진짜 네트워크를 타지 않는다."""

    def __init__(self):
        self.calls = []
        self.n = 0
        self.fail = False           # 토큰 교환부터 막는다
        self.fail_events = False    # 토큰은 되는데 events.insert 만 막는다

    async def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.fail:
            return 500, {"error": "fake fail"}
        if url == gcal.TOKEN_URL:
            return 200, {"access_token": "fake-access", "expires_in": 3600}
        if method == "POST" and url.endswith("/events"):
            if self.fail_events:
                return 500, {"error": "fake events fail"}
            self.n += 1
            eid = f"evt{self.n}"
            return 200, {"id": eid, "htmlLink": f"https://calendar.google.com/{eid}"}
        if method == "PATCH":
            return 200, {"ok": True}
        return 200, {}

    def bodies(self, method="POST", suffix="/events"):
        return [kw.get("json") for m, u, kw in self.calls if m == method and u.endswith(suffix)]


class GcalBase(unittest.TestCase):
    """`gcal.py` 만 본다 — 토큰·클라이언트 파일을 시험마다 데이터 폴더에 두고 지운다."""

    def setUp(self):
        gcal.CLIENT_FILE.write_text(json.dumps({"client_id": "id", "client_secret": "secret"}),
                                    encoding="utf-8")
        gcal.TOKEN_FILE.write_text(json.dumps({"refresh_token": "r", "calendar_id": "cal-1"}),
                                   encoding="utf-8")
        self.http = FakeHttp()
        self.patch = mock.patch.object(gcal, "_http", self.http)
        self.patch.start()
        gcal._cache["token"], gcal._cache["exp"] = None, 0.0

    def tearDown(self):
        self.patch.stop()
        for f in (gcal.CLIENT_FILE, gcal.TOKEN_FILE):
            if f.exists():
                f.unlink()
        gcal._cache["token"], gcal._cache["exp"] = None, 0.0


class ReadyTest(GcalBase):
    def test_ready_needs_both_files(self):
        gcal.TOKEN_FILE.unlink()
        self.assertFalse(gcal.ready())

    def test_ready_needs_calendar_id(self):
        gcal.TOKEN_FILE.write_text(json.dumps({"refresh_token": "r"}), encoding="utf-8")
        self.assertFalse(gcal.ready())

    def test_ready_true_when_both_present(self):
        self.assertTrue(gcal.ready())

    def test_not_ready_calls_nothing(self):
        """`ready()` 가 False 면 `create` 가 HTTP 를 한 번도 안 부른다."""
        gcal.TOKEN_FILE.unlink()
        self.assertIsNone(run(gcal.create(make_meeting())))
        self.assertFalse(self.http.calls)


class RRuleTest(GcalBase):
    """정기 회의는 캘린더에 **반복 일정 하나** — RRULE 이 규칙대로 도는지."""

    def test_once_has_no_recurrence(self):
        m = make_meeting()
        made = run(gcal.create(m))
        self.assertIsNotNone(made)
        body = self.http.bodies()[0]
        self.assertNotIn("recurrence", body)
        self.assertEqual(m["gcal_id"], "evt1")
        self.assertIn("gcal_link", m)

    def test_a_meeting_without_time_is_skipped(self):
        """**시간이 없는 회의는 캘린더에 올리지 않는다** (사장님 결정)."""
        m = make_meeting(time=None)
        self.assertIsNone(run(gcal.create(m)))
        self.assertFalse(self.http.calls)
        self.assertNotIn("gcal_id", m)

    def test_weekly_rrule(self):
        m = make_meeting(every="week", weekday=1)          # 화요일
        run(gcal.create(m))
        self.assertEqual(self.http.bodies()[0]["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=TU"])
        self.assertEqual(m["gcal_rrule"], "FREQ=WEEKLY;BYDAY=TU")

    def test_biweekly_rrule(self):
        m = make_meeting(every="2week", weekday=3)          # 목요일
        run(gcal.create(m))
        self.assertEqual(self.http.bodies()[0]["recurrence"], ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=TH"])

    def test_monthly_rrule(self):
        # 매달은 「같은 요일의 같은 주차」 — core.next_meet 과 같은 셈. 9/22 는 넷째 화요일
        m = make_meeting(every="month", weekday=1, date="2026-09-22")
        run(gcal.create(m))
        self.assertEqual(self.http.bodies()[0]["recurrence"], ["RRULE:FREQ=MONTHLY;BYDAY=4TU"])

    def test_monthly_fifth_week_becomes_last(self):
        # 다섯째 주는 없는 달이 있다 — 「마지막 화요일」 로 둔다 (9/29 는 다섯째 화요일)
        m = make_meeting(every="month", weekday=1, date="2026-09-29")
        run(gcal.create(m))
        self.assertEqual(self.http.bodies()[0]["recurrence"], ["RRULE:FREQ=MONTHLY;BYDAY=-1TU"])

    def test_monthly_calendar_and_cards_agree_for_a_year(self):
        """**카드의 다음 회와 캘린더 반복이 같은 날이어야 한다** (2026-09-25 검토가 잡았다 — 9/22 에
        시작하면 카드는 10/20, 캘린더는 10/27 이었다). 반복 규칙을 손으로 펼쳐 12번 맞대 본다."""
        import calendar
        import core

        def expand(rule, year, month, wd):
            n = int(rule.split("BYDAY=")[1][:-2])
            days = [d for d in range(1, calendar.monthrange(year, month)[1] + 1)
                    if datetime.date(year, month, d).weekday() == wd]
            return datetime.date(year, month, days[n - 1 if n > 0 else n])

        for start in ("2026-09-01", "2026-09-08", "2026-09-22", "2026-09-29", "2026-10-08", "2026-12-31"):
            d = datetime.date.fromisoformat(start)
            nth = core.month_nth(d.weekday(), d)          # 만들 때 한 번 정해 적어 둔다 — 실제 흐름과 같다
            m = make_meeting(every="month", weekday=d.weekday(), date=start, nth=nth)
            run(gcal.create(m))
            rule = self.http.bodies()[-1]["recurrence"][0]
            card = d
            for _ in range(12):
                card = datetime.date.fromisoformat(core.next_meet("month", d.weekday(), card, nth=nth))
                self.assertEqual(card, expand(rule, card.year, card.month, d.weekday()), f"{start} 시작 · {rule}")

    def test_time_zone_is_seoul(self):
        m = make_meeting()
        run(gcal.create(m))
        body = self.http.bodies()[0]
        self.assertEqual(body["start"]["timeZone"], "Asia/Seoul")
        self.assertEqual(body["end"]["timeZone"], "Asia/Seoul")

    def test_default_length_is_sixty_minutes(self):
        m = make_meeting()
        run(gcal.create(m))
        body = self.http.bodies()[0]
        start = datetime.datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.datetime.fromisoformat(body["end"]["dateTime"])
        self.assertEqual((end - start).total_seconds() / 60, 60)

    def test_attendees_are_sent_when_given(self):
        m = make_meeting()
        run(gcal.create(m, attendees=["a@x.com", "b@x.com"]))
        self.assertEqual(self.http.bodies()[0]["attendees"], [{"email": "a@x.com"}, {"email": "b@x.com"}])

    def test_no_attendees_key_when_owner_only(self):
        m = make_meeting()
        run(gcal.create(m))
        self.assertNotIn("attendees", self.http.bodies()[0])

    def test_events_insert_failure_returns_none(self):
        """`events.insert` 가 실패해도(토큰은 받았는데) 예외를 던지지 않고 None."""
        self.http.fail_events = True
        self.assertIsNone(run(gcal.create(make_meeting())))


class StopSeriesTest(GcalBase):
    def test_it_patches_until_today(self):
        m = make_meeting(every="week", weekday=1, gcal_id="evt9", gcal_rrule="FREQ=WEEKLY;BYDAY=TU")
        run(gcal.stop_series(m))
        patch = [kw for method, url, kw in self.http.calls if method == "PATCH"][0]
        # 시간이 있는 일정이라 UNTIL 은 UTC 시각 — 오늘 밤 23:59:59 서울 = 오늘 14:59:59Z
        until = datetime.date.today().strftime("%Y%m%d") + "T145959Z"
        self.assertEqual(patch["json"]["recurrence"],
                         [f"RRULE:FREQ=WEEKLY;BYDAY=TU;UNTIL={until}"])

    def test_it_does_nothing_without_a_gcal_id(self):
        run(gcal.stop_series(make_meeting(every="week", weekday=1)))
        self.assertFalse(self.http.calls)


class TokenCacheTest(GcalBase):
    def test_it_reuses_the_cached_token(self):
        run(gcal.create(make_meeting()))
        calls_after_first = len(self.http.calls)
        run(gcal.create(make_meeting(id="M2")))
        token_calls = [c for c in self.http.calls if c[1] == gcal.TOKEN_URL]
        # 두 번 부르는 동안 토큰 교환은 한 번만 — 캐시가 있으면 다시 안 받는다
        self.assertEqual(len(token_calls), 1, self.http.calls)
        self.assertGreater(len(self.http.calls), calls_after_first - 1)


# ── flows/meeting.py 연결 — Slack 은 test_meeting.py 와 같은 Fake, 구글은 여기서 가짜로 ──

class FakeSlack:
    def __init__(self):
        self.sent = []

    async def api(self, s, method, body=None, **params):
        self.sent.append((method, body or params))
        if method == "chat.postMessage":
            return {"ok": True, "ts": f"7.{len(self.sent)}"}
        if method == "conversations.members":
            return {"ok": True, "members": ["U1", "U2", "UBOT"]}
        if method == "users.info":
            uid = (params or {}).get("user")
            if uid == "UBOT":
                return {"ok": True, "user": {"id": uid, "is_bot": True}}
            email = {"U1": "u1@x.com"}.get(uid)         # U2 는 이메일이 없다 — 권한 없는 상황을 흉내
            return {"ok": True, "user": {"id": uid, "profile": {"email": email}}}
        return {"ok": True}

    def texts(self):
        return [b.get("text", "") for m, b in self.sent if m == "chat.postMessage"]


class FakeGcal:
    """`meeting.gcal` 을 통째로 갈아 끼운다 — `flows/meeting.py` 는 `gcal.ready`·`gcal.create`·
    `gcal.stop_series`·`gcal.INVITE` 만 본다."""

    def __init__(self, ok=True, invite="owner", fail=False):
        self.ok, self.INVITE, self.fail = ok, invite, fail
        self.created, self.stopped = [], []

    def ready(self):
        return self.ok

    async def create(self, m, attendees=None):
        self.created.append((m["id"], attendees))
        if self.fail:
            raise RuntimeError("fake gcal 500")
        m["gcal_id"] = f"evt{len(self.created)}"
        m["gcal_link"] = f"https://x/{m['gcal_id']}"
        if (m.get("every") or "once") != "once" and m.get("weekday") is not None:
            m["gcal_rrule"] = "FREQ=WEEKLY;BYDAY=TU"
        return m

    async def stop_series(self, m):
        if not m.get("gcal_id"):                  # 진짜 `gcal.stop_series` 도 이때 조용히 넘어간다
            return
        self.stopped.append(m.get("gcal_id"))


class MeetingBase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSlack()
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
        STATE.pop("meetings", None)

    def made(self):
        return list(STATE.get("meetings", {}).values())


class MbuildTest(MeetingBase):
    """DM 흐름 전체(`maybe`)를 거치지 않고, 다 물은 뒤의 `_mbuild` 만 — 캘린더 연결이 여기 붙는다."""

    def _st(self, **kw):
        st = {"title": "주간 점검", "every": "once", "date": "2026-09-30", "time": [14, 0],
              "th": "1.0"}
        st.update(kw)
        return st

    def test_it_creates_the_event_and_says_so(self):
        fg = FakeGcal()
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(), "U0"))
        self.assertEqual(len(fg.created), 1)
        m = self.made()[0]
        self.assertEqual(m["gcal_id"], "evt1")
        self.assertIn("구글 캘린더", self.fake.texts()[-1])
        self.assertIn("evt1", self.fake.texts()[-1])

    def test_owner_invite_sends_no_attendees(self):
        fg = FakeGcal(invite="owner")
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(), "U0"))
        self.assertIsNone(fg.created[0][1])

    def test_room_invite_gathers_channel_emails_and_skips_the_bot(self):
        fg = FakeGcal(invite="room")
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(pkey=""), "U0"))
        attendees = fg.created[0][1]
        self.assertEqual(attendees, ["u1@x.com"])          # U2 는 이메일이 없어 빠지고, UBOT 은 아예 뺀다

    def test_chosen_people_are_invited_instead_of_the_room(self):
        """만들 때 `@사람` 으로 고르셨으면 **그분들만** — 방 전원이 아니다 (2026-09-24)."""
        fg = FakeGcal(invite="room")
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(pkey="", who=["U1"]), "U0"))
        self.assertEqual(fg.created[0][1], ["u1@x.com"])
        self.assertNotIn(("conversations.members", {"channel": meeting.mch(self.made()[0]), "limit": 1000}),
                         self.fake.sent, "고른 사람이 있는데 방 사람을 읽었다")

    def test_chosen_people_work_even_in_owner_mode(self):
        fg = FakeGcal(invite="owner")
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(who=["U1"]), "U0"))
        self.assertEqual(fg.created[0][1], ["u1@x.com"])

    def test_everyone_means_the_room_even_in_owner_mode(self):
        """⑤ 에서 「모두」 를 고르셨으면 설정이 owner 여도 방 사람 전원이다."""
        fg = FakeGcal(invite="owner")
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(pkey="", who_all=True), "U0"))
        self.assertEqual(fg.created[0][1], ["u1@x.com"])

    def test_missing_email_permission_still_makes_the_meeting_and_warns(self):
        fg = FakeGcal(invite="room")
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(pkey=""), "U0"))
        self.assertTrue(self.made(), "권한이 없다고 회의까지 안 만들면 안 된다")
        self.assertIn("초대는 못 했어요", self.fake.texts()[-1])

    def test_not_connected_is_quiet(self):
        """연결 안 된 팀 — 회의는 그대로 만들어지고, 캘린더 이야기는 아예 없다."""
        fg = FakeGcal(ok=False)
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(), "U0"))
        self.assertTrue(self.made())
        self.assertNotIn("캘린더", self.fake.texts()[-1])

    def test_gcal_failure_does_not_block_the_meeting(self):
        """**캘린더 실패해도 회의는 만들어진다** — 계획: 「로그 ⚠️ + DM 스레드에 한 줄」."""
        fg = FakeGcal(fail=True)
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(), "U0"))
        self.assertTrue(self.made(), "캘린더가 실패했다고 회의까지 안 만들면 안 된다")
        self.assertNotIn("gcal_id", self.made()[0])
        self.assertIn("캘린더에는 못 올렸어요", self.fake.texts()[-1])

    def test_idempotent_does_not_create_twice(self):
        """회의 dict 에 `gcal_id` 가 있으면 다시 만들지 않는다 (재시작·재시도)."""
        fg = FakeGcal()
        with mock.patch.object(meeting, "gcal", fg):
            run(meeting._mbuild(None, "D0", "1.0", self._st(), "U0"))
            m = self.made()[0]
            run(meeting._gcal_note(None, m))               # 같은 회의로 한 번 더 불러 본다
        self.assertEqual(len(fg.created), 1, "gcal_id 가 있는데 또 만들었다")


class DueMeetingsTest(MeetingBase):
    """정기 회의의 다음 회차 — **캘린더를 다시 만들지 않는다** (반복 일정 하나로 충분하다)."""

    def test_the_next_occurrence_does_not_touch_gcal(self):
        fg = FakeGcal()
        with mock.patch.object(meeting, "gcal", fg):
            m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM",
                                        every="week", date="2026-09-29", weekday=1))
            m["gcal_id"], m["gcal_rrule"] = "evt1", "FREQ=WEEKLY;BYDAY=TU"
            made = run(meeting.due_meetings(None, datetime.date(2026, 10, 6)))
        self.assertEqual(len(made), 1)
        self.assertFalse(fg.created, "다음 회차를 열면서 캘린더를 또 만들었다")

    def test_the_new_card_inherits_the_calendar_event(self):
        """새 카드가 `gcal_id` 를 물려받아야 — 안 그러면 그 카드의 「정기 끄기」 가
        캘린더 쪽은 못 끈다 (계획: 「새 카드는 원래 회의의 gcal_id 를 물려받거나」)."""
        fg = FakeGcal()
        with mock.patch.object(meeting, "gcal", fg):
            m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM",
                                        every="week", date="2026-09-29", weekday=1))
            m["gcal_id"], m["gcal_link"], m["gcal_rrule"] = "evt1", "https://x/evt1", "FREQ=WEEKLY;BYDAY=TU"
            run(meeting.due_meetings(None, datetime.date(2026, 10, 6)))
            new = [x for x in self.made() if x["id"] != m["id"]][0]
            self.assertEqual(new["gcal_id"], "evt1")
            run(meeting.stop_every(None, new["card_ts"], "U0"))
        self.assertEqual(fg.stopped, ["evt1"], "새 카드에서 정기를 꺼도 캘린더가 안 멈췄다")


    def test_the_new_card_keeps_time_project_and_nth(self):
        """**둘째 회부터 시간이 비면 10분 전 알림이 안 간다** — 시간·프로젝트·주차를 넘긴다 (2026-09-25 검토)."""
        fg = FakeGcal()
        with mock.patch.object(meeting, "gcal", fg):
            m = run(meeting.new_meeting(None, "월간 점검", None, "이동원", "C0TEAM",
                                        every="month", date="2026-09-22", weekday=1, time=[14, 0], pkey="CH"))
            m["nth"] = 4
            run(meeting.due_meetings(None, datetime.date(2026, 10, 27)))
            new = [x for x in self.made() if x["id"] != m["id"]][0]
        self.assertEqual(new["date"], "2026-10-27", "넷째 화요일")
        self.assertEqual(new["time"], [14, 0])
        self.assertEqual(new["pkey"], "CH")
        self.assertEqual(new["nth"], 4)


class StopEveryTest(MeetingBase):
    def test_stop_every_ends_the_calendar_series_too(self):
        fg = FakeGcal()
        with mock.patch.object(meeting, "gcal", fg):
            m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM",
                                        every="week", date="2026-09-29", weekday=1))
            m["gcal_id"], m["gcal_rrule"] = "evt1", "FREQ=WEEKLY;BYDAY=TU"
            run(meeting.stop_every(None, m["card_ts"], "U0"))
        self.assertEqual(fg.stopped, ["evt1"])
        self.assertEqual(m["every"], "once")

    def test_a_meeting_never_sent_to_gcal_is_skipped_quietly(self):
        fg = FakeGcal()
        with mock.patch.object(meeting, "gcal", fg):
            m = run(meeting.new_meeting(None, "주간 점검", None, "이동원", "C0TEAM",
                                        every="week", date="2026-09-29", weekday=1))
            run(meeting.stop_every(None, m["card_ts"], "U0"))
        self.assertFalse(fg.stopped)


if __name__ == "__main__":
    unittest.main()
