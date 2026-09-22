"""GitHub 동기화 (#58) — 지문 비교 · 검증 게이트 · 커밋 모으기.

이 세 가지가 깨지면 겉으로는 잘 도는 것처럼 보인다. 그래서 테스트로 고정한다:
정의가 없는 카드가 GitHub 에 올라가거나, 상태를 누를 때마다 API 를 부르거나,
파일 하나에 커밋 하나씩 쌓여도 화면은 똑같아 보인다.
"""
import asyncio
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gh_link  # noqa: E402
from flows.github import export_github, fingerprints, sweep  # noqa: E402
from store import STATE  # noqa: E402


def card(no=1, **kw):
    c = {"no": no, "title": f"이슈 {no}", "status": "todo", "card_ts": f"{no}.0", "by": "나",
         "spec": {"why": "왜", "change": "무엇", "expect": "확인", "not_doing": "",
                  "done_criteria": ["끝났다"]}}
    c.update(kw)
    return c


class FingerprintTest(unittest.TestCase):
    def test_same_card_same_print(self):
        self.assertEqual(fingerprints(card()), fingerprints(card()))

    def test_check_changes_both(self):
        """체크는 GitHub 본문에도 그려진다 — 파일만 바뀌는 게 아니다."""
        a, b = fingerprints(card()), fingerprints(card(checked=["끝났다"]))
        self.assertNotEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])

    def test_history_changes_file_only(self):
        """날짜별 기록이 늘어도 GitHub 에 보낼 제목·정의·상태는 그대로 — API 를 부르지 않는다."""
        a, b = fingerprints(card()), fingerprints(card(edits=[{"what": "목표일 바뀜"}]))
        self.assertNotEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])


class QueueTest(unittest.TestCase):
    def setUp(self):
        gh_link.PENDING.clear()

    tearDown = setUp

    def test_queue_holds_until_flush(self):
        gh_link.queue("issues/001.md", "issue #1")
        gh_link.queue("issues/002.md", "issue #2")
        gh_link.queue("issues/001.md", "issue #1 다시")       # 같은 파일은 한 번만
        self.assertEqual(len(gh_link.PENDING), 2)

    def test_flush_empty_does_nothing(self):
        self.assertEqual(gh_link.flush(), "모인 것 없음")

    def test_flush_clears_even_if_commit_fails(self):
        """커밋이 실패해도 모아 둔 목록은 비운다 — 안 비우면 다음 창에서 같은 것을 또 민다."""
        gh_link.PENDING["없는파일.md"] = "issue #9"
        try:
            gh_link.flush()
        except Exception:
            pass
        self.assertEqual(gh_link.PENDING, {})


