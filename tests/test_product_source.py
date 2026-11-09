import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.github_daily_report import parse_product_report, one_line_description, build_report, build_card, main

DOCUMENT = '''## 2026.11.09 最佳产品🔝:example
<span>2026.11.08 日榜排行</span>
| 排名 | 项目名 | 当前Star⭐ | 今日Star增长量 |
|---|---|---|---|
| 1 | [acme/example](https://github.com/acme/example)| 7.7k | 🔺3836 |
## 1.示例:example 7.7k⭐
- **产品地址**：[example](https://discoverhub.cn/product/detail/example)
- **产品介绍**：上游中文介绍
- **Github地址**：[https://github.com/acme/example](https://github.com/acme/example)
'''

class ProductSourceTests(unittest.TestCase):
    def test_parse_table_details_and_dates(self):
        published, ranking, repos = parse_product_report(DOCUMENT)
        self.assertEqual((published, ranking), ('2026.11.09', '2026.11.08'))
        self.assertEqual(repos[0]['currentStarNum'], 7700)
        self.assertEqual(repos[0]['daily_stars_incr'], 3836)
        self.assertEqual(repos[0]['repoDetail'], '上游中文介绍')
        self.assertEqual(repos[0]['url'], 'https://github.com/acme/example')

    def test_missing_structure_or_details_fails(self):
        for document in ('unrelated text', DOCUMENT.replace('- **产品介绍**：上游中文介绍', '')):
            with self.subTest(document=document), self.assertRaises(ValueError):
                parse_product_report(document)

    def test_malformed_row_among_valid_rows_fails(self):
        malformed = DOCUMENT.replace(
            '## 1.示例',
            '| 2 | [acme/other](https://github.com/acme/other)| unknown | 🔺12 |\n## 1.示例',
        )
        with self.assertRaises(ValueError):
            parse_product_report(malformed)

    def test_noncontiguous_ranks_fail(self):
        with self.assertRaises(ValueError):
            parse_product_report(DOCUMENT.replace('| 1 |', '| 2 |'))

    def test_upstream_description_wins(self):
        repo = {'owner': 'morluto', 'name': 'rea', 'repoDetail': '最新介绍', 'source_description': True}
        self.assertEqual(one_line_description(repo, {'morluto/rea': '旧缓存'}), '最新介绍')

    def test_main_uses_source_dates_and_never_translates(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            'sys.argv', ['report', '--no-send', '--output-root', tmp]
        ), patch('scripts.github_daily_report.fetch_product_report',
                 return_value=parse_product_report(DOCUMENT)), patch(
            'scripts.github_daily_report.translate_descriptions'
        ) as translate, patch('sys.stdout', new_callable=io.StringIO):
            main()
            saved = Path(tmp) / '2026/11/20261109.md'
            self.assertIn('2026.11.08 Star 增长榜', saved.read_text())
            translate.assert_not_called()

    def test_statistics_date_in_report_and_card(self):
        published, ranking, repos = parse_product_report(DOCUMENT)
        report = build_report(repos, published, statistics_date=ranking)
        card = build_card(repos, published, statistics_date=ranking)
        self.assertIn('2026.11.08 Star 增长榜 TOP 1', report)
        self.assertIn('2026.11.08 Star 增长榜 TOP 1', str(card))
        self.assertNotIn('今日新增', report)

    def test_complete_details_survive_report_and_card(self):
        extra = """![](https://example.com/image.png)
- **产品分类**：[[开发工具](https://example.com/category)]
- **产品外站**：[[github](https://github.com/acme/example)]
- **Github数据**：star:**7.7k** watch:**23** fork:**375** pr:**3.9k** issue:**738** release:**153**
- **近期趋势**：周增长: **894**⭐ (+9%) 月增长: **1.9k**⭐ (+23%)
- **主要语言**：[Rust](https://example.com/rust)
- **开源时间**：2025-10-18
- **未来字段**：不能丢失
"""
        published, ranking, repos = parse_product_report(DOCUMENT + extra + "\n---\n## 更多Github开源项目\n广告尾部")
        report = build_report(repos, published, statistics_date=ranking)
        card_text = str(build_card(repos, published, statistics_date=ranking))
        for line in extra.splitlines()[1:]:
            self.assertIn(line, report)
            self.assertIn(line, card_text)
        for text in (report, card_text):
            self.assertIn('示例:example 7.7k⭐', text)
            self.assertIn('https://discoverhub.cn/product/detail/example', text)
            self.assertIn('https://example.com/image.png', text)
            self.assertNotIn('广告尾部', text)
        self.assertIn('![](https://example.com/image.png)', report)
        self.assertNotIn('![](', card_text)
