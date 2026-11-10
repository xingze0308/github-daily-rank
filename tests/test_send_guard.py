import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.github_daily_report import main


class SendGuardTests(unittest.TestCase):
    def run_report(self, root, *extra, sender=None, published='2026.11.10'):
        repo = {'owner': 'acme', 'name': 'example', 'url': 'https://github.com/acme/example',
                'repoDetail': '中文', 'source_description': True, 'daily_stars_incr': 1}
        with patch('sys.argv', ['report', '--output-root', str(root), '--skip-sent',
                                 '--app-id', 'test', '--app-secret', 'test', '--chat-id', 'test', *extra]), \
             patch('scripts.github_daily_report.today_in_beijing', return_value='2026.11.10'), \
             patch('scripts.github_daily_report.fetch_product_report', return_value=(published, '2026.11.09', [repo])), \
             patch('scripts.github_daily_report.send_card', side_effect=sender) as send, \
             patch('sys.stdout', new_callable=io.StringIO):
            main()
            return send

    def test_success_records_and_second_run_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_report(tmp, sender=lambda *a: {'data': {'message_id': 'm1'}}).call_count, 1)
            self.assertEqual(self.run_report(tmp).call_count, 0)
            marker = Path(tmp) / '.sent-reports/20261110.json'
            self.assertIn('m1', marker.read_text())
            self.assertNotIn('secret', marker.read_text())

    def test_failure_does_not_record_and_retry_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                self.run_report(tmp, sender=RuntimeError('failed'))
            self.assertFalse((Path(tmp) / '.sent-reports').exists())
            self.assertEqual(self.run_report(tmp, sender=lambda *a: {}).call_count, 1)

    def test_no_send_does_not_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_report(tmp, '--no-send').call_count, 0)
            self.assertFalse((Path(tmp) / '.sent-reports').exists())

    def test_no_save_still_records_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_report(tmp, '--no-save', sender=lambda *a: {})
            self.assertTrue((Path(tmp) / '.sent-reports/20261110.json').exists())
            self.assertFalse((Path(tmp) / '2026').exists())

    def test_stale_source_skips_without_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_report(tmp, published='2026.11.09').call_count, 0)
            self.assertFalse((Path(tmp) / '.sent-reports').exists())