class GateTest(unittest.IsolatedAsyncioTestCase):
    """올리기 전에 막는 자리. 여기까지 왔다는 것은 gh_link 를 불렀다는 뜻이라, 불렀는지만 본다."""

    def setUp(self):
        self.called = []
        self.said = []
        gh_link.PENDING.clear()
        STATE["cards"] = {}

    async def run_export(self, c, **kw):
        import flows.github as g
        real_export, real_api, real_save = gh_link.export, g.api, g.save
        g.api = lambda s, m, **kw: self._api(m, kw)
        g.save = lambda: None
        gh_link.export = lambda c, link, push=True: (self.called.append((c["no"], push)),
                                                     ("owner/repo#1", str(gh_link.HERE / "issues/001.md"), "모음"))[1]
        try:
            await export_github(None, c, **kw)
        finally:
            gh_link.export, g.api, g.save = real_export, real_api, real_save

    async def _api(self, method, kw):
        self.said.append((method, ((kw.get("body") or {}).get("text") or "")))
        return {"permalink": "https://slack/x"}

    async def test_empty_spec_is_pushed_but_nudged(self):
        """정의가 비어 있어도 **올린다** — #60 부터 번호를 받을 때 이슈가 먼저 생기므로,
        여기서 멈추면 GitHub 이 옛 상태로 남는다 (취소한 #63 이 GitHub 에서 열린 채였다).
        대신 빠진 것을 스레드에 한 번 알린다."""
        c = card(1, spec={"why": "", "change": "", "expect": "", "done_criteria": []})
        STATE["cards"] = {"1.0": c}
        await self.run_export(c, thread_ts="1.0")
        self.assertEqual([n for n, _ in self.called], [1])
        self.assertTrue(any(m == "chat.postMessage" for m, _ in self.said))

    async def test_same_gap_is_not_said_twice(self):
        """같은 말을 매번 되풀이하지 않는다 — 30초마다 훑으므로 그대로 두면 도배가 된다."""
        c = card(1, spec={"why": "", "change": "", "expect": "", "done_criteria": []})
        STATE["cards"] = {"1.0": c}
        await self.run_export(c, thread_ts="1.0")
        nudges = lambda: len([1 for m, body in self.said if m == "chat.postMessage" and "채워" in (body or "")])
        self.assertEqual(nudges(), 1)
        c["title"] = "제목만 바뀜"                                    # 지문은 달라지되 빠진 것은 그대로
        await self.run_export(c, thread_ts="1.0")
        self.assertEqual(nudges(), 1, "같은 말을 두 번 하지 않는다")

    async def test_unchanged_card_calls_nothing(self):
        """안 바뀐 카드는 Slack 도 GitHub 도 부르지 않는다."""
        c = card(2)
        STATE["cards"] = {"2.0": c}
        await self.run_export(c)
        c["file_hash"], c["gh_hash"] = fingerprints(c)
        self.called.clear(), self.said.clear()
        await self.run_export(c)
        self.assertEqual((self.called, self.said), ([], []))

    async def test_sweep_finds_changed_cards_in_number_order(self):
        """부르는 자리를 기억하지 않아도 올라간다 (#58). 번호 순이라 gh#N = 카드 #N 이 지켜진다."""
        import flows.github as g
        a, b, c3, d = card(7), card(3), card(5, spec=None), card(9)
        STATE["cards"] = {"7.0": a, "3.0": b, "5.0": c3, "9.0": d}
        d["file_hash"], d["gh_hash"] = fingerprints(d)          # #9 는 안 바뀜 → 건너뛴다
        real_export, real_api, real_save = gh_link.export, g.api, g.save
        g.api, g.save = lambda s, m, **kw: self._api(m, kw), lambda: None
        gh_link.export = lambda c, link, push=True: (self.called.append((c["no"], push)),
                                                     ("owner/repo#1", str(gh_link.HERE / "i.md"), "모음"))[1]
        try:
            n = await sweep(None)
        finally:
            gh_link.export, g.api, g.save = real_export, real_api, real_save
        self.assertEqual(n, 3)
        self.assertEqual([x for x, _ in self.called], [3, 5, 7])   # 번호 순 · 정의 없는 #5 도 상태는 따라간다

    async def test_sweep_does_nothing_when_nothing_changed(self):
        c = card(4)
        STATE["cards"] = {"4.0": c}
        c["file_hash"], c["gh_hash"] = fingerprints(c)
        self.assertEqual(await sweep(None), 0)
        self.assertEqual((self.called, self.said), ([], []))

    async def test_history_only_change_skips_github(self):
        """날짜별 기록만 늘면 파일은 다시 쓰되 GitHub API 는 부르지 않는다 (push=False)."""
        c = card(3)
        STATE["cards"] = {"3.0": c}
        c["file_hash"], c["gh_hash"] = fingerprints(c)
        c["edits"] = [{"what": "목표일 9/23→9/26", "who": "이동원"}]
        await self.run_export(c)
        self.assertEqual(self.called, [(3, False)])


class ReserveTest(unittest.TestCase):
    """번호의 주인은 GitHub 이다 (#60). 받아 오지 못하면 **로컬 번호로 넘어가지 않는다** —
    넘어가면 다음에 받아 온 번호와 겹치고, 그게 2026-09-20 에 59개를 손으로 맞추게 한 원인이다."""

    def setUp(self):
        self.ran = []
        self.out = "https://github.com/o/r/issues/61"
        self.old = gh_link.sh
        gh_link.sh = lambda *a, **k: (self.ran.append(a), self.out)[1]

    def tearDown(self):
        gh_link.sh = self.old

    def test_takes_the_number_github_gave(self):
        no, url = gh_link.reserve("새 요청")
        self.assertEqual((no, url), (61, self.out))
        self.assertEqual(self.ran[0][:3], ("gh", "issue", "create"))

    def test_failure_raises_instead_of_guessing(self):
        def boom(*a, **k):
            raise RuntimeError("GitHub 안 됨")
        gh_link.sh = boom
        with self.assertRaises(RuntimeError):
            gh_link.reserve("새 요청")

    def test_reserve_never_touches_the_real_repo_in_tests(self):
        """sh 를 막으면 밖으로 한 줄도 안 나가야 한다 — 예전에 시험이 진짜 이슈 #57·#58 을 닫았다."""
        import subprocess
        real = subprocess.run
        subprocess.run = lambda *a, **k: self.fail(f"시험이 밖으로 나갔다: {a}")
        try:
            gh_link.reserve("새 요청")
        finally:
            subprocess.run = real


