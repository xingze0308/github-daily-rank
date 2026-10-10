#!/usr/bin/env python3
"""Fetch DiscoverHub's GitHub daily ranking and build a shareable report."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "https://discoverhub.cn/trend/github/rank/2"
DEFAULT_STAR_LOWER = 1
DEFAULT_STAR_UPPER = 500
DEFAULT_API_BASE = "https://open.feishu.cn"
DEFAULT_TRANSLATE_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_TRANSLATE_MODEL = "deepseek-chat"
CARD_HEADER_TEMPLATE = "blue"

# GitHub Actions runners run on UTC, so the report date is computed in Beijing
# time explicitly instead of relying on the host clock.
BEIJING_TZ = timezone(timedelta(hours=8))

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_PATH = SCRIPT_DIR / "description_cache.json"

CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

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

TRANSLATE_PROMPT = """你是开源项目简介翻译助手。请把下面 JSON 中每个 GitHub 项目的英文简介翻译成简洁中文。

要求：
1. 每个简介一行，控制在 40 个汉字以内。
2. 只做翻译与压缩，不要臆测或补充原文没有提到的功能。
3. 保留必要的技术名词（例如 Rust、CLI、MCP、SDK）不翻译。
4. 严格返回一个 JSON 对象，key 与输入完全一致，value 为中文简介，不要输出任何解释或额外内容。

输入：
{payload}
"""


def today_in_beijing() -> str:
    return datetime.now(BEIJING_TZ).strftime("%Y.%m.%d")


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


def contains_cjk(text: str) -> bool:
    return bool(CJK_PATTERN.search(text))


def one_line_description(
    repository: dict[str, Any], translations: dict[str, str] | None = None
) -> str:
    """Resolve the Chinese one-liner.

    Priority: hand-maintained mapping > translation cache > raw description > 暂无简介.
    """
    name = repo_name(repository)
    if name in DESCRIPTION_TRANSLATIONS:
        return DESCRIPTION_TRANSLATIONS[name]
    if translations and name in translations:
        return translations[name]
    description = str(repository.get("repoDetail") or "").strip()
    return description or "暂无简介"


def rank_repositories(repositories: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    ranked = sorted(
        repositories,
        key=lambda repository: int(repository.get("daily_stars_incr") or 0),
        reverse=True,
    )[:top_n]
    if not ranked:
        raise ValueError("No repositories were returned by DiscoverHub")
    return ranked


def repository_line(repository: dict[str, Any]) -> str:
    stars = format_stars(repository.get("currentStarNum"))
    increase = repository.get("daily_stars_incr", 0)
    return f"⭐ 总 Star：{stars}｜今日新增：{increase}"


def build_report(
    repositories: list[dict[str, Any]],
    report_date: str,
    top_n: int = 10,
    translations: dict[str, str] | None = None,
) -> str:
    ranked = rank_repositories(repositories, top_n)
    best = ranked[0]
    lines = [
        f"# GitHub 开源日报｜{report_date}",
        "",
        f"🏆 今日榜首：[{repo_name(best)}]({best.get('url', '')})",
        f"一句话：{one_line_description(best, translations)}",
        repository_line(best),
        "",
        f"## 今日 Star 增长榜 TOP {top_n}",
        "",
    ]

    for rank, repository in enumerate(ranked, 1):
        lines.extend(
            [
                f"### {rank}. [{repo_name(repository)}]({repository.get('url', '')})",
                f"一句话：{one_line_description(repository, translations)}",
                repository_line(repository),
                "",
            ]
        )

    return "\n".join(lines)


def build_card(
    repositories: list[dict[str, Any]],
    report_date: str,
    top_n: int = 10,
    translations: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a Feishu interactive card for the daily ranking.

    Feishu card markdown does not render ``#`` headings, so the hierarchy is
    expressed with bold text and horizontal rules instead.
    """
    ranked = rank_repositories(repositories, top_n)
    best = ranked[0]

    summary = "\n".join(
        [
            f"**🏆 今日榜首：[{repo_name(best)}]({best.get('url', '')})**",
            one_line_description(best, translations),
            repository_line(best),
        ]
    )

    entries = [
        "\n".join(
            [
                f"**{rank}. [{repo_name(repository)}]({repository.get('url', '')})**",
                one_line_description(repository, translations),
                repository_line(repository),
            ]
        )
        for rank, repository in enumerate(ranked, 1)
    ]

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": CARD_HEADER_TEMPLATE,
            "title": {"tag": "plain_text", "content": f"GitHub 开源日报｜{report_date}"},
        },
        "elements": [
            {"tag": "markdown", "content": summary},
            {"tag": "hr"},
            {"tag": "markdown", "content": f"**今日 Star 增长榜 TOP {top_n}**"},
            {"tag": "markdown", "content": "\n\n".join(entries)},
        ],
    }


def load_description_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"读取简介缓存失败，将重建：{error}", file=sys.stderr)
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(key): str(value) for key, value in loaded.items()}


def save_description_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def collect_pending_translations(
    repositories: list[dict[str, Any]], cache: dict[str, str]
) -> dict[str, str]:
    """Pick the repositories that still need a Chinese one-liner."""
    pending: dict[str, str] = {}
    for repository in repositories:
        name = repo_name(repository)
        if not name or name in DESCRIPTION_TRANSLATIONS or name in cache:
            continue
        description = str(repository.get("repoDetail") or "").strip()
        if not description or contains_cjk(description):
            continue
        pending[name] = description
    return pending


