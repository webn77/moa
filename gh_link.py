#!/usr/bin/env python3
"""이슈 카드를 레포의 md 정본과 GitHub 이슈로 내보낸다.

방향은 한쪽이다 — 카드·파일 → GitHub. GitHub 의 제목·본문·열림/닫힘만 덮어쓴다.
그래서 합칠 일이 없고 충돌 해소 코드가 없다 (moduflow2 gh.py 와 같은 규칙).

  issues/012-….md   정본. frontmatter + 왜/바뀌는 것/기대와 확인/하지 않는 것/완료 조건
  GitHub Issue      같은 내용 + Slack 스레드 링크. 상태가 done 이면 닫는다
"""
import json, logging, re, subprocess, pathlib, datetime
import config

HERE = config.DATA                          # 팀 데이터 폴더 (#50) — git 은 여기서 돈다
ISSUES = HERE / "issues"
REPO = config.GITHUB.get("repo")            # 없으면 md 정본만 쓰고 GitHub 이슈·보드는 건너뛴다


def repo_of(c=None):
    """그 카드의 레포 (#70) — **번호를 주는 단위**라 프로젝트마다 다르다.

    카드가 없거나 안 적혀 있으면 첫 프로젝트의 레포 (프로젝트가 하나면 늘 그것 = 예전 REPO).
    """
    key = (c or {}).get("project")
    ps = config.projects()
    return next((p.get("repo") for p in ps if p.get("key") == key), ps[0].get("repo")) or REPO


LABEL = {"todo": "할 일", "doing": "진행 중", "blocked": "막힘", "review": "확인 대기", "done": "완료", "cancelled": "취소"}


def sh(*args, **kw):
    r = subprocess.run(args, capture_output=True, text=True, cwd=str(HERE), **kw)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout.strip()


def sh_try(*args):
    """실패해도 넘어가는 gh 호출. **반드시 sh 를 거친다** — 2026-09-20 에 pad 가 subprocess 를 직접 불러서,
    시험이 sh 를 막았는데도 진짜 레포의 이슈 두 개(#57·#58)가 닫혔다. 나가는 길은 sh 하나다."""
    try:
        return sh(*args)
    except RuntimeError as e:
        logging.warning(f"⚠️ {' '.join(args[:3])}: {str(e)[:120]}")
        return ""


BOT_FILES = ("cards.json", "ask.jsonl", "ask.done.jsonl", "backlog.md")
BOT_DIRS = ("issues/", "daily/", "meetings/", "decisions/")


def _bot_owned(path):
    """봇이 스스로 쓰는 파일인가 — `git pull` 전 「사람이 고치던 것」 을 셀 때 뺀다."""
    return path in BOT_FILES or path.startswith(BOT_DIRS)


def _path_of(line):
    """`git status --porcelain` 한 줄에서 경로만 꺼낸다.

    **글자 자리로 자르면 안 된다** — `sh()` 가 출력 전체를 strip 하므로 첫 줄은 앞 공백이 없어져
    `" M cards.json"` 이 `"M cards.json"` 이 되고, `line[3:]` 은 `ards.json` 이 된다.
    그래서 2026-09-21 에 cards.json 이 봇 파일로 안 걸러져 `git pull` 을 건너뛰었다.
    """
    parts = line.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    path = parts[1].strip().strip('"')
    return path.split(" -> ")[-1] if " -> " in path else path        # 이름이 바뀐 것은 새 이름으로 본다


def _head():
    """지금 돌고 있는 코드가 어느 버전인가 — (짧은 이름, 한 줄 제목, 올린 이, 언제). 못 읽으면 None."""
    out = sh_try("git", "log", "-1", "--date=format:%m/%d %H:%M", "--format=%h\x1f%s\x1f%an\x1f%ad")
    parts = (out or "").split("\x1f")
    return tuple(parts) if len(parts) == 4 and parts[0] else None


