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

DEFAULT_SOURCE_URL = "https://raw.githubusercontent.com/discoverhubgroup/product-daily-rank/main/README.md"
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


def parse_product_report(document: str) -> tuple[str, str, list[dict[str, Any]]]:
    """Join upstream ranking rows with product descriptions by GitHub URL."""
    publication = re.search(r"^##\s+(\d{4}\.\d{2}\.\d{2})\s+最佳", document, re.MULTILINE)
    statistics = re.search(r"(\d{4}\.\d{2}\.\d{2})\s+日榜排行", document)
    if not publication or not statistics:
        raise ValueError("上游日报缺少发布日期或榜单日期")
    for date in (publication[1], statistics[1]):
        datetime.strptime(date, "%Y.%m.%d")
    descriptions = {}
    details = {}
    for section in re.split(r"^##\s+\d+\.", document, flags=re.MULTILINE)[1:]:
        section = re.split(r"^\s*---+\s*$", section, maxsplit=1, flags=re.MULTILINE)[0].strip()
        title, _, body = section.partition("\n")
        address = re.search(r"\*\*Github地址\*\*：.*?\]\((https://github\.com/[^)]+)\)", section)
        description = re.search(r"\*\*产品介绍\*\*：([^\n]+)", section)
        if address and description:
            descriptions[address[1].rstrip("/")] = description[1].strip()
            details[address[1].rstrip("/")] = {"title": title.strip(), "body": body.strip()}
    rows = re.findall(
        r"^\|\s*(\d+)\s*\|\s*\[([^\]]+)\]\((https://github\.com/[^)]+)\)\s*\|\s*([\d.,]+[kKmM]?)\s*\|\s*🔺?([\d,]+)\s*\|",
        document, re.MULTILINE,
    )
    candidates = re.findall(r"^\|\s*\d+\s*\|.*$", document, re.MULTILINE)
    if len(candidates) != len(rows):
        raise ValueError("上游榜单存在无法解析的排名行")
    if [int(row[0]) for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("上游榜单排名必须从 1 开始连续递增")
    repositories = []
    seen = set()
    for _, name, url, stars, increase in rows:
        url = url.rstrip("/")
        if url in seen:
            raise ValueError(f"上游榜单包含重复项目：{name}")
        seen.add(url)
        if url not in descriptions:
            raise ValueError(f"上游榜单缺少项目介绍：{name}")
        owner, repo = url.removeprefix("https://github.com/").split("/")
        number = stars.replace(",", "").lower()
        multiplier = 1000 if number.endswith("k") else 1000000 if number.endswith("m") else 1
        repositories.append({
            "owner": owner, "name": repo, "url": url,
            "repoDetail": descriptions[url], "source_description": True,
            "product_details": details[url],
            "currentStarNum": round(float(number.rstrip("km")) * multiplier),
            "daily_stars_incr": int(increase.replace(",", "")),
        })
    if not repositories:
        raise ValueError("上游日报未解析到任何榜单项目")
    return publication[1], statistics[1], repositories


def fetch_product_report(source_url: str) -> tuple[str, str, list[dict[str, Any]]]:
    result = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--url", source_url],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return parse_product_report(result.stdout)


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

    Priority: upstream product description > hand-maintained mapping > cache > raw text.
    """
    name = repo_name(repository)
    if repository.get("source_description"):
        return str(repository.get("repoDetail") or "暂无简介").strip()
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
    return f"⭐ 总 Star：{stars}｜日增 Star：{increase}"


def repository_details(repository: dict[str, Any], translations: dict[str, str] | None = None) -> str:
    details = repository.get("product_details")
    if details:
        return f"{details['body']}\n{repository_line(repository)}"
    return f"一句话：{one_line_description(repository, translations)}\n{repository_line(repository)}"


def repository_title(repository: dict[str, Any]) -> str:
    details = repository.get("product_details")
    return details["title"] if details else repo_name(repository)


def build_report(
    repositories: list[dict[str, Any]],
    report_date: str,
    top_n: int = 10,
    translations: dict[str, str] | None = None,
    statistics_date: str | None = None,
) -> str:
    ranked = rank_repositories(repositories, top_n)
    best = ranked[0]
    heading = f"{statistics_date or '今日'} Star 增长榜 TOP {len(ranked)}"
    lines = [
        f"# GitHub 开源日报｜{report_date}",
        "",
        f"🏆 今日榜首：[{repo_name(best)}]({best.get('url', '')})",
        f"一句话：{one_line_description(best, translations)}",
        repository_line(best),
        "",
        f"## {heading}",
        "",
    ]

    for rank, repository in enumerate(ranked, 1):
        lines.extend(
            [
                f"### {rank}. [{repository_title(repository)}]({repository.get('url', '')})",
                repository_details(repository, translations),
                "",
            ]
        )

    return "\n".join(lines)


def build_card(
    repositories: list[dict[str, Any]],
    report_date: str,
    top_n: int = 10,
    translations: dict[str, str] | None = None,
    statistics_date: str | None = None,
) -> dict[str, Any]:
    """Build a Feishu interactive card for the daily ranking.

    Feishu card markdown does not render ``#`` headings, so the hierarchy is
    expressed with bold text and horizontal rules instead.
    """
    ranked = rank_repositories(repositories, top_n)
    best = ranked[0]
    heading = f"{statistics_date or '今日'} Star 增长榜 TOP {len(ranked)}"

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
                f"**{rank}. [{repository_title(repository)}]({repository.get('url', '')})**",
                re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"[图片：\1](\2)", repository_details(repository, translations)),
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
            {"tag": "markdown", "content": f"**{heading}**"},
            *({"tag": "markdown", "content": entry} for entry in entries),
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
    parser.add_argument("--date", help="覆盖发布标题日期，默认使用上游发布日期")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--endpoint", help="显式使用旧版 DiscoverHub 接口")
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
    parser.add_argument("--skip-sent", action="store_true", help="跳过已发送日报及尚未更新的上游日报")
    args = parser.parse_args()

    statistics_date = None
    if args.top < 1:
        parser.error("--top 必须大于 0")
    if args.endpoint:
        repositories = fetch_all_repositories(args.endpoint, args.star_lower, args.star_upper)
        report_date = args.date or today_in_beijing()
    else:
        publication_date, statistics_date, repositories = fetch_product_report(args.source_url)
        report_date = args.date or publication_date

    report_day = datetime.strptime(report_date, "%Y.%m.%d")
    sent_path = args.output_root / ".sent-reports" / f"{report_day:%Y%m%d}.json"
    if args.skip_sent and not args.no_send:
        if not args.endpoint and publication_date < today_in_beijing():
            print(f"上游尚未更新：{publication_date}，等待备用触发")
            return
        if sent_path.exists():
            print(f"{report_date} 日报已发送，跳过重复推送")
            return

    # Only the repositories that actually make it into the report need a
    # Chinese one-liner, so translation is limited to the displayed TOP N.
    ranked = rank_repositories(repositories, args.top)
    cache = load_description_cache(args.cache_path) if args.endpoint else {}
    pending = collect_pending_translations(ranked, cache) if args.endpoint else {}
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

    report = build_report(repositories, report_date, args.top, cache, statistics_date)

    if not args.no_save:
        print(f"日报已保存：{save_report(report, report_date, args.output_root)}")

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
        card = build_card(repositories, report_date, args.top, cache, statistics_date)
        result = send_card(args.api_base, args.app_id, args.app_secret, args.chat_id, card)
        message_id = (result.get("data") or {}).get("message_id", "unknown")
        if args.skip_sent:
            sent_path.parent.mkdir(parents=True, exist_ok=True)
            sent_path.write_text(
                json.dumps({"report_date": report_date, "message_id": message_id}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        print(f"日报已发送到飞书群，message_id={message_id}")

    print(report)


if __name__ == "__main__":
    main()
