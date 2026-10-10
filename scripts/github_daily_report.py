#!/usr/bin/env python3
"""Fetch DiscoverHub's GitHub daily ranking and build a shareable report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "https://discoverhub.cn/trend/github/rank/2"
DEFAULT_STAR_LOWER = 1
DEFAULT_STAR_UPPER = 500

DESCRIPTION_TRANSLATIONS = {
    "morluto/rea": "利用智能代理进行逆向工程分析，覆盖应用行为和原生二进制程序。",
    "boykopovar/AnyPS5": "将 PS5 可执行程序自动移植到 Linux 和 Windows。",
    "storytold/artcraft": "面向艺术家、设计师和电影制作人的创意制作引擎。",
    "cathrynlavery/diagram-design": "为多种 AI 编程助手提供 42 种图表类型的编辑式图表设计工具。",
    "mattpocock/skills": "面向真实工程师的 AI 编程技能集合，来自作者的 agents 配置目录。",
    "tt-a1i/archify": "将想法、计划或代码库转换为精美的交互式架构图。",
    "Albert-Weasker/niubigeo": "开源的 AI 品牌曝光度与竞品分析报告工具。",
    "DietrichGebert/ponytail": "让 AI 编程代理以资深开发者的方式思考，帮助减少不必要的代码编写。",
    "openai/math": "OpenAI 的数学相关开源项目。",
    "liquidslr/system-design-notes": "系统设计面试学习笔记，整理自相关技术面试书籍。",
}


def fetch_page(endpoint: str, page: int, star_lower: int, star_upper: int) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"page": page, "starLessLimt": star_lower, "starUpperLimt": star_upper}
    )
    url = f"{endpoint}?{query}"
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--url",
            url,
            "-H",
            "Accept: */*",
            "-H",
            "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
            "-H",
            "Referer: https://discoverhub.cn/trend/github/rank",
            "-H",
            "User-Agent: github-daily-rank-local/1.0",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def fetch_all_repositories(endpoint: str, star_lower: int, star_upper: int) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1

    while True:
        payload = fetch_page(endpoint, page, star_lower, star_upper)
        if payload.get("status") == 201 and "最多返回前5页数据" in payload.get("message", ""):
            break
        if payload.get("status") != 200 or not payload.get("ok", False):
            raise RuntimeError(f"DiscoverHub request failed on page {page}: {payload}")

        page_items = payload.get("result") or []
        if not page_items:
            break

        for repository in page_items:
            key = str(repository.get("id") or repository.get("url"))
            if key not in seen:
                seen.add(key)
                repositories.append(repository)

        page += 1

    return repositories


def format_stars(value: Any) -> str:
    number = int(value or 0)
    if number >= 1000:
        formatted = f"{number / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{formatted}k"
    return str(number)


def repo_name(repository: dict[str, Any]) -> str:
    return f"{repository.get('owner', '')}/{repository.get('name', '')}".strip("/")


def one_line_description(repository: dict[str, Any]) -> str:
    name = repo_name(repository)
    if name in DESCRIPTION_TRANSLATIONS:
        return DESCRIPTION_TRANSLATIONS[name]
    description = str(repository.get("repoDetail") or "").strip()
    return description or "暂无简介"


def build_report(repositories: list[dict[str, Any]], report_date: str, top_n: int = 10) -> str:
    ranked = sorted(
        repositories,
        key=lambda repository: int(repository.get("daily_stars_incr") or 0),
        reverse=True,
    )[:top_n]
    if not ranked:
        raise ValueError("No repositories were returned by DiscoverHub")

    best = ranked[0]
    lines = [
        f"# GitHub 开源日报｜{report_date}",
        "",
        f"🏆 今日榜首：[{repo_name(best)}]({best.get('url', '')})",
        f"一句话：{one_line_description(best)}",
        f"⭐ 总 Star：{format_stars(best.get('currentStarNum'))}｜今日新增：{best.get('daily_stars_incr', 0)}",
        "",
        "## 今日 Star 增长榜 TOP 10",
        "",
    ]

    for rank, repository in enumerate(ranked, 1):
        lines.extend(
            [
                f"### {rank}. [{repo_name(repository)}]({repository.get('url', '')})",
                f"一句话：{one_line_description(repository)}",
                f"⭐ 总 Star：{format_stars(repository.get('currentStarNum'))}｜今日新增：{repository.get('daily_stars_incr', 0)}",
                "",
            ]
        )

    return "\n".join(lines)


def send_to_feishu(webhook: str, report: str) -> None:
    payload = json.dumps({"msg_type": "text", "content": {"text": report}}, ensure_ascii=False).encode()
    request = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("code", 0) != 0:
        raise RuntimeError(f"Feishu webhook failed: {result}")


def save_report(report: str, report_date: str, output_root: Path) -> Path:
    date = datetime.strptime(report_date, "%Y.%m.%d")
    output_path = output_root / str(date.year) / f"{date.month:02d}" / f"{date:%Y%m%d}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y.%m.%d"))
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--star-lower", type=int, default=DEFAULT_STAR_LOWER)
    parser.add_argument("--star-upper", type=int, default=DEFAULT_STAR_UPPER)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--feishu-webhook", default=os.environ.get("FEISHU_WEBHOOK_URL"))
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    repositories = fetch_all_repositories(args.endpoint, args.star_lower, args.star_upper)
    report = build_report(repositories, args.date, args.top)

    if not args.no_save:
        print(f"日报已保存：{save_report(report, args.date, args.output_root)}")
    if args.feishu_webhook:
        send_to_feishu(args.feishu_webhook, report)
        print("日报已发送到飞书群")
    print(report)


if __name__ == "__main__":
    main()
