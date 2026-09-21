#!/usr/bin/env python3
"""다른 Slack 워크스페이스에 설치한다 (#50). 여러 번 돌려도 된다 — 있는 것은 찾아 쓰고 없는 것만 만든다.

  python3 setup.py --data ~/teams/우리팀 --project "우리 팀 프로젝트" --pm U0123ABC
      [--request proj-request] [--issues proj-issues] [--github owner/repo --project-number 1]
      [--env-file ~/.config/우리팀.env] [--launchagent]

하는 일
  1. 토큰 확인 (값은 출력하지 않는다) · 워크스페이스 · 봇 · 앱 ID
  2. 팀 대화방 · 프로젝트 방을 찾고 없으면 만든다 → 봇이 들어가고 PM 을 초대한다
  3. 프로젝트 방 캔버스를 찾고 없으면 만든다
  4. 데이터 폴더에 config.json 을 쓰고, 없는 문서만 templates/starter 에서 채운다
  5. (--launchagent) 맥 재부팅 뒤에도 봇이 돌게 하는 plist 를 데이터 폴더에 쓴다 — 설치(launchctl)는 사람이 한다

설치 전에 `--check` 를 먼저 돌리면 막힐 것을 미리 잡아 준다 (Slack 에는 아무것도 안 만든다).
"""
import argparse, datetime, getpass, json, pathlib, re, sys, urllib.parse, urllib.request

CODE = pathlib.Path(__file__).resolve().parent
STARTER = CODE / "templates" / "starter"


def manifest_problems(man):
    """Slack 이 **링크를 연 뒤에야** 알려 주는 것들을 미리 잡는다 (2026-09-22 실측).

    둘 다 만든 사람이 mju 워크스페이스에 처음부터 깔아 보다 막힌 것이다. 링크를 열고,
    워크스페이스를 고르고, Next 를 누른 뒤에야 빨간 줄이 떴다 — 그때마다 매니페스트를 고치고
    링크를 다시 만들어야 했다. **여기서 걸러 주면 그 왕복이 사라진다.**
    """
    out = []
    name = ((man.get("display_information") or {}).get("name") or "").strip()
    if len(name) < 3:
        out.append(f"앱 이름 「{name}」 이 너무 짧아요 (3글자 이상) — manifest.yaml 의 display_information.name")
    handle = (((man.get("features") or {}).get("bot_user") or {}).get("display_name") or "").strip()
    if handle and not re.fullmatch(r"[A-Za-z0-9._-]+", handle):
        out.append(f"부르는 이름 「{handle}」 은 영문이어야 해요 — Slack 이 여기서 @아이디를 만드는데 "
                   "한글은 못 바꿔요 (「display_name cannot be converted to a username」). "
                   "manifest.yaml 의 features.bot_user.display_name\n"
                   "     메시지에 보이는 이름은 한글로 둘 수 있어요 — `setup.py --bot-name 모아`")
    return out