# 누가 올렸나 — git 이름 → 사람이 읽는 말 (2026-09-21 사장님 요청).
# 셋이 다 다른 이름으로 올린다: 봇은 `commit()` 에서, Claude 는 `--author` 로, 사장님은 맥의 git 이름으로.
# **Claude 가 이름을 따로 안 쓰면 사장님 계정으로 찍혀 둘이 같아 보인다** (docs/github-sync.md §6).
# `sandbox-bot` 은 이름을 「모아」 로 바꾸기 전(2026-09-21)에 올린 커밋들이다 — **지난 기록을 읽으려면
# 옛 이름도 알아야 한다.** 새로 올리는 것은 `commit()` 에서 `moa-bot` 으로 찍는다.
AUTHOR = {"moa-bot": "🤖 모아가 올림", "sandbox-bot": "🤖 봇이 올림", "claude": "🤖 Claude 가 올림"}


def _origin():
    """어디서 받아오나 — (owner/repo, 브랜치). 못 읽으면 ("", "").

    **`config.GITHUB` 의 레포가 아니다.** 저기는 이슈를 올리는 곳이고 프로젝트마다 다를 수 있다
    (`repo_of()`). 여기는 **이 작업 트리가 코드를 받아오는 곳**이라 `git remote` 가 정답이다.
    https(`https://github.com/a/b.git`) 든 ssh(`git@github.com:a/b.git`) 든 같은 모양으로 만든다.
    """
    m = re.search(r"[:/]([^/:]+/[^/:]+?)(?:\.git)?/?$", sh_try("git", "remote", "get-url", "origin") or "")
    return (m.group(1) if m else ""), (sh_try("git", "rev-parse", "--abbrev-ref", "HEAD") or "")


def version_line(head, origin=None):
    """버전 몇 줄 — 「어디서 · 어떤 버전 · 누가 올렸는지」 (2026-09-21 사장님 요청).

    받아오는 **곳**이 없으면 버전만 보고는 어느 레포 이야기인지 알 수 없다 — 프로젝트가 둘이 된 뒤로는
    더 그렇다 (사장님 지적).
    """
    if not head:
        return ""
    short, title, who, when = head
    repo, branch = origin if origin is not None else _origin()
    where = f"받는 곳: `{repo}` · `{branch}`\n" if repo else ""
    by = AUTHOR.get(who.lower(), f"{who} 님이 올림")
    return f"{where}지금 버전: `{short}` {title[:44]}{'…' if len(title) > 44 else ''}\n{by} · {when}"


