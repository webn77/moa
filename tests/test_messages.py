"""봇 문구 규칙 (#48) — messages.py 머리말의 말투 규칙을 지킨다."""
import os
import re
import string
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from messages import MSG  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")
TEXTS = {k: v for k, v in MSG.items() if isinstance(v, str)} | \
        {f"{k}.{kk}": vv for k, v in MSG.items() if isinstance(v, dict) for kk, vv in v.items()}


class MessageRules(unittest.TestCase):
    def test_polite_not_command(self):
        """명령 대신 부탁 — 「~하세요 · ~하라 · 반드시 · 즉시」 를 쓰지 않는다.
        「안녕하세요」 는 인사라 시킴이 아니다 (2026-09-20)."""
        for k, v in TEXTS.items():
            for bad in ("하세요", "하라", "해라", "반드시", "즉시", "누르세요", "보세요"):
                self.assertNotIn(bad, v.replace("안녕하세요", ""), f"{k}: 「{bad}」")

    def test_emoji_at_most_two(self):
        """**줄마다** 둘까지 — 목록은 줄마다 이모지 하나가 읽기를 돕는다 (2026-09-21 사장님 요청).

        전체로 세면 「친절하게 이모지로 나눈 목록」 을 만들 수 없고, 줄 수를 안 세면 한 줄에
        흩뿌린 것을 못 막는다. 그래서 **줄 단위**로 센다.
        """
        for k, v in TEXTS.items():
            for line in v.splitlines():
                self.assertLessEqual(len(EMOJI.findall(line)), 2, f"{k}: 한 줄에 이모지 {EMOJI.findall(line)}")

    def test_one_set_of_terms(self):
        """옛 용어 · 다른 말을 섞지 않는다 (막힘 → 보류 · 마감 → 목표일 · 백로그 구간 이름)."""
        for k, v in TEXTS.items():
            for bad in ("막혔", "막힘", "마감일", "지금 칸", "다음 칸"):
                self.assertNotIn(bad, v, f"{k}: 「{bad}」")

    def test_one_word_for_the_work(self):
        """일을 가리키는 말은 **「할 일」** 하나 (2026-09-21 사장님: 「이슈란 단어 어려운 거 같아」).

        「이슈」 는 버그 추적기 시대의 말이다 — Atlassian 도 2025-03 에 Jira 에서 버렸다
        (「issue 는 문제·결함을 떠올리게 하는데 이제 다루는 것은 모든 종류의 일이다」).
        우리는 **트래커가 없는 팀**을 위해 만드는데 남의 옛말을 빌려 쓰고 있었다 (research/words.md).

        **「이슈」 를 쓸 수 있는 자리는 하나뿐 — GitHub 의 것을 가리킬 때.** 그쪽 도구의 이름이라
        바꾸면 오히려 헷갈린다. 「카드」·「티켓」 은 그대로 금지, 「회의 카드」 는 다른 물건이라 둔다.
        """
        for k, v in TEXTS.items():
            if "이슈" in v:
                self.assertIn("GitHub", v, f"{k}: 「이슈」 는 GitHub 것을 가리킬 때만 — 아니면 「할 일」")
            for bad in ("카드", "티켓"):
                if bad == "카드" and "회의 카드" in v:
                    continue
                self.assertNotIn(bad, v, f"{k}: 「{bad}」 대신 「할 일」")

    def test_particles_after_the_work_word(self):
        """「할 일」 은 받침이 있다 — 「할 일가」·「할 일는」 은 틀린 말이다 (2026-09-21).

        「이슈」(받침 없음)를 「할 일」(받침 있음)로 바꾸면서 조사가 통째로 깨졌다 —
        이 파일에서만 13곳이었다. 낱말을 갈아 끼울 때 늘 따라오는 일이라 시험으로 막는다.
        """
        for k, v in TEXTS.items():
            for bad, good in (("할 일가", "할 일이"), ("할 일는", "할 일은"),
                              ("할 일를", "할 일을"), ("할 일와", "할 일과")):
                self.assertNotIn(bad, v, f"{k}: 「{bad}」 → 「{good}」")

    def test_no_particle_after_date(self):
        """{due} 바로 뒤에 조사를 붙이지 않는다 — 9/21이에요 · 9/23예요 는 날짜마다 틀린다."""
        for k, v in TEXTS.items():
            self.assertIsNone(re.search(r"\{due\}(이|가|예|로|으로|은|는|을|를)", v), k)

    def test_placeholders_are_known(self):
        """자리표시는 이 목록 안에서만 — 오타면 실행 중에 KeyError 가 난다."""
        known = {"link", "no", "ref", "what", "bot", "err", "n", "url", "tracker", "path", "who", "body", "plan",
                 "freed", "p", "nobody", "uid", "due", "dc", "next", "why", "done", "refs", "channel", "detail",
                 "title", "k", "word", "name"}
        for k, v in TEXTS.items():
            used = {f for _, f, _, _ in string.Formatter().parse(v) if f}
            self.assertLessEqual(used, known, f"{k}: {used - known}")


if __name__ == "__main__":
    unittest.main()
