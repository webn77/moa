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
        self.assertEqual(STATE["new_project"][ME]["step"], "key")

    def test_a_key_already_in_use_is_refused(self):
        taken = next(p["key"] for p in common.PROJECTS if p.get("key"))
        self.say("프로젝트 만들기"); self.say("결제 개편")
        self.say(taken)
        self.assertIn("이미 쓰고 있는", self.fake.texts()[-1])
        self.assertEqual(STATE["new_project"][ME]["step"], "key")


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
