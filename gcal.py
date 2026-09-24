"""회의를 구글 캘린더에도 올린다 (PA #45 v1, 2026-09-24 사장님 결정).

  · 계정은 **사장님 기존 구글 계정** — 봇은 그 안에 「모아」 보조 캘린더를 스스로 만들고 그것만 다룬다
  · 권한은 `calendar.app.created` 하나 (「제한」 등급) — 막히면 사람이 `config.json` 의 `gcal.scope` 를 바꾼다
  · 초대는 회의 ⑤ 에서 고른 사람(@사람 · 모두 · 나만). 안 고른 옛 흐름은 `gcal.invite`
    (`"owner"` 초대 안 함 | `"room"` 방 사람 전원)를 따른다 — 두 팀 모두 `room` (2026-09-24)
  · 회의 60분(`gcal.minutes`) · 시간대 `Asia/Seoul` · **시간이 없는 회의는 올리지 않는다**

구글 라이브러리를 쓰지 않는다 — 의존성은 `aiohttp`·`pyyaml` 뿐이라(README), OAuth·Calendar REST 를
직접 부른다. HTTP 는 `_http` 한 곳으로 모아서 시험에서 가짜로 바꿀 수 있게 한다.

두 파일은 **데이터 폴더**(`config.DATA`)에 둔다 — 코드 저장소에 안 들어간다(#59, `.gitignore`):
  gcal_client.json   구글 클라우드 콘솔에서 받은 OAuth 클라이언트
  gcal_token.json    refresh token · 만든 보조 캘린더 id
"""
import datetime
import http.server
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import config
from common import log  # noqa: E402

DATA = config.DATA
CLIENT_FILE = DATA / "gcal_client.json"
TOKEN_FILE = DATA / "gcal_token.json"
GCFG = config.CFG.get("gcal") or {}
SCOPE = GCFG.get("scope") or "https://www.googleapis.com/auth/calendar.app.created"
MINUTES = GCFG.get("minutes") or 60
INVITE = GCFG.get("invite") or "owner"           # "owner"(초대 안 함) | "room"(방 사람 전원)
TZ = "Asia/Seoul"
CAL_SUMMARY = "모아"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
WEEKDAY_CODE = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

_cache = {"token": None, "exp": 0.0}


async def _http(method, url, **kw):
    """HTTP 를 부르는 **한 곳** — 시험은 이 함수만 가짜로 바꾼다. (상태 코드, 응답 dict) 를 돌려준다."""
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.request(method, url, **kw) as r:
            try:
                body = await r.json()
            except Exception:
                body = {}
            return r.status, body


def ready():
    """토큰 파일 둘과 캘린더 id 가 다 있으면 True. 없으면 **조용히 건너뛴다**(로그 한 줄)."""
    if not (CLIENT_FILE.exists() and TOKEN_FILE.exists()):
        log("구글 캘린더 연결 안 됨 — `python3 gcal.py connect` 로 연결하면 회의가 캘린더에도 올라가요")
        return False
    try:
        tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log("구글 캘린더 토큰 파일을 못 읽었어요 — `python3 gcal.py connect` 를 다시 돌려 주세요")
        return False
    if not tok.get("calendar_id"):
        log("구글 캘린더 「모아」 캘린더 id 가 없어요 — `python3 gcal.py connect` 를 다시 돌려 주세요")
        return False
    return True


def _token():
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))


