"""DM 에서 프로젝트 만들기 (flows/project.py) — 세 마디 대화가 끊기지 않는지.

Slack 을 부르지 않는다. `api` 를 가짜로 바꿔 **무엇을 보냈는지**만 본다 — 방을 진짜로 만들면
시험을 돌릴 때마다 채널이 쌓인다.
"""
import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common  # noqa: E402
import config  # noqa: E402
from flows import project  # noqa: E402
from store import STATE  # noqa: E402

ME = "U0EXAMPLEPM"
DM = {"user": ME, "channel": "D0TEST", "ts": "1.0"}


def run(coro):
    return asyncio.run(coro)


class Fake:
    """Slack 대신. 보낸 것을 모아 두고, 미리 정한 답을 돌려준다."""

    def __init__(self, **answers):
        self.sent, self.answers = [], answers

    async def api(self, s, method, body=None, **params):
        self.sent.append((method, body or params))
        return self.answers.get(method, {"ok": True})

    def texts(self):
        return [b.get("text", "") for m, b in self.sent if m == "chat.postMessage"]


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = Fake(**{
            "conversations.create": {"ok": True, "channel": {"id": "C0NEW"}},
            "conversations.canvases.create": {"ok": True, "canvas_id": "F0NEW"},
        })
        self.saved_cfg = json.loads(config.PATH.read_text(encoding="utf-8")) if config.PATH.exists() else {}
        self.saved_projects = [dict(p) for p in common.PROJECTS]
        STATE.pop("new_project", None)
        # **진짜 AI 를 부르지 않는다** — `claude -p` 는 한 번에 몇 초씩 걸리고 답도 매번 다르다.
        # 기본값은 「AI 가 안 됨」(None) 이라 옛 방식(한 칸씩 묻기)이 돌아간다.
        # AI 갈래는 `self.ai(...)` 로 가짜 답을 넣어 따로 시험한다 (AiFillTest)
        self.ai_says = None

        async def fake_fill(s, turns, taken):
            return self.ai_says
        self.patches = [mock.patch.object(project, "_fill", fake_fill),
                        mock.patch.object(project, "api", self.fake.api),
                        mock.patch.object(project, "save", lambda: None),
                        # `config.PATH` 통째로 가짜 — Path 인스턴스의 메서드는 갈아 끼울 수 없다.
                        # 진짜 config.json 을 시험이 덮어쓰면 그 팀의 설정이 날아간다
                        mock.patch.object(config, "PATH", mock.MagicMock()),
                        mock.patch.object(project, "reload_projects", lambda: common.PROJECTS)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        STATE.pop("new_project", None)
        common.PROJECTS[:] = self.saved_projects

    def say(self, q):
        return run(project.maybe(None, DM, q))


class StartTest(Base):
    def test_only_starts_on_a_making_phrase(self):
        """「프로젝트」 만 있다고 시작하면 안 된다 — 프로젝트 이야기는 늘 한다."""
        for q in ("프로젝트 만들기", "프로젝트 하나 만들자", "새 프로젝트", "프로젝트 추가해줘"):
            STATE.pop("new_project", None)
            self.assertTrue(self.say(q), q)
        for q in ("프로젝트 현황", "이 프로젝트 어때", "프로젝트 목록"):
            STATE.pop("new_project", None)
            self.assertFalse(self.say(q), q)

    def test_unrelated_talk_passes_through(self):
        """시작하지도 묻는 중도 아니면 **손대지 않는다** — 다른 갈래가 받아야 한다."""
        self.assertFalse(self.say("내 할 일"))
        self.assertEqual(self.fake.sent, [])


class ConversationTest(Base):
    def test_three_answers_make_a_project(self):
        self.say("프로젝트 만들기")
        self.say("결제 개편")
        self.say("네")
        self.say("혼자")
        methods = [m for m, _ in self.fake.sent]
        self.assertIn("conversations.create", methods)
        self.assertIn("conversations.canvases.create", methods)
        self.assertIn("✅", self.fake.texts()[-1])
        self.assertNotIn(ME, STATE.get("new_project", {}))      # 끝나면 묻는 상태를 놓는다

    def test_name_is_not_taken_as_a_search(self):
        """이름을 말했을 뿐인데 검색으로 새면 대화가 끊긴다 — 묻는 중에는 늘 True."""
        self.say("프로젝트 만들기")
        self.assertTrue(self.say("결제 개편"))

    def test_cancel_stops_and_makes_nothing(self):
        self.say("프로젝트 만들기")
        self.say("취소")
        self.assertNotIn(ME, STATE.get("new_project", {}))
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])

    def test_people_are_invited_with_the_asker(self):
        """부른 사람이 빠지면 자기가 만든 방에 못 들어간다."""
        self.say("프로젝트 만들기"); self.say("결제 개편"); self.say("네")
        self.say("<@U0OTHER1> <@U0OTHER2> 같이 해요")
        body = next(b for m, b in self.fake.sent if m == "conversations.invite")
        self.assertEqual(set(body["users"].split(",")), {ME, "U0OTHER1", "U0OTHER2"})

    def test_a_bad_key_asks_again_instead_of_giving_up(self):
        self.say("프로젝트 만들기"); self.say("결제 개편")
        before = len(self.fake.texts())
        self.assertTrue(self.say("이건 앞말이 아니라 문장이에요"))
        self.assertEqual(len(self.fake.texts()), before + 1)     # 다시 물었다
        self.assertEqual(STATE["new_project"][ME]["old"], "key")   # AI 가 안 될 때의 단계

    def test_a_key_already_in_use_is_refused(self):
        taken = next(p["key"] for p in common.PROJECTS if p.get("key"))
        self.say("프로젝트 만들기"); self.say("결제 개편")
        self.say(taken)
        self.assertIn("이미 쓰고 있는", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["old"], "key")   # AI 가 안 될 때의 단계


