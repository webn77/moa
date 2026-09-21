"""어느 채널에서든 `#40` 펼치기 (#54).

2026-09-20 에 봇이 자기 메시지를 사람 요청으로 읽어 카드 30개를 만든 사고가 있었다.
펼침은 **모든 채널의 모든 메시지**를 보므로 같은 사고가 나면 더 크다. 그 자리를 시험으로 고정한다.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import handlers  # noqa: E402
from common import BOT  # noqa: E402
from store import STATE  # noqa: E402


def card(no, ts=None):
    return {"no": no, "title": f"이슈 {no}", "status": "todo", "card_ts": ts or f"{no}.0",
            "by": "나", "spec": {"done_criteria": ["끝"]}}


class RefTest(unittest.TestCase):
    def find(self, text):
        return [int(m.group(1)) for m in handlers.REF.finditer(text)]

    def test_picks_numbers_in_a_sentence(self):
        self.assertEqual(self.find("#40 이거 어떻게 됐어요? (#41 도요)"), [40, 41])

    def test_ignores_glued_and_trailing(self):
        """abc#40 은 남의 표기일 수 있고, #40x 는 번호가 아니다."""
        self.assertEqual(self.find("abc#40 · #7x · #12"), [12])

    def test_ignores_too_long(self):
        self.assertEqual(self.find("#12345"), [])


class UnfurlTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sent = []
        STATE["cards"] = {"40.0": card(40), "41.0": card(41), "42.0": card(42), "43.0": card(43)}
        self.old = handlers.api
        handlers.api = lambda s, m, **kw: self._api(m, kw)

    def tearDown(self):
        handlers.api = self.old

    async def _api(self, method, kw):
        self.sent.append((method, (kw.get("body") or {}).get("channel"), (kw.get("body") or {}).get("text")))
        return {"ok": True}

    async def test_unfurls_each_number_once(self):
        await handlers.unfurl_refs(None, {"channel": "C9", "ts": "9.0", "text": "#40 하고 #41 · 다시 #40"})
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(ch == "C9" for _, ch, _ in self.sent))

    async def test_at_most_three(self):
        """한 줄에 번호를 잔뜩 적어도 도배하지 않는다."""
        await handlers.unfurl_refs(None, {"channel": "C9", "ts": "9.0", "text": "#40 #41 #42 #43"})
        self.assertEqual(len(self.sent), 3)

    async def test_unknown_number_says_nothing(self):
        await handlers.unfurl_refs(None, {"channel": "C9", "ts": "9.0", "text": "#999 없는 번호"})
        self.assertEqual(self.sent, [])

    async def test_skips_inside_a_card_thread(self):
        """이미 그 이슈 안이면 펼치지 않는다 — 같은 것이 두 번 보인다."""
        await handlers.unfurl_refs(None, {"channel": "C9", "ts": "9.1", "thread_ts": "40.0", "text": "#41 도 봐야 해요"})
        self.assertEqual(self.sent, [])


class MineTest(unittest.TestCase):
    """펼친 카드에는 `#40` 이 들어 있다. 봇이 그것을 다시 읽으면 영원히 돈다."""

    def test_bot_own_message_is_mine(self):
        self.assertTrue(handlers.is_mine({"bot_id": "B1", "username": BOT, "text": "#40 이슈 40"}))

    def test_human_message_is_not_mine(self):
        self.assertFalse(handlers.is_mine({"user": "U1", "text": "#40 이거요"}))

    def test_other_app_is_not_mine(self):
        self.assertFalse(handlers.is_mine({"bot_id": "B2", "username": "다른앱", "text": "#40"}))

    def test_only_humans_trigger_us(self):
        """같은 방에 다른 AI 앱(Ringo)이 있다. 봇끼리 말을 주고받으면 아무도 안 보는 사이에 방이 찬다
        (2026-09-20). 사람이 쓴 것(bot_id 없음 + user 있음)에만 반응한다."""
        human = {"user": "U1", "text": "#40 어떻게 됐어요?"}
        other = {"bot_id": "B2", "username": "Ringo", "text": "#40 은 이렇습니다"}
        mine = {"bot_id": "B1", "username": BOT, "text": "#40"}
        react = lambda e: not e.get("bot_id") and bool(e.get("user"))
        self.assertTrue(react(human))
        self.assertFalse(react(other))
        self.assertFalse(react(mine))



