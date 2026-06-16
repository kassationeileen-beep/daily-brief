"""
jin10_breakfast.py — 金十数据「全球财经早餐」抓取与解析

每日 07:00 更新，内容结构化程度高，适合替代 LLM 宏观生成：
  - 今日优选（精选 6 条）
  - 国际要闻
  - 国内要闻

流程：
  1. 从 /30 栏目页找今日文章 URL（按日期标题匹配）
  2. 抓取文章页解析各段落
  3. 返回格式化的宏观段落文本
"""
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Referer": "https://xnews.jin10.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_CATEGORY_URL = "https://xnews.jin10.com/30"
_ARTICLE_BASE  = "https://xnews.jin10.com"

# 我们关心的章节（中文标题匹配子串）
_WANT_SECTIONS = ["今日优选", "今日優選", "国内要闻", "國內要聞", "国际要闻", "國際要聞"]
# 遇到以下标题时停止采集当前章节
_STOP_SECTIONS = ["市场盘点", "市場盤點", "风险预警", "風險預警", "往期回顾", "往期回顧"]


def _today_label(date_hkt: datetime) -> str:
    """返回如 '2026年6月16日' 格式的日期标签（不带前导零）"""
    return f"{date_hkt.year}年{date_hkt.month}月{date_hkt.day}日"


def _find_breakfast_url(date_hkt: datetime) -> Optional[str]:
    """从金十早餐栏目页找今日文章 URL，失败返回 None"""
    import requests
    today = _today_label(date_hkt)
    try:
        resp = requests.get(_CATEGORY_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[Jin10Breakfast] 栏目页请求失败: {e}")
        return None

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"[Jin10Breakfast] 栏目页解析失败: {e}")
        return None

    # 先找精确匹配今日日期的链接
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "details/" not in href:
            continue
        text = a.get_text(strip=True)
        if today in text:
            url = href if href.startswith("http") else f"{_ARTICLE_BASE}{href}"
            logger.info(f"[Jin10Breakfast] 找到今日文章: {url}")
            return url

    # 降级：取含"早餐"的第一篇（最新）
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if "details/" in href and "早餐" in text:
            url = href if href.startswith("http") else f"{_ARTICLE_BASE}{href}"
            logger.warning(f"[Jin10Breakfast] 未找到今日早餐，使用最新: {url}")
            return url

    logger.warning(f"[Jin10Breakfast] 栏目页无早餐文章: {today}")
    return None


def _parse_article(url: str) -> dict[str, list[str]]:
    """
    抓取并解析文章页，提取各章节内容。
    返回 {"今日优选": [...], "国际要闻": [...], "国内要闻": [...]} 等。
    """
    import requests
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"[Jin10Breakfast] 文章请求失败: {e}")
        return {}

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"[Jin10Breakfast] 文章解析失败: {e}")
        return {}

    # 找正文容器，按优先级尝试多个选择器
    body = None
    for selector in [
        {"class_": "article-content"},
        {"class_": "content"},
        {"name": "article"},
        {"name": "main"},
    ]:
        tag = selector.pop("name", None)
        if tag:
            body = soup.find(tag, **selector) or soup.find(tag)
        else:
            body = soup.find(**selector)
        if body:
            break
    if not body:
        body = soup.body or soup  # 最后兜底

    sections: dict[str, list[str]] = {}
    current: Optional[str] = None

    def _norm(t: str) -> str:
        return t.strip()

    def _is_section_header(t: str) -> Optional[str]:
        """如果 t 是已知章节标题，返回规范名；否则 None"""
        for s in _WANT_SECTIONS + _STOP_SECTIONS:
            if s in t:
                return s
        return None

    # 遍历正文所有文本节点（按文档顺序）
    for elem in body.find_all(["h1", "h2", "h3", "h4", "strong", "b", "p", "li", "span"]):
        raw = elem.get_text(strip=True)
        if not raw or len(raw) > 200:
            continue

        header = _is_section_header(raw)
        if header:
            if header in _STOP_SECTIONS:
                # 遇到不需要的章节，将当前段落置 None 但不退出（后面可能还有需要的）
                current = None
            else:
                current = header
                if current not in sections:
                    sections[current] = []
            continue

        if current and current in _WANT_SECTIONS:
            # 过滤过短的噪声行、纯数字序号
            if len(raw) > 4 and not re.match(r"^[\d①②③④⑤⑥⑦⑧⑨⑩]+[.、]?$", raw):
                # 去掉行首序号（数字+点/顿号、①②等）
                clean = re.sub(r"^[\d①②③④⑤⑥⑦⑧⑨⑩]+[.、\s]*", "", raw)
                clean = clean.strip()
                if clean and clean not in sections[current]:
                    sections[current].append(clean)

    logger.info(
        f"[Jin10Breakfast] 解析完成: "
        + ", ".join(f"{k}={len(v)}" for k, v in sections.items())
    )
    return sections


def _fmt_section(key: str, items: list[str], max_items: int) -> str:
    """将 items 格式化为带标题的要点块"""
    if "优选" in key or "優選" in key:
        header = "🔸今日優選"
    elif "国际" in key or "國際" in key:
        header = "🔸國際要聞"
    else:
        header = "🔸國內要聞"
    bullets = "\n".join(f"• {item}" for item in items[:max_items])
    return f"{header}\n{bullets}"


def fetch_jin10_breakfast(date_hkt: datetime = None) -> Optional[str]:
    """
    抓取金十财经早餐，返回格式化宏观段落，或 None（失败/无内容时）。

    输出格式：
      ▶️三、*宏觀要聞*（金十財經早餐）
      🔸今日優選
      • ...
      🔸國際要聞
      • ...
      🔸國內要聞
      • ...
    """
    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    url = _find_breakfast_url(date_hkt)
    if not url:
        return None

    sections = _parse_article(url)
    if not sections:
        return None

    # 输出顺序：今日优选 → 国际要闻 → 国内要闻
    output_order = [
        ("今日优选", "今日優選", 6),
        ("国际要闻", "國際要聞", 6),
        ("国内要闻", "國內要聞", 5),
    ]

    parts = [f"▶️三、*{date_hkt.month}月{date_hkt.day}日 宏觀要聞*（金十財經早餐）"]
    for zh_cn, zh_tw, max_n in output_order:
        for key in sections:
            if zh_cn in key or zh_tw in key:
                items = sections[key]
                if items:
                    parts.append(_fmt_section(key, items, max_n))
                break

    if len(parts) == 1:
        logger.warning("[Jin10Breakfast] 未提取到任何有效段落")
        return None

    return "\n\n".join(parts)
