import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from fetchers import market_data, stock_news
from fetchers.telegram_input import _parse_message
from main import build_brief


class USWatchlistTests(unittest.TestCase):
    def test_holiday_and_future_rows(self):
        history = pd.DataFrame(
            {"Close": [100.0, 110.0, 999.0]},
            index=pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-07"]),
        )
        ticker = Mock(return_value=SimpleNamespace(history=Mock(return_value=history)))
        with patch.dict(sys.modules, {"yfinance": SimpleNamespace(Ticker=ticker)}):
            data = market_data.fetch_us_indices(datetime(2026, 7, 6, 7, 30))
        self.assertEqual(data["sp500"]["trade_date"], "2026-07-02")
        self.assertEqual(data["nasdaq"]["pct"], 10.0)
        self.assertEqual(data["sp500"]["close"], 110.0)
        self.assertEqual([c.args[0] for c in ticker.call_args_list], ["^GSPC", "^IXIC"])

    def test_partial_failure_and_single_close(self):
        history = pd.DataFrame({"Close": [123.0]}, index=pd.to_datetime(["2026-10-13"]))
        ticker = Mock(side_effect=[RuntimeError("offline"), SimpleNamespace(history=lambda **kw: history)])
        with patch.dict(sys.modules, {"yfinance": SimpleNamespace(Ticker=ticker)}):
            data = market_data.fetch_us_indices(datetime(2026, 10, 14, 7, 30))
        self.assertIsNone(data["sp500"]["close"])
        self.assertEqual(data["nasdaq"]["close"], 123)
        self.assertIsNone(data["nasdaq"]["pct"])
        brief = build_brief({"hsi": {"trade_date": "2026-10-12"}, "us": data}, [],
                            datetime(2026, 10, 14, 7, 30, tzinfo=timezone(timedelta(hours=8))))
        self.assertIn("納斯達克綜合指數（2026-10-13）：123.00", brief)
        self.assertIn("標普500指數（日期未取得）：N/A", brief)

    def test_company_news_reaches_pipeline(self):
        with patch.object(stock_news, "HK_STOCKS", {}), patch.object(stock_news, "A_STOCKS", {}), \
             patch.object(stock_news, "fetch_us_news", return_value=[]), \
             patch.object(stock_news, "fetch_futu_news", return_value=[{"title": "新產品發布"}]) as fetch, \
             patch.object(stock_news.time, "sleep"):
            data = stock_news.fetch_all_stock_news(0)
        self.assertEqual(data["us"]["TSM"]["name"], "台積電")
        self.assertEqual(set(data["global"]), {"SAMSUNG", "SKHYNIX", "SPACEX"})
        self.assertEqual([c.args[0] for c in fetch.call_args_list], ["三星電子", "SK海力士", "SpaceX"])
        from llm import refiner
        with patch.object(refiner, "load_seen_events", return_value={}), \
             patch.object(refiner, "cleanup_old_events", return_value={}), \
             patch.object(refiner, "get_history_context", return_value=""), \
             patch.object(refiner, "refine_stock_news", return_value="") as refine:
            refiner.refine_all_stocks(data, llm_interval=0)
        self.assertEqual([c.args[0] for c in refine.call_args_list], ["三星電子", "SK海力士", "SpaceX"])

    def test_manual_tags(self):
        for tag, code in [("三星", "SAMSUNG"), ("海力士", "SKHYNIX"),
                          ("台积电", "TSM"), ("SpaceX", "SPACEX")]:
            parsed = _parse_message(f"#{tag}\n新產品發布")
            self.assertEqual(code, parsed["company"])


if __name__ == "__main__":
    unittest.main()
