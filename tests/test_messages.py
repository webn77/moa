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

    def test_no_forced_metaphors(self):
        """억지 비유를 쓰지 않는다 (2026-09-22 사장님 지적: 「굴리다 라는 표현은 아닌 거 같아」).

        **프로젝트는 구르지 않고, 일은 차려지지 않는다.** 밥상·수레에 쓰는 말을 일에 붙이면
        한 번 멈칫하게 된다. 사장님이 이런 것을 잡으려고 `writeflow` 를 만들어 두셨는데,
        그 규칙(`~/projects/writeflow/rules/words.md`)에도 같이 넣었다. 다만 writeflow 는
        이 저장소 밖에 있어 시험에서 부를 수 없으므로, **낱말만 여기 옮겨 둔다.**

        「정본」 도 뺐다 — 우리끼리 쓰는 문서 용어이고, 봇이 사람에게 할 말로는 어렵다.
        """
        for k, v in TEXTS.items():
            for bad, good in (("굴리", "해 나가다"), ("차려", "준비가 끝나다 · 만들다"), ("차리", "만들다"),
                              ("정본", "기록 파일"),
                              # 2026-09-22 사장님: 「오래된 메시지가 가려지거나 이거 너무 어려워」
                              ("가려지", "안 보이게 되다"), ("가려집", "안 보이게 돼요")):
                self.assertNotIn(bad, v, f"{k}: 「{bad}」 → 「{good}」")

    def test_no_particle_after_date(self):
        """{due} 바로 뒤에 조사를 붙이지 않는다 — 9/21이에요 · 9/23예요 는 날짜마다 틀린다."""
        for k, v in TEXTS.items():
            self.assertIsNone(re.search(r"\{due\}(이|가|예|로|으로|은|는|을|를)", v), k)

    def test_no_particle_after_user_text(self):
        """**사람이 적은 말이 들어오는 칸 뒤에 조사를 붙이지 않는다** (2026-09-22 시뮬레이션).

        「충전성공」 는 · 「현황」 는 처럼 어긋났다. 끝 글자를 알 수 없으니 은/는 · 이/가 ·
        로/으로 중 무엇이 맞는지 미리 못 정한다. **줄표(—)로 끊으면** 어떤 말이 와도 맞는다.
        `{due}` 에 이미 같은 규칙이 있다 (위 시험) — 같은 이유다.
        """
        holes = ("word", "title", "name", "goal", "what", "who", "body", "err", "k")
        # **닫는 낫표·따옴표를 사이에 끼워도 잡아야 한다** — 처음 쓴 규칙은 `{word}` 바로 뒤만
        # 봐서 「{word}」 는 을 놓쳤다. 이가 없는 시험은 통과해도 아무것도 지키지 않는다
        JOSA = re.compile(r"\{(" + "|".join(holes) + r")\}[」』\"'`\*]*\s*"
                          r"(은|는|이|가|을|를|으로|로|와|과|이랑|예요|이에요|이군요)\b")
        self.assertTrue(JOSA.search("「{word}」 는 번호 앞말로"), "시험에 이가 없다")
        for k, v in TEXTS.items():
            self.assertIsNone(JOSA.search(v), f"{k}: 자리표시 뒤에 조사 — 줄표(—)로 끊어 주세요")

    def test_the_one_who_gets_the_work_can_say_no(self):
        """**받는 쪽에 선택지가 있어야 한다** (2026-09-23 사장님: 「받는쪽 선택지 이거 없는거 문제일 거 같다」).

        예전 담당 DM 은 읽고 링크를 누르는 것뿐이었다 — 맡긴 사람이 다 정하고 **통보**하는 모양이다.
        못 받겠다는 말을 할 자리가 없으면 그 일은 말없이 멈추고, 맡긴 사람은 되고 있는 줄 안다.
        """
        self.assertIn("못 받아요", TEXTS["dm_assigned"])
        self.assertIn("dm_declined", TEXTS, "거절을 맡긴 사람에게 전할 말이 없다")
        self.assertIn("담당이 비었어요", TEXTS["dm_declined"])

    def test_placeholders_are_known(self):
        """자리표시는 이 목록 안에서만 — 오타면 실행 중에 KeyError 가 난다."""
        known = {"link", "no", "ref", "what", "bot", "err", "n", "url", "tracker", "path", "who", "body", "plan",
                 "freed", "p", "nobody", "uid", "due", "dc", "next", "why", "done", "refs", "channel", "detail",
                 "title", "k", "word", "name", "at", "detail", "who", "goal", "kind", "total", "list", "step", "note", "done", "git", "now", "repo", "make", "where", "lock", "when", "got", "want", "left",
                 "every", "label", "ch"}
        for k, v in TEXTS.items():
            used = {f for _, f, _, _ in string.Formatter().parse(v) if f}
            self.assertLessEqual(used, known, f"{k}: {used - known}")


if __name__ == "__main__":
    unittest.main()
