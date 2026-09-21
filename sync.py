#!/usr/bin/env python3
"""Slack 채널을 읽어 이슈 목록·보드를 채널 캔버스에 다시 그린다.

규칙 (캔버스의 「여기서 일하는 법」과 같다)
  이슈     🎫 로 시작하는 메시지, 또는 🎫 반응이 달린 메시지
  담당     스레드에 「담당 @이름」. 없으면 👀 를 누른 첫 사람
  상태     ✅ 완료 > ⛔ 막힘 > 👀 진행 중 > 할 일
  번호     처음 발견한 순서대로 매기고 state.json 에 고정한다

새 이슈를 처음 발견하면 스레드에 「#N 으로 등록」 답글을 단다.
토큰은 ~/.config/moa.env 에서 읽고 출력하지 않는다.
"""
import json, os, re, sys, pathlib, datetime, urllib.request, urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / "state.json"
import config

CHANNEL = config.CFG.get("issue_channel") or ""   # 박아 두지 않는다 — 팀마다 다르다
CANVAS = config.CFG.get("canvas") or ""            # 박아 두지 않는다 — 팀마다 다르다
TICKET, EYES, DONE, BLOCK = "ticket", "eyes", "white_check_mark", "no_entry"


def token():
    for line in (pathlib.Path.home() / ".config/moa.env").read_text().splitlines():
        if line.startswith("SLACK_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("토큰 파일에 SLACK_BOT_TOKEN 이 없습니다")


def api(method, params=None, body=None):
    h = {"Authorization": "Bearer " + token()}
    url = "https://slack.com/api/" + method
    if body is not None:
        h["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h)
    else:
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params or {}), headers=h)
    d = json.load(urllib.request.urlopen(req))
    if not d.get("ok"):
        raise RuntimeError(f"{method}: {d.get('error')}")
    return d


def reacted(m, name):
    for r in m.get("reactions", []):
        if r["name"] == name:
            return r.get("users", [])
    return []


def is_issue(m):
    if m.get("subtype") not in (None, "bot_message"):
        return False
    return m.get("text", "").startswith(("🎫", ":ticket:")) or bool(reacted(m, TICKET))


def title_of(m):
    t = re.sub(r"^(🎫|:ticket:)\s*", "", m.get("text", "")).strip()
    return t.splitlines()[0][:60] if t else "(제목 없음)"


def author_of(m):
    return m.get("username") or (f"![](@{m['user']})" if m.get("user") else "누군가")


def assignee_of(m):
    if m.get("reply_count"):
        for r in api("conversations.replies", {"channel": CHANNEL, "ts": m["ts"]})["messages"][1:]:
            hit = re.search(r"담당\s*<@(U[A-Z0-9]+)>", r.get("text", ""))
            if hit:
                return hit.group(1)
    eyes = reacted(m, EYES)
    return eyes[0] if eyes else None


def status_of(m):
    if reacted(m, DONE):
        return "done"
    if reacted(m, BLOCK):
        return "blocked"
    if reacted(m, EYES):
        return "doing"
    return "todo"


LABEL = {"todo": "🎫 할 일", "doing": "👀 진행 중", "blocked": "⛔ 막힘", "done": "✅ 완료"}


def render(issues):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    done = sum(i["status"] == "done" for i in issues)
    total = len(issues)
    bar = "▓" * round(10 * done / total) + "░" * (10 - round(10 * done / total)) if total else "░" * 10
    who = lambda i: f"![](@{i['assignee']})" if i["assignee"] else "미정"
    rows = "\n".join(f"| #{i['no']} | {i['title']} | {who(i)} | {LABEL[i['status']]} | {i['by']} |"
                     for i in issues) or "| - | 아직 없어요 | - | - | - |"
    board = ""
    for key in ("todo", "doing", "blocked", "done"):
        items = [i for i in issues if i["status"] == key]
        lines = "\n".join(f"- #{i['no']} {i['title']} — {who(i)}" for i in items) or "- 없음"
        board += f"\n### {LABEL[key]} ({len(items)})\n{lines}\n"
    head = (HERE / "canvas_head.md").read_text(encoding="utf-8")
    return f"""{head}
---

## 🗺️ 로드맵

| 시기 | 목표 | 진척 |
| --- | --- | --- |
| **지금** | Slack에서 이슈를 만들고 할당하고 끝낼 수 있는지 확인 | {bar} {done}/{total} |
| **다음** | 처음 온 팀원이 안내 없이 이슈를 올리는지 확인 | 시작 전 |
| **나중** | GitHub에 기록이 남도록 연결 | 시작 전 |

---

## 📋 할 일 목록

| 번호 | 제목 | 담당 | 상태 | 올린 사람 |
| --- | --- | --- | --- | --- |
{rows}

---

## 📌 작업 보드

_마지막 갱신: {now} · 봇이 자동으로 채웁니다_
{board}"""


def main():
    state = json.loads(STATE.read_text()) if STATE.exists() else {"next": 1, "ts2no": {}}
    msgs = api("conversations.history", {"channel": CHANNEL, "limit": 200})["messages"]
    issues, new = [], []
    for m in sorted(msgs, key=lambda x: float(x["ts"])):
        if not is_issue(m):
            continue
        if m["ts"] not in state["ts2no"]:
            state["ts2no"][m["ts"]] = state["next"]
            state["next"] += 1
            new.append(m["ts"])
        issues.append({"no": state["ts2no"][m["ts"]], "title": title_of(m), "by": author_of(m),
                       "assignee": assignee_of(m), "status": status_of(m)})
    for ts in new:
        api("chat.postMessage", body={"channel": CHANNEL, "thread_ts": ts,
            "text": f"🎫 #{state['ts2no'][ts]} 으로 등록했어요. 작업판 캔버스에서 볼 수 있어요."})
    api("canvases.edit", body={"canvas_id": CANVAS, "changes": [
        {"operation": "replace", "document_content": {"type": "markdown", "markdown": render(issues)}}]})
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f"이슈 {len(issues)}건 · 새로 등록 {len(new)}건 · 캔버스 갱신")
    for i in issues:
        print(f"  #{i['no']} {LABEL[i['status']]:8} 담당={'있음' if i['assignee'] else '미정'}  {i['title']}")


if __name__ == "__main__":
    main()
