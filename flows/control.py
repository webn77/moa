"""작업 보고 통로 (#56) — 사람이 아닌 도구(Claude · 스크립트)가 봇에게 일을 시키는 유일한 길.

데이터 폴더의 `ask.jsonl` 에 한 줄씩 쓰면 봇이 10초마다 읽어서 처리하고, 처리한 줄은 `ask.done.jsonl` 로 옮긴다.
봇이 상태를 메모리에 들고 있어서 밖에서 cards.json 을 직접 고치면 엇갈린다 — 그래서 통로가 하나 필요하다.

  {"do": "check", "no": 51, "item": 2, "why": "커밋 abc1234"}   체크리스트 2번을 체크 (why 는 근거)
  {"do": "note",  "no": 51, "text": "…"}                        카드 스레드에 한 줄 남기기
  {"do": "issue", "title": "…", "why": "…", "done": ["…"]}      새 이슈 (담당 없이 등록 · project 로 프로젝트 고름)
  {"do": "spec", "no": 59, "title": "…", "why": "…", "force": true}  정의·제목 고치기
  {"do": "review", "no": 58, "to": "U…", "text": "…"}          다 했으니 봐 달라고 (완료로 닫지는 않는다)
  {"do": "cancel", "no": 62, "why": "…"}                        취소 — **사람이 시켰을 때만** · 사유 필수
  {"do": "later",  "no": 25, "why": "…"}                        뒤로 보내기 (없애는 건 아니다)
  {"do": "checksync"}                                           카드와 GitHub 을 지금 맞대어 보기
  {"do": "after"}                                               선행이 빈 이슈에 「무엇이 먼저 끝나야 하나」 채우기
  {"do": "after", "no": 59, "set": [], "why": "…"}              그 이슈의 선행을 직접 정하기 (빈 목록 = 기다리는 것 없음)

규칙: 여기서 이슈를 완료로 닫지 않는다. 다 체크되면 사람이 확인해야 완료다 (flows/review.py).
"""
import asyncio
import json

import core
import gh_link

from common import log
from store import STATE, add_log, progress, save


ASK = None          # bot.py 가 켜질 때 정한다 (데이터 폴더의 ask.jsonl)


def _card(no):
    return next((c for c in STATE["cards"].values() if c["no"] == int(no)), None)


async def do_check(s, r):
    """체크리스트 하나를 체크한다. 근거(why)를 기록에 함께 남긴다. 다 체크되면 확인 대기로."""
    c = _card(r.get("no"))
    dc = ((c or {}).get("spec") or {}).get("done_criteria") or []
    i = int(r.get("item", 0)) - 1
    if not c or not (0 <= i < len(dc)):
        return f"이슈나 조건을 못 찾음 (#{r.get('no')} {r.get('item')}번)"
    if dc[i] in (c.get("checked") or []):
        return f"#{c['no']} {r['item']}번은 이미 체크돼 있음"
    picked = sorted(set((c.get("checked") or []) + [dc[i]]) & set(dc), key=dc.index)   # 정의 순서대로
    await check_criteria(s, c, [{"value": str(dc.index(x))} for x in picked], r.get("by") or "PA", why=r.get("why"))
    k, n = progress(c)
    return f"#{c['no']} {r['item']}번 체크 ({k}/{n})"


async def do_note(s, r):
    c = _card(r.get("no"))
    if not c:
        return f"이슈를 못 찾음 (#{r.get('no')})"
    await post_log(s, c, add_log(c, r.get("text", "")[:200], r.get("by") or "PA", icon="📌"))
    save()
    return f"#{c['no']} 에 메모"


async def do_issue(s, r):
    """새 이슈 — 사람이 정한 것만 여기로 온다 (분류함은 #51)."""
    c = await add_issue(s, r.get("title", "(제목 없음)"), r.get("pm") or "", r.get("project"))
    if not c:                                   # 번호를 못 받으면 카드가 없다 — 남의 카드를 집으면 안 된다 (#60)
        return "카드를 못 만들었어요 — GitHub 에서 번호를 못 받았습니다. 로그를 보세요"
    if r.get("why") or r.get("done"):
        c["spec"] = {"why": r.get("why", ""), "change": r.get("change", ""), "expect": r.get("expect", ""),
                     "not_doing": r.get("not_doing", ""), "done_criteria": r.get("done") or []}
        c["spec_src"], c["coach"] = "tool", "done"
    if r.get("plevel"):
        c["plevel"], c["plevel_src"] = int(r["plevel"]), "human"
    await redraw(s, c)
    save()
    return f"#{c['no']} 등록"