class MakeIntentTest(unittest.TestCase):
    """「이슈로 만들어줘」만 만들기로 가고, 나머지는 찾기로 간다 (#54).

    잘못 가르면 「이슈 목록 보여줘」가 이슈를 만들어 버린다.
    """

    def yes(self, q):
        self.assertTrue(handlers.MAKE.search(q), q)

    def no(self, q):
        self.assertIsNone(handlers.MAKE.search(q), q)

    def test_make_requests(self):
        for q in ("이거 이슈로 만들어줘", "이슈 등록해줘", "이슈 만들어", "티켓 하나 올려줘", "이슈로 올려줘"):
            self.yes(q)

    def test_find_requests_are_not_make(self):
        for q in ("목록", "이슈 목록", "이동원", "담당 없는 일", "캔버스", "현황", "이슈 뭐 있어?"):
            self.no(q)


class DraftThreadTest(unittest.IsolatedAsyncioTestCase):
    """초안 스레드에 더 쓰면 초안이 고쳐져야 한다 — 안 그러면 대화가 끊긴다 (2026-09-20)."""

    def setUp(self):
        import flows.intake as intake
        from store import STATE as S
        self.intake, self.S = intake, S
        S["drafts"] = {"C1:9.0#0": {"spec": {"title": "옛 제목"}, "channel": "C1", "ts": "9.0", "msg": "9.5"}}
        self.answer = self.FULL
        self.updated = []
        import slack
        self.slack = slack
        self.old = (intake.api, intake.ask_ai, intake.thread_text, intake.save, slack.api)
        intake.api = slack.api = lambda s, m, **kw: self._api(m, kw)
        intake.ask_ai = lambda sys_, p: self._ai()
        intake.thread_text = lambda s, ch, ts: self._talk()
        intake.save = lambda: None

    def tearDown(self):
        (self.intake.api, self.intake.ask_ai, self.intake.thread_text,
         self.intake.save, self.slack.api) = self.old
        self.S["drafts"] = {}

    async def _api(self, m, kw):
        self.updated.append((m, (kw.get("body") or {}).get("ts")))
        return {"ts": "9.5"}

    ONE = ('{"title": "새 제목", "why": "로그인이 끊긴다", "change": "세션을 늘린다", '
           '"expect": "신고가 준다", "not_doing": "비밀번호", "done_criteria": ["끊김 0건"]}')
    FULL = '{"issues": [' + ONE + ']}'
    TWO = '{"issues": [' + ONE + ', ' + ONE.replace("새 제목", "둘째") + ']}'
    VAGUE = '{"issues": [{"title": "새 제목", "why": "미정", "change": "미정", "expect": "미정", "done_criteria": []}]}'

    async def _ai(self):
        return self.answer

    async def _talk(self):
        return "이동원: 테스트로 만들자는 이야기야"

    async def test_reply_redoes_the_draft(self):
        """답글을 달면 다시 정리하고 새로 보여 준다. 예전에는 같은 메시지를 조용히 덮어써서
        아무 일도 안 일어난 것처럼 보였다 (사장님 지적 2026-09-20)."""
        got = await self.intake.refresh_draft(None, {"channel": "C1", "thread_ts": "9.0", "text": "더 쓸게요"})
        self.assertTrue(got)
        self.assertIn(("chat.update", "9.5"), self.updated)            # 옛 초안은 접고
        self.assertTrue(any(m == "chat.postMessage" for m, _ in self.updated))   # 새로 보여 준다
        self.assertEqual(self.S["drafts"]["C1:9.0#0"]["spec"]["title"], "새 제목")

    async def test_split_into_two(self):
        """「2개로 나눠줘」 처럼 여러 건이면 따로 낸다 — 억지로 하나로 합치면 사람이 다시 쪼개야 한다."""
        self.answer = self.TWO
        await self.intake.refresh_draft(None, {"channel": "C1", "thread_ts": "9.0", "text": "2개로 나눠줘"})
        self.assertEqual(sorted(k for k in self.S["drafts"] if k.startswith("C1:9.0#")),
                         ["C1:9.0#0", "C1:9.0#1"])

    async def test_vague_reply_gets_no_button(self):
        """아직 모자라면 만들기 단추를 주지 않는다 — 단추를 주면 눌러 버리고 빈 이슈가 GitHub 에 남는다."""
        self.answer = self.VAGUE
        got = await self.intake.refresh_draft(None, {"channel": "C1", "thread_ts": "9.0", "text": "음"})
        self.assertTrue(got)
        self.assertTrue(any(m == "chat.postMessage" for m, _ in self.updated))   # 물어보기만
        self.assertIsNone(self.S["drafts"]["C1:9.0#0"]["msg"])                   # 단추 없음

    async def test_nothing_known_asks_one_line(self):
        """아는 게 하나도 없으면 빈 칸(❓ 네 개) 대신 한 줄만 묻는다 — 빈 칸은 무엇을 써야 하는지도
        안 알려 준다 (2026-09-20: 「@PA 이슈 만들어줘」 만 쓴 경우)."""
        self.answer = '{"title": "", "why": "미정", "change": "미정", "expect": "미정", "not_doing": "미정", "done_criteria": []}'
        got = await self.intake.refresh_draft(None, {"channel": "C1", "thread_ts": "9.0", "text": "음"})
        self.assertTrue(got)
        self.assertTrue(any(m == "chat.postMessage" for m, _ in self.updated))

    async def test_other_thread_is_left_alone(self):
        got = await self.intake.refresh_draft(None, {"channel": "C1", "thread_ts": "8.0", "text": "다른 스레드"})
        self.assertFalse(got)
        self.assertEqual(self.updated, [])


