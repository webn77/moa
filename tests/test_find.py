"""이슈 찾기 (#54) — 번호를 몰라도 목록·사람·검색으로 찾는다.

답이 순수 계산이라 여기서 전부 고정할 수 있다. 읽기만 하는 기능이라 상태를 바꾸지 않는 것도 본다.
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from messages import say  # noqa: E402
from flows.find import reply_for  # noqa: E402
from store import STATE  # noqa: E402

A, B = "UA", "UB"
TEAM = {A: {"name": "이동원", "max": 3}, B: {"name": "won7036", "max": 3}}


def card(no, **kw):
    c = {"no": no, "title": f"이슈 {no}", "status": "todo", "card_ts": f"{no}.0", "by": "나", "spec": None}
    c.update(kw)
    return c


class FindTest(unittest.TestCase):
    def setUp(self):
        STATE["cards"] = {c["card_ts"]: c for c in [
            card(1, assignee=A, status="doing"),
            card(2, assignee=A),
            card(3, assignee=B, title="캔버스 다시 그리기"),
            card(4),
            card(5, status="done", title="캔버스 옛날 일"),
        ]}

    def ask(self, q, user=A):
        return reply_for(q, user, TEAM)

    def test_list_is_everything(self):
        """「목록」 은 전체다 — 내 것만 보여 주면 팀이 뭘 하는지 안 보인다 (2026-09-20 사용자 지적)."""
        out = self.ask("목록")
        self.assertIn("4건", out)                     # 열린 것 전부 (#5 는 완료라 빠짐)
        for n in ("#1", "#2", "#3", "#4"):
            self.assertIn(n, out)

    def test_my_list_is_only_mine(self):
        out = self.ask("내 할 일")
        self.assertIn("2건", out)
        self.assertIn("#1", out)
        self.assertNotIn("#3", out)

    def test_doing_comes_first(self):
        """진행 중인 것이 위에 — 지금 뭘 하고 있나가 먼저 보여야 한다."""
        out = self.ask("목록")
        self.assertLess(out.index("#1"), out.index("#2"))

    def test_by_name(self):
        out = self.ask("won7036")
        self.assertIn("won7036 님이 맡은 일", out)
        self.assertIn("#3", out)

    def test_by_mention(self):
        self.assertIn("won7036 님이 맡은 일", self.ask(f"<@{B}>"))

    def test_nobody(self):
        out = self.ask("담당 없는 일")
        self.assertIn("#4", out)
        self.assertNotIn("#1", out)

    def test_search_includes_done_but_after_open(self):
        """끝난 것도 찾아 주되 열린 것 뒤에 — 찾는 사람은 보통 지금 것을 본다."""
        out = self.ask("캔버스")
        self.assertIn("#3", out)
        self.assertIn("#5", out)
        self.assertLess(out.index("#3"), out.index("#5"))

    def test_help_when_empty(self):
        """안내는 한 장짜리 — @PA 와 앱 홈이 같은 글을 쓴다 (messages.guide)."""
        from messages import say
        for q in ("", "도움말", "?"):
            self.assertIn(say("guide"), self.ask(q))

    def test_no_match_says_so_and_helps(self):
        """못 찾았으면 무엇을 할 수 있는지 같이 보여 준다 — 「없어요」 만 하면 다음에 뭘 할지 모른다."""
        out = self.ask("없는말없는말")
        self.assertIn("못 찾았어요", out)
        self.assertIn(say("guide"), out)

    def test_greeting_gets_a_greeting(self):
        """인사에는 인사로 — 검색어로 받으면 「‘하이’ 로 찾은 이슈 없어요」 가 나온다 (2026-09-20 실측)."""
        for q in ("하이", "안녕", "ㅎㅇ", "hello"):
            self.assertIn("안녕하세요", self.ask(q), q)

    def test_a_real_word_is_still_searched(self):
        """인사말이 들어가도 길면 찾기로 간다 — 「안녕 알림 고치기」 는 이슈 이름일 수 있다."""
        self.assertNotIn("안녕하세요", self.ask("캔버스 다시 그리기 안녕"))

    def test_reads_only(self):
        """찾기는 아무것도 바꾸지 않는다 (#11 — AI는 읽기까지)."""
        before = copy.deepcopy(STATE["cards"])
        for q in ("목록", "이동원", "담당 없는 일", "캔버스", ""):
            self.ask(q)
        self.assertEqual(STATE["cards"], before)



class LinkTest(unittest.TestCase):
    """끝난 이슈도 눌러서 열 수 있어야 한다 (2026-09-20)."""

    def link(self, status, slack):
        from views.card import link_of
        return link_of({"no": 48, "title": "옛 이슈", "status": status, "permalink": "https://s/48"}, slack=slack)

    def test_done_link_still_works_in_slack(self):
        """Slack 표기는 <url|글자> 다 — 글자 안에 물결을 넣으면 링크가 아니라 물결이 보인다."""
        self.assertEqual(self.link("done", True), "~<https://s/48|#48 옛 이슈>~")

    def test_done_link_still_works_in_canvas(self):
        self.assertEqual(self.link("done", False), "~~[#48 옛 이슈](https://s/48)~~")

    def test_open_has_no_strike(self):
        self.assertEqual(self.link("todo", True), "<https://s/48|#48 옛 이슈>")

    def test_no_permalink_falls_back_to_text(self):
        from views.card import link_of
        self.assertEqual(link_of({"no": 9, "title": "링크 없음", "status": "done"}, slack=True), "~#9 링크 없음~")


class DoubleLinkTest(unittest.TestCase):
    """링크를 두 번 감싸면 주소가 글자로 튀어나온다 (2026-09-20 현황에서 발견)."""

    def test_already_linked_text_is_left_alone(self):
        from views.card import link_of
        c = {"no": 7, "title": "무엇", "status": "todo", "permalink": "https://s/7"}
        inner = "<https://s/7|#7 무엇>"
        self.assertEqual(link_of(c, True, inner), inner)

    def test_digest_does_not_double_wrap(self):
        """현황이 쓰는 자리 — ref(link=False) 여야 한다."""
        import pathlib, re
        src = pathlib.Path(__file__).parent.parent / "views" / "digest.py"
        for m in re.finditer(r"link_of\([^)]*ref\([^)]*\)", src.read_text(encoding="utf-8")):
            self.assertIn("link=False", m.group(0))

if __name__ == "__main__":
    unittest.main()


class TalkTest(unittest.TestCase):
    """못 알아들었을 때 검색으로 떨어뜨리지 않는다 (#71)."""

    def r(self, q):
        from flows.find import reply_for
        from docs import load_team
        return reply_for(q, "U0EXAMPLEPM", load_team())

    def test_question_mark_is_not_help(self):
        """예전에는 "?" 가 도움말 낱말이라 물음표가 든 질문이 전부 인사말을 받았다."""
        for q in ("PA-40 어떻게 됐어?", "지금 뭐 해?", "이거 어떻게 해?"):
            self.assertNotIn("안녕하세요", self.r(q), q)

    def test_our_own_tag_is_understood(self):
        """PA-40 은 우리가 만든 표기다 — 우리가 못 알아들으면 안 된다."""
        for q in ("PA-40", "#40", "PA-40 어떻게 됐어?"):
            self.assertIn("40", self.r(q), q)

    def test_unknown_number_says_so(self):
        self.assertIn("없는 번호", self.r("PA-9999"))

    def test_small_talk_is_not_a_search(self):
        for q in ("고마워", "ㅋㅋㅋ", "수고했어"):
            self.assertNotIn("못 찾았어요", self.r(q), q)

    def test_sentence_is_not_a_search(self):
        """「'지금 뭐 해?' 로는 못 찾았어요」 는 검색어로 받았다는 뜻이라 말귀를 못 알아듣는 것처럼 보인다."""
        for q in ("지금 뭐 해?", "이거 어떻게 해?"):
            self.assertIn("잘 모르겠어요", self.r(q), q)

    def test_real_search_still_works(self):
        self.assertIn("찾은 할 일", self.r("캔버스"))

    def test_help_and_greeting_still_work(self):
        self.assertIn("안녕하세요", self.r("하이"))
        self.assertIn("안녕하세요", self.r("도움말"))


class SpacingTest(unittest.TestCase):
    """띄어쓰기로 갈라지지 않는다 (#71, 2026-09-21 사장님이 실제로 치신 말).

    「내 할일」 이 안 먹혔다 — 목록에 「내 할 일」(다 띄움)·「내할일」(다 붙임)만 있고
    **중간 형태가 빠져** 있었다. 한국어는 띄어쓰기가 흔들리므로 양쪽에서 공백을 지우고 맞춘다.
    """

    def setUp(self):
        # **원래대로 돌려 놓는다** — TalkTest 는 진짜 cards.json 을 보는데, 여기서 덮어쓴 채로
        # 두면 옆 시험이 없는 카드를 찾게 된다 (2026-09-21 실제로 깨뜨렸다)
        self.old = STATE["cards"]
        STATE["cards"] = {c["card_ts"]: c for c in [card(1, assignee=A, status="doing"), card(2, assignee=A), card(3)]}

    def tearDown(self):
        STATE["cards"] = self.old

    def r(self, q, user=A):
        return reply_for(q, user, TEAM)

    def test_my_work_any_spacing(self):
        for q in ("내 할일", "내할일", "내 할 일", "내  할  일", "내 할일 뭐야", "제 할일", "내 업무"):
            self.assertIn("내 계획", self.r(q), q)

    def test_natural_sentences_find_my_work(self):
        """「내가 할일 찾아줘」 처럼 말해도 된다 — 낱말을 외우게 하지 않는다."""
        for q in ("내가 할일 찾아줘", "나 뭐해야 돼", "내가 맡은 일 보여줘"):
            self.assertIn("내 계획", self.r(q), q)

    def test_does_not_over_match(self):
        """넓히다가 남의 것까지 내 것으로 끌어오면 안 된다."""
        self.assertIn("열려 있는 할 일", self.r("목록"))
        self.assertNotIn("내 계획", self.r("캔버스"))


class GuideOnceTest(unittest.TestCase):
    """안내는 처음 한 번만 — 도움말일 때만 다시 (#71, 2026-09-21 사장님 지적).

    못 알아들을 때마다 여섯 줄을 붙였더니 **같은 글을 연달아 두 번** 보게 됐다.
    """

    def setUp(self):
        self.old = STATE["cards"]
        STATE["cards"] = {c["card_ts"]: c for c in [card(1, assignee=A)]}

    def tearDown(self):
        STATE["cards"] = self.old

    def test_first_time_gets_the_whole_guide(self):
        self.assertIn(say("guide"), reply_for("없는말", A, TEAM, seen=[]))

    def test_second_time_gets_one_line(self):
        out = reply_for("없는말", A, TEAM, seen=[A])
        self.assertNotIn(say("guide"), out)
        self.assertIn("도움말", out)

    def test_help_always_gets_the_whole_guide(self):
        """달라고 했으면 본 사람에게도 전부 준다 — 「도움말」 은 인사가 아니다."""
        self.assertIn(say("guide"), reply_for("도움말", A, TEAM, seen=[A]))

    def test_greeting_does_not_repeat_the_guide(self):
        """「안녕」 은 도움말 요청이 아니다 — 아는 사람에게 여섯 줄을 또 주지 않는다."""
        self.assertNotIn(say("guide"), reply_for("안녕", A, TEAM, seen=[A]))

    def test_asking_what_the_bot_does(self):
        """봇에게 뭘 할 수 있냐고 **어떻게 묻든** 무엇을 하는지는 말한다 (PA-75).

        사장님이 실제로 치신 말들이다 — 2026-09-21 오후에 PA-71 을 닫은 지 40분 만에
        「업무 어디까지 도와줄 수 있어?」 가 떨어졌다. **낱말 목록을 늘리는 대신
        떨어지는 자리(dont_get_it)의 답을 고쳤다** — 그래야 생각 못 한 말에도 답이 된다.
        """
        for q in ("업무 어디까지 도와줄 수 있어?", "ㅇㅇ 업무 어디까지 도와줄 수 있어?",
                  "뭘 도와줄 수 있어?", "어디까지 할 수 있어?", "어떤 일 해줘?", "뭐 도와줄래?"):
            out = reply_for(q, A, TEAM, seen=[A])
            self.assertIn("프로젝트 팀의 비서", out, q)     # 자기를 「할 일」 로 좁혀 말하지 않는다
            self.assertNotIn("찾은 할 일", out, q)          # 검색어로 받지 않는다

    def test_introduces_itself_as_project_not_issue(self):
        """「저는 할 일을 챙겨요」 로 좁혀 말하면 사람이 그 밖의 것은 묻지 않게 된다.

        2026-09-21 사장님 정의: **프로젝트 관리 봇이고 할 일은 그중 하나다.**
        그리고 **프로젝트 만들기가 시작점**이라는 것이 두 소개 모두에 있어야 한다 —
        DM 을 처음 연 사람이 무엇부터 하면 되는지 모르면 빈 화면과 같다.
        """
        from messages import say
        for k in ("dont_get_it", "dm_hello"):
            self.assertIn("프로젝트", say(k), k)

    def test_question_mark_is_not_a_search_word(self):
        """물음표로 끝나면 찾아 달라는 말이 아니다.

        「뭘 도와줄 수 있어?」 가 검색어가 되어 **「봇이 자기가 뭘 하는지 설명한다」 카드를
        찾아왔다** — 봇에게 물은 것을 이슈 제목으로 받은 셈이다 (2026-09-21 실측).
        """
        STATE["cards"]["x"] = card(99, title="봇이 자기가 뭘 하는지 설명한다")
        try:
            self.assertNotIn("99", reply_for("뭘 도와줄 수 있어?", A, TEAM, seen=[A]))
            self.assertIn("찾은 할 일", reply_for("자기가 뭘 하는지", A, TEAM, seen=[A]))   # 물음표 없으면 찾는다
        finally:
            STATE["cards"].pop("x", None)

    def test_the_guide_does_not_trap_itself(self):
        """안내에 「찾을 말」 이라 적었더니 사장님이 그대로 치셨다 — 자리표시가 아니라 예시를 준다."""
        from messages import say
        self.assertNotIn("· 찾을 말", say("guide"))
