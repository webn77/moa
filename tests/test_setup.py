"""설치 테스트 (#50) — 새 팀 문서 채우기는 있는 파일을 절대 덮어쓰지 않는다."""
import os
import pathlib
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