class ImportTest(unittest.TestCase):
    """밖에서 만든 이슈만 가져온다 — 우리가 만든 것과 자리표시는 건너뛴다 (#60)."""

    def rows(self, known):
        data = [{"number": 61, "title": "우리 밖에서 만든 이슈"},
                {"number": 62, "title": "#62 우리가 만든 것"},
                {"number": 63, "title": "(빈 번호)"},
                {"number": 64, "title": "이미 카드 있음"}]
        old = gh_link.sh
        gh_link.sh = lambda *a, **k: json.dumps(data)
        try:
            return [g["number"] for g in gh_link.open_issues_without_cards(known)]
        finally:
            gh_link.sh = old

    def test_skips_ours_and_placeholders_and_known(self):
        self.assertEqual(self.rows({64}), [61])

    def test_known_numbers_are_not_reimported(self):
        """한 번 가져온 번호는 다시 안 가져온다 — 카드를 취소해도 되살아나지 않게."""
        self.assertEqual(self.rows({61, 64}), [])


class CanvasTest(unittest.IsolatedAsyncioTestCase):
    async def test_render_canvas_does_not_block_caller(self):
        """캔버스는 최대 10분을 기다린다. 부르는 쪽이 그걸 같이 기다리면 통로가 막힌다 (2026-09-20)."""
        import views.canvas as cv
        cv._CANVAS["pending"] = False
        started = asyncio.get_running_loop().time()
        await cv.render_canvas(None, wait=5)
        self.assertLess(asyncio.get_running_loop().time() - started, 0.5)
        me = asyncio.current_task()
        for t in asyncio.all_tasks():                      # 뒤에 남은 일은 정리한다 (나 자신은 빼고)
            if t is not me and "render_canvas" in repr(t.get_coro()):
                t.cancel()
        cv._CANVAS["pending"] = False



class ReconcileTest(unittest.TestCase):
    """카드와 GitHub 맞대어 보기 (#59) — 동기화는 조용히 깨지므로 이것만이 알려 준다.

    2026-09-20 에 시험이 진짜 이슈 #57·#58 을 닫았는데 카드는 열려 있었고, 사람이 알 방법이 없었다.
    """

    def run_it(self, cards, issues):
        import core
        return dict(core.reconcile(cards, issues))

    def card(self, no, status="todo", tracker=True):
        c = {"no": no, "title": f"이슈 {no}", "status": status}
        if tracker:
            c["tracker"] = f"o/r#{no}"
        return c

    def gh(self, no, state="OPEN", title=None):
        return {no: {"state": state, "title": title or f"#{no} 이슈 {no}"}}

    def test_all_matched_says_nothing(self):
        self.assertEqual(self.run_it([self.card(1), self.card(2, "done")],
                                     {**self.gh(1), **self.gh(2, "CLOSED")}), {})

    def test_open_card_closed_issue(self):
        """오늘 실제로 난 사고 — 카드는 대기인데 GitHub 은 닫혀 있었다."""
        out = self.run_it([self.card(57)], self.gh(57, "CLOSED"))
        self.assertIn("닫힘", out[57])

    def test_done_card_open_issue(self):
        out = self.run_it([self.card(9, "done")], self.gh(9, "OPEN"))
        self.assertIn("열림", out[9])

    def test_card_without_tracker(self):
        self.assertIn("짝이 없어요", self.run_it([self.card(3, tracker=False)], {})[3])

    def test_number_missing_on_github(self):
        self.assertIn("그 번호가 없어요", self.run_it([self.card(4)], {})[4])

    def test_issue_without_card(self):
        self.assertIn("카드가 없어요", self.run_it([], self.gh(70, title="밖에서 만든 것"))[70])

    def test_placeholder_and_closed_orphans_are_fine(self):
        """자리표시와 닫힌 고아 이슈는 어긋난 게 아니다 — 매일 울리면 아무도 안 본다."""
        self.assertEqual(self.run_it([], {**self.gh(1, title="(빈 번호)"),
                                          **self.gh(2, "CLOSED", "옛날 것")}), {})

    def test_title_changed_on_github(self):
        """제목은 Slack 이 주인이라 30초마다 맞춰진다 — 하루가 지나도 다르면 GitHub 에서 고친 것이다."""
        out = self.run_it([self.card(5)], self.gh(5, title="#5 GitHub 에서 고친 제목"))
        self.assertIn("덮어써져요", out[5])

    def test_same_title_is_fine(self):
        self.assertEqual(self.run_it([self.card(5)], self.gh(5)), {})


