"""
macro_news.py — 第三部分：宏观 & 行业新闻抓取
来源优先级：
  0. 金十数据快讯 API（实时中文宏观快讯，首选）
  1. Reuters Business/Markets（稳定，英文）
  2. Bloomberg Markets（英文）
  3. SCMP Business（英文，港媒）
  4. Caixin Global（英文，中国宏观）
  5. 财新网 中文 RSS（中文备用）
"""
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# RSS 源配置
# ─────────────────────────────────────────────

# 宏观/全球经济
MACRO_FEEDS = [
    {
        "name": "Reuters Business",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "lang": "en",
    },
    {
        "name": "Reuters Markets",
        "url": "https://feeds.reuters.com/reuters/financialNews",
        "lang": "en",
    },
    {
        "name": "Bloomberg Markets",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "lang": "en",
    },
    {
        "name": "SCMP Business",
        "url": "https://www.scmp.com/rss/2/feed",
        "lang": "en",
    },
    {
        "name": "Caixin Global",
        "url": "https://www.caixinglobal.com/rss/caixinglobal.xml",
        "lang": "en",
    },
    {
        "name": "财新网",
        "url": "https://www.caixin.com/rss/",
        "lang": "zh",
    },
]

# 行业 / 板块
SECTOR_FEEDS = [
    {
        "name": "Reuters Tech",
        "url": "https://feeds.reuters.com/reuters/technologyNews",
        "lang": "en",
    },
    {
        "name": "Reuters Energy",
        "url": "https://feeds.reuters.com/reuters/energy",
        "lang": "en",
    },
    {
        "name": "SCMP Tech",
        "url": "https://www.scmp.com/rss/36/feed",
        "lang": "en",
    },
]

# 关键词过滤（命中其中之一才保留）
MACRO_KEYWORDS = [
    # 货币政策
    "fed", "federal reserve", "interest rate", "rate cut", "rate hike",
    "fomc", "powell", "ecb", "pboc", "hkma", "central bank",
    "inflation", "cpi", "pce", "ppi", "deflation",
    # 中国/香港宏观
    "china gdp", "china economy", "china trade", "china export",
    "hong kong", "hksar", "renminbi", "rmb", "yuan",
    "tariff", "trade war", "sanctions",
    # 全球宏观
    "recession", "gdp", "unemployment", "nonfarm", "payroll",
    "treasury yield", "bond yield", "yield curve",
    "oecd", "imf", "world bank",
    # 能源/大宗
    "oil price", "crude oil", "opec", "brent", "wti",
    "gold price", "commodity",
    # 中文关键词
    "利率", "降息", "加息", "美联储", "央行", "人民银行",
    "通货膨胀", "通胀", "cpi", "gdp", "关税", "贸易战",
    "宏观经济", "经济数据", "经济放缓", "经济复苏",
    "美债", "国债", "原油", "黄金", "大宗商品",
]

NEWS_MAX_AGE_HOURS = 20  # 只保留20小时内的新闻（早报前一天傍晚至今）
MAX_ITEMS_PER_FEED = 10

# ─────────────────────────────────────────────
# 金十数据快讯 API
# ─────────────────────────────────────────────

