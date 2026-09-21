"""설치 테스트 (#50) — 새 팀 문서 채우기는 있는 파일을 절대 덮어쓰지 않는다."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import setup  # noqa: E402


class StarterTest(unittest.TestCase):
    def test_fills_missing_only(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "project.md").write_text("우리 팀이 쓴 목표", encoding="utf-8")
            made = setup.write_starter(d, "시험 팀", "홍길동", "U0TEST")
            self.assertNotIn("project.md", made)
            self.assertEqual((d / "project.md").read_text(encoding="utf-8"), "우리 팀이 쓴 목표")
            self.assertIn("team.md", made)
            team = (d / "team.md").read_text(encoding="utf-8")
            self.assertIn("| 홍길동 | U0TEST | PM |", team)
            self.assertNotIn("{members}", team)
            self.assertEqual(setup.write_starter(d, "시험 팀", "홍길동", "U0TEST"), [])   # 두 번째는 아무것도 안 함

    def test_starter_has_no_team_specific_text(self):
        """다른 팀에 그대로 나가는 문서 — 이 워크스페이스 이름·ID 가 섞이면 안 된다."""
        for p in setup.STARTER.glob("*.md"):
            t = p.read_text(encoding="utf-8")
            for bad in ("proj-request", "moa-bot", "U03G", "C0C", "webn77"):
                self.assertNotIn(bad, t, f"{p.name} 에 {bad}")

    def test_plist(self):
        with tempfile.TemporaryDirectory() as d:
            path, label = setup.write_launchagent(pathlib.Path(d), "T0ABC")
            self.assertEqual(label, "com.moa.t0abc")
            self.assertIn("<key>MOA_DATA</key>", path.read_text())


class ManifestCheckTest(unittest.TestCase):
    """Slack 이 **링크를 연 뒤에야** 알려 주는 것을 미리 잡는다 (2026-09-22 실측).

    둘 다 mju 워크스페이스에 처음부터 깔아 보다 막힌 것이다. 잡아 주지 않으면
    링크 만들기 → 열기 → 워크스페이스 고르기 → Next → 빨간 줄 을 매번 되풀이한다.
    """

    def man(self, name="모아 프로젝트 비서", handle="Moa"):
        return {"display_information": {"name": name}, "features": {"bot_user": {"display_name": handle}}}

    def test_a_good_manifest_passes(self):
        self.assertEqual(setup.manifest_problems(self.man()), [])

    def test_a_two_letter_app_name_is_caught(self):
        """「모아」 로는 앱을 못 만든다 — Slack 이 세 글자를 요구한다."""
        bad = setup.manifest_problems(self.man(name="모아"))
        self.assertEqual(len(bad), 1)
        self.assertIn("짧아요", bad[0])

    def test_a_korean_handle_is_caught(self):
        """`@모아` 는 안 된다 — Slack 이 부르는 이름에서 영문 아이디를 만드는데 한글은 못 바꾼다."""
        bad = setup.manifest_problems(self.man(handle="모아"))
        self.assertEqual(len(bad), 1)
        self.assertIn("영문", bad[0])
        self.assertIn("--bot-name", bad[0])      # 한글로 보이게 하는 길도 같이 알려 준다

    def test_ascii_handles_pass(self):
        for h in ("Moa", "moa", "moa-bot", "moa_bot", "moa.bot", "PA"):
            self.assertEqual(setup.manifest_problems(self.man(handle=h)), [], h)

    def test_the_real_manifest_is_installable(self):
        """우리가 내보내는 매니페스트 자체가 통과해야 한다 — 안 그러면 아무도 못 깐다."""
        import yaml
        man = yaml.safe_load((setup.CODE / "manifest.yaml").read_text(encoding="utf-8"))
        self.assertEqual(setup.manifest_problems(man), [])

    def test_app_link_refuses_a_broken_manifest(self):
        """고칠 곳을 알려 주고 **링크를 안 준다** — 열어 봐야 거절당할 링크다."""
        with mock.patch.object(setup, "manifest_problems", lambda m: ["앱 이름 「모아」 이 너무 짧아요"]):
            url, err = setup.app_link()
        self.assertIsNone(url)
        self.assertIn("짧아요", err)


if __name__ == "__main__":
    unittest.main()
