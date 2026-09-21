"""재시작 안전 (#30 6단계) — 9/20 #49 GitHub 연결이 재시작 중 끊긴 사고의 재발 방지."""
import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class DrainTest(unittest.TestCase):
    def test_waits_for_work_in_flight(self):
        """끌 때 하던 일(버튼 처리 등)은 끝까지 한다. 끝나지 않는 반복 일(skip)은 기다리지 않는다."""
        import common
        done = []

        async def work():
            await asyncio.sleep(0.2)
            done.append("버튼 처리 끝")

        async def forever():
            await asyncio.sleep(3600)

        async def main():
            asyncio.create_task(work())
            loop_task = asyncio.create_task(forever())
            finished, pending = await common.drain(skip={loop_task}, timeout=5)
            loop_task.cancel()
            return finished, pending
        finished, pending = asyncio.run(main())
        self.assertEqual(done, ["버튼 처리 끝"])
        self.assertEqual((finished, pending), (1, 0))


class SaveTest(unittest.TestCase):
    def test_atomic_replace(self):
        """저장은 임시 파일 → 바꿔 끼우기. 끝난 뒤 임시 파일이 남지 않고 내용이 온전하다."""
        import store
        with tempfile.TemporaryDirectory() as d:
            old = store.CARDS
            store.CARDS = store.CARDS.__class__(d) / "cards.json"
            try:
                store.save()
                self.assertEqual(json.loads(store.CARDS.read_text())["cards"].keys(), store.STATE["cards"].keys())
                self.assertFalse(store.CARDS.with_suffix(".json.tmp").exists())
            finally:
                store.CARDS = old


class GitHubNumberTest(unittest.TestCase):
    def test_number(self):
        import gh_link
        self.assertEqual(gh_link.gh_no({"gh_no": 7, "tracker": "a/b#7"}), "7")
        self.assertEqual(gh_link.gh_no({"tracker": "webn77/slack-sandbox#3"}), "3")     # 예전 카드


if __name__ == "__main__":
    unittest.main()


class CanvasThrottleTest(unittest.TestCase):
    """캔버스 쓰기 줄이기 — 쓸 때마다 열어 둔 화면에 옛 칸이 쌓인다 (9/20 제보 · 실험)."""

    def run_render(self, since):
        import time
        import views.canvas as cv
        slept, calls = [], []

        async def fake_sleep(sec):
            slept.append(round(sec))

        async def fake_render(s):
            calls.append("canvas")

        async def fake_homes(s):
            calls.append("homes")
        old = (cv.asyncio.sleep, cv._render_canvas, cv.refresh_homes, cv.STATE.get("canvas_at"))
        cv.asyncio.sleep, cv._render_canvas, cv.refresh_homes = fake_sleep, fake_render, fake_homes
        cv.STATE["canvas_at"] = time.time() - since
        try:
            asyncio.run(cv._render_canvas_later(None, 5))   # 기다리는 쪽만 본다 — render_canvas 는 예약만 한다
        finally:
            cv.asyncio.sleep, cv._render_canvas, cv.refresh_homes = old[:3]
            cv.STATE["canvas_at"] = old[3] or 0
        return slept, calls

    def test_waits_out_the_gap_since_last_write(self):
        """30초 전에 썼으면 앱 홈은 바로, 캔버스는 남은 간격만큼 기다렸다가 한 번 (간격 2분)."""
        slept, calls = self.run_render(since=30)
        self.assertEqual(calls, ["homes", "canvas"])
        self.assertEqual(slept[0], 5)
        import views.canvas as cv
        self.assertAlmostEqual(slept[1], cv.GAP - 30, delta=2)

    def test_writes_soon_when_long_ago(self):
        slept, calls = self.run_render(since=3600)
        self.assertEqual(calls, ["canvas"])
        self.assertEqual(slept, [5])


class MineTest(unittest.TestCase):
    """봇이 자기 메시지를 사람 요청으로 읽으면 카드가 무한히 늘어난다 (2026-09-20 실제 사고: 31개)."""

    def test_own_message_is_ignored(self):
        import common, handlers
        self.assertTrue(handlers.is_mine({"bot_id": "B1", "username": common.BOT, "text": "🎫 …"}))
        self.assertTrue(handlers.is_mine({"bot_id": "B1", "text": "🎫 …"}))          # 이름 없이 보낸 옛 메시지

    def test_person_and_virtual_teammate_are_not_mine(self):
        import handlers
        self.assertFalse(handlers.is_mine({"user": "U1", "text": "🎫 결제 오류"}))
        self.assertFalse(handlers.is_mine({"bot_id": "B1", "username": "won7036", "text": "🎫 결제 오류"}))