def pull():
    """켤 때 `git pull` 을 한 번 — **안전할 때만** (#59). (받았나, 사람에게 할 말)을 돌려준다.

    **말하는 틀: `(git 명령)` 을 앞에, 무슨 일인지 쉬운 말로 뒤에** (2026-09-21 사장님이 정한 형식).

      (git pull) 최신 코드를 받아오는 중입니다.

    「당겨오기」 처럼 새 이름을 지으면 검색도 안 되고, 막혔을 때 무엇을 찾아봐야 할지도 모른다.
    그렇다고 `git pull` 만 쓰면 모르는 사람은 여전히 모른다 — **둘 다 준다.** 명령을 앞에 두는 것은
    사장님이 정한 것이다: 무엇에 대한 이야기인지가 먼저 보여야 한다.

    쉬운 말 쪽에는 **봇이 지금 어떤 상태이고 사람이 할 일이 있는지**를 적는다. 사장님은 Slack 에서
    일하다 이 글을 본다 — 「커밋이 안 됐다」 만 쓰면 무엇을 해야 하는지 알 수 없다.

    데이터 폴더가 코드 레포와 같은 동안은 봇이 **자기 소스가 든 작업 트리**를 당기는 셈이다.
    그래서 봇은 세 가지를 지킨다:

      · git 이 아니거나 remote 가 없으면 아무 일도 하지 않는다
      · **커밋 안 된 것이 있으면 건너뛴다** — 남의 편집 위에 머지하지 않는다
      · `--ff-only` — 갈라졌으면 실패하고 **사람이 푼다** (#59 「충돌은 사람이」)

    조용히 실패하면 며칠 뒤에야 드러나므로, 건너뛰거나 실패하면 할 말을 돌려준다.
    """
    if not (HERE / ".git").exists():
        return False, None
    try:
        if not sh("git", "remote"):
            return False, None
        dirty = sh("git", "status", "--porcelain")
    except RuntimeError as e:
        return False, ("*(git status)* 지금 코드가 어떤 상태인지 읽어 보려 했는데 못 읽었어요.\n"
                       f"봇은 하던 대로 계속 일해요. ({str(e)[:80]})")
    # 봇이 늘 쓰는 파일은 세지 않는다 — cards.json 은 봇이 살아 있는 한 거의 항상 바뀌어 있어서,
    # 이걸 막으면 **`git pull` 이 영영 안 된다** (2026-09-20 첫 실행에서 바로 드러났다).
    # 사람이 고치던 소스가 있을 때만 건너뛴다. `--ff-only` 는 겹치면 git 이 알아서 거부한다.
    mine = [x for x in dirty.splitlines() if not _bot_owned(_path_of(x))]
    if mine:
        # 「저장 안 된 파일」 이라고 하면 사장님이 자기가 뭘 안 저장했나 찾게 된다 — 파일은 저장돼 있고
        # git 에 커밋이 안 된 것이다. **하실 일이 없다는 것**을 분명히 적는다
        return False, ("*(git pull)* 최신 코드를 받아오려다 이번에는 건너뛰었어요.\n"
                       f"지금 고쳐지고 있는 파일이 {len(mine)}개 있어서예요 "
                       f"({', '.join(_path_of(x)[:30] for x in mine[:3])}). "
                       "그 위에 새 코드를 덮으면 고치던 것이 사라지거든요.\n"
                       "봇은 지금 코드로 하던 일을 그대로 해요 — *동원님이 하실 일은 없어요.*\n"
                       + version_line(_head()))          # 못 받았을 때야말로 어느 버전인지가 중요하다
    before = _head()
    try:
        sh("git", "pull", "--ff-only", "-q")
        # **잘 됐을 때도 말한다** (2026-09-21 사장님 요청) — 조용하면 지금 어느 버전이 도는지 알 수 없다.
        # 켤 때 한 번뿐이라 시끄럽지 않다. 이슈를 올리는 push 는 그대로 조용히 한다
        after = _head()
        head = "*(git pull)* 받아올 새 코드가 없었어요 — 이미 최신이에요." if after == before \
            else "*(git pull)* 최신 코드를 받아왔어요."
        return True, (head + "\n" + version_line(after)).strip()
    except RuntimeError as e:
        return False, ("*(git pull)* 최신 코드를 받아오지 못했어요.\n"
                       "코드가 두 갈래로 갈라져서, 어느 쪽이 맞는지 봇이 정할 수 없어요. "
                       f"*이건 사람이 풀어 주셔야 해요.* ({str(e)[:100]})")


def slug(title):
    s = re.sub(r"[^\w가-힣]+", "-", title).strip("-").lower()
    return s[:40] or "issue"


def history_md(c):
    """날짜별 기록 — 무엇이 · 누가/무엇에 의해 · 왜. 한 줄씩."""
    rows = [e for e in c.get("edits") or [] if e.get("what")]
    if not rows:
        return ""
    lines = [f"- {e.get('date', '')} {e.get('icon', '')} {e['what']} · {e.get('who', '')}"
             + (f" ({e['reason']})" if e.get("reason") else "") for e in rows]
    return "\n## 날짜별 기록\n" + "\n".join(lines) + "\n"


SLACK_NOTE = ("> 이 이슈는 **Slack 에서 관리합니다.** 여기서 고친 제목·본문은 다음 갱신 때 덮어써져요 —\n"
              "> 고칠 것은 Slack 스레드에 써 주세요. 댓글은 Slack 으로 전해집니다.")


