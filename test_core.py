"""배정 계산 테스트 (#35) — `python3 -m unittest discover -s tests -t .`

**`-t .` 를 빼면 안 된다.** 빼면 `tests/__init__.py` 가 안 불려서 `MOA_DATA` 가 비고,
데이터 폴더가 **코드 폴더**가 된다 — 시험이 진짜 `cards.json` 을 덮어쓴다
(2026-09-22 실측: 71건이 5건으로 줄었다. 돌던 봇의 기억으로만 살아남았다).

배정 계산은 2026-09-19 하루에 여러 번 깨졌다 (사람 배정 유실, 전부 배정, 내려놓기 즉시 재배정, 과부하 증가).
그 자리를 테스트로 고정한다.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import core  # noqa: E402

A, B = "UA", "UB"
TEAM = {A: {"max": 3}, B: {"max": 3}}
STAGES = {"개발"}
HIGH = {"value": 5, "urgency": 5, "goal_fit": 5, "effort": 2}      # 점수 10


def card(no, who=A, status="todo", **kw):
    c = {"no": no, "status": status, "suggested": who, "stage": "개발", "scores": dict(HIGH)}
    c.update(kw)
    return c


def run(cards, **kw):
    d = {c["no"]: c for c in cards}
    core.place(d, TEAM, STAGES, **kw)
    return d


def mine(d, uid):
    return sorted(c["no"] for c in d.values() if c.get("assignee") == uid and c["status"] not in ("done", "cancelled"))


class PlaceTest(unittest.TestCase):
    def test_1_limit_then_backlog(self):
        """한 사람에게 7건 → 진행 3 + 대기 2 만 배정, 나머지 2건은 담당 없음."""
        d = run([card(n) for n in range(1, 8)])
        self.assertEqual(len(mine(d, A)), 5)
        self.assertEqual(sum(c["priority"] == "now" for c in d.values()), 3)
        self.assertEqual(sum(c["priority"] == "next" for c in d.values()), 2)
        left = [c for c in d.values() if not c.get("assignee")]
        self.assertEqual(len(left), 2)
        self.assertTrue(all(c["priority"] == "backlog" for c in left))

    def test_2_done_pulls_next(self):
        """하나 끝내면 담당 없던 일이 추천 담당에게 올라온다."""
        cards = [card(n) for n in range(1, 8)]
        d = run(cards)
        waiting = [c["no"] for c in d.values() if not c.get("assignee")]
        d[mine(d, A)[0]]["status"] = "done"
        core.place(d, TEAM, STAGES)
        self.assertEqual(len(mine(d, A)), 5)
        self.assertTrue(any(d[n].get("assignee") == A for n in waiting))

    def test_3_human_assignment_kept(self):
        """사람이 맡은 일은 점수가 낮고 자리가 차도 빠지지 않는다."""
        low = card(99, assignee=A, assign_src="human", scores={"value": 1, "urgency": 1, "goal_fit": 2, "effort": 5})
        d = run([card(n) for n in range(1, 8)] + [low])
        self.assertEqual(d[99]["assignee"], A)
        self.assertEqual(d[99]["assign_src"], "human")

    def test_4_waits_for_prerequisite(self):
        """먼저 끝나야 할 일이 남아 있으면 진행 칸이 아니라 대기 칸. 끝나면 진행 칸으로."""
        d = run([card(1, who=B), card(2, after=[1])])
        self.assertEqual(d[2]["priority"], "next")
        d[1]["status"] = "done"
        core.place(d, TEAM, STAGES)
        self.assertEqual(d[2]["priority"], "now")

    def test_5_outside_stage_or_duplicate_is_later(self):
        """로드맵 단계 밖 · 중복은 나중, 담당 없음."""
        d = run([card(1, stage=None), card(2, duplicate_of=1), card(3)])
        for n in (1, 2):
            self.assertEqual(d[n]["priority"], "later")
            self.assertIsNone(d[n].get("assignee"))
        self.assertEqual(core.plevel(d[1]), 4)

    def test_6_capcut_reduces_and_does_not_refill(self):
        """맡는 개수 줄이기 — 5 → 4, 빈자리가 다시 채워지지 않는다 (2026-09-19 과부하 증가 버그)."""
        d = run([card(n) for n in range(1, 9)], capcut={A: 1})
        self.assertEqual(len(mine(d, A)), 4)

    def test_7_no_auto_stays_unassigned(self):
        """과부하로 내려놓은 일은 자리가 있어도 자동 배정하지 않는다 (2026-09-19 즉시 재배정 버그)."""
        d = run([card(1, no_auto=True)])
        self.assertIsNone(d[1].get("assignee"))

    def test_p1_is_human_only_and_goes_first(self):
        """AI 점수로는 P1 이 나오지 않는다. 사람이 P1 으로 정하면 점수가 낮아도 먼저."""
        d = run([card(1), card(2, plevel=1, plevel_src="human", scores={"value": 2, "urgency": 2, "goal_fit": 2, "effort": 4})])
        self.assertEqual(core.plevel(d[1]), 2)
        self.assertEqual(d[2]["rank"], 1)


LABELS = [("why", "왜"), ("change", "바뀌는 것")]


class SpecDiffTest(unittest.TestCase):
    def test_only_changed_fields(self):
        """바뀐 칸만 나온다 — 제목 · 본문 칸 · 완료 조건(추가·삭제)."""
        old = {"why": "A", "change": "B", "done_criteria": ["x", "y"]}
        new = {"why": "A", "change": "B2", "done_criteria": ["y", "z"]}
        d = core.spec_diff("t", old, "t2", new, LABELS)
        self.assertEqual(d, [("제목", "t", "t2"), ("바뀌는 것", "B", "B2"), ("완료 조건", "x", "z")])

    def test_no_change_is_empty(self):
        """안 바뀌면 이력을 남기지 않는다 — 공백 차이도 무시."""
        s = {"why": "A ", "done_criteria": ["x"]}
        self.assertEqual(core.spec_diff("t", s, "t", {"why": "A", "done_criteria": ["x"]}, LABELS), [])

    def test_from_empty(self):
        """정의가 없다가 생기면 채운 칸이 나온다."""
        d = core.spec_diff("t", None, "t", {"why": "A"}, LABELS)
        self.assertEqual(d, [("왜", "", "A")])


class DecisionDiffTest(unittest.TestCase):
    def test_changed_only(self):
        """결정 4칸 중 바뀐 것만 — AI 배정이 풀리면 담당·목표일 둘 다 나온다."""
        L = [("assignee", "담당"), ("plevel", "우선순위"), ("due", "목표일"), ("stage", "단계")]
        a = {"assignee": "U1", "plevel": 2, "due": "2026-09-21", "stage": "개발"}
        b = dict(a, assignee=None, due=None)
        self.assertEqual(core.decision_diff(a, b, L),
                         [("assignee", "담당", "U1", None), ("due", "목표일", "2026-09-21", None)])
        self.assertEqual(core.decision_diff(a, dict(a), L), [])


class ReviewTest(unittest.TestCase):
    TEAM = {"UPM": {"role": "PM", "max": 3}, "UDEV": {"role": "개발", "max": 3}}

    def test_worker_done_goes_to_review(self):
        """담당자가 완료하면 확인 대기 — 요청자가 확인한다."""
        c = {"status": "doing", "assignee": "UDEV", "by_id": "UREQ"}
        self.assertEqual(core.next_status(c, "done", "UDEV", self.TEAM), "review")
        self.assertEqual(core.confirmer(c, self.TEAM), "UREQ")

    def test_approver_closes(self):
        """요청자나 PM 이 완료하면 바로 완료. 확인 대기에서 확인해도 완료."""
        c = {"status": "review", "assignee": "UDEV", "by_id": "UREQ"}
        self.assertEqual(core.next_status(c, "done", "UREQ", self.TEAM), "done")
        self.assertEqual(core.next_status(c, "done", "UPM", self.TEAM), "done")
        self.assertEqual(core.next_status(c, "done", "UDEV", self.TEAM), "review")   # 담당자가 다시 눌러도 그대로

    def test_no_requester_asks_pm(self):
        c = {"status": "doing", "assignee": "UDEV", "by_id": "UDEV"}
        self.assertEqual(core.confirmer(c, self.TEAM), "UPM")

    def test_alone_or_off_closes_directly(self):
        """PM 이 스스로 요청하고 맡은 일 · 팀 설정으로 끈 경우는 바로 완료."""
        c = {"status": "doing", "assignee": "UPM", "by_id": "UPM"}
        self.assertEqual(core.next_status(c, "done", "UPM", self.TEAM), "done")
        c2 = {"status": "doing", "assignee": "UDEV", "by_id": "UREQ"}
        self.assertEqual(core.next_status(c2, "done", "UDEV", self.TEAM, review=False), "done")

    def test_review_keeps_slot(self):
        """확인 대기도 담당 자리를 차지한다 — 빠지면 그 사이 다른 일이 배정된다."""
        d = run([card(1, status="review", assignee=A)] + [card(n) for n in range(2, 9)])
        self.assertEqual(d[1]["assignee"], A)
        self.assertEqual(d[1]["priority"], "now")


class CheckTest(unittest.TestCase):
    """올려도 되는가 (#58) — 막는 것은 화면을 거짓말하게 만드는 것뿐이다."""

    def ok(self, **kw):
        c = {"no": 10, "title": "제목", "spec": {"why": "왜", "done_criteria": ["끝"]},
             "due": "2026-09-30", "stage": "개발", "feature": "봇"}
        c.update(kw)
        return c

    def test_full_card_passes(self):
        block, warn = core.check_card(self.ok(), [self.ok()], "number", stage_names=STAGES)
        self.assertEqual((block, warn), ([], []))

    def test_empty_spec_blocks(self):
        """제목만 있는 이슈는 올리지 않는다 — 이게 21건 새는 자리였다."""
        block, _ = core.check_card({"no": 1, "title": "제목"}, [], "number")
        self.assertEqual(len(block), 2)                       # 정의 없음 + 완료 조건 없음

    def test_missing_only_warns(self):
        """목표일·단계·기능이 없는 건 막지 않는다 — 모자란 것은 거절 사유가 아니다."""
        c = self.ok(due=None, stage=None, feature=None)
        block, warn = core.check_card(c, [c], "number", stage_names=STAGES)
        self.assertEqual(block, [])
        self.assertEqual(len(warn), 3)

    def test_duplicate_number_blocks(self):
        c = self.ok()
        block, _ = core.check_card(c, [c, self.ok()], "number")
        self.assertTrue(any("다른 이슈가" in x for x in block))

    def test_waiting_for_nobody_blocks(self):
        c = self.ok(after=[99])
        block, _ = core.check_card(c, [c], "number")
        self.assertTrue(any("#99" in x for x in block))
        self.assertEqual(core.check_card(self.ok(after=[99]), [], "number")[0], [])   # 판을 모르면 안 센다

    def test_self_wait_blocks(self):
        c = self.ok(after=[10])
        block, _ = core.check_card(c, [c], "number")
        self.assertTrue(any("제 자신" in x for x in block))

    def test_cycle_blocks(self):
        """서로 기다리면 둘 다 영영 「지금」 칸에 못 온다 (#68)."""
        a = self.ok(no=10, after=[11])
        b = self.ok(no=11, after=[10])
        block, _ = core.check_card(a, [a, b], "number")
        self.assertTrue(any("서로 기다리면" in x for x in block))

    def test_long_cycle_blocks(self):
        a = self.ok(no=10, after=[11])
        b = self.ok(no=11, after=[12])
        d = self.ok(no=12, after=[10])
        block, _ = core.check_card(a, [a, b, d], "number")
        self.assertTrue(any("서로 기다리면" in x for x in block))


class RelationsTest(unittest.TestCase):
    """🔗 관계 (#68) — 그리는 쪽이 쓰는 한 곳. 끝난 선행이 대기에서 빠지는지가 핵심."""

    def board(self, s1="todo"):
        return [{"no": 1, "status": s1, "title": "앞"},
                {"no": 2, "status": "todo", "title": "가운데", "after": [1]},
                {"no": 3, "status": "todo", "title": "뒤", "after": [2]},
                {"no": 4, "status": "cancelled", "title": "취소된 뒤", "after": [2]}]

    def test_both_directions(self):
        cards = self.board()
        wait, block, done = core.relations(cards[1], cards)
        self.assertEqual([x["no"] for x in wait], [1])
        self.assertEqual([x["no"] for x in block], [3])      # 취소된 #4 는 막히는 쪽에 안 센다
        self.assertEqual(done, [])

    def test_finished_predecessor_leaves_waiting(self):
        """선행이 끝나면 after 값은 남지만 「대기」 에서는 빠진다 — 안 그러면 풀린 카드에 자물쇠가 붙는다."""
        cards = self.board(s1="done")
        wait, _, done = core.relations(cards[1], cards)
        self.assertEqual(wait, [])
        self.assertEqual([x["no"] for x in done], [1])
        self.assertEqual(cards[1]["after"], [1])             # 값 자체는 안 지운다

    def test_unknown_number_ignored(self):
        cards = self.board()
        cards[1]["after"] = [1, 99]
        wait, _, done = core.relations(cards[1], cards)
        self.assertEqual([x["no"] for x in wait], [1])
        self.assertEqual(done, [])

    def test_nothing(self):
        self.assertEqual(core.relations({"no": 1, "status": "todo"}, [{"no": 1, "status": "todo"}]),
                         ([], [], []))


class CleanAfterTest(unittest.TestCase):
    """AI 가 준 선행 번호를 다듬는다 (#68) — 넣기 전에 막는 자리."""

    def cards(self):
        return [{"no": 1, "status": "todo"}, {"no": 2, "status": "todo", "after": [1]},
                {"no": 3, "status": "done"}, {"no": 4, "status": "todo", "after": [2]}]

    def test_keeps_open_numbers(self):
        self.assertEqual(core.clean_after([1, 2], 9, self.cards()), [1, 2])

    def test_drops_unknown_and_self(self):
        self.assertEqual(core.clean_after([99, 9, 1], 9, self.cards()), [1])

    def test_drops_finished(self):
        """이미 끝난 일은 선행으로 적어 봐야 화면만 어지럽다."""
        self.assertEqual(core.clean_after([3, 1], 9, self.cards()), [1])

    def test_drops_cycle(self):
        """#2 가 #1 을 기다리는데 #1 이 #2 를 기다리게 하면 둘 다 멈춘다."""
        self.assertEqual(core.clean_after([2], 1, self.cards()), [])
        self.assertEqual(core.clean_after([4], 1, self.cards()), [])   # 건너건너도 막는다

    def test_dedupes_and_ignores_junk(self):
        self.assertEqual(core.clean_after([1, 1, "2", None, "가"], 9, self.cards()), [1, 2])

    def test_empty(self):
        self.assertEqual(core.clean_after(None, 9, self.cards()), [])

    def test_done_never_blocks(self):
        """완료는 막지 않는다 — 팀이 쓰는 곳이라 막으면 일이 멈춘다."""
        c = {"no": 1, "spec": {"done_criteria": ["a", "b"]}, "checked": ["a"], "due": "2026-09-18"}
        block, warn = core.check_card(c, [], "done", today="2026-09-20")
        self.assertEqual(block, [])
        self.assertEqual(len(warn), 2)                        # 1/2 체크 + 2일 늦음
        self.assertIn("1/2", warn[0])

    def test_doing_asks_for_criteria(self):
        block, warn = core.check_card({"no": 1}, [], "doing")
        self.assertEqual(block, [])
        self.assertEqual(len(warn), 1)

    def test_urgent_by_date_first(self):
        """P2 인데 내일 마감인 일이 P1 모레 마감보다 먼저다 (2026-09-20)."""
        u = lambda d: core.due_urgency({"due": d}, "2026-09-20")
        self.assertEqual((u("2026-09-19"), u("2026-09-21"), u("2026-09-22"), u(None), u("언젠가")),
                         (0, 1, 2, 2, 2))

    def test_bad_date_is_not_late(self):
        """모르는 값은 통과가 아니라 걸린다 — 다만 없는 날짜로 늦었다고 하지는 않는다."""
        self.assertEqual(core.days_late({"due": "언젠가"}, "2026-09-20"), 0)
        self.assertEqual(core.days_late({}, "2026-09-20"), 0)
        self.assertEqual(core.days_late({"due": "2026-09-30"}, "2026-09-20"), 0)


if __name__ == "__main__":
    unittest.main()


class ReadableTest(unittest.TestCase):
    """긴 정의를 읽을 수 있게 펴기 (2026-09-20 사장님 지적: 「너무 어려워」)."""

    def r(self, t):
        from views.card import readable
        return readable(t)

    def test_steps_go_on_their_own_lines(self):
        self.assertEqual(self.r("앞말 ① 하나 ② 둘"), "앞말\n  ① 하나\n  ② 둘")

    def test_no_blank_line_between_steps(self):
        """빈 너비 매치가 같은 자리에서 두 번 걸리면 빈 줄이 하나 더 생긴다."""
        self.assertNotIn("\n  \n", self.r("앞말 ① 하나 ② 둘 ③ 셋"))

    def test_double_star_becomes_slack_bold(self):
        """Slack 은 별 하나가 굵게다 — md 습관으로 쓴 별 두 개가 글자로 새어 나왔다."""
        self.assertEqual(self.r("**굵게** 보통"), "*굵게* 보통")

    def test_plain_text_untouched(self):
        self.assertEqual(self.r("그냥 한 줄이에요"), "그냥 한 줄이에요")

    def test_empty(self):
        self.assertEqual(self.r(None), "")


class PlanTest(unittest.TestCase):
    """계획 (2026-09-22 사장님: 「내가 언제 뭘 하면 되는지를 계획할 수 있게」).

    `due` 는 **언제까지**, `start` 는 **언제 손을 대나**다. 2026-09-21 은 월요일이라
    이어지는 날이 화·수·목·금이고, 주말을 건너뛰는지도 여기서 드러난다.
    """
    MON = datetime.date(2026, 9, 21)

    def plan(self, cards, per_day=1.0, hours=None):
        d = {str(c["no"]): c for c in cards}
        core.plan(d, {A: per_day, B: per_day}, today=self.MON, hours_fn=hours or (lambda c: 1.0))
        return {c["no"]: c.get("start") for c in cards}

    def mine(self, no, **kw):
        return card(no, assignee=A, **kw)

    def test_every_open_card_of_mine_gets_a_day(self):
        """**묻지 않는다** — 열어 보면 이미 날짜가 붙어 있어야 한다."""
        got = self.plan([self.mine(1), self.mine(2), self.mine(3)])
        self.assertEqual(list(got.values()), ["2026-09-21", "2026-09-22", "2026-09-23"])

    def test_it_skips_the_weekend(self):
        got = self.plan([self.mine(i) for i in range(1, 8)])
        self.assertNotIn("2026-09-26", got.values())      # 토
        self.assertNotIn("2026-09-27", got.values())      # 일
        self.assertEqual(got[6], "2026-09-28")            # 여섯째는 월요일로

    def test_a_day_holds_as_much_as_the_person_has(self):
        """하루 2시간이면 1시간짜리 둘이 같은 날에 들어간다."""
        got = self.plan([self.mine(1), self.mine(2), self.mine(3)], per_day=2.0)
        self.assertEqual([got[1], got[2], got[3]], ["2026-09-21", "2026-09-21", "2026-09-22"])

    def test_a_job_bigger_than_a_day_still_gets_a_day(self):
        """하루치보다 큰 일이 놓을 자리를 못 찾고 영영 밀리면 안 된다."""
        got = self.plan([self.mine(1)], per_day=1.0, hours=lambda c: 9.0)
        self.assertEqual(got[1], "2026-09-21")

    def test_a_day_the_person_moved_is_left_alone(self):
        """사람이 정한 값은 AI 가 덮어쓰지 않는다 — `due_src`·`assign_src` 와 같은 약속."""
        fixed = self.mine(1, start="2026-10-01", start_src="human")
        got = self.plan([fixed, self.mine(2)])
        self.assertEqual(got[1], "2026-10-01")
        self.assertEqual(got[2], "2026-09-21")

    def test_a_missed_deadline_comes_first(self):
        """목표일이 지났는데 계획이 목요일이면 사람은 그걸 계획으로 안 읽는다 (2026-09-22 실측)."""
        got = self.plan([self.mine(1, status="doing"), self.mine(2, due="2026-09-18")])
        self.assertEqual(got[2], "2026-09-21")           # 지난 것이 오늘
        self.assertEqual(got[1], "2026-09-22")           # 진행 중이라도 그 뒤

    def test_what_must_finish_first_is_planned_first(self):
        """#1 이 #2 를 기다리면 #2 가 먼저다 — 아니면 계획이 거짓말이 된다."""
        got = self.plan([self.mine(1, after=[2]), self.mine(2)])
        self.assertLess(got[2], got[1])

    def test_a_cycle_does_not_hang(self):
        got = self.plan([self.mine(1, after=[2]), self.mine(2, after=[1])])
        self.assertEqual(len([x for x in got.values() if x]), 2)

    def test_finished_and_unassigned_work_has_no_plan(self):
        cards = [self.mine(1, status="done", start="2026-09-21", start_src="ai"),
                 card(2, assignee=None, start="2026-09-21", start_src="ai")]
        got = self.plan(cards)
        self.assertEqual([got[1], got[2]], [None, None])

    def test_the_plan_is_written_down_once(self):
        """앱 홈을 열거나 「내 할 일」 을 물을 때도 계획을 짠다 — 그 길에는 `redraw` 가 없어서
        **저장이 안 됐다.** 사람이 옮긴 날이 다시 켜면 사라진다. 안 바뀌면 안 쓴다 (홈을 열
        때마다 파일을 쓰면 켜자마자 수십 번 쓴다)."""
        from unittest import mock
        import flows.status as st
        with mock.patch.object(st, "save") as saved:
            st.plan()
            first = saved.call_count
            st.plan()
            self.assertEqual(saved.call_count, first, "안 바뀌었는데 또 썼다")

    def test_the_buckets_read_like_a_person_talks(self):
        from views.home import plan_when
        w = lambda iso: plan_when({"start": iso}, self.MON)[1]
        self.assertEqual(w("2026-09-18"), "오늘")        # 지난 날짜도 오늘 할 일이다
        self.assertEqual(w("2026-09-21"), "오늘")
        self.assertEqual(w("2026-09-22"), "내일")
        self.assertEqual(w("2026-09-25"), "이번 주")     # 같은 주 금요일
        self.assertEqual(w("2026-09-28"), "그다음")
        self.assertEqual(w(None), "언제 할지 미정")


class CardButtonsTest(unittest.TestCase):
    """카드에 무엇을 두고 무엇을 뺐나 (2026-09-22 사장님: 「단계 기능 이건 지금하고 안 맞는 거 같은데」).

    단계·기능은 **사람이 고를 칸이 아니다** — 단계는 만들 때 자동, 기능은 AI가 붙인다.
    새 팀에서는 고를 것이 하나뿐이라 칸만 차지했다. 값은 그대로 살아서 현황판이 쓴다.
    """

    def blocks(self, **kw):
        from views.card import card_blocks
        return card_blocks({"no": 1, "title": "권한 정리", "status": "todo", "card_ts": "1.0",
                            "stage": "개발·도그푸딩", "feature": "요청 입구", **kw})

    def ids(self, bs):
        return [e.get("action_id") for b in bs if b["type"] == "actions" for e in b["elements"]]

    def labels(self, bs):
        return [e["text"]["text"] for b in bs if b["type"] == "actions"
                for e in b["elements"] if e["type"] == "button"]

    def test_the_card_has_a_place_to_write_the_description(self):
        """전에는 📄 상세 → ✏️ 내용 수정 으로 창이 두 겹이라 아무도 못 찾았다."""
        self.assertIn("edit_content", self.ids(self.blocks()))

    def test_stage_and_feature_are_not_on_the_card(self):
        got = self.ids(self.blocks())
        self.assertNotIn("set_stage", got)
        self.assertNotIn("set_feature", got)

    def test_a_finished_card_has_no_description_button(self):
        self.assertNotIn("edit_content", self.ids(self.blocks(status="done")))

    def test_the_label_says_write_when_empty_and_fix_when_written(self):
        self.assertIn("📝 설명 쓰기", self.labels(self.blocks()))
        self.assertIn("✏️ 설명 고치기", self.labels(self.blocks(spec={"why": "권한이 샌다"})))


class DailyTest(unittest.TestCase):
    """하루 묶음 (#59) — 새로 쌓지 않고 edits 를 날짜로 모은다."""

    def md(self, cards):
        import store
        from views.digest import daily_md
        old = dict(store.STATE["cards"])
        store.STATE["cards"].clear()
        store.STATE["cards"].update({str(c["no"]): c for c in cards})
        try:
            return daily_md("2026-09-20")
        finally:
            store.STATE["cards"].clear()
            store.STATE["cards"].update(old)

    def card(self, no, *edits):
        return {"no": no, "title": f"일 {no}", "status": "todo", "edits": list(edits)}

    def e(self, icon, what="한 것", date="2026-09-20", **kw):
        return {"date": date, "icon": icon, "what": what, "who": "이동원", **kw}

    def test_nothing_happened_writes_nothing(self):
        """빈 파일을 남기지 않는다 — 아무 일도 없던 날은 파일도 없다."""
        self.assertIsNone(self.md([self.card(1)]))
        self.assertIsNone(self.md([self.card(1, self.e("✅", date="2026-09-19"))]))

    def test_groups_by_icon(self):
        md = self.md([self.card(1, self.e("✅")), self.card(2, self.e("💤"))])
        self.assertIn("✅ 끝낸 일 (1)", md)
        self.assertIn("💤 뒤로 보낸 것 (1)", md)
        self.assertIn("#1", md)

    def test_human_decisions_are_kept(self):
        """「그날 팀이 무엇을 정했나」 가 남아야 한다 (PA-22).

        🔄 는 **동기화가 아니라 결정**(담당·우선순위·목표일·단계)인데, 예전 주석이 「동기화」 라고
        잘못 적혀 있어 통째로 빠져 있었다 — PA-22 가 풀려던 바로 그것이 안 남고 있었다 (2026-09-21).
        """
        md = self.md([self.card(1, self.e("🔄", "담당 없음→이동원", reason="예상 시간으로 계산", by="U0EXAMPLEPM"))])
        self.assertIn("🔄 사람이 정한 것 (1)", md)
        self.assertIn("예상 시간으로 계산", md)

    def test_ai_auto_moves_are_only_counted(self):
        """AI 자동 배정이 하루를 채우면 사람이 정한 것이 그 속에 묻힌다 — 수만 센다."""
        md = self.md([self.card(1, self.e("🔄", "우선순위 P3→P4", by="ai")),
                      self.card(2, self.e("🔄", "담당 없음→이동원", by="U0EXAMPLEPM"))])
        self.assertIn("🔄 사람이 정한 것 (1)", md)          # 둘이 아니라 하나
        self.assertIn("AI 자동 조정 1건", md)
        self.assertNotIn("우선순위 P3→P4", md)

    def test_checks_are_only_counted(self):
        """완료 조건 체크는 근거가 달려 있어도 **진행**이지 **결정**이 아니다 — 이슈별 기록에 그대로 있다."""
        md = self.md([self.card(1, self.e("☑", "1번 체크")), self.card(2, self.e("✅"))])
        self.assertIn("완료 조건 체크", md)
        self.assertNotIn("1번 체크", md)

    def test_reason_wins_over_what(self):
        """왜 했는지가 무엇을 했는지보다 남을 값어치가 있다."""
        md = self.md([self.card(1, self.e("💤", "나중으로", reason="지금은 아님"))])
        self.assertIn("지금은 아님", md)

    def test_noisy_icons_are_only_counted(self):
        """체크·AI 자동 조정은 수만 센다 — 줄로 늘어놓으면 하루가 200줄이 된다.

        `by` 가 없는 🔄 는 **누가 정했는지 모르는 옛 기록**이라 올리지 않고 세기만 한다 (2026-09-21).
        """
        md = self.md([self.card(1, self.e("✅"), self.e("☑"), self.e("🔄"))])
        self.assertIn("체크·AI 자동 조정 2건", md)
        self.assertNotIn("☑ ", md)
        self.assertNotIn("사람이 정한 것", md)
