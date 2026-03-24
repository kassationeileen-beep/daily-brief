"""
earnings_fetcher.py — 业绩公告日历（Finnhub）

流程：
  1. 调用 Finnhub earnings calendar API 查询过去 24-48h 的全球业绩日历
  2. 按 HK 股票代码格式过滤，与 watchlist 匹配
  3. 返回命中列表（仅代码+公司名+日期），供 main.py 决定是否触发豆包详情查询

环境变量：
  FINNHUB_API_KEY  Finnhub API Key（免费 tier 即可）

注意：
  Finnhub 免费 tier 对 HK 股票覆盖有限，会有漏报但不会误报。
  HK 股票符号格式：Finnhub 通常使用 "0700.HK" 或 "HK:700"。
"""
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"


def fetch_finnhub_earnings_watchlist(
    date_hkt: datetime,
    api_key: str,
    lookback_days: int = 2,
) -> list[dict]:
    """
    查询 Finnhub 业绩日历，返回 watchlist 中今日或昨日有业绩公告的股票列表。

    lookback_days=2：覆盖昨日收市后（amc）披露的情况，早报 07:30 仍需展示。

    返回:
    [{"code": "0291", "name": "華潤啤酒", "date": "2026-03-24", "hour": "amc"}, ...]
    """
    try:
        import requests
    except ImportError:
        logger.warning("[Finnhub] 缺少 requests 依赖")
        return []

    from fetchers.stock_news import HK_STOCKS

    # 查询日期范围
    to_date = date_hkt.strftime("%Y-%m-%d")
    from_date = (date_hkt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            FINNHUB_EARNINGS_URL,
            params={"from": from_date, "to": to_date, "token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        earnings_list = resp.json().get("earningsCalendar", [])
    except Exception as e:
        logger.warning(f"[Finnhub] 请求失败: {e}")
        return []

    logger.info(f"[Finnhub] 查询 {from_date}~{to_date}，共 {len(earnings_list)} 条全球业绩记录")

    # 建立 watchlist 查找表：去前导零的纯数字 → (标准4位code, 公司名)
    watchlist_by_digits = {
        code.lstrip("0") or "0": (code, name)
        for code, name in HK_STOCKS.items()
    }

    matched = []
    seen_codes = set()

    for item in earnings_list:
        symbol = item.get("symbol", "")

        # 匹配 HK 股票：0700.HK / 700.HK / HK:700 / HK:0700
        m = re.search(r'(?:^|HK:)(\d+)(?:\.HK)?$', symbol, re.IGNORECASE)
        if not m:
            m = re.search(r'(\d+)\.HK$', symbol, re.IGNORECASE)
        if not m:
            continue

        digits = m.group(1).lstrip("0") or "0"
        if digits not in watchlist_by_digits:
            continue

        code, name = watchlist_by_digits[digits]
        if code in seen_codes:
            continue
        seen_codes.add(code)

        matched.append({
            "code":  code,
            "name":  name,
            "date":  item.get("date", ""),
            "hour":  item.get("hour", ""),   # "bmo" / "amc" / ""
        })
        logger.info(f"[Finnhub] 命中 watchlist: {name}（{code}.HK）{item.get('date','')} {item.get('hour','')}")

    return matched