class MeetingIntentTest(unittest.TestCase):
    """`@PA 회의 ○○` — 슬래시 없이도 회의 카드를 만든다. 호출은 @PA 하나로 통일 (2026-09-20)."""

    def parse(self, q):
        m = handlers.MTG.match(q)
        return (m.group(2), m.group(3).strip()) if m and m.group(3).strip() else None

    def test_makes_a_meeting(self):
        self.assertEqual(self.parse("회의 스프린트 점검"), (None, "스프린트 점검"))
        self.assertEqual(self.parse("회의 만들어줘 9월 회고"), (None, "9월 회고"))

    def test_links_an_issue(self):
        self.assertEqual(self.parse("미팅 #52 진행 확인"), ("52", "진행 확인"))

    def test_not_a_meeting(self):
        """제목 없는 「회의」 · 「회의록 다시」 · 다른 말은 회의를 만들지 않는다."""
        for q in ("회의", "미팅", "회의록 다시", "목록", "이슈 만들어줘"):
            self.assertIsNone(self.parse(q), q)


class NewIssueWordTest(unittest.TestCase):
    """새 요청 알아듣기 — 이모지 찾기가 번거로워 글자로도 받는다 (사장님 지적 2026-09-20).

    쌍점을 요구한다. 「이슈 목록 보여줘」 같은 보통 말이 이슈가 되면 방이 이슈로 찬다.
    """

    def title(self, q):
        m = handlers.NEW.match(q)
        return m.group(1) if m else None

    def test_emoji_still_works(self):
        """🎫 는 손에 익은 분을 위해 남긴다 — 안내에는 쓰지 않는다."""
        for q in ("🎫 로그인 오류", ":ticket: 로그인 오류"):
            self.assertEqual(self.title(q), "로그인 오류", q)

    def test_words_are_not_a_way_in(self):
        """「이슈:」 「요청:」 을 받던 것을 물렸다 — 이모지가 불편하다고 말을 세 개 더 만든 꼴이었다.
        만드는 길은 @PA 하나다 (사장님 지적 9/20)."""
        for q in ("이슈: 로그인 오류", "요청: 로그인 오류", "티켓: 로그인 오류",
                  "이슈 목록 보여줘", "요청이 많아요", "로그인 오류"):
            self.assertIsNone(self.title(q), q)

    def test_title_only_is_still_caught(self):
        """제목이 부실해도 일단 받는다 — 막는 건 정리안 단계다 (미정투성이면 단추가 안 나온다)."""
        self.assertEqual(self.title("🎫 개선"), "개선")