class NamingTest(Base):
    def test_channel_name_follows_slack_rules(self):
        """대문자·공백·마침표는 Slack 이 안 받는다. 한글은 받는다."""
        self.assertEqual(project._slug("결제 개편"), "프로젝트-결제-개편")
        self.assertEqual(project._slug("Pay V2.0"), "프로젝트-pay-v2-0")   # 마침표도 하이픈
        self.assertNotIn(" ", project._slug("a b c"))

    def test_key_suggestion_avoids_what_is_taken(self):
        self.assertEqual(project._suggest_key("Pay 개편", set()), "PAY")
        self.assertEqual(project._suggest_key("Pay 개편", {"PAY"}), "P2")
        self.assertEqual(project._suggest_key("결제 개편", {"P2"}), "P3")   # 한글만이면 약칭을 지어내지 않는다


class FailureTest(Base):
    def test_a_failed_channel_leaves_nothing_behind(self):
        self.fake.answers["conversations.create"] = {"ok": False, "error": "restricted_action"}
        self.say("프로젝트 만들기"); self.say("결제 개편"); self.say("네"); self.say("혼자")
        self.assertIn("restricted_action", self.fake.texts()[-1])
        self.assertNotIn("conversations.canvases.create", [m for m, _ in self.fake.sent])
        self.assertNotIn(ME, STATE.get("new_project", {}))       # 붙잡아 두지 않는다

    def test_a_failed_canvas_still_makes_the_room(self):
        """작업판은 나중에 붙일 수 있다 — 방까지 버리면 사람이 한 말이 사라진다."""
        self.fake.answers["conversations.canvases.create"] = {"ok": False, "error": "restricted_action"}
        self.say("프로젝트 만들기"); self.say("결제 개편"); self.say("네"); self.say("혼자")
        self.assertIn("✅", self.fake.texts()[-1])


class ReloadTest(unittest.TestCase):
    def test_reload_keeps_the_same_list_object(self):
        """**통째로 새로 대입하면 안 된다** — 들고 있는 모듈들에게 안 보인다 (2026-09-21).

        `store` 는 `from common import PROJECTS` 로 **같은 리스트**를 들고 있다. 이름만 갈면
        `store` 는 옛 목록을 계속 본다 — 만든 프로젝트를 봇이 모르는 채로 돈다.
        """
        import store
        before = id(common.PROJECTS)
        common.reload_projects()
        self.assertEqual(id(common.PROJECTS), before)
        self.assertIs(store.PROJECTS, common.PROJECTS)


if __name__ == "__main__":
    unittest.main()