class NoRepoTest(unittest.IsolatedAsyncioTestCase):
    """GitHub 을 안 쓰는 팀 — 번호는 예전처럼 봇 안의 카운터로, 봇은 그대로 돈다 (#60 체크리스트 5)."""

    def setUp(self):
        import flows.intake as intake
        self.intake = intake
        STATE["cards"], STATE["next"] = {}, 7
        self.old = (gh_link.REPO, intake.gh_link.REPO, intake.api, intake.name_of,
                    intake.save, intake.render_canvas, intake.card_blocks, intake.ensure_ctl,
                    intake.recommend, intake.prioritize, intake.note_decisions, intake.coach)
        gh_link.REPO = intake.gh_link.REPO = None
        intake.api = lambda s, m, **kw: self._api(m)
        intake.name_of = lambda s, m: self._val("나")
        intake.save = lambda: None
        intake.card_blocks = lambda c: []
        for k in ("render_canvas", "ensure_ctl", "note_decisions"):
            setattr(intake, k, self._noop)
        intake.recommend = intake.prioritize = self._noop
        intake.coach = self._noop

    def tearDown(self):
        i = self.intake
        (gh_link.REPO, i.gh_link.REPO, i.api, i.name_of, i.save, i.render_canvas, i.card_blocks,
         i.ensure_ctl, i.recommend, i.prioritize, i.note_decisions, i.coach) = self.old
        STATE["cards"] = {}

    async def _noop(self, *a, **k):
        return None

    async def _val(self, v):
        return v

    async def _api(self, m):
        return {"ok": True, "ts": "7.0", "permalink": ""}

    async def test_local_number_when_no_repo(self):
        c = await self.intake.new_card(None, {"text": "요청 하나"})
        self.assertIsNotNone(c, "GitHub 이 없어도 카드는 만들어져야 한다")
        self.assertEqual(c["no"], 7)                       # STATE.next 그대로
        self.assertIsNone(c.get("tracker"))                # GitHub 짝은 없다
        self.assertEqual(STATE["next"], 8)

if __name__ == "__main__":
    unittest.main()