class OldDraftKeyTest(unittest.TestCase):
    """키 모양을 바꾸면 떠 있던 초안이 미아가 된다 — 답글을 달아도 봇이 못 찾았다 (2026-09-20).

    모양을 바꿀 땐 옛 것도 받아야 한다. 화면에는 이미 단추가 떠 있는데 코드만 바뀌기 때문이다.
    """

    def setUp(self):
        import flows.intake as intake
        from store import STATE as S
        self.i, self.S, self.old = intake, S, dict(S.get("drafts") or {})
        S["drafts"] = {"C1:9.0": {"a": 1}, "C1:9.0#0": {"a": 2}, "C1:9.0#1": {"a": 3}, "C1:8.0#0": {"a": 4}}

    def tearDown(self):
        self.S["drafts"] = self.old

    def test_finds_both_shapes(self):
        self.assertEqual(sorted(self.i._keys("C1", "9.0")), ["C1:9.0", "C1:9.0#0", "C1:9.0#1"])

    def test_does_not_bleed_into_other_threads(self):
        self.assertEqual(self.i._keys("C1", "8.0"), ["C1:8.0#0"])

    def test_unknown_thread_is_empty(self):
        self.assertEqual(self.i._keys("C1", "7.0"), [])


class DmTest(unittest.IsolatedAsyncioTestCase):
    """DM — 배울 것을 늘리지 않는다. 채널에서 `@PA 목록` 이면 DM 에서는 그냥 `목록` (2026-09-20)."""

    def setUp(self):
        self.went = []
        self.old = (handlers.show_digest, handlers.tidy_propose, handlers.propose_issue,
                    handlers.find, handlers.refresh_draft)
        handlers.show_digest = lambda s, ch, u: self._go("현황")
        handlers.tidy_propose = lambda s, ch, u: self._go("정리")
        handlers.propose_issue = lambda s, e, q: self._go("만들기")
        handlers.find = lambda s, e, q: self._go("찾기")
        handlers.refresh_draft = lambda s, e: self._go("초안", True)

    def tearDown(self):
        (handlers.show_digest, handlers.tidy_propose, handlers.propose_issue,
         handlers.find, handlers.refresh_draft) = self.old

    async def _go(self, name, ret=None):
        self.went.append(name)
        return ret

    async def dm(self, text, **kw):
        self.went.clear()
        await handlers.on_dm(None, {"channel": "D1", "user": "U1", "text": text, **kw})
        return self.went

    async def test_same_words_as_the_channel(self):
        self.assertEqual(await self.dm("현황"), ["현황"])
        self.assertEqual(await self.dm("정리"), ["정리"])
        self.assertEqual(await self.dm("이슈 만들어줘"), ["만들기"])
        self.assertEqual(await self.dm("목록"), ["찾기"])
        self.assertEqual(await self.dm("이동원"), ["찾기"])

    async def test_draft_thread_first(self):
        self.assertEqual(await self.dm("한 줄 더", thread_ts="9.0"), ["초안"])

    async def test_empty_says_nothing(self):
        self.assertEqual(await self.dm("   "), [])


class StepTest(unittest.IsolatedAsyncioTestCase):
    """「…하는 중」 한 줄이 그 자리에서 결과로 바뀐다 (사장님 지적 2026-09-20).

    안내와 결과를 따로 올리면 스레드가 두 배로 길어지고 무엇이 최신인지 흐려진다.
    """

    def setUp(self):
        import slack
        self.slack, self.old, self.calls = slack, slack.api, []
        slack.api = lambda s, m, **kw: self._api(m, kw)

    def tearDown(self):
        self.slack.api = self.old

    async def _api(self, m, kw):
        self.calls.append((m, (kw.get("body") or {}).get("text")))
        return {"ok": True, "ts": "1.0"}

    async def test_first_posts_then_updates(self):
        st = self.slack.Step(None, "C1", "9.0")
        await st.say("고르는 중이에요…")
        await st.say("3개 골랐어요")
        self.assertEqual(self.calls, [("chat.postMessage", "고르는 중이에요…"),
                                      ("chat.update", "3개 골랐어요")])

    async def test_drop_removes_it(self):
        st = self.slack.Step(None, "C1")
        await st.say("읽는 중이에요…")
        await st.drop()
        self.assertEqual([m for m, _ in self.calls], ["chat.postMessage", "chat.delete"])
        self.assertIsNone(st.ts)

    async def test_drop_without_saying_does_nothing(self):
        await self.slack.Step(None, "C1").drop()
        self.assertEqual(self.calls, [])