async def do_spec(s, r):
    """이미 있는 이슈의 정의를 채운다 — 사람이 쓴 정의는 덮어쓰지 않는다 (강제하려면 force)."""
    c = _card(r.get("no"))
    if not c:
        return f"이슈를 못 찾음 (#{r.get('no')})"
    if c.get("spec_src") == "human" and not r.get("force"):
        return f"#{c['no']} 은 사람이 쓴 정의가 있어 두었음"
    old_title, old_spec = c["title"], dict(c.get("spec") or {})
    if (r.get("title") or "").strip():          # 제목도 고칠 수 있어야 한다 — #59 는 제목이 본문과 반대였는데
        c["title"] = r["title"].strip()[:80]    # 통로에 길이 없어서 못 고쳤다 (2026-09-20)
    c["spec"] = {"why": r.get("why", ""), "change": r.get("change", ""), "expect": r.get("expect", ""),
                 "not_doing": r.get("not_doing", ""), "done_criteria": r.get("done") or []}
    c["spec_src"], c["coach"] = "tool", "done"
    if old_spec:
        await record_change(s, c, None, old_title, old_spec, r.get("why_change") or "도구가 정의를 채움", how="통로")
    await redraw(s, c)
    save()
    return f"#{c['no']} 정의 채움 (조건 {len(c['spec']['done_criteria'])}개)"


async def do_sync(s, r):
    """밀린 이슈를 한 번에 올린다 (#58). 검증에 걸리는 것은 두고 이유를 돌려준다.

    평소에는 쓸 일이 없다 — 내보내기는 카드가 바뀔 때마다 알아서 돈다.
    규칙을 바꾼 뒤 한 번, 또는 새 팀 레포를 붙였을 때 쓴다.
    """
    only = set(r.get("only") or [])
    todo = [c for c in sorted(STATE["cards"].values(), key=lambda x: x["no"])
            if c.get("spec") and (not only or c["no"] in only)]
    new = [c["no"] for c in todo if not c.get("tracker")]
    for c in todo:
        await export_github(s, c, c.get("card_ts") if r.get("say") else None)
    held = [c["no"] for c in todo if not c.get("tracker")]          # 검증에 걸려 안 올라간 것
    out = await asyncio.get_running_loop().run_in_executor(None, gh_link.flush)
    save()
    return (f"새로 올림 {len(new) - len(held)}건 · 이미 있던 것 {len(todo) - len(new)}건 갱신 · "
            f"두고 온 것 {len(held)}건 {held} · {out}")


async def do_review(s, r):
    """일이 끝났으니 확인을 부탁한다 — AI가 일하고 사람이 검증하는 흐름의 마지막 한 칸.

    여기서 완료로 닫지 않는다. 닫는 것은 언제나 사람이다 (flows/review.py).
      {"do": "review", "no": 58, "to": "U…", "text": "무엇을 고쳤는지 한 줄"}
    """
    c = _card(r.get("no"))
    if not c:
        return f"이슈를 못 찾음 (#{r.get('no')})"
    if r.get("text"):
        await post_log(s, c, add_log(c, r["text"][:200], r.get("by") or "PA", icon="🔁"))
    c["status"] = "review"
    await request_review(s, c, r.get("by") or "PA", who=r.get("to"))
    await redraw(s, c)
    save()
    return f"#{c['no']} 확인 대기 — {c.get('review_by')} 님께 부탁함"


async def do_cancel(s, r):
    """이슈를 취소한다 — **사람이 시켰을 때만.**

    「AI는 제안까지, 누르는 건 사람」 (#11) 에서 취소는 AI가 스스로 하지 않는 쪽이다.
    통로에 두는 이유는 사람이 대화로 시켰을 때 도구가 대신 눌러 주기 위해서다 — 사유가 없으면 안 한다.
    """
    c = _card(r.get("no"))
    if not c:
        return f"이슈를 못 찾음 (#{r.get('no')})"
    if not (r.get("why") or "").strip():
        return "취소 사유가 없어요 — 왜 취소하는지 적어 주세요"
    c["cancel_reason"] = r["why"][:200]
    await apply_change(s, c, "set_status", "cancelled", r.get("by") or "PA", why=r["why"][:200])
    save()
    return f"#{c['no']} 취소 — {r['why'][:60]}"


async def do_later(s, r):
    """이슈를 뒤로 보낸다 — 없애는 건 아니고 지금은 아니라는 뜻. 사람이 시켰을 때만."""
    c = _card(r.get("no"))
    if not c:
        return f"이슈를 못 찾음 (#{r.get('no')})"
    why = (r.get("why") or "").strip() or "지금은 아님"
    c["priority"], c["prio_src"], c["prio_reason"] = "later", "human", why
    c["no_auto"] = True                            # 사람이 직접 가져갈 때까지 자동 배정하지 않는다
    await post_log(s, c, add_log(c, "나중으로", r.get("by") or "PA", why, icon="💤"))
    await redraw(s, c)
    save()
    return f"#{c['no']} 나중으로 — {why[:50]}"


