"""
stock_news.py — 第四部分：个股新闻抓取
港股：东方财富个股新闻 API
A股：akshare stock_news_em
美股：Yahoo Finance RSS (feedparser)
"""
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Watchlist
# ─────────────────────────────────────────────

HK_STOCKS = {
    "0175": "吉利汽車",
    "3606": "福耀玻璃",
    "1211": "比亞迪",
    "3988": "中國銀行",
    "3968": "招商銀行",
    "0291": "華潤啤酒",
    "9633": "農夫山泉",
    "1810": "小米集團",
    "0836": "華潤電力",
    "0710": "京東方精電",
    "0636": "嘉里物流",
    "0868": "信義玻璃",
    "9961": "攜程集團",
    "9987": "百勝中國",
    "0300": "美的集團",
    "6690": "海爾智家",
    "1299": "友邦保險",
    "2318": "中國平安",
    "0388": "港交所",
    "1428": "耀才證券",
    "0580": "賽晶科技",
    "0669": "創科實業",
    "2382": "舜宇光學",
    "3750": "寧德時代",
    "9901": "新東方",
    "0857": "中石油",
    "1138": "中遠海能",
    "0066": "港鐵",
    "0587": "海螺環保",
    "2669": "中海物業",
    "0688": "中國海外發展",
    "1109": "華潤置地",
    "0960": "龍湖集團",
    "3900": "綠城中國",
    "0501": "豪威集團",
    "1024": "快手",
    "3690": "美團",
    "0700": "騰訊",
    "9988": "阿里巴巴",
    "0020": "商湯",
    "0941": "中國移動",
    "0933": "非凡領越",
    "2020": "安踏",
    "2331": "李寧",
    "2313": "申洲國際",
    "1880": "中國中免",
}

A_STOCKS = {
    "600519": "貴州茅台",
}

US_STOCKS = {
    "INTC": "Intel",
    "AAPL": "蘋果",
    "MSFT": "微軟",
    "GOOGL": "谷歌",
    "AMZN": "亞馬遜",
    "META": "Meta",
    "NVDA": "英偉達",
    "TSLA": "特斯拉",
}


# ─────────────────────────────────────────────
# 港股新闻（东方财富）
# ─────────────────────────────────────────────

def fetch_hk_news_yahoo(code: str, name: str, max_items: int = 10) -> list[dict]:
    """
    主：Yahoo Finance RSS for HK stocks（海外服务器稳定）
    港股代码格式：0700.HK, 9988.HK（前导零保留）
    """
    import feedparser
    # 四位数代码（含前导零）
    ticker = f"{code.zfill(4)}.HK"
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=HK&lang=zh-Hant-HK"
    try:
        feed = feedparser.parse(url)
        news = []
        for entry in feed.entries[:max_items]:
            news.append({
                "title": entry.get("title", ""),
                "content": entry.get("summary", ""),
                "time": entry.get("published", ""),
                "lang": "zh",
            })
        return news
    except Exception as e:
        logger.debug(f"[HK-Yahoo/{code}] {e}")
        return []


