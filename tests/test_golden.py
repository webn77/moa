"""골든 테스트 (#30) — bot.py 를 나누는 동안 모든 화면이 한 글자도 안 바뀌었는지 본다.

기준을 새로 뽑을 때(화면·동작을 일부러 바꿨을 때만):
  MOA_DATA=example python3 tests/golden/render.py > tests/golden/expected.json
  MOA_DATA=example python3 tests/golden/actions.py > tests/golden/expected_actions.json
"""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def run(script, expected):
    env = dict(os.environ)      # tests/__init__ 이 **베낀 곳**을 가리킨다 — 원본에 쓰지 않게
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "golden", script)],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    if r.returncode:
        return None, r.stderr[-800:]
    got = json.loads(r.stdout)
    want = json.load(open(os.path.join(ROOT, "tests", "golden", expected), encoding="utf-8"))
    return sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k)), ""


class GoldenTest(unittest.TestCase):
    def test_screens_unchanged(self):
        diff, err = run("render.py", "expected.json")
        self.assertEqual(err, "")
        self.assertEqual(diff, [], f"바뀐 화면 {len(diff)}개: {diff[:10]}")

    def test_actions_unchanged(self):
        """버튼 · 반응 · 명령 · 창 제출 31가지 — Slack 에 보낸 것과 최종 상태가 같아야 한다 (tests/golden/actions.py)."""
        diff, err = run("actions.py", "expected_actions.json")
        self.assertEqual(err, "")
        self.assertEqual(diff, [], f"바뀐 동작 {len(diff)}개: {diff[:10]}")


if __name__ == "__main__":
    unittest.main()