def body_md(c, permalink):
    spec = c.get("spec") or {}
    if not spec:                                   # 아직 정리 전 — 빈 칸을 늘어놓지 않는다
        return (f"{SLACK_NOTE}\n\n아직 정의가 없는 이슈예요. Slack 스레드에서 정리하면 여기에 채워집니다."
                f"{history_md(c)}\n---\n상태: {LABEL[c['status']]} · Slack 스레드: {permalink}\n")
    done = spec.get("done_criteria") or []
    return f"""{SLACK_NOTE}

## 왜
{spec.get('why', '-')}

## 바뀌는 것
{spec.get('change', '-')}

## 기대와 확인
{spec.get('expect', '-')}

## 하지 않는 것
{spec.get('not_doing', '-')}

## 완료 조건
{chr(10).join(f"- [{'x' if x in (c.get('checked') or []) else ' '}] {x}" for x in done) or '- [ ] 미정'}
{history_md(c)}
---
정본: `issues/{c['file']}` · 상태: {LABEL[c['status']]} · Slack 스레드: {permalink}
"""


KEEP = ("## 회의록", "## 할 일 —", "## 메모")   # 봇이 다시 써도 지우지 않는 구역 — 회의·사람이 쓴 곳


def kept_tail(path):
    """기존 파일에서 보존 구역만 뽑는다."""
    if not path.exists():
        return ""
    parts = re.split(r"(?m)^(?=## )", path.read_text(encoding="utf-8"))
    return "".join(p for p in parts if p.startswith(KEEP)).rstrip()


def write_file(c, permalink):
    ISSUES.mkdir(exist_ok=True)
    c.setdefault("file", f"{c['no']:03d}-{slug(c['title'])}.md")
    tail = kept_tail(ISSUES / c["file"])
    spec = c.get("spec") or {}
    fm = {"id": c["no"], "title": c["title"], "state": c["status"], "by": c["by"],
          "slack": permalink, "tracker": c.get("tracker", ""),
          "updated": datetime.date.today().isoformat()}
    head = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items())
    (ISSUES / c["file"]).write_text(f"---\n{head}\n---\n\n# #{c['no']} {c['title']}\n\n"
                                   + body_md(c, permalink) + (f"\n\n{tail}\n" if tail else ""),
                                   encoding="utf-8")
    return ISSUES / c["file"]


PENDING = {}            # {파일경로: 메시지} — 5분 창이 닫힐 때 한 번에 커밋한다 (#58)


def queue(path, msg):
    """바로 커밋하지 않고 모은다. 창을 여는 쪽은 flows/github.py, 끌 때는 bot.py 가 flush 한다."""
    PENDING[str(path)] = msg
    return f"모음 ({len(PENDING)}건)"


def flush():
    """모인 파일을 한 번에 커밋·푸시한다. 모인 게 없으면 아무 일도 하지 않는다."""
    if not PENDING:
        return "모인 것 없음"
    paths, msgs = list(PENDING), list(PENDING.values())
    PENDING.clear()
    msg = msgs[0] if len(msgs) == 1 else f"이슈 {len(msgs)}건 갱신\n\n" + "\n".join(f"- {m}" for m in msgs)
    return commit(paths, msg)


