"""**문서와 코드가 짝을 맞추고 있나** — `python3 -m unittest discover -s tests -t .`

2026-09-22~23 하루에 같은 모양의 고장을 **네 번** 찾았다. 전부 「코드 속 한글 문자열이
`.md` 의 제목·칸 이름과 짝을 맞추는데 한쪽만 바뀌어 조용히 빈 값이 된 것」 이다:

  · `## 기능 (상위 이슈)` vs `## 기능 (여러 할 일을 묶는 칸)` → 기능 표가 통째로 비었다
  · `체크리스트를 갖춘 **이슈** 비율` vs `… **할 일** 비율` → 현황판 지표가 늘 `-` 였다
  · `## 가용 시간` 을 정확히 맞춰야 했다 → 괄호만 붙어도 일정이 전원 「넘침」
  · `MARK` 에 `review` 가 없었다 → 앱 홈의 확인 대기 카드는 아이콘이 빈 채였다

**골든으로는 못 막는다.** 골든은 「바뀌었으면 다시 뽑는다」 인데, 빈 표도 「바뀐 화면」 으로
보여서 되감아진다. 여기 있는 것은 **비면 깨진다** — 되감을 수가 없다.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import common  # noqa: E402
import docs  # noqa: E402
import gh_link  # noqa: E402


class TablesAreNotEmpty(unittest.TestCase):
    """시험은 `example/` 을 베껴 본다 — 거기 표가 비면 진짜 팀에서도 빈다."""

    def test_team_md_tables(self):
        self.assertTrue(docs.load_team(), "team.md 사람 표가 비었어요")
        self.assertTrue(docs.load_hours(), "team.md 「가용 시간」 표가 비었어요")
        self.assertTrue(docs.vacant_roles(), "team.md 「빈 역할과 대행」 표가 비었어요")

    def test_project_md_tables(self):
        self.assertTrue(docs.load_features(), "project.md 「기능」 표가 비었어요")
        self.assertTrue(docs.feature_state(), "project.md 기능 상태가 비었어요")
        self.assertTrue(docs.load_initiatives(), "project.md 「할 일」 표가 비었어요")

    def test_roadmap_md_table(self):
        self.assertTrue(docs.load_stages(), "roadmap.md 단계 표가 비었어요")
        self.assertTrue(docs.deadline(), "roadmap.md 에서 판단일을 못 읽었어요")


class MetricsMatchProjectMd(unittest.TestCase):
    """성공 기준은 `project.md` 가 적고 `docs.metrics()` 가 값을 낸다 — 이름이 짝이다."""

    def test_every_success_metric_has_a_value(self):
        rows = {i["kpi"]: i["target"] for i in docs.load_initiatives()}
        self.assertTrue(rows, "project.md 「할 일」 표를 못 읽었어요")
        have = set(docs.metrics())
        # 「정할 것」 으로 비워 둔 줄은 뺀다 — 아직 안 재기로 한 것이다
        missing = {k for k, target in rows.items() if k and k not in have and target != "정할 것"}
        self.assertEqual(missing, set(),
                         f"project.md 가 적은 성공 기준을 docs.metrics() 가 안 냅니다: {missing}")


class StatusMapsCoverEveryStatus(unittest.TestCase):
    """상태를 하나 늘리고 한 곳을 빠뜨리면 **그 상태의 카드가 GitHub 에 영영 안 올라간다** —
    `body_md` 의 `KeyError` 를 카드별 try 가 삼켜서 로그 한 줄만 남는다."""

    def test_all_maps_have_all_statuses(self):
        for name, d in (("common.SICON", common.SICON), ("common.MARK", common.MARK),
                        ("gh_link.LABEL", gh_link.LABEL), ("gh_link.COLUMN", gh_link.COLUMN)):
            self.assertGreaterEqual(set(d), set(common.LABEL),
                                    f"{name} 에 빠진 상태: {set(common.LABEL) - set(d)}")

    def test_slack_and_the_repo_call_a_status_the_same(self):
        """같은 카드가 자리마다 다른 말로 불리면 사람이 찾지 못한다."""
        self.assertEqual(common.LABEL["blocked"], gh_link.LABEL["blocked"])
        self.assertEqual(common.LABEL["review"], gh_link.LABEL["review"])


class StarterTeamStillWorks(unittest.TestCase):
    """`templates/starter` 로 시작한 새 팀에서도 표가 읽혀야 한다 — 아무 시험도 안 보던 곳이다."""

    def test_starter_markdown_has_the_headings_the_code_looks_for(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "templates" / "starter"
        self.assertTrue(root.exists(), "templates/starter 가 없어요")
        text = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.md"))
        # 코드가 실제로 찾는 제목들 — `docs.load_*` 가 쓰는 것과 같아야 한다
        for head in ("## 가용 시간", "## 기능", "## 할 일", "## 기간"):
            self.assertIn(head, text, f"starter 에 「{head}」 가 없어요 — 새 팀은 이 표가 빈 채로 시작합니다")


if __name__ == "__main__":
    unittest.main()
