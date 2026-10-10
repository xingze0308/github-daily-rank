#!/usr/bin/env python3
"""List the Feishu group chats an app bot belongs to, to find FEISHU_CHAT_ID.

Usage:
    FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx \
        python3 scripts/list_feishu_chats.py

The bot must already be added to the target group, and the app needs the
``im:chat:readonly`` permission (published and approved) to list chats.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request

from github_daily_report import DEFAULT_API_BASE, get_tenant_access_token


def list_chats(api_base: str, token: str, page_size: int = 100) -> list[dict[str, object]]:
    chats: list[dict[str, object]] = []
    page_token = ""

    while True:
        query = {"page_size": page_size}
        if page_token:
            query["page_token"] = page_token
        url = f"{api_base}/open-apis/im/v1/chats?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)

        if payload.get("code") != 0:
            raise RuntimeError(f"Failed to list chats: {payload}")

        data = payload.get("data") or {}
        chats.extend(data.get("items") or [])
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more"):
            break

    return chats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=os.environ.get("FEISHU_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--app-id", default=os.environ.get("FEISHU_APP_ID"))
    parser.add_argument("--app-secret", default=os.environ.get("FEISHU_APP_SECRET"))
    args = parser.parse_args()

    if not args.app_id or not args.app_secret:
        raise SystemExit(
            "请先设置环境变量 FEISHU_APP_ID 与 FEISHU_APP_SECRET，再运行本脚本。"
        )

    token = get_tenant_access_token(args.api_base, args.app_id, args.app_secret)
    chats = list_chats(args.api_base, token)

    if not chats:
        print("机器人目前不在任何群里。")
        print("请先把机器人添加进目标群，再重新运行本脚本。")
        return

    print(f"机器人所在的群（共 {len(chats)} 个）：")
    print()
    for chat in chats:
        print(f"  chat_id : {chat.get('chat_id')}")
        print(f"  群名称  : {chat.get('name') or '(未命名)'}")
        print()


if __name__ == "__main__":
    main()