class OnboardTest(unittest.TestCase):
    """처음 오신 분의 걸음 — **데이터에서 계산한다.** 따로 세어 두면 사람이 Slack 에서
    직접 한 일과 어긋나서, 이미 한 걸 또 하라고 조른다 (2026-09-22)."""

    def setUp(self):
        from flows import onboard
        self.o = onboard
        STATE.pop("onboard_done", None)

    def tearDown(self):
        STATE.pop("onboard_done", None)

    def test_a_finished_team_is_not_nagged(self):
        """예시 데이터는 목표·팀·할 일·담당이 다 있다 — 조르면 안 된다."""
        self.assertIsNone(self.o.step(ME))
        self.assertEqual(self.o.nudge(ME), "")

    def test_every_step_message_renders(self):
        """2·3번 걸음은 「<#…> 에서 하세요」 라고 방을 가리킨다 — 자리를 안 채우면 KeyError 로
        인사 한 줄 때문에 DM 전체가 안 간다 (실제로 났다)."""
        from messages import say
        for n in (1, 2, 3, 4):
            self.assertIn("️⃣", say(f"onboard_{n}", channel=self.o.room()))

    def test_saying_done_stops_the_nagging(self):
        for word in ("됐어요", "나중에 할게요", "건너뛸래요"):
            STATE.pop("onboard_done", None)
            self.assertTrue(self.o.skipped(word), word)
        self.o.give_up(ME)
        self.assertIsNone(self.o.step(ME))

    def test_the_room_it_points_at_is_the_newest_project(self):
        """3번 걸음이 「<#…> 에 쓰세요」 라고 가리키는 방은 **방금 만든 프로젝트**여야 한다.
        setup 이 만든 첫 방을 가리키면 거기 쓴 글이 아무 데도 안 걸린다 (2026-09-22)."""
        self.assertEqual(self.o.room(), common.PROJECTS[-1].get("channel"))
        self.assertGreater(len(common.PROJECTS), 1)          # 예시는 프로젝트가 둘이다

    def test_step_one_is_making_your_own_project(self):
        """setup 이 만든 방 하나는 **자리만 잡은 것**이라 세지 않는다."""
        saved = [dict(p) for p in common.PROJECTS]
        try:
            common.PROJECTS[:] = saved[:1]
            self.assertFalse(self.o.own_project())
            self.assertEqual(self.o.step(ME), 1)
        finally:
            common.PROJECTS[:] = saved

    def test_a_command_is_not_taken_as_a_goal(self):
        """1번 걸음은 **아무 글이나** 목표로 받는다 — 울타리가 없으면 「현황」 이 목표가 된다."""
        for q in ("현황 알려줘", "내 할 일", "도움말", "프로젝트 만들기", "정리"):
            self.assertTrue(any(x in q for x in self.o.NOT_A_GOAL), q)

    def test_a_goal_is_written_and_read_back(self):
        """적은 목표를 다시 읽을 수 있어야 한다 — 못 읽으면 1번 걸음에서 영원히 못 나간다."""
        import tempfile, pathlib, shutil
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            shutil.copy(pathlib.Path(self.o.HERE) / "project.md", d / "project.md")
            (d / "project.md").write_text("---\nkind: project\n---\n\n## 목표\n**(한 문장 — 무엇을 이루나)**\n",
                                          encoding="utf-8")
            with mock.patch.object(self.o, "HERE", d), mock.patch("docs.HERE", d):
                self.assertFalse(self.o.goal_set())
                self.assertTrue(self.o.set_goal("결제 실패를 하루 40건에서 20건으로 줄인다"))
                self.assertTrue(self.o.goal_set())