def app_link():
    """앱 만들기 화면을 미리 채워 여는 링크 — 매니페스트를 복사·붙여 넣지 않아도 된다."""
    try:
        import yaml
        man = yaml.safe_load((CODE / "manifest.yaml").read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"manifest.yaml 을 읽지 못했어요({e}) — api.slack.com/apps 에서 직접 붙여 넣어 주세요"
    bad = manifest_problems(man)
    if bad:
        return None, "링크를 열어도 Slack 이 거절해요. 먼저 고쳐 주세요:\n  ⛔ " + "\n  ⛔ ".join(bad)
    return "https://api.slack.com/apps?new_app=1&manifest_json=" + urllib.parse.quote(json.dumps(man)), None


def check(env_file):
    """설치 전에 **막힐 것을 미리 잰다.** 막히는 곳마다 칠 명령을 그대로 찍어 준다.

    여기 있는 것은 전부 **조용히 죽던 것들**이다 (2026-09-21~22 실측):
      · `claude` 가 없거나 못 부르면 → Slack 에 붙고 카드도 생기는데 **생각만 안 하는** 봇이 된다
      · 앱 토큰 스코프가 빠지면 → 설치는 깨끗이 끝나고 **나중에 「봇이 반응이 없다」** 로 나타난다
    그래서 **`claude auth status` 가 아니라 `claude -p` 를 진짜 부른다** — 로그인은 됐는데
    모델 이름·옵션에서 터지는 경우를 상태 확인만으로는 못 잡는다.
    """
    import shutil, subprocess
    bad = []

    def line(ok, what, detail="", fix=""):
        print(f"   {'✅' if ok else '⛔'} {what}" + (f" — {detail}" if detail else ""))
        if not ok:
            bad.append(what)
            if fix:
                print(f"      → {fix}")

    line(sys.version_info >= (3, 11), "파이썬 3.11+", ".".join(map(str, sys.version_info[:3])),
         "brew install python@3.13")
    for mod, pkg in (("aiohttp", "aiohttp"), ("yaml", "pyyaml")):
        try:
            __import__(mod)
            line(True, f"{pkg}")
        except ImportError:
            line(False, f"{pkg}", "없음", f"pip3 install {pkg}")

    claude = shutil.which("claude")
    line(bool(claude), "claude 명령", claude or "없음",
         "claude 로그인이 안 돼 있으면 봇의 AI 가 통째로 멈춰요 — docs.claude.com 에서 설치")
    if claude:
        try:
            r = subprocess.run([claude, "-p", "--output-format", "json", "--tools", "",
                                "--no-session-persistence"], input="1+1은?", capture_output=True,
                               text=True, timeout=120)
            d = json.loads(r.stdout or "{}")
            line(not d.get("is_error") and bool(d.get("result")), "claude 실제 호출",
                 (d.get("result") or r.stderr or "")[:60].replace("\n", " "),
                 "claude 로 한 번 로그인해 주세요")
        except Exception as e:
            line(False, "claude 실제 호출", type(e).__name__, "claude 로 한 번 로그인해 주세요")

    if env_file.exists():
        env = read_env(env_file)
        sl = Slack(env.get("SLACK_BOT_TOKEN", ""))
        me = sl.call("auth.test") if env.get("SLACK_BOT_TOKEN") else {"error": "없음"}
        line(bool(me.get("ok")), "봇 토큰", me.get("team") or me.get("error"),
             "Install App → Bot User OAuth Token 을 다시 넣어 주세요")
        # **앱 토큰은 지금껏 `xapp-` 로 시작하는지만 봤다.** 스코프가 빠져도 통과했고,
        # 그러면 봇을 켤 때(bot.py 의 apps.connections.open)에야 터진다
        d = Slack(env.get("SLACK_APP_TOKEN", "")).call("apps.connections.open") \
            if env.get("SLACK_APP_TOKEN") else {"error": "없음"}
        line(bool(d.get("ok")), "앱 토큰 (connections:write)", d.get("error") or "됨",
             "Basic Information → App-Level Tokens 에서 connections:write 로 다시 만들어 주세요")
    else:
        print(f"   · 토큰 파일 없음 ({env_file}) — `--ask-tokens` 로 만들 수 있어요")

    try:
        import yaml
        for p in manifest_problems(yaml.safe_load((CODE / "manifest.yaml").read_text(encoding="utf-8"))):
            line(False, "매니페스트", p)
    except Exception as e:
        line(False, "manifest.yaml", str(e)[:60])

    import subprocess as sp
    gh = sp.run(["gh", "auth", "status"], capture_output=True, text=True) if shutil.which("gh") else None
    print(f"   {'✅' if gh and not gh.returncode else '·'} gh (GitHub 쓸 때만)"
          + ("" if gh and not gh.returncode else " — 없어도 돼요. 할 일 정본을 md 로만 써요"))
    return bad


def ask_tokens(path):
    """토큰 두 개를 받아 파일로 저장한다 — 화면에 보이지 않고, 나만 읽을 수 있게(600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    got = {}
    for key, what, head in (("SLACK_BOT_TOKEN", "봇 토큰 (OAuth & Permissions → Bot User OAuth Token)", "xoxb-"),
                            ("SLACK_APP_TOKEN", "앱 토큰 (Basic Information → App-Level Tokens, 스코프 connections:write)", "xapp-")):
        while True:
            v = getpass.getpass(f"   {what}\n   붙여 넣고 Enter (화면에 안 보여요): ").strip()
            if v.startswith(head):
                got[key] = v
                break
            print(f"   {head} 로 시작해야 해요. 다시 붙여 넣어 주세요.")
    path.write_text("".join(f"{k}={v}\n" for k, v in got.items()))
    path.chmod(0o600)
    print(f"   저장했어요: {path} (나만 읽기)")


def read_env(path):
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class Slack:
    def __init__(self, token):
        self.token = token

    def call(self, method, **params):
        data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(f"https://slack.com/api/{method}", data=data,
                                     headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())


def find_or_create_channel(sl, name, dry):
    cursor = None
    while True:
        d = sl.call("conversations.list", types="public_channel", exclude_archived="true", limit=1000, cursor=cursor)
        if not d.get("ok"):
            raise SystemExit(f"채널 목록을 못 읽었어요: {d.get('error')} (channels:read 권한)")
        hit = next((c for c in d["channels"] if c["name"] == name), None)
        if hit:
            return hit["id"], "있음" + (" · 봇 들어가 있음" if hit.get("is_member") else "")
        cursor = (d.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    if dry:
        return None, "없음 — 만들 예정 (--dry-run)"
    d = sl.call("conversations.create", name=name)
    if not d.get("ok"):
        raise SystemExit(f"#{name} 를 만들지 못했어요: {d.get('error')} — 직접 만들고 봇을 초대한 뒤 다시 돌려 주세요")
    return d["channel"]["id"], "만듦 · 봇 들어가 있음"


def join_and_invite(sl, cid, pm, dry, member):
    if dry:
        return
    if not member:
        j = sl.call("conversations.join", channel=cid)
        if not j.get("ok") and j.get("error") != "already_in_channel":
            print(f"   ! 봇이 채널에 못 들어갔어요 ({j.get('error')}) — 채널에서 `/invite @봇` 해 주세요")
    if pm and pm in (sl.call("conversations.members", channel=cid, limit=1000).get("members") or []):
        return
    if pm:
        i = sl.call("conversations.invite", channel=cid, users=pm)
        if not i.get("ok") and i.get("error") not in ("already_in_channel", "cant_invite_self"):
            print(f"   ! PM 초대 실패 ({i.get('error')}) — 직접 초대해 주세요")


def find_or_create_canvas(sl, cid, dry):
    info = sl.call("conversations.info", channel=cid)
    props = (info.get("channel") or {}).get("properties") or {}
    fid = ((props.get("canvas") or {}).get("file_id")                       # 채널 캔버스는 탭으로 붙어 있다
           or next((t["data"]["file_id"] for t in props.get("tabs") or [] if t.get("type") == "canvas"
                    and (t.get("data") or {}).get("file_id")), None))
    if fid:
        return fid, "있음"
    if dry:
        return None, "없음 — 만들 예정 (--dry-run)"
    d = sl.call("conversations.canvases.create", channel_id=cid,
                document_content=json.dumps({"type": "markdown", "markdown": "봇이 곧 채워요."}))
    if not d.get("ok"):
        raise SystemExit(f"채널 캔버스를 만들지 못했어요: {d.get('error')} (canvases:write 권한)")
    return d["canvas_id"], "만듦"


def write_starter(data, project, pm_name, pm):
    """없는 문서만 채운다 — 이미 있는 팀 문서는 절대 덮어쓰지 않는다."""
    today = datetime.date.today().isoformat()
    made = []
    for src in sorted(STARTER.glob("*.md")):
        dst = data / src.name
        if dst.exists():
            continue
        text = src.read_text(encoding="utf-8")
        text = text.replace("{today}", today).replace("{project}", project)
        text = text.replace("{members}", f"| {pm_name} | {pm} | PM | 목표·우선순위, 이슈 정의 승인, 회의 진행 | 3 |" if pm else "")
        text = text.replace("{hours}", f"| {pm_name} | 5 | 가정 — 본인 확인 필요 |" if pm else "")
        dst.write_text(text, encoding="utf-8")
        made.append(src.name)
    if not (data / "cards.json").exists():
        (data / "cards.json").write_text(json.dumps({"next": 1, "cards": {}}), encoding="utf-8")
        made.append("cards.json")
    for d in ("issues", "meetings", "notes"):
        (data / d).mkdir(exist_ok=True)
    return made


def _plist_path():
    """LaunchAgent 의 PATH — **로그인 셸이 아니라서 좁다.** `claude` 가 있는 곳을 찾아 앞에 둔다.

    claude 는 설치 방법에 따라 자리가 다르다 (`~/.local/bin` · `/opt/homebrew/bin` ·
    `/usr/local/bin`). 박아 둔 목록만 쓰면 **옛 버전을 조용히 쓰거나**, 아예 못 찾아
    봇이 Slack 에는 붙는데 생각만 안 하게 된다 (2026-09-22 실측: 두 곳에 다른 버전이 있었다).
    """
    import shutil
    dirs = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    got = shutil.which("claude")
    if got:
        d = str(pathlib.Path(got).parent)
        if d in dirs:
            dirs.remove(d)
        dirs.insert(0, d)
    return ":".join(dirs)


def write_launchagent(data, team):
    label = f"com.moa.{team.lower()}"
    path = data / f"{label}.plist"
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array><string>{sys.executable}</string><string>{CODE / 'bot.py'}</string></array>
  <key>WorkingDirectory</key><string>{CODE}</string>
  <key>EnvironmentVariables</key>
  <dict><key>MOA_DATA</key><string>{data}</string>
        <key>PATH</key><string>{_plist_path()}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{data / 'bot.out'}</string>
  <key>StandardErrorPath</key><string>{data / 'bot.out'}</string>
</dict>
</plist>
""", encoding="utf-8")
    return path, label