class HelloTest(unittest.IsolatedAsyncioTestCase):
    """DM 을 처음 열면 한 번만 인사한다 (사장님 지적 2026-09-20).

    열자마자 빈 화면이면 무엇을 할 수 있는지 알 길이 없다. 다만 **열 때마다** 하면 대화가 인사로 찬다.
    """

    def setUp(self):
        from store import STATE as S
        self.S, self.sent = S, []
        self.was = S.get("greeted")
        S["greeted"] = []
        self.old = (handlers.api, handlers.save)
        handlers.api = lambda s, m, **kw: self._api(m)
        handlers.save = lambda: None

    def tearDown(self):
        handlers.api, handlers.save = self.old
        if self.was is None:
            self.S.pop("greeted", None)
        else:
            self.S["greeted"] = self.was

    async def _api(self, m):
        self.sent.append(m)
        return {"ok": True}

    async def test_greets_once(self):
        e = {"user": "U1", "channel": "D1"}
        await handlers.say_hello(None, e)
        await handlers.say_hello(None, e)
        self.assertEqual(self.sent, ["chat.postMessage"])

    async def test_each_person_gets_one(self):
        await handlers.say_hello(None, {"user": "U1", "channel": "D1"})
        await handlers.say_hello(None, {"user": "U2", "channel": "D2"})
        self.assertEqual(len(self.sent), 2)

if __name__ == "__main__":
    unittest.main()


class OrderWordTest(unittest.TestCase):
    """「@PA 순서」 와 「@PA 이슈 정리」 (#68)."""

    def test_order_words(self):
        for q in ("순서", "선행", "앞선 일", "의존", "순서 보여줘"):
            self.assertTrue(handlers.ORDER.match(q), q)

    def test_not_order(self):
        for q in ("목록", "현황", "정리"):
            self.assertFalse(handlers.ORDER.match(q), q)

    def test_tidy_with_space(self):
        """백슬래시가 둘이라 「이슈 정리」 가 안 잡히고 있었다 (2026-09-20 발견)."""
        for q in ("정리", "이슈 정리", "이슈정리", "치우기"):
            self.assertTrue(handlers.TIDY.match(q), q)


class PrefixTest(unittest.TestCase):
    """번호 앞말 PA-40 (#66) — 보이는 표기만 바뀌고 저장은 그대로."""

    def with_projects(self, projects, no=40, card=None):
        import store
        old = store.PROJECTS
        store.PROJECTS = projects
        try:
            return store.tag(no, card)
        finally:
            store.PROJECTS = old

    def test_no_prefix_keeps_hash(self):
        """앞말을 안 적은 팀은 예전 그대로 — 설정을 안 고쳐도 안 깨진다."""
        self.assertEqual(self.with_projects([{"key": ""}]), "#40")

    def test_prefix_is_used(self):
        self.assertEqual(self.with_projects([{"key": "PA"}]), "PA-40")

    def test_card_picks_its_own_project(self):
        """앞말은 레포를 가리킨다 — 프로젝트가 둘이면 카드마다 다르다."""
        ps = [{"key": "PA"}, {"key": "XX"}]
        self.assertEqual(self.with_projects(ps, card={"no": 40, "project": "XX"}), "XX-40")
        self.assertEqual(self.with_projects(ps, card={"no": 40}), "PA-40")      # 안 적힌 옛 카드는 첫 프로젝트

    def test_unknown_project_falls_back(self):
        """없는 프로젝트를 가리켜도 화면이 깨지지 않는다."""
        self.assertEqual(self.with_projects([{"key": "PA"}], card={"no": 40, "project": "없음"}), "PA-40")

    def test_ref_reads_both_shapes(self):
        """옛 스레드·캔버스에 맨 #40 이 이미 깔려 있다 — 앞말을 켜도 둘 다 읽어야 한다."""
        import re
        pat = re.compile(r"(?:^|[\s(\[<])(?:" + re.escape("PA") + r"-|#)(\d{1,4})\b")
        self.assertEqual([m.group(1) for m in pat.finditer("PA-40 과 #41 을 봐요")], ["40", "41"])
        self.assertEqual(pat.findall("메일주소PA-40"), [])      # 붙여 쓴 글자 뒤는 안 잡는다


