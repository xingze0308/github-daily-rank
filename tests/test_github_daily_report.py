import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.github_daily_report import (
    build_card,
    build_report,
    collect_pending_translations,
    fetch_all_repositories,
    load_description_cache,
    main,
    one_line_description,
    parse_json_object,
    save_description_cache,
    send_card,
    today_in_beijing,
    translate_descriptions,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "github_daily_report.py"

SAMPLE_REPOSITORIES = [
    {
        "owner": "acme", "name": "slow", "url": "https://github.com/acme/slow",
        "repoDetail": "Slow project", "createDate": "2024-01-01", "currentStarNum": 100,
        "daily_stars_incr": 5,
    },
    {
        "owner": "acme", "name": "fast", "url": "https://github.com/acme/fast",
        "repoDetail": "Fast project", "createDate": "2025-01-01", "currentStarNum": 200,
        "daily_stars_incr": 50,
    },
]


class GithubDailyReportTests(unittest.TestCase):
    def test_one_line_description_falls_back_when_missing(self):
        self.assertEqual(one_line_description({"repoDetail": ""}), "暂无简介")
        self.assertEqual(one_line_description({"repoDetail": "项目简介"}), "项目简介")
        self.assertEqual(
            one_line_description({"owner": "morluto", "name": "rea", "repoDetail": "English description"}),
            "利用智能代理进行逆向工程分析，覆盖应用行为和原生二进制程序。",
        )

    def test_one_line_description_priority_manual_then_cache_then_raw(self):
        repository = {"owner": "acme", "name": "fast", "repoDetail": "Fast project"}

        self.assertEqual(one_line_description(repository, {"acme/fast": "缓存译文"}), "缓存译文")
        self.assertEqual(one_line_description(repository), "Fast project")

        manual_wins = {"owner": "morluto", "name": "rea", "repoDetail": "English description"}
        self.assertEqual(
            one_line_description(manual_wins, {"morluto/rea": "缓存译文"}),
            "利用智能代理进行逆向工程分析，覆盖应用行为和原生二进制程序。",
        )

    def test_fetch_all_repositories_reads_pages_until_empty_and_deduplicates(self):
        responses = [
            {"status": 200, "ok": True, "exist": False, "result": [
                {"id": "1", "owner": "acme", "name": "first", "url": "https://github.com/acme/first", "daily_stars_incr": 20},
                {"id": "2", "owner": "acme", "name": "second", "url": "https://github.com/acme/second", "daily_stars_incr": 10},
            ]},
            {"status": 200, "ok": True, "exist": False, "result": [
                {"id": "2", "owner": "acme", "name": "second", "url": "https://github.com/acme/second", "daily_stars_incr": 10},
                {"id": "3", "owner": "acme", "name": "third", "url": "https://github.com/acme/third", "daily_stars_incr": 30},
            ]},
            {"status": 200, "ok": True, "exist": False, "result": []},
        ]

        with patch("scripts.github_daily_report.fetch_page", side_effect=responses) as fetch_page:
            repositories = fetch_all_repositories("https://example.test/rank/2", 1, 500)

        self.assertEqual([repo["id"] for repo in repositories], ["1", "2", "3"])
        self.assertEqual(fetch_page.call_count, 3)

    def test_fetch_all_repositories_stops_at_five_page_limit(self):
        responses = [
            {"status": 200, "ok": True, "exist": False, "result": [
                {"id": "1", "owner": "acme", "name": "first", "url": "https://github.com/acme/first", "daily_stars_incr": 20},
            ]},
            {"status": 201, "ok": False, "exist": False, "result": [], "message": "最多返回前5页数据"},
        ]

        with patch("scripts.github_daily_report.fetch_page", side_effect=responses) as fetch_page:
            repositories = fetch_all_repositories("https://example.test/rank/2", 1, 500)

        self.assertEqual([repo["id"] for repo in repositories], ["1"])
        self.assertEqual(fetch_page.call_count, 2)

    def test_build_report_sorts_by_daily_star_increase_and_includes_details(self):
        report = build_report(SAMPLE_REPOSITORIES, "2026.10.10", top_n=2)

        self.assertIn("GitHub 开源日报｜2026.10.10", report)
        self.assertIn("🏆 今日榜首：[acme/fast](https://github.com/acme/fast)", report)
        self.assertLess(report.index("acme/fast"), report.index("acme/slow"))
        self.assertIn("一句话：Fast project", report)
        self.assertIn("⭐ 总 Star：200｜今日新增：50", report)
        self.assertNotIn("数据来源", report)
        self.assertNotIn("上周增长", report)

    def test_build_report_uses_cached_translations(self):
        report = build_report(
            SAMPLE_REPOSITORIES, "2026.10.10", top_n=2, translations={"acme/fast": "快速项目"}
        )

        self.assertIn("一句话：快速项目", report)
        self.assertIn("一句话：Slow project", report)

    def test_build_card_uses_header_and_ranks_entries(self):
        card = build_card(SAMPLE_REPOSITORIES, "2026.10.10", top_n=2)

        self.assertTrue(card["config"]["wide_screen_mode"])
        self.assertEqual(card["header"]["title"]["content"], "GitHub 开源日报｜2026.10.10")
        self.assertEqual(card["header"]["title"]["tag"], "plain_text")

        body = "\n".join(element.get("content", "") for element in card["elements"])
        self.assertIn("**🏆 今日榜首：[acme/fast](https://github.com/acme/fast)**", body)
        self.assertLess(body.index("acme/fast"), body.index("acme/slow"))
        self.assertIn("⭐ 总 Star：200｜今日新增：50", body)
        self.assertNotIn("数据来源", body)
        self.assertNotIn("# ", body)

    def test_send_card_obtains_token_then_posts_interactive_message(self):
        calls = []

        def fake_post(url, payload, headers=None):
            calls.append((url, payload, headers or {}))
            if "tenant_access_token" in url:
                return {"code": 0, "tenant_access_token": "t-abc"}
            return {"code": 0, "data": {"message_id": "om_1"}}

        with patch("scripts.github_daily_report._post_json", side_effect=fake_post):
            send_card("https://open.feishu.cn", "cli_x", "shh", "oc_y", {"header": {}})

        self.assertEqual(len(calls), 2)

        token_url, token_payload, _ = calls[0]
        self.assertEqual(token_url, "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal")
        self.assertEqual(token_payload, {"app_id": "cli_x", "app_secret": "shh"})

        send_url, send_payload, send_headers = calls[1]
        self.assertEqual(send_url, "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id")
        self.assertEqual(send_headers["Authorization"], "Bearer t-abc")
        self.assertEqual(send_payload["receive_id"], "oc_y")
        self.assertEqual(send_payload["msg_type"], "interactive")
        self.assertEqual(json.loads(send_payload["content"]), {"header": {}})

    def test_send_card_raises_when_message_api_rejects_request(self):
        def fake_post(url, payload, headers=None):
            if "tenant_access_token" in url:
                return {"code": 0, "tenant_access_token": "t-abc"}
            return {"code": 99991663, "msg": "permission denied"}

        with patch("scripts.github_daily_report._post_json", side_effect=fake_post):
            with self.assertRaises(RuntimeError) as ctx:
                send_card("https://open.feishu.cn", "cli_x", "shh", "oc_y", {})

        self.assertIn("99991663", str(ctx.exception))

    def test_app_credentials_come_from_environment_not_hardcoded(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        for name in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_CHAT_ID", "TRANSLATE_API_KEY"):
            self.assertIn(f'os.environ.get("{name}")', source)
        self.assertNotIn("open.feishu.cn/open-apis/bot", source)

    def test_main_fails_loudly_when_feishu_config_is_missing(self):
        with patch("sys.argv", ["github_daily_report", "--no-save"]), patch(
            "scripts.github_daily_report.fetch_all_repositories", return_value=SAMPLE_REPOSITORIES
        ), patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                main()

        self.assertIn("FEISHU_APP_ID", str(ctx.exception))


class TranslationTests(unittest.TestCase):
    def test_collect_pending_translations_filters_mapped_cached_and_chinese(self):
        repositories = [
            {"owner": "acme", "name": "fast", "repoDetail": "Fast project"},
            {"owner": "morluto", "name": "rea", "repoDetail": "Reverse engineer"},
            {"owner": "acme", "name": "cached", "repoDetail": "Cached project"},
            {"owner": "acme", "name": "chinese", "repoDetail": "已经是中文简介"},
            {"owner": "acme", "name": "empty", "repoDetail": ""},
        ]

        pending = collect_pending_translations(repositories, {"acme/cached": "已缓存"})

        self.assertEqual(pending, {"acme/fast": "Fast project"})

    def test_translate_descriptions_returns_only_requested_keys(self):
        pending = {"acme/fast": "Fast project", "acme/slow": "Slow project"}
        model_reply = json.dumps(
            {"acme/fast": "快速项目", "acme/slow": "慢速项目", "other/repo": "多余"},
            ensure_ascii=False,
        )
        response = {"choices": [{"message": {"content": model_reply}}]}

        with patch("scripts.github_daily_report._post_json", return_value=response) as post:
            result = translate_descriptions(pending, "https://api.example.test/v1", "key", "model-x")

        self.assertEqual(result, {"acme/fast": "快速项目", "acme/slow": "慢速项目"})

        url, payload, headers = post.call_args.args
        self.assertEqual(url, "https://api.example.test/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer key")
        self.assertEqual(payload["model"], "model-x")
        self.assertIn("Fast project", payload["messages"][0]["content"])

    def test_translate_descriptions_raises_when_no_choices(self):
        with patch("scripts.github_daily_report._post_json", return_value={"error": "quota"}):
            with self.assertRaises(RuntimeError):
                translate_descriptions({"acme/fast": "Fast"}, "https://api.test/v1", "k", "m")

    def test_parse_json_object_strips_markdown_fence(self):
        raw = '```json\n{"acme/fast": "快速项目"}\n```'
        self.assertEqual(parse_json_object(raw), {"acme/fast": "快速项目"})

    def test_parse_json_object_raises_without_object(self):
        with self.assertRaises(ValueError):
            parse_json_object("抱歉，我无法完成这个请求")

    def test_cache_roundtrip_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            save_description_cache(path, {"acme/fast": "快速项目"})

            self.assertEqual(load_description_cache(path), {"acme/fast": "快速项目"})
            self.assertEqual(load_description_cache(Path(tmp) / "missing.json"), {})

    def test_main_degrades_to_raw_text_without_translate_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "sys.argv",
            [
                "github_daily_report", "--no-save", "--no-send",
                "--cache-path", str(Path(tmp) / "cache.json"),
            ],
        ), patch(
            "scripts.github_daily_report.fetch_all_repositories", return_value=SAMPLE_REPOSITORIES
        ), patch.dict("os.environ", {}, clear=True), patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            main()

        output = stdout.getvalue()
        self.assertIn("一句话：Fast project", output)
        self.assertFalse((Path(tmp) / "cache.json").exists())

    def test_today_in_beijing_uses_dot_separated_date(self):
        self.assertRegex(today_in_beijing(), r"^\d{4}\.\d{2}\.\d{2}$")


if __name__ == "__main__":
    unittest.main()
