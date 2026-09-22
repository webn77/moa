"""DM 에서 GitHub 붙이기 (flows/repo.py) — 세 번째 「한 칸씩 묻는」 흐름.

**진짜 `gh`·`git` 을 부르지 않는다.** `_sh` 하나만 갈아 끼우면 된다 — 나가는 길을 하나로
모아 둔 이유가 이것이다 (gh_link 가 2026-09-20 에 같은 일로 진짜 이슈를 닫은 적이 있다).
"""
import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402
from flows import repo  # noqa: E402
from store import STATE  # noqa: E402

ME = "U0EXAMPLEPM"
DM = {"user": ME, "channel": "D0TEST", "ts": "1.0"}


def run(coro):
    return asyncio.run(coro)


class Fake:
    def __init__(self):
        self.sent = []

    async def api(self, s, method, body=None, **params):
        self.sent.append((method, body or params))
        return {"ok": True, "ts": "9.9"}

    def texts(self):
        return [b.get("text", "") for m, b in self.sent if m == "chat.postMessage"]


class Base(unittest.TestCase):
    """`gh` 는 있고 로그인돼 있다 — 없는 경우는 `MissingTest` 가 따로 본다."""

    GH = True
    AUTH = True
    VIEW = (True, '{"name":"x"}')

    def setUp(self):
        self.fake, self.ran = Fake(), []
        STATE.pop("new_repo", None)

        def sh(*args):
            self.ran.append(" ".join(args))
            if args[:3] == ("gh", "auth", "status"):
                return (True, "Logged in to github.com account webn77 (keyring)") if self.AUTH \
                    else (False, "You are not logged into any GitHub hosts")
            if args[:3] == ("gh", "repo", "view"):
                return self.VIEW
            if args[:3] == ("gh", "repo", "create"):
                return True, "created"
            if args[:2] == ("git", "remote") and args[2] == "get-url":
                return False, "no origin"
            return True, ""
        self.patches = [mock.patch.object(repo, "api", self.fake.api),
                        mock.patch.object(repo, "save", lambda: None),
                        mock.patch.object(repo, "_sh", sh),
                        mock.patch.object(repo.shutil, "which", lambda x: "/gh" if self.GH else None),
                        mock.patch.object(config, "PATH", mock.MagicMock()),
                        # `config.PATH` 가 가짜라 **다시 읽을 수가 없다.** 다시 읽는 일 자체는
                        # `ReloadTest` 가 따로 본다 (같은 리스트를 유지하나)
                        mock.patch("common.reload_projects", lambda: None),
                        # `_build` 이 `gh_link.REPO` 를 바꾼다 — 되돌리지 않으면 **뒤에 도는
                        # 시험**이 없던 레포를 보게 된다 (도는 순서에 따라 들쑥날쑥해진다)
                        mock.patch("gh_link.REPO", None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        STATE.pop("new_repo", None)

    def say(self, q):
        return run(repo.maybe(None, DM, q))

    def last(self):
        return self.fake.texts()[-1]


class StartTest(Base):
    def test_it_claims_github_words_and_nothing_else(self):
        """「등록」 은 여러 흐름이 쓰는 낱말이다 — 섞이면 엉뚱한 것을 묻는다."""
        for q in ("깃허브 등록", "깃헙 연결해줘", "레포 붙여줘", "github 연동", "저장소 설정"):
            STATE.pop("new_repo", None)
            self.assertTrue(self.say(q), q)
        for q in ("할 일 등록", "프로젝트 만들기", "내 할 일", "현황"):
            STATE.pop("new_repo", None)
            self.assertFalse(self.say(q), q)

    def test_a_task_registration_is_not_taken_as_github(self):
        """반대쪽도 막혀 있어야 한다 — 「깃허브 등록 하려고」 가 할 일 등록으로 잡혔다
        (2026-09-22 시뮬레이션). **순서에 기대지 않는다.**"""
        from flows.task import START as TASK, NOT_MINE
        self.assertTrue(NOT_MINE.search("깃허브 등록 하려고"))
        self.assertFalse(bool(TASK.search("깃허브 등록 하려고") and not NOT_MINE.search("깃허브 등록 하려고")))
        self.assertTrue(TASK.search("충전성공에 할일 등록 하려고"))
        self.assertFalse(NOT_MINE.search("충전성공에 할일 등록 하려고"))


class NameTest(Base):
    def test_it_reads_owner_slash_name(self):
        self.assertEqual(repo._repo_of("webn77/moa-team"), "webn77/moa-team")
        self.assertEqual(repo._repo_of("https://github.com/webn77/moa-team"), "webn77/moa-team")
        self.assertEqual(repo._repo_of("https://github.com/webn77/moa-team.git 만들어줘"), "webn77/moa-team")
        self.assertEqual(repo._repo_of("webn77/moa_team.v2"), "webn77/moa_team.v2")

    def test_it_rejects_what_is_not_a_repo(self):
        for q in ("이거 아님", "만들어줘", "네", "충전성공", "webn77"):
            self.assertEqual(repo._repo_of(q), "", q)


class MissingTest(Base):
    """**못 하는 것은 먼저 말한다** — 물어 놓고 마지막에 막히면 헛일을 시킨 것이다."""

    def test_no_gh_command_says_so_and_stops(self):
        self.GH = False
        self.say("깃허브 등록")
        self.assertIn("brew install gh", self.last())
        self.assertNotIn(ME, STATE.get("new_repo", {}))       # 붙잡아 두지 않는다

    def test_not_logged_in_says_so_and_stops(self):
        self.AUTH = False
        self.say("깃허브 등록")
        self.assertIn("gh auth login", self.last())
        self.assertNotIn(ME, STATE.get("new_repo", {}))

    def test_it_never_asks_for_a_token(self):
        """**토큰을 Slack 으로 받지 않는다** — 비밀 값이 대화 기록에 평문으로 남는다."""
        from messages import MSG
        for k, v in MSG.items():
            if k.startswith("repo_") and isinstance(v, str):
                for bad in ("토큰", "token", "비밀번호", "password", "ghp_"):
                    self.assertNotIn(bad, v, f"{k}: 「{bad}」 를 달라고 하면 안 된다")
        self.AUTH = False
        self.say("깃허브 등록")
        self.assertIn("제가 안 받아요", self.last())


class FlowTest(Base):
    def ready(self):
        self.say("깃허브 등록")
        self.say("webn77/moa-team")
        self.say("2")

    def test_the_confirm_says_team_data_goes_up(self):
        """**이걸 안 밝히고 붙이면** 사람이 모르는 사이에 회의록·팀 명단이 GitHub 로 나간다."""
        self.ready()
        self.assertIn("이 레포로 올라가요", self.last())
        self.assertIn("팀 명단", self.last())

    def test_nothing_happens_before_you_say_yes(self):
        self.ready()
        self.assertEqual([x for x in self.ran if x.startswith("git ")], [])
        self.say("네")
        self.assertIn("git init -q -b main", self.ran)
        self.assertIn("git remote add origin https://github.com/webn77/moa-team.git", self.ran)

    def test_an_existing_repo_is_checked_not_created(self):
        self.ready(); self.say("네")
        self.assertIn("gh repo view webn77/moa-team --json name", self.ran)
        self.assertNotIn("gh repo create webn77/moa-team --private", self.ran)

    def test_make_it_creates_a_private_repo(self):
        self.say("깃허브 등록")
        self.say("webn77/moa-new 만들어줘")
        self.say("2")
        self.assertIn("비공개", self.last())
        self.say("네")
        self.assertIn("gh repo create webn77/moa-new --private", self.ran)

    def test_a_repo_that_does_not_exist_leaves_the_settings_alone(self):
        self.VIEW = (False, "Could not resolve to a Repository")
        self.ready(); self.say("네")
        self.assertIn("못 찾았어요", self.last())
        self.assertFalse(config.PATH.write_text.called, "설정을 건드렸다")
        self.assertNotIn(ME, STATE.get("new_repo", {}))

    def test_what_it_writes_to_the_settings(self):
        self.ready(); self.say("네")
        got = json.loads(config.PATH.write_text.call_args[0][0])
        self.assertEqual(got["github"]["repo"], "webn77/moa-team")
        hit = next(p for p in got["projects"] if p.get("key") == "XX")
        self.assertEqual(hit["repo"], "webn77/moa-team")
        self.assertEqual(next(p for p in got["projects"] if p.get("key") == "PA").get("repo"), None)

    def test_cancel_stops_and_touches_nothing(self):
        self.say("깃허브 등록")
        self.say("취소")
        self.assertNotIn(ME, STATE.get("new_repo", {}))
        # 읽기만 하는 것(`gh auth status` · `gh api`)은 아무것도 바꾸지 않는다 —
        # 무엇을 **바꿨나**만 본다: 레포 만들기 · git init · remote
        self.assertEqual([x for x in self.ran
                          if not x.startswith(("gh auth", "gh api"))], [])

    def test_it_says_where_you_are_when_the_answer_does_not_fit(self):
        self.say("깃허브 등록")
        self.say("이거 아님")
        self.assertIn("GitHub 등록", self.last())
        self.assertIn("취소", self.last())
        self.assertIn("1/2 단계", self.last())


class AiBudgetTest(Base):
    def test_the_conversation_calls_the_ai_zero_times(self):
        """세 흐름 모두 같은 약속이다 — 등록 대화는 AI 를 안 쓴다."""
        import ai
        calls = []

        async def counted(system, prompt, tries=3):
            calls.append(prompt)
            return "{}"
        with mock.patch.object(ai, "ask_ai", counted):
            self.say("깃허브 등록"); self.say("webn77/moa-team"); self.say("2"); self.say("네")
        self.assertIn("붙였어요", self.last())
        self.assertEqual(calls, [])


class ThreadTest(Base):
    def test_the_whole_talk_lands_in_one_thread(self):
        run(repo.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "111.1"}, "깃허브 등록"))
        run(repo.maybe(None, {"user": ME, "channel": "D0TEST", "ts": "222.2"}, "webn77/moa-team"))
        posts = [b for m, b in self.fake.sent if m == "chat.postMessage"]
        self.assertEqual({b.get("thread_ts") for b in posts}, {"111.1"})


if __name__ == "__main__":
    unittest.main()
