import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from unittest.mock import patch
from fetchers.market_data import _parse_hkex_daily, _hsi_session, fetch_hsi

FIXTURES = Path(__file__).parent / 'tests' / 'fixtures'
MAIN = (FIXTURES / 'hkex_main_20260904.html').read_text(encoding='utf-8')
GEM = (FIXTURES / 'hkex_gem_20260904.html').read_text(encoding='utf-8')

class HsiTests(unittest.TestCase):
    def test_real_official_report(self):
        row = _parse_hkex_daily(MAIN, date(2026, 9, 4), 'main')
        self.assertEqual(row['close'], 25650.87)  # afternoon, not morning or previous
        self.assertEqual(row['pct'], 1.74)
        self.assertEqual(row['turnover_hkd'], 276856881141)

    def test_stale_report_rejected(self):
        with self.assertRaisesRegex(ValueError, 'mismatched'):
            _parse_hkex_daily(MAIN, date(2026, 9, 7), 'main')

    def test_calendar(self):
        for now, expected in [(datetime(2026,9,8,7,30), date(2026,9,7)),
                              (datetime(2026,9,7,7,30), date(2026,9,4)),
                              (datetime(2026,4,7,7,30), date(2026,4,2)),
                              (datetime(2026,9,7,23,30,tzinfo=timezone.utc), date(2026,9,7))]:
            with self.subTest(now=now):
                self.assertEqual(_hsi_session(now), expected)

    @patch('fetchers.market_data._get_hkex_report', side_effect=[MAIN, GEM])
    def test_total_is_main_plus_gem(self, get):
        row = fetch_hsi(datetime(2026,9,7,7,30))
        self.assertIsNone(row['error'])
        self.assertEqual(row['trade_date'], '2026-09-04')
        self.assertEqual(row['turnover_hkd_100m'], 2769.69)

    @patch('fetchers.market_data._get_hkex_report', side_effect=[MAIN, RuntimeError('unavailable')])
    def test_missing_gem_does_not_publish_partial_total(self, get):
        row = fetch_hsi(datetime(2026,9,7,7,30))
        self.assertEqual(row['close'], 25650.87)
        self.assertIsNone(row['turnover_hkd_100m'])

    @patch('fetchers.market_data._get_hkex_report', return_value=MAIN)
    def test_stale_source_never_becomes_current_close(self, get):
        row = fetch_hsi(datetime(2026,9,8,7,30))
        self.assertIsNone(row['close'])
        self.assertIsNone(row['turnover_hkd_100m'])
        self.assertTrue(row['error'])

    def test_wrong_board_and_bad_layout(self):
        for html in [GEM, MAIN.replace('25650.87', 'NaN'), MAIN.replace('DATE:', 'NO DATE')]:
            with self.assertRaises(ValueError):
                _parse_hkex_daily(html, date(2026,9,4), 'main')

    def test_output_labels(self):
        from main import build_brief
        text = build_brief({'hsi': {'trade_date':'2026-09-04','close':25650.87,
                                   'turnover_hkd_100m':2769.69}}, [], datetime(2026,9,7,7,30))
        self.assertIn('交易日期：2026-09-04', text)
        self.assertIn('港股成交額（主板＋GEM）：2,769.69', text)

if __name__ == '__main__':
    unittest.main()