def main():
    ap = argparse.ArgumentParser(description="모아를 다른 Slack 워크스페이스에 설치")
    ap.add_argument("--data", help="팀 데이터 폴더 (없으면 만든다)")
    ap.add_argument("--project", default="우리 팀 프로젝트", help="프로젝트 이름 (project.md 제목 · 프로젝트 방 이름)")
    ap.add_argument("--pm", help="PM 의 Slack 멤버 ID (U로 시작) — 채널 초대 · team.md 첫 줄")
    ap.add_argument("--talk", "--request", dest="talk", default="팀-대화", help="팀 대화방 이름 (사람과 AI가 이야기하는 곳, 프로젝트가 여럿이어도 하나)")
    ap.add_argument("--room", "--issues", dest="room", help="프로젝트 방 이름 (기본: 프로젝트-<프로젝트 이름>)")
    ap.add_argument("--bot-name", help="메시지에 보이는 이름 (기본: Slack 앱의 봇 이름). "
                                      "Slack 은 봇 아이디를 영문만 받지만 메시지 이름은 한글로 둘 수 있다")
    ap.add_argument("--github", help="GitHub 레포 owner/name (없으면 md 정본만)")
    ap.add_argument("--project-number", help="GitHub Projects 보드 번호 (없으면 보드 동기화 안 함)")
    ap.add_argument("--env-file", default="~/.config/moa.env", help="SLACK_BOT_TOKEN · SLACK_APP_TOKEN 이 든 파일")
    ap.add_argument("--launchagent", action="store_true", help="재부팅 뒤에도 돌게 하는 plist 를 데이터 폴더에 쓴다 (설치는 안 함)")
    ap.add_argument("--dry-run", action="store_true", help="Slack 에 아무것도 만들지 않고 찾기만 한다")
    ap.add_argument("--ask-tokens", action="store_true", help="토큰 두 개를 물어봐서 파일로 저장한다 (화면에 안 보임)")
    ap.add_argument("--app-link", action="store_true", help="앱 만들기 화면을 미리 채워 여는 링크만 출력하고 끝낸다")
    ap.add_argument("--check", action="store_true", help="설치 전에 막힐 것을 미리 잰다 (파이썬·claude·토큰·매니페스트). Slack 에 아무것도 안 만든다)")
    a = ap.parse_args()

    if not a.app_link and not a.check and not a.data:
        ap.error("--data 가 필요해요 (예: --data ~/teams/우리팀)")
    if a.check:
        print("설치 전 점검 — Slack 에는 아무것도 만들지 않아요")
        bad = check(pathlib.Path(a.env_file).expanduser())
        print("\n" + ("다 됐어요. 이제 설치해도 돼요." if not bad
                       else f"{len(bad)}가지를 먼저 고쳐 주세요 — 위의 → 를 따라 하시면 돼요."))
        return 0 if not bad else 1
    if a.app_link:
        url, err = app_link()
        print(err or f"이 링크를 열고 Create → Install 두 번만 누르면 돼요:\n{url}")
        return
    data = pathlib.Path(a.data).expanduser().resolve()
    env_file = pathlib.Path(a.env_file).expanduser()
    a.room = a.room or ("프로젝트-" + re.sub(r"\s+", "-", a.project.strip())[:40])
    print(f"① 토큰 — {env_file}")
    if a.ask_tokens and not env_file.exists():
        ask_tokens(env_file)
    if not env_file.exists():
        raise SystemExit("   토큰 파일이 없어요. docs/install.md 2단계대로 SLACK_BOT_TOKEN · SLACK_APP_TOKEN 을 적어 주세요")
    env = read_env(env_file)
    for k in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        print(f"   {k}: {'있음' if env.get(k) else '없음'}")
        if not env.get(k):
            raise SystemExit(f"   {k} 가 없어요")
    sl = Slack(env["SLACK_BOT_TOKEN"])
    me = sl.call("auth.test")
    if not me.get("ok"):
        raise SystemExit(f"   봇 토큰이 맞지 않아요: {me.get('error')}")
    bot = sl.call("bots.info", bot=me["bot_id"]).get("bot") or {}
    prof = (sl.call("users.info", user=me["user_id"]).get("user") or {}).get("profile") or {}
    handle = prof.get("display_name") or prof.get("real_name") or me["user"]      # @ 로 부르는 이름 — Slack 이 정한다(영문)
    bot_name = a.bot_name or handle                                               # 메시지에 보이는 이름 — 팀이 정한다(한글 가능)
    print(f"   워크스페이스 {me['team']} ({me['team_id']}) · 부를 때 @{handle}"
          + (f" · 보이는 이름 {bot_name}" if bot_name != handle else "") + f" · 앱 {bot.get('app_id', '?')}")

    print("② 채널")
    ids = {}
    for key, name in (("request", a.talk), ("issues", a.room)):
        cid, how = find_or_create_channel(sl, name, a.dry_run)
        ids[key] = cid
        print(f"   #{name}: {how}" + (f" ({cid})" if cid else ""))
        if cid:
            join_and_invite(sl, cid, a.pm, a.dry_run, "봇 들어가 있음" in how)

    print("③ 캔버스")
    canvas, how = find_or_create_canvas(sl, ids["issues"], a.dry_run) if ids["issues"] else (None, "채널이 없어 건너뜀")
    print(f"   #{a.room} 캔버스: {how}" + (f" ({canvas})" if canvas else ""))

    cfg = {"workspace": me["team_id"], "app_id": bot.get("app_id", ""), "bot_name": bot_name, "bot_handle": handle,
           "request_channel": ids["request"], "request_name": a.talk,
           "issue_channel": ids["issues"], "issue_name": a.room, "canvas": canvas,
           "env_file": a.env_file}
    if a.github:
        cfg["github"] = {"repo": a.github, "project_owner": a.github.split("/")[0], "project_number": a.project_number}
    if a.dry_run:
        print("④ (--dry-run) config.json 은 쓰지 않아요:\n" + json.dumps(cfg, ensure_ascii=False, indent=2))
        return
    data.mkdir(parents=True, exist_ok=True)
    print(f"④ 데이터 폴더 {data}")
    (data / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("   config.json 씀")
    pm_name = ((sl.call("users.info", user=a.pm).get("user") or {}).get("real_name") or "PM") if a.pm else "PM"
    made = write_starter(data, a.project, pm_name, a.pm)
    print("   " + (f"새로 채운 문서: {', '.join(made)}" if made else "문서는 이미 있어 그대로 둠"))

    if a.launchagent:
        path, label = write_launchagent(data, me["team_id"])
        print(f"⑤ LaunchAgent 파일을 썼어요 (설치는 아직 안 함): {path}")
        print(f"   설치:  cp '{path}' ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist")
        print(f"   제거:  launchctl bootout gui/$(id -u)/{label}")

    print("\n끝. 다음:")
    # 앱 아이콘은 **매니페스트에 항목이 없어서** API 로 못 올린다 (2026-09-22 확인:
    # display_information 은 name·description·long_description·background_color 넷뿐).
    # 안 올려도 대화에는 지장이 없다 — 메시지 속 얼굴과 이름은 봇이 매번 실어 보낸다.
    print(f"  0. (선택) 앱 아이콘 올리기 — api.slack.com/apps/{bot.get('app_id','')}"
          " → Basic Information → Display Information → App icon")
    print(f"     파일: {CODE / 'assets/pa-icon-white-1024.png'}  (512 는 Slack 이 거절한다)")
    print(f"  1. {data}/project.md · team.md · roadmap.md 를 팀에 맞게 고친다")
    print(f"  2. 실행:  MOA_DATA='{data}' python3 {CODE / 'bot.py'}")
    print(f"  3. #{a.talk} 에 「🎫 첫 요청」 을 써 본다")


if __name__ == "__main__":
    main()