class PullTest(unittest.TestCase):
    """켤 때 git pull (#59) — 안전할 때만. 데이터 폴더가 코드 레포와 같아서 조심한다."""

    def setUp(self):
        self.calls = []
        self.out = {}
        self.old_sh, self.old_here = gh_link.sh, gh_link.HERE

        def fake_sh(*a, **k):
            self.calls.append(a)
            key = " ".join(a[:3])
            if key in self.out:
                v = self.out[key]
                # 같은 명령을 두 번 부르고 **다른 답**이 와야 할 때가 있다 — `git pull` 앞뒤로
                # `git log -1` 을 불러 버전이 바뀌었는지 보는 곳 (2026-09-21). 목록이면 차례로 준다
                if isinstance(v, list):
                    v = v.pop(0) if len(v) > 1 else v[0]
                if isinstance(v, Exception):
                    raise v
                return v
            return ""
        gh_link.sh = fake_sh
        self.tmp = tempfile.mkdtemp()
        (pathlib.Path(self.tmp) / ".git").mkdir()
        gh_link.HERE = pathlib.Path(self.tmp)

    def tearDown(self):
        gh_link.sh, gh_link.HERE = self.old_sh, self.old_here
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_git_does_nothing(self):
        gh_link.HERE = pathlib.Path(tempfile.mkdtemp())
        self.assertEqual(gh_link.pull(), (False, None))
        self.assertEqual(self.calls, [])

    def test_no_remote_does_nothing(self):
        self.out["git remote"] = ""
        self.assertEqual(gh_link.pull(), (False, None))
        self.assertNotIn(("git", "pull", "--ff-only", "-q"), self.calls)

    def test_dirty_tree_is_skipped_and_told(self):
        """사람이 고치던 소스가 있으면 git pull 하지 않는다 — 남의 편집 위에 머지하지 않는다."""
        self.out["git remote"] = "origin"
        self.out["git status --porcelain"] = " M bot.py\n M core.py"
        ok, why = gh_link.pull()
        self.assertFalse(ok)
        self.assertIn("2개", why)
        self.assertNotIn(("git", "pull", "--ff-only", "-q"), self.calls)

    def test_bot_own_files_do_not_block(self):
        """cards.json 은 봇이 살아 있는 한 거의 항상 바뀌어 있다 — 이걸 막으면 git pull 이 영영 안 된다."""
        self.out["git remote"] = "origin"
        self.out["git status --porcelain"] = " M cards.json\n M issues/066-x.md\n M daily/2026-09-20.md"
        ok, note = gh_link.pull()
        self.assertTrue(ok)
        self.assertIn(("git", "pull", "--ff-only", "-q"), self.calls)

    def test_mixed_still_blocks(self):
        """봇 파일에 사람 편집이 섞여 있으면 여전히 건너뛴다 — 사람 것 하나만 세어 알린다."""
        self.out["git remote"] = "origin"
        self.out["git status --porcelain"] = " M cards.json\n M core.py"
        ok, why = gh_link.pull()
        self.assertFalse(ok)
        self.assertIn("1개", why)
        self.assertIn("core.py", why)

    def test_clean_tree_pulls_ff_only(self):
        self.out["git remote"] = "origin"
        ok, note = gh_link.pull()
        self.assertTrue(ok)
        self.assertIn(("git", "pull", "--ff-only", "-q"), self.calls)

    # 「지금 어떤 버전인지 누가 올렸는지」 (2026-09-21 사장님 요청) — 잘 됐을 때도 말한다.
    # 조용하면 어느 버전이 도는지 알 수 없다. 켤 때 한 번뿐이라 시끄럽지 않다
    OLD = "aaa1111\x1f옛 버전\x1f이동원\x1f09/20 10:00"
    NEW = "bbb2222\x1f새 버전이 들어왔다\x1fsandbox-bot\x1f09/21 11:00"

    def test_success_tells_the_version(self):
        self.out["git remote"] = "origin"
        self.out["git log -1"] = [self.OLD, self.NEW]        # pull 앞뒤로 달라졌다
        ok, note = gh_link.pull()
        self.assertTrue(ok)
        self.assertIn("(git pull)", note)
        self.assertIn("최신 코드를 받아왔어요", note)
        self.assertIn("bbb2222", note)                        # 지금 도는 버전
        self.assertIn("새 버전이 들어왔다", note)

    def test_already_up_to_date_says_so(self):
        """켤 때 가장 흔한 일이다 — 「받아왔어요」 라고 하면 없던 일을 했다고 말하는 셈이다."""
        self.out["git remote"] = "origin"
        self.out["git log -1"] = [self.OLD]                   # 앞뒤가 같다
        ok, note = gh_link.pull()
        self.assertTrue(ok)
        self.assertIn("이미 최신", note)
        self.assertIn("aaa1111", note)

    def test_three_authors_are_told_apart(self):
        """봇 · Claude · 사람이 다 달라 보여야 한다 (2026-09-21).

        Claude 가 `--author` 를 안 쓰면 사장님 계정으로 찍혀 **둘이 한 사람처럼 보인다.**
        """
        line = lambda who: gh_link.version_line(("h", "제목", who, "09/21 11:00"))
        self.assertIn("봇이 올림", line("sandbox-bot"))
        self.assertIn("Claude 가 올림", line("Claude"))
        self.assertIn("이동원 님이 올림", line("이동원"))
        self.assertNotIn("Claude", line("이동원"))

    def test_says_where_it_pulls_from(self):
        """「어디서 받아오는지」 가 없으면 어느 레포 이야기인지 알 수 없다 (2026-09-21 사장님 지적).

        프로젝트가 둘이 된 뒤로는 더 그렇다. 이슈를 올리는 레포(`repo_of`)와 **코드를 받아오는
        레포는 다른 것**이라, 여기서는 작업 트리의 `git remote` 를 본다.
        """
        line = gh_link.version_line(("h", "제목", "Claude", "09/21"), origin=("webn77/slack-sandbox", "main"))
        self.assertIn("webn77/slack-sandbox", line)
        self.assertIn("main", line)

    def test_origin_reads_https_and_ssh(self):
        """받아오는 곳 주소는 두 모양으로 온다 — 둘 다 같은 `owner/repo` 로 읽어야 한다."""
        for url, want in [("https://github.com/webn77/slack-sandbox.git", "webn77/slack-sandbox"),
                          ("git@github.com:webn77/pa-second.git", "webn77/pa-second"),
                          ("https://github.com/a/b", "a/b")]:
            self.out["git remote get-url"] = url
            self.out["git rev-parse --abbrev-ref"] = "main"
            self.assertEqual(gh_link._origin(), (want, "main"), url)

    def test_blocked_still_shows_the_version(self):
        """못 받았을 때야말로 **어느 버전으로 돌고 있는지**가 중요하다."""
        self.out["git remote"] = "origin"
        self.out["git status --porcelain"] = " M core.py"
        self.out["git log -1"] = [self.OLD]
        self.assertIn("aaa1111", gh_link.pull()[1])

    def test_diverged_tells_the_human(self):
        """갈라졌으면 봇이 풀지 않는다 — 사람이 푼다 (#59)."""
        self.out["git remote"] = "origin"
        self.out["git pull --ff-only"] = RuntimeError("Not possible to fast-forward")
        ok, why = gh_link.pull()
        self.assertFalse(ok)
        self.assertIn("사람이 풀어", why)

    def test_git_words_are_kept_and_explained(self):
        """`(git 명령)` 을 앞에, 쉬운 말을 뒤에 (2026-09-21 사장님이 정한 형식).

        「당겨오기」 처럼 새 이름을 지으면 검색이 안 되고, `git pull` 만 쓰면 모르는 사람은 모른다.
        **둘 다** 준다. 그리고 사람이 할 일이 있는지 없는지를 분명히 적는다.
        """
        self.out["git remote"] = "origin"
        self.out["git status --porcelain"] = " M core.py"
        why = gh_link.pull()[1]
        self.assertIn("(git pull)", why)                  # 명령을 그대로
        self.assertIn("최신 코드", why)                    # 쉬운 말도 함께
        self.assertIn("하실 일은 없어요", why)             # 사람이 할 일이 없다는 것
        self.assertNotIn("당겨", why)                      # 지어낸 이름은 다시 들어오지 않는다

    def test_diverged_asks_the_human_to_step_in(self):
        """갈라진 것은 사람 일이다 — 여기서는 「없어요」 라고 하면 안 된다."""
        self.out["git remote"] = "origin"
        self.out["git pull --ff-only"] = RuntimeError("diverged")
        why = gh_link.pull()[1]
        self.assertIn("(git pull)", why)
        self.assertNotIn("하실 일은 없어요", why)


