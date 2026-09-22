"""DM 에서 GitHub 붙이기 — **한 칸씩 묻는다** (2026-09-22 사장님: 「그럼 깃허브도 등록도 안내 하는건?」).

  사람: 깃허브 등록
  모아: 먼저 확인해 봤어요 — gh 있음 · webn77 로그인됨 · 데이터 폴더는 git 아님
        ①어느 레포에 올릴까요? `owner/name` · 「만들어줘」
  사람: webn77/moa-mju
  모아: ②어느 프로젝트에 붙일까요?            ← 여럿일 때만
  모아: 이렇게 할까요? (팀 데이터가 그 레포에 올라가요) → 「네」 → 붙임

**GitHub 는 없어도 된다** (2026-09-22 실측). 레포가 없으면 번호를 봇이 매기고, 등록·담당·
현황판·회의록은 전부 그대로 돈다. 붙이면 얻는 것은 셋: 할 일이 GitHub 이슈로도 남고,
기록 파일이 레포에 쌓이고, 코드와 할 일이 한자리에서 보인다.

**토큰을 Slack 으로 받지 않는다.** 인증은 `gh` 명령이 맥에 저장해 둔 것을 쓴다 — 사람이
터미널에서 `gh auth login` 을 한 번 하면 된다. 비밀 값이 대화 기록에 남는 길을 아예 만들지 않는다.

**봇이 할 수 있는 것과 사람이 할 것을 가려서 말한다**
  봇: 레포 만들기(비공개) · `git init` · `.gitignore` · remote 붙이기 · 설정에 적기
  사람: `gh auth login` (터미널에서 한 번) · 공개로 할지 정하기
"""
import asyncio
import json
import re
import shutil

from common import HERE, PROJECTS, log
from flows.ask import CANCEL, COMMANDS, HEAD, yes as _yes
from messages import say
from slack import api
from store import STATE, save


START = re.compile(r"(깃허브|깃헙|github|레포|repo|저장소)\S*\s*\S{0,4}\s*(등록|연결|연동|붙여|붙이|설정|추가|쓰)"
                   r"|(등록|연결|연동)\S*\s*\S{0,4}\s*(깃허브|깃헙|github|레포|repo|저장소)", re.I)
# `owner/name` — GitHub 이 받는 글자만. 「https://github.com/a/b」 로 붙여 넣어도 받는다
REPO_OK = re.compile(r"(?:https?://github\.com/)?([A-Za-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?/?\s*$")
MAKE_IT = ("만들어", "새로", "없어", "없음", "만들자", "생성")
# **remote 가 GitHub 일 필요는 없다** (2026-09-22 사장님: 「위험하지 않도록 내부서버에서
# 관리할 방법도 있나 git 으로」). 사내 서버·사내 GitLab·NAS 의 bare 저장소 — 무엇이든 된다.
# 봇이 올리는 것은 `git push origin HEAD:main` 하나뿐이라 remote 종류를 안 가린다.
#
# 다만 **이슈·보드는 GitHub 것이다** — 사내 서버로 두면 기록은 쌓이고 밖으로는 안 나가지만,
# 할 일이 이슈로 남지는 않고 번호는 봇이 매긴다. 그 차이를 확인 줄에서 밝힌다
GIT_URL = re.compile(r"^(?:ssh://|git@|https?://|file://|/)\S+$", re.I)
_HEAD = re.compile(HEAD, re.I)
STEP_WHAT = {"repo": "어느 레포", "project": "어느 프로젝트", "confirm": "마지막 확인"}
# 데이터 폴더에는 로그·pid·plist 가 같이 있다 — 사람이 `git add .` 을 하면 그게 다 올라간다.
# 봇은 파일을 짚어서만 커밋하지만(gh_link.commit), 사람 손을 막아 주는 건 이 파일이다
IGNORE = """# 모아가 만든 것 — 봇이 쓰는 기록·상태는 레포에 올리지 않는다
bot.log
bot.out
bot.pid
*.plist
.DS_Store
"""


def _asking(user):
    return (STATE.get("new_repo") or {}).get(user)


def _steps(st):
    return ["repo"] + ([] if st.get("pkey_known") else ["project"])


def _where(st):
    steps, step = _steps(st), st.get("step") or "repo"
    n = steps.index(step) + 1 if step in steps else len(steps)
    return n, len(steps), STEP_WHAT.get(step, "확인")