JIN10_FLASH_URL = "https://flash-api.jin10.com/get_flash_list"
JIN10_HEADERS = {
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
JIN10_PARAMS = {"channel": "-8200", "vip": "1"}
JIN10_MAX_ITEMS = 50  # 最多拉取条数


def fetch_jin10_flash(max_age_hours: int = NEWS_MAX_AGE_HOURS) -> list[dict]:
    """
    抓取金十数据快讯，过滤宏观关键词后返回。
    返回: [{title, content, time, source, lang}, ...]
    """
    try:
        import requests
    except ImportError:
        logger.warning("[Jin10] 缺少 requests 依赖，跳过")
        return []

    try:
        resp = requests.get(
            JIN10_FLASH_URL, headers=JIN10_HEADERS, params=JIN10_PARAMS, timeout=12
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"[Jin10] 请求失败: {e}")
        return []

    # 响应格式：{"data": {"items": [...]}} 或 {"data": [...]}
    try:
        raw = data.get("data") or {}
        if isinstance(raw, list):
            items = raw
        else:
            items = raw.get("items") or []
    except Exception:
        logger.warning("[Jin10] 响应格式异常")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    results = []

    for item in items[:JIN10_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue

        content = (item.get("content") or item.get("body") or "").strip()
        if not content:
            continue

        # 时间解析（格式通常为 "2026-03-20 10:30:00"）
        time_str = item.get("time") or item.get("created_at") or ""
        pub_dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                pub_dt = datetime.strptime(time_str[:19], fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

        if pub_dt and pub_dt < cutoff:
            continue  # 超出时间窗口

        # 关键词过滤
        if not _keyword_match(content):
            continue

        results.append({
            "title": content[:80],        # 快讯无独立标题，取前80字作标题
            "content": content[:300],
            "time": time_str,
            "source": "金十快讯",
            "lang": "zh",
        })

    logger.info(f"[Jin10] 命中 {len(results)} 条宏观快讯")
    return results


def _is_recent(pub_time_str: str, max_hours: int = NEWS_MAX_AGE_HOURS) -> bool:
    """判断文章是否在 max_hours 内"""
    if not pub_time_str:
        return True  # 无时间信息时保留
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_time_str)
        dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        return (now_utc - dt_utc).total_seconds() < max_hours * 3600
    except Exception:
        return True


def _keyword_match(text: str) -> bool:
    """标题+摘要中是否包含宏观关键词"""
    lower = text.lower()
    return any(kw in lower for kw in MACRO_KEYWORDS)


def _fetch_one_feed(feed_cfg: dict, timeout: int = 12) -> list[dict]:
    """抓取单个RSS源，返回 [{title, content, time, source}]"""
    import feedparser
    items = []
    try:
        fp = feedparser.parse(feed_cfg["url"])
        for entry in fp.entries[:MAX_ITEMS_PER_FEED]:
            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            pub = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""
            # 时间过滤
            if not _is_recent(pub):
                continue
            # 关键词过滤
            if not _keyword_match(title + " " + summary):
                continue
            items.append({
                "title": title.strip(),
                "content": summary.strip()[:200],
                "time": pub,
                "source": feed_cfg["name"],
                "lang": feed_cfg.get("lang", "en"),
            })
    except Exception as e:
        logger.warning(f"[MacroFeed/{feed_cfg['name']}] 抓取失败: {e}")
    return items


def fetch_macro_news(include_sectors: bool = True) -> list[dict]:
    """
    抓取宏观 + 行业新闻，合并去重后返回。
    返回: [{title, content, time, source, lang}, ...]
    金十快讯优先（实时中文），RSS源作为补充。
    """
    all_items = []

    # 优先：金十数据快讯（实时，中文）
    jin10_items = fetch_jin10_flash()
    all_items.extend(jin10_items)

    # 补充：RSS源
    feeds = MACRO_FEEDS + (SECTOR_FEEDS if include_sectors else [])

    for feed in feeds:
        items = _fetch_one_feed(feed)
        logger.info(f"[MacroFeed/{feed['name']}] 命中 {len(items)} 条")
        all_items.extend(items)
        time.sleep(0.5)

    # 按标题去重（标准化后比较）
    seen_titles: set[str] = set()
    deduped = []
    for item in all_items:
        key = item["title"].lower().strip()[:60]
        if key and key not in seen_titles:
            seen_titles.add(key)
            deduped.append(item)

    # 按时间排序（最新在前），无法解析的排到后面
    def _sort_key(item):
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(item["time"]).timestamp()
        except Exception:
            return 0

    deduped.sort(key=_sort_key, reverse=True)
    logger.info(f"[MacroFeed] 共 {len(deduped)} 条去重后新闻（来自 {len(feeds)} 个源）")
    return deduped