async def _access_token():
    """refresh token 으로 액세스 토큰을 받는다 — 만료 1분 전이면 다시 받고, 아니면 캐시를 쓴다."""
    now = time.time()
    if _cache["token"] and now < _cache["exp"] - 60:
        return _cache["token"]
    client = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    web = client.get("installed") or client.get("web") or client
    tok = _token()
    status, d = await _http("POST", TOKEN_URL, data={
        "client_id": web["client_id"], "client_secret": web["client_secret"],
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if status != 200 or "access_token" not in d:
        raise RuntimeError(f"구글 토큰 갱신 실패({status}): {d}")
    _cache["token"], _cache["exp"] = d["access_token"], now + float(d.get("expires_in") or 3600)
    return _cache["token"]


def _rrule(m):
    """회의 dict 의 `every`·`weekday`·`date` 로 RRULE 을 짓는다. 한 번짜리면 None.

    매주 `FREQ=WEEKLY;BYDAY=..` · 격주 `FREQ=WEEKLY;INTERVAL=2;BYDAY=..` ·
    매달 `FREQ=MONTHLY;BYDAY=4TH` 식 — 주차는 만들 때 정해 둔 `m["nth"]`(없으면 `core.month_nth`)를
    쓴다. `core.next_meet` 도 같은 값을 쓰므로 Slack 카드와 캘린더가 같은 날을 가리킨다(1년치 시험).
    다섯째 주는 없는 달이 있어 「마지막 주」(`-1`) 로 둔다.
    """
    every = m.get("every") or "once"
    if every == "once" or m.get("weekday") is None:
        return None
    day = WEEKDAY_CODE[m["weekday"]]
    if every == "week":
        return f"FREQ=WEEKLY;BYDAY={day}"
    if every == "2week":
        return f"FREQ=WEEKLY;INTERVAL=2;BYDAY={day}"
    if every == "month":
        import core
        nth = m.get("nth") if m.get("nth") is not None else core.month_nth(m["weekday"], m["date"])
        return f"FREQ=MONTHLY;BYDAY={nth}{day}"
    return None


async def create(m, attendees=None):
    """캘린더에 일정을 만든다 (`events.insert`, `sendUpdates=all`). 성공하면 `m` 에
    `gcal_id`·`gcal_link`(·정기면 `gcal_rrule`) 를 채워 넣고 그 `m` 을 돌려준다.

    **시간이 없는 회의는 올리지 않는다** (사장님 결정). `ready()` 가 False 면 아무것도 안 부른다.
    실패해도 예외를 던지지 않는다 — 회의는 만들어져야 하니 부르는 쪽(`flows/meeting.py`)이
    로그·DM 을 책임진다.
    """
    if not m.get("time") or not ready():
        return None
    h, mi = m["time"]
    start = datetime.datetime.combine(datetime.date.fromisoformat(m["date"]), datetime.time(h, mi))
    end = start + datetime.timedelta(minutes=MINUTES)
    body = {"summary": m["title"],
            "start": {"dateTime": start.isoformat(), "timeZone": TZ},
            "end": {"dateTime": end.isoformat(), "timeZone": TZ}}
    rule = _rrule(m)
    if rule:
        body["recurrence"] = [f"RRULE:{rule}"]
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees]
    token = await _access_token()
    cal_id = _token()["calendar_id"]
    status, d = await _http("POST", f"{API}/calendars/{urllib.parse.quote(cal_id, safe='')}/events",
                            params={"sendUpdates": "all"},
                            headers={"Authorization": f"Bearer {token}"}, json=body)
    if status not in (200, 201):
        log(f"⚠️ 구글 캘린더 일정 만들기 실패({status}): {d}")
        return None
    m["gcal_id"], m["gcal_link"] = d.get("id"), d.get("htmlLink")
    if rule:
        m["gcal_rrule"] = rule
    return m


async def stop_series(m):
    """정기 일정을 **오늘까지만** 돌게 patch 한다 — 앞으로의 초대가 안 남는다. 지운 게 아니다.

    이 회의의 `gcal_id`·`gcal_rrule` 이 없으면(캘린더에 안 올렸던 회의) 조용히 넘어간다.
    """
    if not ready() or not m.get("gcal_id") or not m.get("gcal_rrule"):
        return
    # 시간이 있는 반복 일정은 UNTIL 도 UTC 시각이어야 한다(RFC 5545) — 「오늘 밤 23:59 서울」 을 UTC 로
    kst = datetime.timezone(datetime.timedelta(hours=9))
    end = datetime.datetime.combine(datetime.date.today(), datetime.time(23, 59, 59), kst)
    until = end.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rule = f"{m['gcal_rrule']};UNTIL={until}"
    token = await _access_token()
    cal_id = _token()["calendar_id"]
    status, d = await _http("PATCH",
        f"{API}/calendars/{urllib.parse.quote(cal_id, safe='')}/events/{m['gcal_id']}",
        headers={"Authorization": f"Bearer {token}"}, json={"recurrence": [f"RRULE:{rule}"]})
    if status not in (200, 201):
        log(f"⚠️ 구글 캘린더 정기 끄기 실패({status}): {d}")


# ── 여기부터는 `python3 gcal.py connect` — 사람이 한 번 손으로 돌리는 설치형 앱 OAuth ──
# 브라우저·루프백 서버를 쓰므로 시험 대상이 아니다(연결 자체는 사람이 눈으로 확인한다).
# `create`·`stop_series` 처럼 매번 도는 길이 아니라 여기서는 `_http` 를 거치지 않고 그냥 부른다.

def _get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _find_or_create_calendar(token):
    """「모아」 캘린더를 찾는다 — **있으면 다시 만들지 않는다.**

    전에 연결해 저장한 id 가 있으면 그것을 쓴다. 목록 조회(`calendarList`)는
    `calendar.app.created` 권한으로는 막힐 수 있어서, 막히면 찾지 않고 새로 만든다.
    """
    if TOKEN_FILE.exists():
        try:
            saved = json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("calendar_id")
        except (json.JSONDecodeError, OSError):
            saved = None
        if saved:
            return saved
    try:
        d = _get(f"{API}/users/me/calendarList", token)
        hit = next((c for c in d.get("items", []) if c.get("summary") == CAL_SUMMARY), None)
        if hit:
            return hit["id"]
    except urllib.error.HTTPError as e:
        print(f"캘린더 목록은 못 봤어요({e.code}) — 「{CAL_SUMMARY}」 캘린더를 새로 만들게요")
    req = urllib.request.Request(f"{API}/calendars",
        data=json.dumps({"summary": CAL_SUMMARY, "timeZone": TZ}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["id"]


def connect():
    """설치형 앱 OAuth(루프백) — 브라우저 주소를 찍고, 코드를 받아 refresh token 을 저장한 뒤
    「모아」 보조 캘린더를 만들거나(없으면) 찾아 그 id 를 같이 저장한다."""
    if not CLIENT_FILE.exists():
        raise SystemExit(f"{CLIENT_FILE} 이 없어요 — 클라우드 콘솔에서 받은 OAuth 클라이언트 JSON 을 "
                         "여기 두고 다시 돌려 주세요 (docs/install.md 「구글 캘린더 연결」)")
    client = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    web = client.get("installed") or client.get("web") or client
    client_id, client_secret = web["client_id"], web["client_secret"]

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    redirect = f"http://127.0.0.1:{port}"

    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            got["code"] = (q.get("code") or [None])[0]
            self.send_response(200)
            # 글자 인코딩을 안 알리면 브라우저가 다른 인코딩으로 읽어 한국어가 깨진다 (2026-09-24 사장님 화면)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("연결됐어요 — 이 창은 닫으셔도 돼요.".encode("utf-8"))

        def log_message(self, *a):
            pass                                  # 콘솔에 접속 로그를 안 찍는다

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent"})
    print(f"아래 주소를 브라우저에서 열어 로그인해 주세요 (자동으로도 열어 볼게요):\n{url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    http.server.HTTPServer(("127.0.0.1", port), Handler).handle_request()
    if not got.get("code"):
        raise SystemExit("코드를 못 받았어요 — 다시 시도해 주세요")

    data = urllib.parse.urlencode({"code": got["code"], "client_id": client_id,
        "client_secret": client_secret, "redirect_uri": redirect,
        "grant_type": "authorization_code"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=20) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"토큰 교환에 실패했어요: {e.read().decode()[:300]}")
    if "refresh_token" not in tok:
        raise SystemExit(f"refresh token 을 못 받았어요 — 이미 한 번 동의한 계정이면 구글 계정 설정에서 "
                         f"이 앱 접근을 지우고 다시 시도해 주세요: {tok}")

    cal_id = _find_or_create_calendar(tok["access_token"])
    TOKEN_FILE.write_text(json.dumps({"refresh_token": tok["refresh_token"], "calendar_id": cal_id},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    print(f"연결됐어요 — 「{CAL_SUMMARY}」 캘린더 id: {cal_id}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2 or sys.argv[1] != "connect":
        raise SystemExit("사용법: python3 gcal.py connect")
    connect()