def _repo_of(text):
    """말에서 `owner/name` 만 골라낸다. 못 고르면 빈 글자."""
    for word in re.split(r"[\s,]+", (text or "").strip()):
        m = REPO_OK.fullmatch(word)
        if m and m.group(2) not in ("", "."):
            return f"{m.group(1)}/{m.group(2)}"
    return ""


def target_of(text):
    """말에서 **어디에 쌓을지**를 읽는다 — ("github", "owner/name") · ("git", 주소) · (None, "").

    GitHub 모양을 먼저 본다: 맨 `owner/name` 과 github.com 주소. 그다음이 아무 git 주소다.
    """
    for word in re.split(r"[\s,]+", (text or "").strip()):
        if not word:
            continue
        got = _repo_of(word)
        if got:
            return "github", got
        if GIT_URL.match(word) and (word.endswith(".git") or word.count("/") >= 2):
            return "git", word
    return None, ""


def _short(name):
    return re.sub(r"^프로젝트[-\s]*", "", name or "").strip()


def _project_of(text):
    flat = re.sub(r"\s+", "", text or "").lower()
    for p in PROJECTS:
        name = _short(p.get("name"))
        if name and len(re.sub(r"\s+", "", name)) >= 2 and re.sub(r"\s+", "", name).lower() in flat:
            return p
    return None


def _pick_project(text):
    s = (text or "").strip()
    m = re.fullmatch(r"(\d{1,2})\s*(?:번|번째|요|이요)?[.!~]*", s)
    if m and 1 <= int(m.group(1)) <= len(PROJECTS):
        return PROJECTS[int(m.group(1)) - 1]
    return _project_of(s)


def _project_list():
    return "\n".join(f"  {i + 1}. {_short(p.get('name')) or '이름 없음'}"
                     + (f" — 지금 `{p['repo']}`" if p.get("repo") else "")
                     for i, p in enumerate(PROJECTS))


def _sh(*args):
    """`gh`·`git` 한 번. 실패하면 (False, 이유). **나가는 길은 여기 하나다** (gh_link 와 같은 규칙)."""
    import subprocess
    try:
        r = subprocess.run(args, capture_output=True, text=True, cwd=str(HERE), timeout=60)
    except Exception as ex:
        return False, f"{type(ex).__name__}"
    return (r.returncode == 0), (r.stdout or r.stderr).strip()[:300]


async def _run(*args):
    return await asyncio.get_running_loop().run_in_executor(None, lambda: _sh(*args))


# **목록을 뽑아 보여 주지 않는다** (2026-09-22 사장님: 「실측이 아니라 내부 서버가 있는지
# 물어보는 거로 하면 될듯」). 한때 `gh api user/orgs` 로 조직을 긁어 와 골라 드렸는데,
# 그건 **내가 그 팀의 사정을 짐작하는 것**이다 — 사내 서버는 애초에 `gh` 로 보이지도 않는다.
# 그냥 묻고, 주시는 주소를 쓴다.

async def ready():
    """무엇이 준비됐나 — **사람이 할 것과 봇이 할 수 있는 것을 가른다.**

    묻기 전에 본다. 「레포 이름을 알려 주세요」 라고 물어 놓고 마지막에 「gh 가 없어요」 라고
    하면 헛일을 시킨 것이다.
    """
    import config
    got = {"gh": bool(shutil.which("gh")), "git": (HERE / ".git").exists(),
           "repo": config.GITHUB.get("repo"), "who": ""}
    if got["gh"]:
        ok, out = await _run("gh", "auth", "status")
        got["auth"] = ok
        m = re.search(r"account (\S+)", out or "")
        got["who"] = m.group(1) if m else ""
    else:
        got["auth"] = False
    return got


async def _say(s, ch, text, thread=None):
    body = {"channel": ch, "text": text, "unfurl_links": False}
    if thread:
        body["thread_ts"] = thread
    await api(s, "chat.postMessage", body=body)


async def _again(s, ch, th, st, text):
    n, total, what = _where(st)
    st["miss"] = st.get("miss", 0) + 1
    await _say(s, ch, text + "\n\n" + say("ask_where", kind="GitHub 등록", n=n, total=total, what=what), th)
    save()
    return True