def parse_json_object(raw: str) -> dict[str, str]:
    cleaned = JSON_FENCE_PATTERN.sub("", raw.strip()).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"模型未返回 JSON 对象：{raw[:200]}")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"模型返回的不是 JSON 对象：{raw[:200]}")
    return {str(key): str(value).strip() for key, value in parsed.items() if str(value).strip()}


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def translate_descriptions(
    pending: dict[str, str], api_base: str, api_key: str, model: str
) -> dict[str, str]:
    """Translate English descriptions into one-line Chinese via an OpenAI-compatible API."""
    payload = json.dumps(pending, ensure_ascii=False, indent=2)
    response = _post_json(
        f"{api_base.rstrip('/')}/chat/completions",
        {
            "model": model,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": TRANSLATE_PROMPT.format(payload=payload)}],
        },
        {"Authorization": f"Bearer {api_key}"},
    )

    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError(f"翻译接口未返回结果：{response}")

    content = str((choices[0].get("message") or {}).get("content") or "")
    translated = parse_json_object(content)
    return {name: translated[name] for name in pending if name in translated}


def get_tenant_access_token(api_base: str, app_id: str, app_secret: str) -> str:
    payload = _post_json(
        f"{api_base}/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    if payload.get("code") != 0:
        raise RuntimeError(f"Failed to obtain tenant_access_token: {payload}")
    return str(payload["tenant_access_token"])


def send_card(
    api_base: str, app_id: str, app_secret: str, chat_id: str, card: dict[str, Any]
) -> dict[str, Any]:
    """Send an interactive card to a Feishu group through an app bot."""
    token = get_tenant_access_token(api_base, app_id, app_secret)
    payload = _post_json(
        f"{api_base}/open-apis/im/v1/messages?receive_id_type=chat_id",
        {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        {"Authorization": f"Bearer {token}"},
    )
    if payload.get("code") != 0:
        raise RuntimeError(f"Feishu send message failed: {payload}")
    return payload


def save_report(report: str, report_date: str, output_root: Path) -> Path:
    date = datetime.strptime(report_date, "%Y.%m.%d")
    output_path = output_root / str(date.year) / f"{date.month:02d}" / f"{date:%Y%m%d}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report + "\n", encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=today_in_beijing())
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--api-base", default=os.environ.get("FEISHU_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--star-lower", type=int, default=DEFAULT_STAR_LOWER)
    parser.add_argument("--star-upper", type=int, default=DEFAULT_STAR_UPPER)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--app-id", default=os.environ.get("FEISHU_APP_ID"))
    parser.add_argument("--app-secret", default=os.environ.get("FEISHU_APP_SECRET"))
    parser.add_argument("--chat-id", default=os.environ.get("FEISHU_CHAT_ID"))
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument(
        "--translate-api-base",
        default=os.environ.get("TRANSLATE_API_BASE", DEFAULT_TRANSLATE_API_BASE),
    )
    parser.add_argument(
        "--translate-model", default=os.environ.get("TRANSLATE_MODEL", DEFAULT_TRANSLATE_MODEL)
    )
    parser.add_argument("--translate-api-key", default=os.environ.get("TRANSLATE_API_KEY"))
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--no-send", action="store_true")
    args = parser.parse_args()

    repositories = fetch_all_repositories(args.endpoint, args.star_lower, args.star_upper)

    # Only the repositories that actually make it into the report need a
    # Chinese one-liner, so translation is limited to the displayed TOP N.
    ranked = rank_repositories(repositories, args.top)
    cache = load_description_cache(args.cache_path)
    pending = collect_pending_translations(ranked, cache)
    if pending and args.translate_api_key:
        try:
            cache.update(
                translate_descriptions(
                    pending, args.translate_api_base, args.translate_api_key, args.translate_model
                )
            )
            save_description_cache(args.cache_path, cache)
            print(f"已翻译并缓存 {len(pending)} 条简介：{args.cache_path}")
        except Exception as error:  # noqa: BLE001 - translation must never break the report
            print(f"简介翻译失败，本次回退原文：{error}", file=sys.stderr)
    elif pending:
        print(f"未配置 TRANSLATE_API_KEY，{len(pending)} 条简介将使用原文", file=sys.stderr)

    report = build_report(repositories, args.date, args.top, cache)

    if not args.no_save:
        print(f"日报已保存：{save_report(report, args.date, args.output_root)}")

    if args.no_send:
        print("已跳过飞书推送")
    else:
        missing = [
            name
            for name, value in (
                ("FEISHU_APP_ID", args.app_id),
                ("FEISHU_APP_SECRET", args.app_secret),
                ("FEISHU_CHAT_ID", args.chat_id),
            )
            if not value
        ]
        if missing:
            raise SystemExit(f"缺少飞书配置，无法推送：{', '.join(missing)}")
        card = build_card(repositories, args.date, args.top, cache)
        send_card(args.api_base, args.app_id, args.app_secret, args.chat_id, card)
        print("日报已发送到飞书群")

    print(report)


if __name__ == "__main__":
    main()
