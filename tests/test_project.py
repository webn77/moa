"""DM 에서 프로젝트 만들기 (flows/project.py) — **한 칸씩 묻는 대화**가 끊기지 않는지.

Slack 을 부르지 않는다. `api` 를 가짜로 바꿔 **무엇을 보냈는지**만 본다 — 방을 진짜로 만들면
시험을 돌릴 때마다 채널이 쌓인다.

**AI 를 몇 번 부르는지도 센다** (`AiBudgetTest`). 이 흐름을 단계형으로 되돌린 이유가 그것이기
때문이다 (2026-09-22 사장님: 「우리는 구독으로 동작하는데 차라리 하나씩 받아서 하는 건 어때?」).
세는 시험이 없으면 다음에 누가 편의상 AI 를 한 줄 더 부르고, 한도는 조용히 녹는다.
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
        self.patches = [mock.patch.object(project, "api", self.fake.api),
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

    def make(self, name="결제 개편", key="네", goal="결제 실패를 절반으로 줄인다"):
        """①이름 ②앞말 ③목표 ④확인 — 만들어지는 가장 짧은 길."""
        self.say("프로젝트 만들기")
        self.say(name)
        self.say(key)
        self.say(goal)
        self.say("네")


class StartTest(Base):
    def test_only_starts_on_a_making_phrase(self):
        """「프로젝트」 만 있다고 시작하면 안 된다 — 프로젝트 이야기는 늘 한다."""
        for q in ("프로젝트 만들기", "프로젝트 하나 만들자", "새 프로젝트", "프로젝트 추가해줘", "프로젝트 등록"):
            STATE.pop("new_project", None)
            self.assertTrue(self.say(q), q)
        for q in ("프로젝트 현황", "이 프로젝트 어때", "프로젝트 목록"):
            STATE.pop("new_project", None)
            self.assertFalse(self.say(q), q)

    def test_unrelated_talk_passes_through(self):
        """시작하지도 묻는 중도 아니면 **손대지 않는다** — 다른 갈래가 받아야 한다."""
        self.assertFalse(self.say("내 할 일"))
        self.assertEqual(self.fake.sent, [])

    def test_the_intent_router_can_force_it_open(self):
        """낱말로는 안 걸리지만 뜻은 프로젝트 등록인 말 — `ai.intent` 가 이 문으로 들여보낸다."""
        self.assertFalse(self.say("새로 시작하는 일 하나 올려줘"))
        STATE.pop("new_project", None)
        self.assertTrue(run(project.maybe(None, DM, "새로 시작하는 일 하나 올려줘", force=True)))

    def test_a_forced_open_does_not_pick_up_a_name(self):
        """**뜻으로 들어온 말은 이름이 아니다** (2026-09-22 시뮬레이션이 잡았다).

        AI 는 「프로젝트를 만들려는 말」 이라고만 했고 이름이 무엇인지는 말하지 않았다.
        그런데 문장 전체가 이름이 되어 방 이름이 「새로 시작하는 일 하나 올려줘」 가 됐다.
        """
        run(project.maybe(None, DM, "새로 시작하는 일 하나 올려줘", force=True))
        self.assertIn("이름을 알려", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["step"], "title")
        self.assertNotIn("title", STATE["new_project"][ME])


class ConversationTest(Base):
    def test_three_questions_make_a_project(self):
        self.make()
        methods = [m for m, _ in self.fake.sent]
        self.assertIn("conversations.create", methods)
        self.assertIn("conversations.canvases.create", methods)
        self.assertIn("✅", self.fake.texts()[-1])
        self.assertNotIn(ME, STATE.get("new_project", {}))      # 끝나면 묻는 상태를 놓는다

    def test_the_questions_come_one_at_a_time_and_in_order(self):
        """①이름 ②앞말 ③목표 — 한 번에 하나만 묻는다 (사장님이 정한 순서)."""
        self.say("프로젝트 만들기")
        self.assertIn("이름을 알려", self.fake.texts()[-1])
        self.say("결제 개편")
        self.assertIn("번호 앞말", self.fake.texts()[-1])
        self.say("네")
        self.assertIn("이루려고", self.fake.texts()[-1])
        self.say("결제 실패를 절반으로 줄인다")
        self.assertIn("이렇게 만들까요", self.fake.texts()[-1])

    def test_name_is_not_taken_as_a_search(self):
        """이름을 말했을 뿐인데 검색으로 새면 대화가 끊긴다 — 묻는 중에는 늘 True."""
        self.say("프로젝트 만들기")
        self.assertTrue(self.say("결제 개편"))

    def test_cancel_stops_and_makes_nothing(self):
        self.say("프로젝트 만들기")
        self.say("취소")
        self.assertNotIn(ME, STATE.get("new_project", {}))
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])

    def test_nothing_is_made_before_you_say_yes(self):
        """**되돌릴 수 없는 일 앞에는 늘 확인이 있다** — Slack 은 채널 삭제가 없다."""
        self.say("프로젝트 만들기"); self.say("결제 개편"); self.say("네")
        self.say("결제 실패를 절반으로 줄인다")
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])
        self.say("네")
        self.assertIn("conversations.create", [m for m, _ in self.fake.sent])

    def test_the_asker_is_invited_to_their_own_room(self):
        """부른 사람이 빠지면 자기가 만든 방에 못 들어간다."""
        self.make()
        body = next(b for m, b in self.fake.sent if m == "conversations.invite")
        self.assertEqual(body["users"], ME)

    def test_a_bad_key_asks_again_instead_of_giving_up(self):
        self.say("프로젝트 만들기"); self.say("결제 개편")
        before = len(self.fake.texts())
        self.assertTrue(self.say("이건 앞말이 아니라 문장이에요"))
        self.assertEqual(len(self.fake.texts()), before + 1)     # 다시 물었다
        self.assertEqual(STATE["new_project"][ME]["step"], "key")

    def test_a_key_already_in_use_is_refused(self):
        taken = next(p["key"] for p in common.PROJECTS if p.get("key"))
        self.say("프로젝트 만들기"); self.say("결제 개편")
        self.say(taken)
        self.assertIn("이미 쓰고 있는", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["step"], "key")


class GoalTest(Base):
    """③번째로 묻는 것은 **목표 한 줄**이다 (2026-09-22 사장님이 정한 세 질문의 마지막).

    물어본 것은 **보이는 곳에 남아야 한다** — 답이 어디에도 안 나오는 질문은 질문이 아니라
    고장이다. 그래서 작업판 맨 위와 끝 인사 둘 다에서 확인한다.
    """

    def test_the_goal_is_written_where_it_can_be_seen(self):
        self.make(goal="충전 실패를 하루 40건에서 20건으로 줄인다")
        body = next(b for m, b in self.fake.sent if m == "conversations.canvases.create")
        md = body["document_content"]["markdown"]
        self.assertIn("충전 실패를 하루 40건에서 20건으로 줄인다", md)
        self.assertTrue(md.startswith("# "), "작업판이 제목으로 시작하지 않는다")
        self.assertIn("충전 실패를", self.fake.texts()[-1])

    def test_the_goal_is_kept_in_the_settings(self):
        """다시 띄운 뒤에도 남아야 한다 — 말로만 하고 안 적으면 다음에 또 묻는다."""
        self.make(goal="충전 실패를 절반으로 줄인다")
        written = json.loads(config.PATH.write_text.call_args[0][0])
        made = written["projects"][-1]
        self.assertEqual(made["goal"], "충전 실패를 절반으로 줄인다")
        self.assertEqual(made["channel"], "C0NEW")
        self.assertEqual(len(written["projects"]), len(self.saved_projects) + 1)   # 있던 것을 지우지 않는다

    def test_saying_later_does_not_get_you_stuck(self):
        """지금 못 정할 수도 있다 — **묻는 칸 때문에 갇히면 안 된다** (이 파일이 두 번 겪은 일)."""
        self.say("프로젝트 만들기"); self.say("결제 개편"); self.say("네")
        self.say("나중에 정할게요")
        self.assertIn("이렇게 만들까요", self.fake.texts()[-1])
        self.say("네")
        self.assertIn("✅", self.fake.texts()[-1])
        self.assertIn("아직 비어 있어요", self.fake.texts()[-1])

    def test_too_short_a_goal_is_asked_again(self):
        self.say("프로젝트 만들기"); self.say("결제 개편"); self.say("네")
        self.say("음")
        self.assertIn("한 줄만 더", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["step"], "goal")


class ConfirmTest(Base):
    """확인 단계에서 고치자는 말 — **알아들은 것만** 고친다.

    통째로 이름으로 받으면 「아니 그게 아니고…」 가 프로젝트 이름이 되고, 그 방은 그 이름으로
    남는다 (Slack 은 채널 삭제가 없다). 못 알아들으면 어떻게 말하면 되는지 알려 주고 다시 묻는다.
    """

    def ready(self):
        self.say("프로젝트 만들기"); self.say("충전성공"); self.say("네")
        self.say("충전 실패를 절반으로 줄인다")

    def test_fixing_the_key(self):
        self.ready()
        self.say("앞말은 CH 로")
        self.assertIn("CH-1", self.fake.texts()[-1])
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])

    def test_fixing_the_name(self):
        self.ready()
        self.say("이름은 충전성공2")
        self.assertIn("충전성공2", self.fake.texts()[-1])

    def test_fixing_the_goal(self):
        self.ready()
        self.say("목표는 충전 성공률을 99% 로 올린다")
        self.assertIn("99%", self.fake.texts()[-1])

    def test_a_key_already_in_use_is_refused_here_too(self):
        self.ready()
        taken = next(p["key"] for p in common.PROJECTS if p.get("key"))
        self.say(f"앞말은 {taken} 로")
        self.assertIn("이미 쓰고 있는", self.fake.texts()[-1])

    def test_words_it_cannot_place_do_not_become_the_name(self):
        """**못 알아들은 말이 이름이 되면 안 된다** — 그 방은 그 이름으로 남는다."""
        self.ready()
        self.say("아니 그게 아니고요")
        self.assertIn("어느 걸 고칠까요", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["title"], "충전성공")
        self.assertNotIn("conversations.create", [m for m, _ in self.fake.sent])


class NotStuckTest(Base):
    """**되물을 때는 어디에 있는지와 나가는 길을 함께 말한다** (2026-09-22 사장님 지시:
    「만약 다른 대답하면 다시 되물어서 프로젝트 등록 진행 단계라고 안내하고 취소하려면
    나가면 된다고 안내」).

    되묻는 말만 오면 사람은 자기가 어디에 갇혔는지 모른다 — 9/22 에 실제로 그랬다.
    """

    def where(self):
        return self.fake.texts()[-1]

    def test_every_re_ask_says_where_you_are_and_how_to_get_out(self):
        for setup, bad in (
            (["프로젝트 만들기"], "🎉🎉"),                                   # ①이름
            (["프로젝트 만들기", "충전성공"], "이건 앞말이 아니라 문장이에요"),   # ②앞말
            (["프로젝트 만들기", "충전성공", "네"], "음"),                     # ③목표
            (["프로젝트 만들기", "충전성공", "네", "충전 실패를 줄인다"], "아니 그게 아니고"),   # ④확인
        ):
            STATE.pop("new_project", None)
            for q in setup:
                self.say(q)
            self.say(bad)
            self.assertIn("프로젝트 등록", self.where(), bad)
            self.assertIn("취소", self.where(), bad)
            self.assertIn("/3 단계", self.where(), bad)

    def test_it_says_which_step_you_are_on(self):
        self.say("프로젝트 만들기"); self.say("🎉🎉")
        self.assertIn("1/3 단계", self.where())
        self.say("충전성공"); self.say("이건 문장이에요")
        self.assertIn("2/3 단계", self.where())

    def test_a_command_does_not_become_the_project_name(self):
        """「현황」 이 프로젝트 이름이 되면 그 방은 그 이름으로 남는다 (채널 삭제가 없다)."""
        self.say("프로젝트 만들기")
        self.say("현황")
        self.assertIn("잠깐 미뤄", self.where())
        self.assertIn("프로젝트 등록", self.where())
        self.assertNotIn("title", STATE["new_project"][ME])

    def test_a_sentence_at_the_key_step_does_not_rename_the_project(self):
        """**꼬리말만 떼면 아무 문장이나 이름처럼 보인다** (2026-09-22 시험이 잡았다).

        「이건 앞말이 아니라 문장이에요」 를 앞말 단계에서 받으면 조용히 **프로젝트 이름이**
        그 문장으로 바뀌었다. 이름은 「이름은 X」 처럼 또렷이 말했을 때만 고친다.
        """
        self.say("프로젝트 만들기"); self.say("충전성공")
        self.say("이건 앞말이 아니라 문장이에요")
        self.assertEqual(STATE["new_project"][ME]["title"], "충전성공")
        self.assertIn("영문", self.where())

    def test_saying_the_name_clearly_still_goes_back(self):
        """또렷이 말하면 이름 단계로 돌아간다 — 안전망을 없애는 게 아니다."""
        self.say("프로젝트 만들기"); self.say("오케이 충전성공으로")
        self.say("아니 결제개편이 프로젝트 이름이야!")
        self.assertEqual(STATE["new_project"][ME]["title"], "결제개편")

    def test_a_goal_said_early_is_kept(self):
        """앞말 단계에서 「목표는 …」 을 말했으면 받아 둔다 — 두 번 묻지 않는다."""
        self.say("프로젝트 만들기"); self.say("충전성공")
        self.say("목표는 충전 실패를 절반으로 줄인다")
        self.assertEqual(STATE["new_project"][ME]["goal"], "충전 실패를 절반으로 줄인다")
        self.assertIn("번호 앞말", self.where())

    def test_a_name_that_merely_contains_a_command_word_is_fine(self):
        """「현황판 개편」 은 진짜 프로젝트 이름일 수 있다 — 딱 그 말일 때만 막는다."""
        self.say("프로젝트 만들기")
        self.say("현황판 개편")
        self.assertEqual(STATE["new_project"][ME]["title"], "현황판 개편")


class AiBudgetTest(Base):
    """**이 흐름은 AI 를 한 번도 안 부른다** (2026-09-22 사장님 결정).

    하루 전까지는 대화가 올 때마다 AI 에게 칸을 채우게 했다 — 세 마디면 세 번이고 구독 한도는
    그렇게 녹는다. 게다가 AI 가 안 되는 날(9/22 Anthropic 500)에는 프로젝트를 아예 못 만들었다.
    **세는 시험이 없으면** 다음에 누가 편의상 한 줄 더 부르고, 그건 아무도 못 본다.
    """

    def test_making_a_project_calls_the_ai_zero_times(self):
        import ai
        calls = []

        async def counted(system, prompt, tries=3):
            calls.append(prompt)
            return "{}"
        with mock.patch.object(ai, "ask_ai", counted):
            self.make()
        self.assertIn("✅", self.fake.texts()[-1])
        self.assertEqual(calls, [], "AI 를 불렀다 — 이 흐름은 AI 없이 돌아야 한다")

    def test_the_router_only_wakes_the_ai_for_askish_words(self):
        """인사·현황에는 AI 를 안 쓴다 — 모든 DM 을 AI 에게 보이면 한도가 인사말에도 녹는다."""
        from handlers import ASKISH
        for q in ("프로젝트 하나 만들어줘", "새로 시작하는 일 등록", "할 일 추가"):
            self.assertTrue(ASKISH.search(q), q)
        for q in ("안녕", "고마워요", "현황", "목록", "내 할 일", "홍길동"):
            self.assertFalse(ASKISH.search(q), q)

    def test_the_ai_saying_nothing_useful_does_not_break_anything(self):
        """AI 가 죽어도 뜻 고르기는 「other」 로 물러난다 — 찾기로 흘러가고 막히지 않는다."""
        import ai

        async def dead(system, prompt, tries=3):
            raise RuntimeError("API Error: 500")
        with mock.patch.object(ai, "ask_ai", dead):
            self.assertEqual(run(ai.intent("프로젝트 하나 만들어줘")), "other")


class NamingTest(Base):
    def test_channel_name_follows_slack_rules(self):
        """대문자·공백·마침표는 Slack 이 안 받는다. 한글은 받는다."""
        self.assertEqual(project._slug("결제 개편"), "프로젝트-결제-개편")
        self.assertEqual(project._slug("Pay V2.0"), "프로젝트-pay-v2-0")   # 마침표도 하이픈
        self.assertNotIn(" ", project._slug("a b c"))

    def test_key_suggestion_is_two_letters(self):
        """사장님이 정한 기본은 **영문 두 자**다 — 「앞 2자리 구분 영문 대문자」."""
        self.assertEqual(project._suggest_key("Pay 개편", set()), "PA")
        self.assertEqual(project._suggest_key("Pay 개편", {"PA"}), "P2")
        self.assertEqual(project._suggest_key("결제 개편", {"P2"}), "P3")   # 한글만이면 약칭을 지어내지 않는다
        self.assertEqual(project._suggest_key("A 개편", set()), "P2")        # 한 자면 두 자를 못 만든다


class FailureTest(Base):
    def test_a_failed_channel_leaves_nothing_behind(self):
        self.fake.answers["conversations.create"] = {"ok": False, "error": "restricted_action"}
        self.make()
        self.assertIn("restricted_action", self.fake.texts()[-1])
        self.assertNotIn("conversations.canvases.create", [m for m, _ in self.fake.sent])
        self.assertNotIn(ME, STATE.get("new_project", {}))       # 붙잡아 두지 않는다

    def test_a_failed_canvas_still_makes_the_room(self):
        """작업판은 나중에 붙일 수 있다 — 방까지 버리면 사람이 한 말이 사라진다."""
        self.fake.answers["conversations.canvases.create"] = {"ok": False, "error": "restricted_action"}
        self.make()
        self.assertIn("✅", self.fake.texts()[-1])
        self.assertIn("못 붙였어요", self.fake.texts()[-1])


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


class OnboardTest(unittest.TestCase):
    """처음 오신 분의 걸음 — **데이터에서 계산한다.** 따로 세어 두면 사람이 Slack 에서
    직접 한 일과 어긋나서, 이미 한 걸 또 하라고 조른다 (2026-09-22).

    **세 걸음이다** — 목표 걸음을 없앴다. 프로젝트를 만들 때 이미 여쭙기 때문이다.
    두 군데서 물으면 이미 답한 것을 또 물어보고, 그게 「말이 안 통한다」 로 보인다.
    """

    def setUp(self):
        from flows import onboard
        self.o = onboard
        STATE.pop("onboard_done", None)

    def tearDown(self):
        STATE.pop("onboard_done", None)

    def test_a_finished_team_is_not_nagged(self):
        """예시 데이터는 팀·할 일·담당이 다 있다 — 조르면 안 된다."""
        self.assertIsNone(self.o.step(ME))
        self.assertEqual(self.o.nudge(ME), "")

    def test_every_step_message_renders(self):
        """2번 걸음은 「<#…> 에서 하세요」 라고 방을 가리킨다 — 자리를 안 채우면 KeyError 로
        인사 한 줄 때문에 DM 전체가 안 간다 (실제로 났다)."""
        from messages import say
        for n in (1, 2, 3):
            self.assertIn("️⃣", say(f"onboard_{n}", channel=self.o.room()))

    def test_the_head_line_matches_the_number_of_steps(self):
        """머리줄이 네 걸음이라고 하면서 셋만 있으면 사람이 하나를 기다린다."""
        from messages import say
        head = say("onboard_head", n=1)
        self.assertIn("③", head)
        self.assertNotIn("④", head)

    def test_saying_done_stops_the_nagging(self):
        for word in ("됐어요", "나중에 할게요", "건너뛸래요"):
            STATE.pop("onboard_done", None)
            self.assertTrue(self.o.skipped(word), word)
        self.o.give_up(ME)
        self.assertIsNone(self.o.step(ME))

    def test_the_room_it_points_at_is_the_newest_project(self):
        """2번 걸음이 「<#…> 에 쓰세요」 라고 가리키는 방은 **방금 만든 프로젝트**여야 한다.
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

    def test_it_no_longer_swallows_any_sentence_as_a_goal(self):
        """예전에는 아무 글이나 목표로 받아서 「현황」 이 프로젝트 목표가 됐다.
        지금은 **묻는 자리에서만** 받는다 — 여기서는 「됐어요」 만 집는다."""
        fake = Fake()
        with mock.patch.object(self.o, "api", fake.api), mock.patch.object(self.o, "save", lambda: None):
            for q in ("현황 알려줘", "내 할 일", "결제 실패를 줄인다"):
                self.assertFalse(run(self.o.catch(None, DM, q)), q)
            self.assertTrue(run(self.o.catch(None, DM, "됐어요")))