def commit(paths, msg):
    """지정한 파일만 커밋한다 — 사람이 스테이징해 둔 다른 파일을 봇 커밋에 쓸어 넣지 않게 (검토 결함 A)."""
    paths = [str(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
    if not (HERE / ".git").exists():                 # 데이터 폴더가 git 이 아니면 파일만 남긴다
        return "git 아님 — 파일만 저장"
    sh("git", "add", "--", *paths)
    if sh("git", "status", "--porcelain", "--", *paths):
        sh("git", "-c", "user.name=moa-bot", "-c", "user.email=bot@local", "commit", "-q", "-m", msg, "--", *paths)
        try:
            sh("git", "push", "-q", "origin", "HEAD:main")
        except RuntimeError as e:                    # 조용히 묻히지 않게 — 로그에 ⚠️ (검토 결함 E). 다음 push 때 같이 올라간다
            logging.warning(f"⚠️ push 실패 ({msg[:60]}): {str(e)[:120]}")
            return f"커밋함(푸시 실패: {str(e)[:60]})"
        return f"커밋·푸시함 ({len(paths)}건)" if len(paths) > 1 else "커밋·푸시함"
    return "바뀐 것 없음"


PROJECT = {"owner": config.GITHUB.get("project_owner"), "number": str(config.GITHUB.get("project_number") or "")}
COLUMN = {"todo": "Todo", "doing": "In Progress", "blocked": "In Progress", "review": "In Progress", "done": "Done", "cancelled": "Done"}


_BOARD = {}


def gh_no(c):
    """GitHub 이슈 번호 — 숫자로 따로 둔다 (검토 결함 D). 예전 카드는 tracker 「owner/repo#N」 에서 읽는다."""
    return str(c.get("gh_no") or (c.get("tracker") or "").split("#")[-1])


def board_sync(c):
    """이슈를 보드에 올리고 상태 열을 맞춘다. 막힘은 In Progress + 막힘 라벨."""
    if not c.get("issue_url"):
        return "이슈 없음"
    if not PROJECT["owner"] or not PROJECT["number"]:
        return "보드 없음"
    if not c.get("item_id"):
        out = sh("gh", "project", "item-add", PROJECT["number"], "--owner", PROJECT["owner"],
                 "--url", c["issue_url"], "--format", "json")
        c["item_id"] = json.loads(out)["id"]
    if not _BOARD:                                   # 보드 정보는 한 번만 조회 (검토 결함 B — 매번 두 번씩 불렀다)
        _BOARD["meta"] = json.loads(sh("gh", "project", "view", PROJECT["number"], "--owner", PROJECT["owner"], "--format", "json"))
        opts = json.loads(sh("gh", "project", "field-list", PROJECT["number"], "--owner", PROJECT["owner"],
                             "--format", "json"))["fields"]
        _BOARD["status"] = next(f for f in opts if f["name"] == "Status")
    meta, status = _BOARD["meta"], _BOARD["status"]
    want = COLUMN[c["status"]]
    option = next(o["id"] for o in status["options"] if o["name"] == want)
    sh("gh", "project", "item-edit", "--id", c["item_id"], "--project-id", meta["id"],
       "--field-id", status["id"], "--single-select-option-id", option)
    num = gh_no(c)
    add = ["--add-label", "막힘"] if c["status"] == "blocked" else ["--remove-label", "막힘"]
    sh_try("gh", "issue", "edit", num, "-R", repo_of(c), *add)
    return want


def new_comments(c, seen):
    """GitHub 이슈에 새로 달린 댓글만 돌려준다. seen 은 이미 가져온 댓글 id 목록."""
    if not c.get("tracker") or not REPO:
        return []
    num = gh_no(c)
    data = json.loads(sh("gh", "issue", "view", num, "-R", repo_of(c), "--json", "comments"))
    out = []
    for m in data.get("comments", []):
        cid = str(m.get("id") or m.get("createdAt"))
        if cid in seen:
            continue
        seen.append(cid)
        who = (m.get("author") or {}).get("login", "누군가")
        out.append((who, (m.get("body") or "").strip()))
    return out


def save_meeting(m, body, permalink):
    """회의록을 확정한다. 같은 회의는 같은 파일을 갱신한다 (확정을 누를 때만 바뀐다)."""
    box = HERE / "meetings"
    box.mkdir(exist_ok=True)
    if not m.get("file"):
        m["file"] = f"{m['date']}-{slug(m['title'])}.md"
    path = box / m["file"]
    issue = f"\nissue: {m['issue']}" if m.get("issue") else ""
    path.write_text(f"""---
date: "{m['date']}"
title: {json.dumps(m['title'], ensure_ascii=False)}
kind: meeting{issue}
slack: "{permalink}"
updated: "{datetime.datetime.now().isoformat(timespec='minutes')}"
---

# {m['title']}

{body}

---
출처: Slack 스레드 {permalink}
""", encoding="utf-8")
    commit(path, f"meeting {m['date']} {m['title']}")
    return f"meetings/{m['file']}"


def render_minutes(r, agenda):
    """회의록을 템플릿(meetings/TEMPLATE.md) 순서로 만든다. r 은 AI 가 낸 JSON."""
    cell = lambda x: str(x or "미정").replace("|", "/").replace("\n", " ")
    rows = "\n".join(f"| {cell(a.get('item'))} | {cell(a.get('discussion'))} | {cell(a.get('decision'))} |"
                     for a in r.get("agenda_results") or []) or "| - | - | - |"
    acts = "\n".join(f"| {cell(a.get('what'))} | {cell(a.get('owner'))} | {cell(a.get('due'))} |"
                     for a in r.get("action_items") or []) or "| - | - | - |"
    chg = "\n".join(f"- `#{x.get('issue')}` {cell(x.get('field'))} → {cell(x.get('to'))} ({cell(x.get('reason'))})"
                    for x in r.get("issue_changes") or []) or "- 없음"
    new = "\n".join(f"- {cell(x.get('title'))} — {cell(x.get('why'))}" for x in r.get("new_issues") or []) or "- 없음"
    dec = "\n".join(f"- {cell(x)}" for x in r.get("decisions") or []) or "- 없음"
    ag = "\n".join(f"{i}. {a}" for i, a in enumerate(agenda or [], 1)) or "- (아젠다 없음)"
    return f"""## 아젠다
{ag}

## 아젠다별 논의
| 아젠다 | 논의 | 결론 |
| --- | --- | --- |
{rows}

## 정한 것
{dec}

## 액션 아이템
| 할 일 | 담당 | 기한 |
| --- | --- | --- |
{acts}

## 이슈 변경 제안
{chg}

## 새 이슈 제안
{new}

## 다음 회의
- {cell(r.get('next_meeting'))}"""


def add_actions_to_issue(issue_no, meeting_title, actions):
    """회의에서 나온 액션 아이템을 이슈 정본 md 에 더한다."""
    for f in (HERE / "issues").glob(f"{int(issue_no):03d}-*.md"):
        text = f.read_text(encoding="utf-8")
        block = f"\n\n## 할 일 — {meeting_title}\n" + "\n".join(
            f"- [ ] {a.get('what')} · {a.get('owner') or '담당 미정'} · {a.get('due') or '기한 미정'}" for a in actions)
        if block.strip() in text:
            return f.name
        f.write_text(text + block + "\n", encoding="utf-8")
        commit(f, f"issue #{issue_no} 회의 액션 아이템")
        return f.name
    return None


def link_meeting_to_issue(issue_no, meeting_path, title):
    """이슈 정본 md 맨 아래에 회의록 줄을 더한다. 같은 줄은 한 번만."""
    for f in (HERE / "issues").glob(f"{int(issue_no):03d}-*.md"):
        text = f.read_text(encoding="utf-8")
        line = f"- [{title}]({meeting_path})"
        if line in text:
            return f.name
        if "## 회의록" not in text:
            text += "\n\n## 회의록\n"
        text += line + "\n"
        f.write_text(text, encoding="utf-8")
        commit(f, f"issue #{issue_no} 회의록 연결")
        return f.name
    return None


def save_note(title, body, permalink):
    """📌 로 찍힌 스레드를 회의록·기록으로 저장하고 커밋한다. 상대 경로를 돌려준다."""
    notes = HERE / "notes"
    notes.mkdir(exist_ok=True)
    today = datetime.date.today().isoformat()
    path = notes / f"{today}-{slug(title)}.md"
    n = 2
    while path.exists():
        path = notes / f"{today}-{slug(title)}-{n}.md"
        n += 1
    path.write_text(f"""---
date: "{today}"
title: {json.dumps(title, ensure_ascii=False)}
slack: "{permalink}"
kind: note
---

# {title}

{body}

---
출처: Slack 스레드 {permalink}
""", encoding="utf-8")
    commit(path, f"note {today} {title}")
    return f"notes/{path.name}"


NEW_BODY = ("아직 정의가 없는 이슈예요. Slack 스레드에서 정리하면 여기에 채워집니다.\n\n"
            "> 이 이슈는 **Slack 에서 관리합니다.** 고칠 것은 Slack 에 써 주세요 — 댓글은 Slack 으로 전해집니다.")


def reserve(title, repo=None):
    """**GitHub 이 번호의 주인이다** (#60). 이슈를 먼저 만들고 그 번호를 카드 번호로 쓴다.

    예전에는 봇 안의 카운터가 번호를 정하고 GitHub 이 따라왔다. 그래서 누가 GitHub 에서 이슈를
    하나 만들거나 PR 을 하나만 열어도 번호가 밀려 영영 어긋났다 (2026-09-20 에 59개를 손으로 맞췄다).
    받아 오면 PR 이 번호를 먹어도, 밖에서 이슈를 만들어도 어긋날 일이 없다.

    실패하면 **예외를 낸다 — 로컬 번호로 넘어가지 않는다.** 넘어가면 다음에 받아 온 번호와 겹친다.
    (no, url) 을 돌려준다.
    """
    url = sh("gh", "issue", "create", "-R", repo or REPO, "--title", title[:250], "--body", NEW_BODY)
    return int(url.rstrip("/").split("/")[-1]), url


def open_issues_without_cards(known):
    """밖에서 만든 이슈를 찾는다 — 번호가 이미 맞으므로 카드만 붙이면 된다 (#60 의 되가져오기).

    known 은 이미 카드가 있거나 한 번 가져온 번호들. 우리가 만든 이슈는 제목이 「#N …」 로 시작하고,
    자리표시는 「(빈 번호)」 다 — 둘 다 건너뛴다.
    """
    out = sh("gh", "issue", "list", "-R", REPO, "--state", "open", "--limit", "100",
             "--json", "number,title,body,url")
    return [g for g in json.loads(out)
            if g["number"] not in known
            and not g["title"].startswith(f"#{g['number']} ")
            and "(빈 번호)" not in g["title"]]


def adopt(no, title):
    """밖에서 만든 이슈를 가져올 때 제목에 번호를 붙인다 — 「#63 …」.

    GitHub 에서만 보는 사람도 우리 카드와 짝이 보이고, 매일 도는 대조가 이 꼴로 짝을 확인한다.
    """
    sh_try("gh", "issue", "edit", str(no), "-R", REPO, "--title", f"#{no} {title}"[:250])


def all_issues():
    """{번호: {state, title}} — 대조용. 이슈와 PR 을 한 번에 본다."""
    out = sh("gh", "issue", "list", "-R", REPO, "--state", "all", "--limit", "300", "--json", "number,state,title")
    return {g["number"]: {"state": g["state"], "title": g["title"]} for g in json.loads(out)}


def export(c, permalink, push=True):
    """파일을 쓰고 GitHub 이슈를 만들거나 갱신한다. (tracker, 파일경로, 로그) 를 돌려준다.

    push=False 면 정본 md 만 고친다 — GitHub 에 보낼 내용이 안 바뀐 경우 (지문 비교, #58).
    커밋은 바로 하지 않고 모은다. 창을 닫는 것은 flows/github.py · 끌 때는 bot.py.
    """
    path = write_file(c, permalink)
    title = f"#{c['no']} {c['title']}"
    body = body_md(c, permalink)
    if not REPO or not push:                          # GitHub 없이 쓰는 팀 · 보낼 게 안 바뀐 경우 — 정본 md 만
        return c.get("tracker"), str(path), queue(path, f"issue #{c['no']} {c['title']}")
    if not c.get("tracker"):
        # 번호는 카드를 만들 때 GitHub 에서 받아 온다 (#60). 여기까지 짝이 없다면 그때 실패한 것이다 —
        # 지금 새로 만들면 번호가 어긋나므로 만들지 않는다. 하루 한 번 대조가 잡아 준다 (#59)
        logging.warning(f"⚠️ #{c['no']} 에 GitHub 짝이 없어요 — 정본 파일만 씁니다")
        return None, str(path), queue(path, f"issue #{c['no']} {c['title']}")
    num = gh_no(c)
    sh("gh", "issue", "edit", num, "-R", repo_of(c), "--title", title, "--body", body)
    if c["status"] in ("done", "cancelled"):
        reason = "completed" if c["status"] == "done" else "not planned"
        sh_try("gh", "issue", "close", num, "-R", repo_of(c), "--reason", reason)
    else:
        sh_try("gh", "issue", "reopen", num, "-R", repo_of(c))   # 완료에서 돌아온 이슈는 다시 연다
    log = queue(path, f"issue #{c['no']} {c['title']}")
    try:
        col = board_sync(c)
    except Exception as e:
        col = f"보드 실패({str(e)[:60]})"
    return c.get("tracker"), str(path), f"{log} · 보드 {col}"
