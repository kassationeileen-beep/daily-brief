"""
earnings_fetcher.py — 业绩公告日历（Finnhub）

时间戳逻辑（早报运行时间：HKT 07:30 = ET 前一日 18:30）
─────────────────────────────────────────────────────
市场        业绩释放时机              HKT 等价           07:30 可取到？
港股/A股    收盘后 4-8pm HKT          昨日 HKT           ✅
美股 BMO    开盘前 ~8:30am ET         昨日 HKT ~21:30    ✅
美股 AMC    收盘后 ~4pm ET            今日 HKT ~05:00    ✅（提前 2.5h）

查询策略：
  to   = yesterday_hkt（不取今天，避免拉到当日未发布的 BMO）
  from = yesterday_hkt - 2（共 3 天，覆盖周末 → 周一早报捕捉美股周五 AMC）

注：Finnhub 免费 tier HK 股票覆盖有限，会有漏报但不误报。
    需传 international=true 才能返回非美股数据。
    HK 股票符号格式：通常为 "0700.HK"。
"""
import re
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"


def fetch_finnhub_earnings_watchlist(
    date_hkt: datetime,
    api_key: str,
) -> list[dict]:
    """
    查询 Finnhub 业绩日历，返回 watchlist 中过去 ~3 天内有业绩公告的股票。

    date_hkt: 当前 HKT 时间（通常为早报运行时的 now_hkt）

    返回:
    [{"code": "0291", "name": "華潤啤酒", "date": "2026-03-24", "hour": "amc"}, ...]
    """
    try:
        import requests
    except ImportError:
        logger.warning("[Finnhub] 缺少 requests 依赖")
        return []

    from fetchers.stock_news import HK_STOCKS

    # 时间范围：to = 昨日，from = 昨日 - 2（共 3 天，覆盖周末）
    yesterday = date_hkt.date() - timedelta(days=1)
    from_date = (yesterday - timedelta(days=2)).isoformat()   # 3 天前
    to_date   = yesterday.isoformat()                         # 昨日

    try:
        resp = requests.get(
            FINNHUB_EARNINGS_URL,
            params={
                "from":          from_date,
                "to":            to_date,
                "international": "true",   # 必须：否则不返回非美股
                "token":         api_key,
            },
            timeout=20,
        )
        resp.raise_for_status()
        earnings_list = resp.json().get("earningsCalendar", [])
    except Exception as e:
        logger.warning(f"[Finnhub] 请求失败: {e}")
        return []

    logger.info(
        f"[Finnhub] 查询 {from_date}~{to_date}，"
        f"共 {len(earnings_list)} 条记录（含国际市场）"
    )

    # watchlist 查找表：去前导零的纯数字 → (标准4位code, 公司名)
    watchlist_by_digits = {
        code.lstrip("0") or "0": (code, name)
        for code, name in HK_STOCKS.items()
    }

    matched = []
    seen_codes = set()

    for item in earnings_list:
        symbol = item.get("symbol", "")

        # HK 股票符号匹配：0700.HK / 700.HK / HK:700 / HK:0700
        m = re.search(r'(\d+)\.HK$', symbol, re.IGNORECASE)
        if not m:
            m = re.search(r'HK:(\d+)', symbol, re.IGNORECASE)
        if not m:
            continue

        digits = m.group(1).lstrip("0") or "0"
        if digits not in watchlist_by_digits or digits in seen_codes:
            continue

        code, name = watchlist_by_digits[digits]
        seen_codes.add(digits)

        entry = {
            "code":  code,
            "name":  name,
            "date":  item.get("date", ""),
            "hour":  item.get("hour", ""),      # "bmo" / "amc" / ""
            "quarter": item.get("quarter", ""),
            "year":    item.get("year", ""),
        }
        matched.append(entry)
        logger.info(
            f"[Finnhub] 命中: {name}（{code}.HK）"
            f"  date={entry['date']}  hour={entry['hour']}"
        )

    if not matched:
        logger.info("[Finnhub] watchlist 内无业绩记录")

    return matched