def fetch_hk_news(code: str, name: str, max_items: int = 10) -> list[dict]:
    """
    港股新闻：Yahoo Finance RSS 主，东方财富 API 备
    """
    # 主：Yahoo Finance（海外稳定）
    news = fetch_hk_news_yahoo(code, name, max_items)
    if news:
        return news

    # 备：东方财富（可能因海外IP超时）
    import requests
    url = "https://np-listapi.eastmoney.com/comm/web/getListInfo"
    params = {
        "client": "web", "type": "1",
        "mTypeAndCode": f"116.{code}",
        "pageSize": max_items, "pageIndex": "1", "callback": "",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # 防御性取值：data 或 data["data"] 可能为 None
        if not data:
            return []
        inner = data.get("data") or {}
        items = inner.get("list") or []
        news = []
        for item in items:
            if not isinstance(item, dict):
                continue
            news.append({
                "title": item.get("title", ""),
                "content": item.get("digest", item.get("summary", "")),
                "time": item.get("showTime", item.get("time", "")),
            })
        return news
    except Exception as e:
        logger.debug(f"[HK东方财富/{code}/{name}] {e}")
        return []


def fetch_hk_news_alt(code: str, name: str, max_items: int = 10) -> list[dict]:
    """
    备用：东方财富新闻搜索接口（按股票名称搜索）
    """
    import requests
    url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    params = {
        "sr": "-1",
        "page_size": max_items,
        "page_index": "1",
        "ann_type": "HK",
        "client_source": "web",
        "stock_list": code,
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        items = data.get("data", {}).get("list", []) or []
        return [{"title": i.get("ann_title", ""), "content": "", "time": i.get("notice_date", "")}
                for i in items]
    except Exception as e:
        logger.warning(f"[HK新闻备用/{code}] {e}")
        return []


# ─────────────────────────────────────────────
# A股新闻（akshare）
# ─────────────────────────────────────────────

def fetch_a_news(code: str, name: str, max_items: int = 10) -> list[dict]:
    """akshare stock_news_em"""
    import akshare as ak
    try:
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            return []
        news = []
        for _, row in df.head(max_items).iterrows():
            news.append({
                "title": str(row.get("新闻标题", row.get("title", ""))),
                "content": str(row.get("新闻内容", row.get("content", ""))),
                "time": str(row.get("发布时间", row.get("time", ""))),
            })
        return news
    except Exception as e:
        logger.warning(f"[A股新闻/{code}/{name}] {e}")
        return []


# ─────────────────────────────────────────────
# 美股新闻（Yahoo Finance RSS via feedparser）
# ─────────────────────────────────────────────

def fetch_us_news(ticker: str, name: str, max_items: int = 10) -> list[dict]:
    """Yahoo Finance RSS"""
    import feedparser
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    try:
        feed = feedparser.parse(url)
        news = []
        for entry in feed.entries[:max_items]:
            news.append({
                "title": entry.get("title", ""),
                "content": entry.get("summary", ""),
                "time": entry.get("published", ""),
                "lang": "en",
            })
        return news
    except Exception as e:
        logger.warning(f"[美股新闻/{ticker}/{name}] {e}")
        return []


# ─────────────────────────────────────────────
# 港股回购公告（hkexnews.hk）
# ─────────────────────────────────────────────

def fetch_hk_buyback_announcements(code: str, name: str, days: int = 2) -> list[dict]:
    """
    从 hkexnews.hk 抓取近 days 天内该股的回购相关公告（Share Repurchase / Monthly Return）。
    公告标题含回购关键词时纳入，作为个股新闻的前置补充，确保 LLM 能看到。
    """
    import requests
    from datetime import date, timedelta
    today = date.today()
    from_dt = (today - timedelta(days=days)).strftime("%Y%m%d")
    to_dt = today.strftime("%Y%m%d")

    url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
    params = {
        "lang": "EN",
        "scode": code.zfill(5),
        "sdatefrom": from_dt,
        "sdateto": to_dt,
        "statype": "",
        "category": "0",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www1.hkexnews.hk/",
        "Accept-Language": "en-US,en;q=0.9",
    }

    BUYBACK_KEYWORDS = [
        "repurchase", "buyback", "buy-back",
        "monthly return",       # HKEx Monthly Return 包含回购数据
        "回購", "share buy",
    ]

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.debug(f"[HKExNews-buyback/{code}] 请求失败: {e}")
        return []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.debug(f"[HKExNews-buyback/{code}] 解析失败: {e}")
        return []

    results = []
    # hkexnews.hk 搜索结果通常在 class 含 "title" 的 table 或 div 里
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        # 标题通常在第2或3列
        title = max((c.get_text(strip=True) for c in cells), key=len)
        if not any(kw.lower() in title.lower() for kw in BUYBACK_KEYWORDS):
            continue
        date_cell = cells[0].get_text(strip=True)
        results.append({
            "title": f"[港交所披露] {title}",
            "content": f"港交所公告｜{name}（{code}）：{title}",
            "time": date_cell,
        })

    if results:
        logger.info(f"[HKExNews-buyback/{code}/{name}] 找到 {len(results)} 条回购公告")
    else:
        logger.debug(f"[HKExNews-buyback/{code}/{name}] 无回购公告")
    return results


# ─────────────────────────────────────────────
# 统一抓取所有个股新闻
# ─────────────────────────────────────────────

def fetch_all_stock_news(request_interval: float = 1.5) -> dict:
    """
    返回:
    {
      "hk":  {"0700": {"name": "騰訊", "news": [...]}},
      "a":   {"600519": {"name": "貴州茅台", "news": [...]}},
      "us":  {"AAPL": {"name": "蘋果", "news": [...]}},
    }
    """
    result = {"hk": {}, "a": {}, "us": {}}

    logger.info(f"抓取港股新闻（{len(HK_STOCKS)} 只）...")
    for code, name in HK_STOCKS.items():
        news = fetch_hk_news(code, name)
        if not news:
            logger.debug(f"[{code}] 主接口无数据，尝试备用")
            news = fetch_hk_news_alt(code, name)
        # 回购公告前置（来自港交所披露，Yahoo RSS 通常不包含）
        buyback = fetch_hk_buyback_announcements(code, name)
        if buyback:
            news = buyback + news
        result["hk"][code] = {"name": name, "news": news}
        logger.debug(f"  {code} {name}: {len(news)} 条（含回购 {len(buyback)} 条）")
        time.sleep(request_interval)

    logger.info(f"抓取A股新闻（{len(A_STOCKS)} 只）...")
    for code, name in A_STOCKS.items():
        news = fetch_a_news(code, name)
        result["a"][code] = {"name": name, "news": news}
        logger.debug(f"  {code} {name}: {len(news)} 条")
        time.sleep(request_interval)

    logger.info(f"抓取美股新闻（{len(US_STOCKS)} 只）...")
    for ticker, name in US_STOCKS.items():
        news = fetch_us_news(ticker, name)
        result["us"][ticker] = {"name": name, "news": news}
        logger.debug(f"  {ticker} {name}: {len(news)} 条")
        time.sleep(0.5)

    return result