class ThreadReplyTest(unittest.TestCase):
    """DM 의 답은 **물어본 글 아래 스레드로** 간다 (2026-09-22 사장님 지적).

    `agent_view` 를 켜면서 「…하는 중」 은 그 글 아래에 붙였는데 **답은 맨 위로 갔다** —
    둘을 같이 옮겼어야 했다. 묻고 답한 짝이 흩어지면 DM 이 길어질수록 읽기 어렵다.
    """

    def sent(self, fn):
        got = []

        async def api(s, method, body=None, **p):
            got.append((method, body or {}))
            return {"ok": True}
        return got, api

    def test_find_answers_in_the_thread(self):
        from flows import find as find_mod
        got, api = self.sent(None)
        e = {"user": ME, "channel": "D0TEST", "ts": "111.1", "channel_type": "im"}
        with mock.patch.object(find_mod, "api", api):
            run(find_mod.find(None, e, "내 할 일"))
        posts = [b for m, b in got if m == "chat.postMessage"]
        self.assertTrue(posts, "답을 안 보냈다")
        self.assertEqual(posts[0].get("thread_ts"), "111.1")

    def test_a_reply_inside_a_thread_stays_there(self):
        """이미 스레드 안에서 물으면 **그 스레드**에 답한다 — 새 스레드를 파면 안 된다."""
        from flows import find as find_mod
        got, api = self.sent(None)
        e = {"user": ME, "channel": "D0TEST", "ts": "222.2", "thread_ts": "111.1", "channel_type": "im"}
        with mock.patch.object(find_mod, "api", api):
            run(find_mod.find(None, e, "내 할 일"))
        self.assertEqual([b for m, b in got if m == "chat.postMessage"][0].get("thread_ts"), "111.1")

    def test_project_flow_answers_in_the_thread(self):
        fake = Fake()
        with mock.patch.object(project, "api", fake.api), mock.patch.object(project, "save", lambda: None):
            run(project.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "333.3"}, "프로젝트 만들기"))
        STATE.pop("new_project", None)
        self.assertEqual([b for m, b in fake.sent if m == "chat.postMessage"][0].get("thread_ts"), "333.3")


class NameTest(unittest.TestCase):
    """사람은 이름만 딱 말하지 않는다 (2026-09-22 사장님 실측).

    「충전성공 이게 프로젝트 이름이야」 를 통째로 받으면 **그게 방 이름이 되고 카드마다
    따라다닌다.** 붙잡는 모양을 여기 다 적어 둔다 — 새 모양이 나오면 여기에 한 줄 더한다.
    """

    def test_pulls_the_name_out(self):
        for q, want in (
            ("프로젝트 하나 만들어줘 충전성공이라는 프로젝트야!", "충전성공"),
            ("충전성공 이게 프로젝트 이름이야", "충전성공"),
            ("새 프로젝트 만들자 결제개편이야", "결제개편"),
            ("프로젝트 이름은 결제 개편", "결제 개편"),
            ("충전성공", "충전성공"),
        ):
            self.assertEqual(project._name_of(q), want, q)

    def test_a_bare_call_has_no_name(self):
        """이름을 안 말했으면 **빈 글자** — 그래야 물어본다."""
        for q in ("프로젝트 만들기", "프로젝트 하나 만들자", "새 프로젝트"):
            self.assertEqual(project._name_of(q), "", q)


class KeyRuleTest(Base):
    def test_six_letters_pass(self):
        """`charge` 가 튕겼다 — 4글자까지였다 (2026-09-22 실측). 2~6글자로 넓혔다."""
        for k in ("PAY", "charge", "moa", "P2"):
            self.assertTrue(project.KEY_OK.match(k), k)
        for k in ("a", "toolongkey", "결제"):
            self.assertFalse(project.KEY_OK.match(k), k)

    def test_a_bad_key_says_why(self):
        """**같은 질문만 되풀이하면 답을 못 들은 것처럼 보인다** — 왜 안 되는지 말한다."""
        self.say("프로젝트 만들기"); self.say("충전성공")
        self.say("이건 앞말이 아니라 문장입니다")
        self.assertIn("영문", self.fake.texts()[-1])

    def test_name_given_up_front_is_not_asked_again(self):
        """이름을 같이 말했는데 또 물으면 사람은 자기 말을 못 들은 줄 안다."""
        self.say("프로젝트 하나 만들어줘 충전성공이라는 프로젝트야!")
        self.assertIn("충전성공", self.fake.texts()[-1])
        self.assertIn("번호 앞말", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["old"], "key")   # AI 가 안 될 때의 단계