async def _ask(s, ch, th, st, step, head=""):
    st["step"], st["miss"] = step, 0
    save()
    no = f"{_where(st)[0]}️⃣"
    if step == "project":
        await _say(s, ch, head + say("repo_ask_project", step=no, list=_project_list()), th)
        return True
    return await _show(s, ch, th, st)


async def _show(s, ch, th, st):
    """되읽어 준다 — **팀 데이터가 그 레포에 올라간다**는 것을 여기서 밝힌다."""
    st["step"] = "confirm"
    save()
    name = _short(next((p.get("name") for p in PROJECTS if p.get("key") == st.get("pkey")), "")) or "프로젝트"
    await _say(s, ch, say("repo_confirm", repo=st["repo"], name=name,
                          make=say("repo_will_make") if st.get("make") else say("repo_will_use")), th)
    return True


async def maybe(s, e, q, force=False):
    """DM 의 이 말이 GitHub 붙이기와 관련 있으면 처리하고 True. 아니면 False."""
    user, ch = e.get("user"), e.get("channel")
    st, opened = _asking(user), False
    if st is None:
        if not (force or START.search(q)):
            return False
        opened = True
        st = {"by": user, "step": "repo", "th": e.get("thread_ts") or e.get("ts")}
        hit = _project_of(q) if len(PROJECTS) > 1 else (PROJECTS[0] if PROJECTS else None)
        if hit is not None:
            st["pkey"], st["pkey_known"] = hit.get("key") or "", True
        STATE.setdefault("new_repo", {})[user] = st
        save()
    th = e.get("thread_ts") or st.get("th")
    if opened:
        got = await ready()
        # **못 하는 것은 먼저 말한다** — 물어 놓고 마지막에 막히면 헛일을 시킨 것이다
        if not got["gh"]:
            STATE["new_repo"].pop(user, None); save()
            await _say(s, ch, say("repo_need_gh"), th)
            return True
        if not got["auth"]:
            STATE["new_repo"].pop(user, None); save()
            await _say(s, ch, say("repo_need_auth"), th)
            return True
        st["who"] = got["who"]
        save()
        await _say(s, ch, say("repo_ask", who=got["who"] or "(이름 모름)",
                              git=say("repo_git_yes") if got["git"] else say("repo_git_no"),
                              now=say("repo_now", repo=got["repo"]) if got["repo"] else ""), th)
        return True
    if q.strip() in CANCEL:
        STATE["new_repo"].pop(user, None); save()
        await _say(s, ch, say("repo_cancel"), th)
        return True
    if q.strip() in COMMANDS:
        return await _again(s, ch, th, st, say("ask_busy", word=q.strip(), kind="GitHub 를 붙이는"))

    step = st.get("step")

    # ① 어느 레포 — `owner/name` 또는 「만들어줘」
    if step == "repo":
        if any(x in q for x in MAKE_IT) and not _repo_of(q):
            # 이름을 안 주고 「만들어줘」 만 하셨다 — 그래도 이름은 있어야 한다
            return await _again(s, ch, th, st, say("repo_need_name", who=st.get("who") or "owner"))
        repo = _repo_of(q)
        if not repo:
            return await _again(s, ch, th, st, say("repo_bad", word=q.strip()[:30] or "빈 글자"))
        st["repo"], st["make"] = repo, any(x in q for x in MAKE_IT)
        return await _ask(s, ch, th, st, "project" if not st.get("pkey_known") else "confirm",
                          head=say("repo_ok", repo=repo) + "\n\n")

    # ② 어느 프로젝트
    if step == "project":
        hit = _pick_project(q)
        if hit is None:
            return await _again(s, ch, th, st, say("repo_project_bad", list=_project_list()))
        st["pkey"] = hit.get("key") or ""
        return await _show(s, ch, th, st)

    # ③ 확인 — **고치자는 말을 먼저 본다**
    again = _repo_of(_HEAD.sub("", q.strip()))
    if again and again != st.get("repo"):
        st["repo"] = again
        return await _show(s, ch, th, st)
    if _pick_project(q) is not None and len(PROJECTS) > 1 and not _yes(q):
        st["pkey"] = _pick_project(q).get("key") or ""
        return await _show(s, ch, th, st)
    if _yes(q):
        return await _build(s, ch, th, st, user)
    return await _again(s, ch, th, st, say("repo_fix_how"))


