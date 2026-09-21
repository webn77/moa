#!/usr/bin/env python3
"""가상 팀원이 이슈를 올린다. 봇이 이름과 아이콘을 바꿔 게시한다 (chat:write.customize)."""
from sync import api, CHANNEL

POSTS = [
    ("CS 지은", ":headphones:", "🎫 로그인 버튼이 안 눌려요 (iOS 사파리)"),
    ("디자이너 민지", ":art:", "🎫 결제 완료 화면 문구가 두 줄로 깨져요"),
    ("개발 민수", ":computer:", "🎫 정산 배치가 새벽 3시에 두 번 돌아요"),
]

for name, icon, text in POSTS:
    api("chat.postMessage", body={"channel": CHANNEL, "text": text, "username": name, "icon_emoji": icon})
    print("올림:", name, "—", text)