class AiFillTest(Base):
    """AI 가 칸을 채우는 갈래 (2026-09-22 사장님 결정: 「llm으로 하는 게 맞는 거 같은데」).

    **단계가 없다.** 한 문장에 다 말하면 한 번에 끝나고, 나눠 말하면 나눠서 채워진다.
    여기서 지키는 것 둘: **되돌릴 수 없는 일 앞에 확인** · **AI 가 죽으면 옛 방식으로**.
    """

    def ai(self, name="", key="", who=(), alone=False, ask=""):
        self.ai_says = {"name": name, "key": key, "who": list(who), "alone": alone, "ask": ask}

    def test_one_sentence_is_enough(self):
        """「충전성공, 앞말 charge, 나 혼자」 — 세 번 묻지 않는다."""
        self.ai(name="충전성공", key="CHARGE", alone=True)
        self.say("프로젝트 만들어줘 충전성공, 앞말 charge, 나 혼자야")
        last = self.fake.texts()[-1]
        self.assertIn("이렇게 만들까요", last)
        self.assertIn("충전성공", last)
        self.assertIn("CHARGE", last)
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])   # 아직 안 만든다

    def test_nothing_is_made_before_you_say_yes(self):
        """**되돌릴 수 없는 일 앞에는 늘 확인이 있다** — Slack 은 채널 삭제가 없다."""
        self.ai(name="충전성공", key="CHARGE", alone=True)
        self.say("프로젝트 만들어줘 충전성공 혼자")
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])
        self.say("네")
        self.assertIn("conversations.create", [m for m, _ in self.fake.sent])
        self.assertIn("✅", self.fake.texts()[-1])

    def test_saying_something_else_goes_back_to_filling(self):
        """확인 단계에서 「아니 앞말은 PAY 로」 라고 하면 다시 채운다 — 만들어 버리면 안 된다."""
        self.ai(name="충전성공", key="CHARGE", alone=True)
        self.say("프로젝트 만들어줘 충전성공 혼자")
        self.ai(name="충전성공", key="PAY", alone=True)
        self.say("아니 앞말은 PAY 로 해줘")
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])
        self.assertIn("PAY", self.fake.texts()[-1])

    def test_a_made_up_person_is_dropped(self):
        """AI 는 **명단에 없는 사람 ID 를 지어낼 수 있다.** 그대로 초대하면 엉뚱한 사람이 들어온다."""
        self.ai(name="충전성공", key="CHARGE", who=["U0NOTREAL", ME])
        self.say("프로젝트 만들어줘 충전성공 민수랑")
        self.say("네")
        body = next(b for m, b in self.fake.sent if m == "conversations.invite")
        self.assertNotIn("U0NOTREAL", body["users"])

    def test_a_missing_blank_is_asked_for(self):
        """이름만 알아냈으면 **사람만** 묻는다 — 이미 아는 것을 다시 묻지 않는다."""
        self.ai(name="충전성공", key="CHARGE")
        self.say("프로젝트 만들어줘 충전성공")
        self.assertIn("누구와 함께", self.fake.texts()[-1])

    def test_when_the_ai_dies_the_old_way_takes_over(self):
        """**안전망을 지우지 않고 뒤로 미룬다** — AI 가 죽어도 프로젝트는 만들 수 있어야 한다."""
        self.ai_says = None                      # _fill 이 None = AI 실패
        self.say("프로젝트 만들기")
        self.assertIn("어떤 일인가요", self.fake.texts()[-1])
        self.say("충전성공")
        self.assertIn("번호 앞말", self.fake.texts()[-1])
        self.say("charge"); self.say("혼자")
        self.assertIn("✅", self.fake.texts()[-1])


class CanvasTitleTest(unittest.TestCase):
    """캔버스 맨 첫 줄은 **제목(H1)** 이어야 한다 (2026-09-22 사장님 지적).

    Slack 은 그것을 캔버스 이름으로 쓴다. 없으면 채널 탭에 **「제목 없음」** 이라고 뜬다 —
    새 워크스페이스에 깔자마자 드러났다. 프로젝트가 여럿이면 어느 작업판인지도 여기서 갈린다.
    """

    def test_canvas_starts_with_a_title(self):
        import json
        import pathlib
        d = json.loads((pathlib.Path(__file__).parent / "golden" / "expected.json").read_text(encoding="utf-8"))
        md = json.dumps(d.get("canvas"), ensure_ascii=False)
        self.assertRegex(md, r'"markdown": "# ', "캔버스가 제목으로 시작하지 않는다")

    def test_the_title_names_the_project(self):
        from views.canvas import _canvas_title
        import common
        self.assertIn(common.PROJECTS[0].get("name") or common.ISSUE_NAME, _canvas_title())