async def attach(repo, pkey, make=False, kind="github"):
    """레포를 데이터 폴더와 설정에 붙인다 — **(한 일 줄들, 막힌 이유)** 를 돌려준다.

    **입구가 둘, 몸은 하나다** (2026-09-22 사장님: 「프로젝트 등록 할때 붙이는건 어떨까?」)
      · 프로젝트를 만들 때 4번째 칸으로 (flows/project.py)
      · 나중에 따로 — 「깃허브 등록」 (여기)
    두 벌로 두면 한쪽만 고치는 날이 오고, 그날 어느 쪽이 맞는지 아무도 모른다.

    **한 것만 적는다** — 중간에 막히면 거기까지만 했다고 말한다.
    """
    import config
    import gh_link
    from common import PROJECTS, reload_projects
    done = []
    if kind == "github":
        # **`gh` 가 없어도 붙일 수는 있다** — push 는 git 이 한다. 다만 GitHub 이슈·번호는
        # `gh` 가 하는 일이라, 없으면 **remote 만** 붙이고 그렇다고 말한다.
        # 여기서 `github.repo` 를 켜 버리면 카드마다 번호 받기가 실패한다 (조용히 안 만들어진다)
        got = await ready()
        if not (got.get("gh") and got.get("auth")):
            kind = "git"
            repo = repo if "://" in repo or repo.startswith(("git@", "/")) else f"https://github.com/{repo}.git"
            done.append(say("repo_no_gh_yet"))
    if kind == "github" and make:
        ok, out = await _run("gh", "repo", "create", repo, "--private")
        if not ok and "already exists" not in out.lower() and "name already" not in out.lower():
            return done, out[:150]
        done.append(say("repo_made") if ok else say("repo_had"))
    elif kind == "github":
        ok, out = await _run("gh", "repo", "view", repo, "--json", "name")
        if not ok:
            return done, say("repo_no_such", repo=repo, err=out[:120])
    # kind == "git" 이면 **아무것도 확인하지 않는다** — 사내 서버는 `gh` 로 볼 수가 없다.
    # 첫 push 때 맞는지 드러나고, 실패하면 로그에 남는다 (gh_link.commit 의 ⚠️)

    if not (HERE / ".git").exists():
        ok, out = await _run("git", "init", "-q", "-b", "main")
        if not ok:
            return done, out[:150]
        done.append(say("repo_git_made"))
    gi = HERE / ".gitignore"
    if not gi.exists():
        gi.write_text(IGNORE, encoding="utf-8")
        done.append(say("repo_ignore"))
    url = f"https://github.com/{repo}.git" if kind == "github" else repo
    have, _ = await _run("git", "remote", "get-url", "origin")
    await _run(*(("git", "remote", "set-url", "origin") if have else ("git", "remote", "add", "origin")), url)
    done.append(say("repo_remote"))

    cfg = dict(config.CFG)
    if kind == "github":
        cfg.setdefault("github", {})["repo"] = repo
    cfg["git_remote"] = url                   # 어디로 올라가는지 적어 둔다 — 안 적으면 아무도 모른다
    cfg["projects"] = [dict(p) for p in PROJECTS]
    for p in cfg["projects"]:
        if (p.get("key") or "") == (pkey or ""):
            p["repo"] = repo if kind == "github" else None
    config.PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if kind == "github":
        config.GITHUB = cfg["github"]
        gh_link.REPO = repo                   # 다시 띄우지 않아도 이 자리에서 쓰기 시작한다
    reload_projects()
    done.append(say("repo_wrote"))
    log(f"{'GitHub' if kind == 'github' else 'git'} 붙임 {repo} ({pkey or '첫 프로젝트'})")
    return done, None


async def _build(s, ch, th, st, user):
    """정말로 붙인다 — 몸은 `attach` 하나다."""
    await _say(s, ch, say("repo_making"), th)
    done, err = await attach(st["repo"], st.get("pkey"), st.get("make"))
    STATE["new_repo"].pop(user, None); save()
    if err:
        await _say(s, ch, err if err.startswith("`") else say("repo_fail", err=err), th)
        return True
    await _say(s, ch, say("repo_done", repo=st["repo"], list="".join(done)), th)
    return True
