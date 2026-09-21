"""비슷한 이슈 찾기 (#57) — 만들 때 「혹시 같은 일인가요?」 를 물어보려고 쓴다.

AI 로 판정하지 않는다. 낱말 겹침으로 후보만 보여 주고 사람이 정한다.
**시끄러우면 사람이 안 본다** — 그래서 얼마나 안 걸리는지도 함께 시험한다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core  # noqa: E402


def card(no, title, why="", status="todo"):
    return {"no": no, "title": title, "status": status, "spec": {"why": why} if why else None}


class WordsTest(unittest.TestCase):
    def test_drops_short_and_common_and_numbers(self):
        self.assertEqual(core.words("#40 로그인 오류를 하는 것이 2026"), {"로그인", "오류를"})

    def test_empty(self):
        self.assertEqual(core.words(""), set())
        self.assertEqual(core.words(None), set())


class SimilarTest(unittest.TestCase):
    def find(self, c, others):
        return [(o["no"], r) for o, r in core.similar(c, others)]

    def test_finds_the_same_work(self):
        a = card(1, "로그인 오류 안내 문구 고치기")
        b = card(2, "로그인 오류 안내 문구 표시")
        self.assertEqual(self.find(a, [a, b])[0][0], 2)

    def test_ignores_unrelated(self):
        a = card(1, "로그인 오류 안내 문구")
        b = card(2, "캔버스 작업판 새로고침 버튼")
        self.assertEqual(self.find(a, [a, b]), [])

    def test_short_title_is_not_buried(self):
        """짧은 제목이 긴 제목에 묻히지 않게 적은 쪽으로 나눈다."""
        a = card(1, "로그인 오류")
        b = card(2, "로그인 오류가 가끔 나요 재현 조건 정리하고 알림 붙이기")
        self.assertTrue(self.find(a, [a, b]))

    def test_skips_self_and_finished(self):
        a = card(1, "로그인 오류 안내")
        done = card(2, "로그인 오류 안내", status="done")
        cancelled = card(3, "로그인 오류 안내", status="cancelled")
        self.assertEqual(self.find(a, [a, done, cancelled]), [])

    def test_too_few_words_says_nothing(self):
        """낱말이 한둘뿐이면 넘겨짚지 않는다 — 「수정」 하나로 엮으면 안 된다."""
        self.assertEqual(self.find(card(1, "수정"), [card(1, "수정"), card(2, "수정 필요")]), [])

    def test_uses_the_definition_too(self):
        a = card(1, "입구 만들기", why="메일로 온 요청을 팀 이슈로 옮긴다")
        b = card(2, "요청 담기", why="메일로 온 요청을 팀 이슈로 골라 담는다")
        self.assertTrue(self.find(a, [a, b]))

    def test_at_most_three(self):
        same = [card(n, "로그인 오류 안내 문구") for n in range(1, 8)]
        self.assertLessEqual(len(self.find(same[0], same)), 3)

    def test_quiet_on_real_data(self):
        """실제 크기의 데이터에서 시끄럽지 않아야 한다 — 2026-09-20 기준 41건 중 4건만 걸렸다.

        예전에는 만든 사람의 `cards.json` 을 읽었다 — **저장소를 받은 사람 맥에는 없는 파일**이라
        이 시험 하나만 터졌다 (2026-09-21 공개 준비 중 실측). 예시 데이터도 71건이라 크기가 같다.
        """
        import json, os, pathlib
        p = pathlib.Path(os.environ.get("MOA_DATA") or (pathlib.Path(__file__).parent.parent)) / "cards.json"
        cards = [c for c in json.loads(p.read_text(encoding="utf-8"))["cards"].values()
                 if c["status"] not in ("done", "cancelled")]
        hit = sum(1 for c in cards if core.similar(c, cards))
        self.assertLess(hit, len(cards) * 0.25, f"너무 많이 걸린다: {hit}/{len(cards)}")



class TidyTest(unittest.TestCase):
    """정리 후보 고르기 (#57) — 고르기만 하고 지우지 않는다. **왜 골랐는지**가 핵심."""

    STAGES = {"개발"}

    def find(self, cards, **kw):
        return {c["no"]: why for c, why in core.tidy_candidates(cards, self.STAGES, today="2026-09-20", **kw)}

    def c(self, no, **kw):
        base = {"no": no, "title": f"제목 {no} 낱말 여럿", "status": "todo", "stage": "개발",
                "scores": {"goal_fit": 3}, "spec": None}
        base.update(kw)
        return base

    SPECS = {
        "로그인": {"why": "로그인이 자주 끊긴다", "change": "세션 시간을 늘린다", "expect": "끊김 신고가 준다",
                 "not_doing": "비밀번호 규칙", "done_criteria": ["끊김 0건"]},
        "결제": {"why": "환불 처리가 느리다", "change": "은행 연동을 바꾼다", "expect": "평균 대기가 준다",
                "not_doing": "해외 카드", "done_criteria": ["하루 안에 환불"]},
    }

    def spec(self, tag="로그인"):
        """제대로 채워진 정의 — 「미정투성이」 규칙에 걸리지 않게 다섯 칸을 채운다.
        tag 가 다르면 낱말도 아예 달라서 서로 비슷해 보이지 않는다."""
        return dict(self.SPECS[tag])

    def test_nothing_to_tidy(self):
        self.assertEqual(self.find([self.c(1, spec=self.spec("로그인")),
                                    self.c(2, title="아주 다른 말 모음", spec=self.spec("결제"))]), {})

    def test_no_definition_alone_is_not_a_candidate(self):
        """내용 없음은 **혼자서는 취소 사유가 아니다** — 「없앨까요」 가 아니라 「채울까요」 다.
        섞어 놓으면 목록만 길어지고 정작 정리할 것이 묻힌다 (2026-09-20: 17건 중 13건이 그랬다)."""
        self.assertEqual(self.find([self.c(1)]), {})                    # 내용만 없음 → 후보 아님
        self.assertEqual([c["no"] for c in core.needs_spec([self.c(1)])], [1])

    def test_no_definition_is_added_to_a_real_candidate(self):
        """다른 이유가 있으면 「내용도 없다」 를 붙여 준다 — 판단에 도움이 된다."""
        out = self.find([self.c(1, stage=None)])
        self.assertTrue(any("적혀 있지 않" in x for x in out[1]))

    def test_outside_the_roadmap(self):
        self.assertIn("로드맵", self.find([self.c(1, stage=None, spec=self.spec())])[1][0])

    def test_not_tied_to_the_goal(self):
        self.assertIn("목표", self.find([self.c(1, scores={"goal_fit": 1}, spec=self.spec())])[1][0])

    def test_stuck_without_an_owner(self):
        out = self.find([self.c(1, since="2026-08-01", spec=self.spec())])
        self.assertTrue(any("멈춰" in x for x in out[1]))

    def test_stuck_but_owned_is_not_a_candidate(self):
        """담당이 있으면 멈춤으로 세지 않는다 — 맡은 사람이 있으면 하기로 한 일이다."""
        self.assertEqual(self.find([self.c(1, since="2026-08-01", assignee="UA", spec=self.spec())]), {})

    def test_running_work_is_left_alone(self):
        """사람이 맡고 굴러가는 일은 아예 후보가 아니다."""
        self.assertEqual(self.find([self.c(1, stage=None, status="doing",
                                           assignee="UA", assign_src="human")]), {})

    def test_duplicate_is_named(self):
        self.assertIn("#7", self.find([self.c(1, duplicate_of=7, spec=self.spec())])[1][0])

    def test_more_reasons_come_first(self):
        out = core.tidy_candidates([self.c(1, stage=None, spec=self.spec()), self.c(2, stage=None, scores={"goal_fit": 1}, spec=self.spec())],
                                   self.STAGES, today="2026-09-20")
        self.assertEqual(out[0][0]["no"], 2)

    def test_kept_is_not_asked_again(self):
        """[✓ 계속 할 일] 을 누르면 다시 묻지 않는다 — 묻고 또 물으면 아무도 안 본다 (2026-09-20)."""
        self.assertEqual(self.find([self.c(1, stage=None, tidy_keep=True)]), {})

    def test_pushed_back_is_not_asked_again(self):
        """이미 뒤로 보낸 일은 사람이든 AI 든 다시 묻지 않는다 — 사장님 눈엔 둘 다 「뒤로 돌린 것」이다."""
        for src in ("human", "ai"):
            self.assertEqual(self.find([self.c(1, stage=None, priority="later", prio_src=src)]), {}, src)

    def test_filled_but_still_vague(self):
        """AI 가 채워도 미정투성이면 애초에 기록이 없다는 뜻이다 — 「채워졌다」 고 넘어가면
        판단할 수 없는 이슈가 판단된 것처럼 남는다 (2026-09-20: 19건을 채웠더니 절반이 미정)."""
        vague = {"why": "미정. 대화에 이유가 없습니다", "change": "미정", "expect": "미정",
                 "not_doing": "미정", "done_criteria": ["미정입니다"]}
        out = self.find([self.c(1, spec=vague)])
        self.assertTrue(any("미정" in x for x in out[1]))

    def test_a_real_definition_is_left_alone(self):
        good = {"why": "로그인이 자주 끊긴다", "change": "세션 시간을 늘린다",
                "expect": "끊김 신고가 준다", "not_doing": "비밀번호 규칙", "done_criteria": ["끊김 0건"]}
        self.assertEqual(self.find([self.c(1, spec=good)]), {})

    def test_one_or_two_unknown_fields_is_fine(self):
        """두 칸까지는 그럴 수 있다 — 모든 칸을 채우라고 다그치지 않는다."""
        half = {"why": "로그인이 자주 끊긴다", "change": "세션 시간을 늘린다",
                "expect": "미정", "not_doing": "미정", "done_criteria": ["끊김 0건"]}
        self.assertEqual(self.find([self.c(1, spec=half)]), {})

    def test_finished_is_never_a_candidate(self):
        self.assertEqual(self.find([self.c(1, stage=None, status="done"),
                                    self.c(2, stage=None, status="cancelled")]), {})


class MergeWordTest(unittest.TestCase):
    """손으로 합치는 말 알아듣기 — 낱말 겹침이 못 잡는 것을 사람이 잇는다 (2026-09-20).

    #7 「정리할 때 비슷한 이슈를 연결하기」 와 #57 「이슈 정리 도우미」 는 겹치는 낱말이 하나도 없었다.
    """

    def n(self, q):
        import handlers
        m = handlers.MERGE.search(q)
        return int(m.group(1) or m.group(2)) if m else None

    def test_understands_the_ways_people_say_it(self):
        for q in ("합치기 #57", "#57 과 같은 일이야", "중복 #57", "#57 이랑 묶어줘", "#7 합치기"):
            self.assertEqual(self.n(q), 57 if "57" in q else 7, q)

    def test_leaves_other_words_alone(self):
        """정리·상세·질문은 합치기로 가면 안 된다."""
        for q in ("정리", "상세", "이거 왜 필요해?", "#57", "목록"):
            self.assertIsNone(self.n(q), q)


class VagueTest(unittest.TestCase):
    """미정 칸 세기 — 만들 때 막는 기준이자 정리 후보 기준 (2026-09-20)."""

    def v(self, **spec):
        return core.vague({"spec": spec})

    def test_full_definition_is_zero(self):
        self.assertEqual(self.v(why="a", change="b", expect="c", not_doing="d", done_criteria=["e"]), 0)

    def test_all_unknown_is_five(self):
        self.assertEqual(self.v(why="미정", change="미정", expect="미정",
                                not_doing="미정", done_criteria=["미정입니다"]), 5)

    def test_empty_counts_as_unknown(self):
        """빈 칸과 「미정」 은 같다 — 둘 다 판단할 수 없다."""
        self.assertEqual(self.v(why="a", change="", expect=None, not_doing="", done_criteria=[]), 4)

    def test_sentence_containing_unknown(self):
        """「미정. 대화에 이유가 적혀 있지 않습니다」 도 미정이다."""
        self.assertEqual(self.v(why="미정. 대화에 이유가 적혀 있지 않습니다",
                                change="b", expect="c", not_doing="d", done_criteria=["e"]), 1)

    def test_no_spec_is_zero(self):
        """정의가 아예 없는 것은 여기서 세지 않는다 — needs_spec 이 따로 본다."""
        self.assertEqual(core.vague({}), 0)

if __name__ == "__main__":
    unittest.main()