class ThreadReplyTest(unittest.TestCase):
    """DM 에서는 **스레드를 새로 파지 않는다** (2026-09-22 실측).

    한때 모든 답을 스레드로 보냈는데 **답이 접혀서 안 보였다** — 사장님이 「글 남겨도 작동을
    안 하는데?」 라고 하셨고, 봇은 답을 하고 있었다. 접힌 스레드 안이었다.
    **DM 은 그 자체가 한 대화다.** 다만 **이미 스레드 안에서 물었으면 그 스레드에** 답한다 —
    그건 사람이 만든 덩이다.
    """

    def sent(self, fn):
        got = []

        async def api(s, method, body=None, **p):
            got.append((method, body or {}))
            return {"ok": True}
        return got, api

    def test_a_dm_answer_is_not_hidden_in_a_new_thread(self):
        """맨 위에 답한다 — 새 스레드를 파면 접혀서 안 보인다."""
        from flows import find as find_mod
        got, api = self.sent(None)
        e = {"user": ME, "channel": "D0TEST", "ts": "111.1", "channel_type": "im"}
        with mock.patch.object(find_mod, "api", api):
            run(find_mod.find(None, e, "내 할 일"))
        posts = [b for m, b in got if m == "chat.postMessage"]
        self.assertTrue(posts, "답을 안 보냈다")
        self.assertIsNone(posts[0].get("thread_ts"))

    def test_a_reply_inside_a_thread_stays_there(self):
        """이미 스레드 안에서 물으면 **그 스레드**에 답한다 — 새 스레드를 파면 안 된다."""
        from flows import find as find_mod
        got, api = self.sent(None)
        e = {"user": ME, "channel": "D0TEST", "ts": "222.2", "thread_ts": "111.1", "channel_type": "im"}
        with mock.patch.object(find_mod, "api", api):
            run(find_mod.find(None, e, "내 할 일"))
        self.assertEqual([b for m, b in got if m == "chat.postMessage"][0].get("thread_ts"), "111.1")

    def test_project_flow_answers_where_you_can_see_it(self):
        fake = Fake()
        with mock.patch.object(project, "api", fake.api), mock.patch.object(project, "save", lambda: None):
            run(project.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "333.3"}, "프로젝트 만들기"))
        STATE.pop("new_project", None)
        self.assertIsNone([b for m, b in fake.sent if m == "chat.postMessage"][0].get("thread_ts"))


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
            # 2026-09-22 사장님이 실제로 치신 말 — 머리말·꼬리말이 이름에 섞였다
            ("오케이 충전성공으로 프로젝트 하나 만들어줘", "충전성공"),
            ("아니 충전성공이 프로젝트 이름이야!", "충전성공"),
            ("그럼 결제 개편으로 만들자", "결제 개편"),
        ):
            self.assertEqual(project._name_of(q), want, q)

    def test_a_bare_call_has_no_name(self):
        """이름을 안 말했으면 **빈 글자** — 그래야 물어본다."""
        for q in ("프로젝트 만들기", "프로젝트 하나 만들자", "새 프로젝트", "프로젝트 등록"):
            self.assertEqual(project._name_of(q), "", q)