class PathOfTest(unittest.TestCase):
    """git status 한 줄에서 경로 꺼내기 (2026-09-21 버그).

    sh() 가 출력 전체를 strip 하므로 **첫 줄은 앞 공백이 없다.** 글자 자리로 자르면
    `cards.json` 이 `ards.json` 이 되어 봇 파일로 안 걸러지고, git pull 이 건너뛰어졌다.
    """

    def test_first_line_has_no_leading_space(self):
        self.assertEqual(gh_link._path_of("M cards.json"), "cards.json")

    def test_other_lines_keep_it(self):
        self.assertEqual(gh_link._path_of(" M cards.json"), "cards.json")
        self.assertEqual(gh_link._path_of("M  cards.json"), "cards.json")

    def test_untracked_and_dirs(self):
        self.assertEqual(gh_link._path_of("?? daily/2026-09-21.md"), "daily/2026-09-21.md")
        self.assertTrue(gh_link._bot_owned(gh_link._path_of("?? daily/x.md")))

    def test_rename_uses_the_new_name(self):
        self.assertEqual(gh_link._path_of("R  old.py -> new.py"), "new.py")

    def test_human_file_is_not_bot_owned(self):
        self.assertFalse(gh_link._bot_owned(gh_link._path_of(" M core.py")))

    def test_blank(self):
        self.assertEqual(gh_link._path_of(""), "")
