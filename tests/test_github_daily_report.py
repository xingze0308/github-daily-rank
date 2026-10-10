import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.github_daily_report import build_report, fetch_all_repositories, one_line_description

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "github_daily_report.py"


class GithubDailyReportTests(unittest.TestCase):
    def test_one_line_description_falls_back_when_missing(self):
        self.assertEqual(one_line_description({"repoDetail": ""}), "暂无简介")
        self.assertEqual(one_line_description({"repoDetail": "项目简介"}), "项目简介")
        self.assertEqual(
            one_line_description({"owner": "morluto", "name": "rea", "repoDetail": "English description"}),
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

    def test_webhook_is_read_from_environment_and_not_hardcoded(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn('os.environ.get("FEISHU_WEBHOOK_URL")', source)
        self.assertNotIn('os.environ.get("FEISHU_WEBHOOK")', source)
        self.assertNotIn("open.feishu.cn/open-apis/bot", source)

    def test_build_report_sorts_by_daily_star_increase_and_includes_details(self):
        repositories = [
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

        report = build_report(repositories, "2026.10.10", top_n=2)

        self.assertIn("GitHub 开源日报｜2026.10.10", report)
        self.assertIn("🏆 今日榜首：[acme/fast](https://github.com/acme/fast)", report)
        self.assertLess(report.index("acme/fast"), report.index("acme/slow"))
        self.assertIn("一句话：Fast project", report)
        self.assertIn("⭐ 总 Star：200｜今日新增：50", report)
        self.assertNotIn("数据来源", report)
        self.assertNotIn("上周增长", report)


if __name__ == "__main__":
    unittest.main()