class KeyRuleTest(Base):
    def test_six_letters_pass(self):
        """`charge` 가 튕겼다 — 4글자까지였다 (2026-09-22 실측). 받는 쪽은 2~6글자로 넓게 둔다."""
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
        self.assertEqual(STATE["new_project"][ME]["step"], "key")


class StuckConversationTest(Base):
    """대화가 갇히지 않는다 (2026-09-22 사장님 실측 — 「전혀 대화가 안 되는 거 같아」).

    앞말 단계가 **무엇을 받아도 「앞말로 쓸 수 없어요」** 만 되풀이했다. 사람은 이름을
    고치려 했는데 봇은 앞말로만 들었다.
    """

    def test_a_key_inside_a_sentence_is_found(self):
        self.say("프로젝트 만들기"); self.say("충전성공")
        self.say("그리고 앞말은 ch로")
        self.assertIn("이루려고", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["key"], "CH")

    def test_fixing_the_name_goes_back_to_the_name(self):
        """「아니 X가 이름이야」 는 앞말이 아니다 — 이름 단계로 돌아가야 한다."""
        self.say("프로젝트 만들기"); self.say("오케이 충전성공으로")
        self.say("아니 충전성공이 프로젝트 이름이야!")
        last = self.fake.texts()[-1]
        self.assertIn("충전성공", last)
        self.assertIn("번호 앞말", last)
        self.assertNotIn("쓸 수 없어요", last)


class CanvasTitleTest(unittest.TestCase):
    """캔버스 본문 맨 위에 **어느 프로젝트의 작업판인지** 적는다 (2026-09-22 사장님 지적).

    **채널 탭 이름과는 다른 것이다.** 탭 이름은 캔버스의 제목 칸에서 오는데,
    채널 캔버스는 그 칸을 API 로 못 정한다 (실측: `canvases.edit` + title → invalid_arguments).
    그래서 탭에는 「제목 없음」 이 뜨고 사람이 한 번 적어야 한다 — 앱 아이콘과 같다.
    이 H1 은 **탭을 안 고친 팀을 위한 것**이다. 열면 어느 작업판인지 바로 안다.
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


if __name__ == "__main__":
    unittest.main()