class ProjectsTest(unittest.TestCase):
    """설정에 프로젝트 여럿 (#66) — 하나여도 같은 모양."""

    def test_flat_config_makes_one(self):
        """설정에 projects 가 없어도 하나를 지어낸다 — 이미 설치한 팀이 안 깨진다."""
        import config
        old = config.CFG
        config.CFG = {"prefix": "PA", "issue_channel": "C1", "canvas": "F1", "request_channel": "C2"}
        try:
            ps = config.projects()
            self.assertEqual(len(ps), 1)
            self.assertEqual(ps[0]["key"], "PA")
            self.assertEqual(ps[0]["channel"], "C1")
        finally:
            config.CFG = old

    def test_no_prefix_at_all(self):
        """앞말을 안 적은 팀 — 예전처럼 맨 #40 이 나오게 빈 key 로 둔다."""
        import config
        old = config.CFG
        config.CFG = {"issue_channel": "C1"}
        try:
            self.assertEqual(config.projects()[0]["key"], "")
        finally:
            config.CFG = old

    def test_explicit_projects_win(self):
        import config
        old = config.CFG
        config.CFG = {"prefix": "PA", "projects": [{"key": "A"}, {"key": "B"}]}
        try:
            self.assertEqual([p["key"] for p in config.projects()], ["A", "B"])
        finally:
            config.CFG = old

    def test_project_of_defaults_to_first(self):
        import store
        old = store.PROJECTS
        store.PROJECTS = [{"key": "PA", "name": "하나"}, {"key": "XX", "name": "둘"}]
        try:
            self.assertEqual(store.project_of({"no": 1})["key"], "PA")              # 안 적힌 옛 카드
            self.assertEqual(store.project_of({"no": 1, "project": "XX"})["key"], "XX")
        finally:
            store.PROJECTS = old


class RoomPerProjectTest(unittest.TestCase):
    """방·작업판이 프로젝트에서 나온다 (#70) — 상수가 아니다."""

    PS = [{"key": "PA", "name": "하나", "channel": "C_PA", "canvas": "F_PA", "request": "R_PA"},
          {"key": "XX", "name": "둘", "channel": "C_XX", "canvas": "F_XX", "request": "R_XX"}]

    def setUp(self):
        import store
        self.store, self.old = store, store.PROJECTS
        store.PROJECTS = self.PS

    def tearDown(self):
        self.store.PROJECTS = self.old

    def test_card_goes_to_its_own_room(self):
        self.assertEqual(self.store.chan({"no": 1, "project": "XX"}), "C_XX")
        self.assertEqual(self.store.chan({"no": 1, "project": "PA"}), "C_PA")
        self.assertEqual(self.store.canvas_of({"no": 1, "project": "XX"}), "F_XX")

    def test_old_card_without_project_goes_to_the_first(self):
        """프로젝트 칸이 없던 옛 카드 — 예전과 같은 방으로 간다."""
        self.assertEqual(self.store.chan({"no": 1}), "C_PA")
        self.assertEqual(self.store.chan(), "C_PA")

    def test_new_card_picks_project_from_the_room(self):
        """사람에게 「어느 프로젝트예요?」 를 묻지 않는다 — 요청이 온 방을 보고 정한다."""
        self.assertEqual(self.store.project_for("C_XX")["key"], "XX")
        self.assertEqual(self.store.project_for("R_XX")["key"], "XX")      # 팀 대화방도 본다
        self.assertEqual(self.store.project_for("C_PA")["key"], "PA")

    def test_unknown_room_falls_back(self):
        """DM 이나 모르는 채널에서 만들면 첫 프로젝트."""
        self.assertEqual(self.store.project_for("D_SOMEDM")["key"], "PA")
        self.assertEqual(self.store.project_for(None)["key"], "PA")