async def do_after(s, r):
    """선행이 빈 이슈에 「무엇이 먼저 끝나야 하나」 를 채운다 (#68). 점수는 건드리지 않는다.

    `no` 를 주면 그 이슈의 선행을 **직접 정한다** — `set` 이 비면 「기다리는 것 없음」 이다.
    AI 가 넣은 것이 틀렸을 때 고치는 길로, 사람이 정한 것으로 잠긴다(`after_src`).
    """
    if r.get("no") is not None:
        c = _card(r["no"])
        if not c:
            return f"이슈를 못 찾음 (#{r.get('no')})"
        c["after"] = core.clean_after(r.get("set") or [], c["no"], STATE["cards"])
        c["after_src"] = "human"
        await post_log(s, c, add_log(c, "선행 " + (", ".join(f"#{x}" for x in c["after"]) or "없음"),
                                     r.get("by") or "PA", r.get("why"), icon="🔒"))
        await redraw(s, c)
        save()
        return f"#{c['no']} 선행 = {c['after'] or '없음'}"
    filled = await fill_after()
    if not filled:
        return "채울 것 없음"
    for no, aft in filled:
        c = _card(no)
        if c:
            await redraw(s, c)
    return f"선행 {len(filled)}건 채움: " + " · ".join(f"#{n}←{','.join(str(a) for a in aft)}" for n, aft in filled[:12])


async def do_checksync(s, r):
    """카드와 GitHub 을 지금 맞대어 본다 — 평소에는 아침에 저절로 돈다 (#59)."""
    bad = await check_sync(s, tell=r.get("tell"))
    return "어긋난 것 없음" if not bad else f"어긋남 {len(bad)}건: " + " · ".join(f"#{n} {w}" for n, w in bad[:10])


TIMEOUT = {"sync": 900, "checksync": 300, "after": 300}      # 초. 나머지는 120초
DO = {"check": do_check, "note": do_note, "issue": do_issue, "spec": do_spec, "sync": do_sync, "review": do_review, "checksync": do_checksync, "cancel": do_cancel, "later": do_later, "after": do_after}


async def watch_asks(s, every=10):
    """ask.jsonl 을 지켜본다. 한 줄씩 처리하고 결과를 ask.done.jsonl 에 남긴다."""
    done = ASK.with_name("ask.done.jsonl")
    # **한 번 터져도 계속 본다** (2026-09-23 감사) — 파일 읽기·비우기가 try 밖이라
    # 통로가 한 번 꼬이면 도구가 맡기는 일이 재시작 전까지 전부 멈췄다
    while True:
        await asyncio.sleep(every)
        try:
            if not (ASK and ASK.exists()):
                continue
            # **비우지 않고 옮긴다.** 예전에는 읽자마자 빈 파일로 덮었는데, 그 사이에
            # 봇이 내려가면(배포·재시작) 그 줄들은 **아무 데도 안 남고 사라졌다.**
            # 옮겨 두면 남아 있으므로 무슨 일을 잃었는지 볼 수 있다
            hold = ASK.with_suffix(".jsonl.doing")
            ASK.replace(hold)
            lines = [x for x in hold.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception as e:
            log(f"통로 읽기 실패: {type(e).__name__}: {e}")
            continue
        for line in lines:
            try:
                r = json.loads(line)
                # 한 건이 오래 걸려도 나머지가 멈추지 않게 — 2026-09-20 에 캔버스 대기가 통로를 10분 막았다.
                # sync 는 이슈를 수십 건 올리므로 넉넉히 준다
                out = await asyncio.wait_for(DO[r["do"]](s, r), TIMEOUT.get(r.get("do"), 120))
            except asyncio.TimeoutError:
                out = "시간 초과 — 하던 일은 뒤에서 계속될 수 있어요. 로그를 보세요"
            except Exception as e:
                out = f"실패: {type(e).__name__}: {e}"
            log(f"통로: {line[:80]} → {out}")
            with done.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ask": line[:300], "result": out}, ensure_ascii=False) + "\n")
        hold.unlink(missing_ok=True)      # 다 적고 나서 치운다 — 중간에 죽으면 이 파일이 남는다


# 다른 모듈의 이름은 맨 아래에서 가져온다 — 함수는 부를 때 찾으므로 서로 불러도 순환 import 가 안 된다
from ai import fill_after  # noqa: E402,F401
from flows.github import check_sync, export_github  # noqa: E402,F401
from flows.intake import add_issue  # noqa: E402,F401
from flows.review import request_review  # noqa: E402,F401
from flows.status import apply_change, check_criteria, post_log, record_change, redraw  # noqa: E402,F401