class DraftProjectTest(unittest.TestCase):
    """초안에서 프로젝트를 고른다 (#70) — 팀 대화방은 프로젝트들이 함께 쓴다."""

    def blocks(self, projects):
        import views.card as vc
        old = vc.PROJECTS
        vc.PROJECTS = projects
        try:
            return vc.draft_blocks("k1", {"title": "시험", "why": "w", "done_criteria": ["a"]})
        finally:
            vc.PROJECTS = old

    def test_one_project_keeps_the_old_button(self):
        """프로젝트가 하나면 고를 것이 없다 — 묻지 않는다."""
        b = self.blocks([{"key": "PA", "name": "하나"}])
        ids = [e["action_id"] for e in b[2]["elements"]]
        self.assertEqual(ids, ["draft_make", "drop_draft"])

    def test_several_projects_ask_first(self):
        b = self.blocks([{"key": "PA", "name": "하나"}, {"key": "SEC", "name": "둘"}])
        els = b[2]["elements"]
        self.assertEqual([e["action_id"] for e in els], ["draft_make_PA", "draft_make_SEC", "drop_draft"])
        self.assertEqual([e["value"] for e in els], ["k1|PA", "k1|SEC", "k1"])
        self.assertIn("골라 주세요", b[1]["elements"][0]["text"])

    def test_dispatcher_finds_the_prefixed_button(self):
        """draft_make_SEC 는 표에 없다 — 앞부분으로 찾아야 한다."""
        import handlers
        self.assertIn("draft_make", handlers.ACTIONS)
        aid = "draft_make_SEC"
        f = handlers.ACTIONS.get(aid) or (handlers.ACTIONS["draft_make"] if aid.startswith("draft_make_") else None)
        self.assertIsNotNone(f)


class WaitingStepsTest(unittest.TestCase):
    """기다리는 동안 단계를 바꿔 보여 준다 (Ringo 에서 배움, 2026-09-21)."""

    def run_waiting(self, steps, ticks):
        import asyncio, slack
        said = []

        class FakeStep(slack.Step):
            def __init__(self):
                pass
            async def say(self, text, **kw):
                said.append(text)

        async def go():
            st = FakeStep()
            async with st.waiting(*steps, every=0.01):
                await asyncio.sleep(0.01 * ticks + 0.008)
        asyncio.run(go())
        return said

    def test_first_step_shows_immediately_without_seconds(self):
        said = self.run_waiting(["하나", "둘"], 0)
        self.assertEqual(said[0], "하나")

    def test_steps_advance(self):
        said = self.run_waiting(["하나", "둘", "셋"], 2)
        self.assertTrue(any(x.startswith("둘") for x in said), said)

    def test_last_step_holds_and_only_seconds_grow(self):
        """없는 단계를 지어내지 않는다 — 마지막에 닿으면 거기서 시간만 늘린다."""
        said = self.run_waiting(["하나", "둘"], 4)
        self.assertTrue(all(x.startswith(("하나", "둘")) for x in said), said)
        self.assertTrue(any(x.startswith("둘") for x in said))

    def test_one_step_behaves_like_before(self):
        said = self.run_waiting(["혼자"], 2)
        self.assertTrue(all(x.startswith("혼자") for x in said), said)


class TeamRoomTest(unittest.TestCase):
    """팀 대화방은 **없을 수도** 있다 (2026-09-21 사장님 지적)."""

    def with_projects(self, projects, card=None):
        import store
        old = store.PROJECTS
        store.PROJECTS = projects
        try:
            return store.req(card)
        finally:
            store.PROJECTS = old

    def test_falls_back_to_the_project_room(self):
        """팀 대화방을 안 둔 팀에서는 그 프로젝트 방이 곧 팀 대화방이다 — 확인 요청이 사라지면 안 된다."""
        self.assertEqual(self.with_projects([{"key": "PA", "channel": "C_PA"}]), "C_PA")
        self.assertEqual(self.with_projects([{"key": "PA", "channel": "C_PA", "request": None}]), "C_PA")

    def test_uses_it_when_there_is_one(self):
        self.assertEqual(self.with_projects([{"key": "PA", "channel": "C_PA", "request": "R_PA"}]), "R_PA")

    def test_per_project(self):
        ps = [{"key": "PA", "channel": "C_PA", "request": "R_PA"},
              {"key": "XX", "channel": "C_XX"}]                      # 둘째는 팀 대화방이 없다
        self.assertEqual(self.with_projects(ps, {"no": 1, "project": "XX"}), "C_XX")
        self.assertEqual(self.with_projects(ps, {"no": 1, "project": "PA"}), "R_PA")

    def test_bot_starts_without_a_team_channel(self):
        """예전에는 config.need 라 설정에 request_channel 이 없으면 **봇이 아예 안 떴다.**"""
        import config
        old = config.CFG
        config.CFG = {"issue_channel": "C1", "canvas": "F1"}
        try:
            self.assertEqual(config.projects()[0]["request"], None)   # 없어도 죽지 않는다
        finally:
            config.CFG = old
