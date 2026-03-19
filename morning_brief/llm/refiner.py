"""
refiner.py — LLM 提炼 + 历史去重逻辑
支持 DeepSeek（主）→ Kimi（备）自动切换
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SEEN_EVENTS_PATH = Path(__file__).parent.parent / "seen_events.json"
HISTORY_DAYS = 3       # 注入去重用的历史天数
CLEANUP_DAYS = 7       # 超过此天数的记录清除
NEWS_FRESHNESS_DAYS = 2  # 只处理最近N天的新闻，过滤旧新闻


def _build_system_prompt() -> str:
    """动态生成 system prompt，注入当前日期和财务报告期规则"""
    now = datetime.now()
    year = now.year          # e.g. 2026
    month = now.month        # e.g. 3
    prev_year = year - 1     # 2025
    prev2_year = year - 2    # 2024

    # 计算哪些财务期间是"陈旧"的
    # 中期/H1：上半年业绩在8月前后披露，超过6个月即陈旧
    # 若现在是1-6月：上一年H1是陈旧的；若7-12月：本年H1才刚发布不久
    if month <= 6:
        stale_interim = f"{prev_year}年中期/H1/半年"   # 超过6个月
        stale_annual_cutoff = prev2_year               # 2024年及更早年报是旧的
    else:
        # 下半年：本年H1刚发不久，仍有效；2年前年报是旧的
        stale_interim = f"{prev2_year}年及更早的中期/H1/半年"
        stale_annual_cutoff = prev2_year

    return f"""你是专业金融早报编辑。将原始财经新闻提炼为机构投资者早报格式。
今日日期：{now.strftime("%Y年%m月%d日")}

【优先保留，必须输出】
- 业绩发布：季报/年报核心数据、业绩预告、盈利警告
- 新产品/技术发布：新品发布会、重大技术突破、战略合作签约
- 资本动作：回购公告（金额+股数）、分拆上市、增发、大股东增减持
- 重大人事：CEO/CFO变动
- 监管/政策：直接影响该公司的监管决定、罚款、许可证

【直接丢弃，不输出】
- 投行目标价调整、评级变动（无论上调下调）
- 纯技术分析/K线/均线解读
- 无具体数据的行业泛评论
- 分析师预测（非公司官方发布）
- 旧事件的二次解读和评论文章
- [日期未知]或发布日期距今超过2天的文章（除非内容极重大）
- 已在[历史播报]中出现的事件（核心事实相同即为重复，不看标题措辞）

【财务报告期新鲜度规则（重要）】
✅ 接受（当前有效）：
  - {year}年任何期间的业绩
  - {prev_year}年全年/年报（通常在{year}年Q1-Q2发布，属正常披露）
  - {prev_year}年Q3/三季报（若文章发布日期在2天内）

❌ 丢弃（陈旧事件）：
  - {stale_interim}业绩（报告期过旧，即便文章是新发布的也丢弃）
  - {stale_annual_cutoff}年及更早的全年/年报
  - 任何季报/中报若报告期超过9个月

示例：今日{now.strftime("%Y年%m月")}，"{prev_year}年中期业绩"属陈旧事件 → 丢弃；
     "{prev_year}年全年业绩"在{year}年Q1披露属正常 → 接受

【处理规则】
1. 优先处理今日和昨日新闻
2. 去重：同一事件（如同一家公司同一报告期业绩）只保留信息最全版本，不重复输出不同报告期
3. 提炼：每条压缩为1-2句，保留关键数字，删除来源标签和修饰语
4. 无实质新闻则静默跳过，不输出该公司
美股新闻为英文，需翻译为繁體中文后提炼。

【输出格式，严格遵守】
🔸[公司名]：1）[内容含关键数字] 2）[第二条，如有]
禁止：来源名称、省略号截断、研报标题原文、超过3条编号、投行评级内容
如无实质新闻：仅输出 NO_NEWS"""


# ─────────────────────────────────────────────
# 历史去重
# ─────────────────────────────────────────────

def load_seen_events() -> dict:
    if SEEN_EVENTS_PATH.exists():
        try:
            return json.loads(SEEN_EVENTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_seen_events(events: dict):
    SEEN_EVENTS_PATH.write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cleanup_old_events(events: dict) -> dict:
    cutoff = (datetime.now() - timedelta(days=CLEANUP_DAYS)).strftime("%Y-%m-%d")
    return {k: v for k, v in events.items() if k >= cutoff}


def get_history_context(events: dict) -> str:
    """返回过去 HISTORY_DAYS 天的历史播报字符串"""
    lines = []
    for i in range(1, HISTORY_DAYS + 1):
        date_key = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        if date_key in events:
            for ev in events[date_key]:
                lines.append(f"[{date_key}] {ev}")
    if not lines:
        return "（无历史播报）"
    return "\n".join(lines)


def append_today_events(events: dict, new_items: list[str]) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    existing = events.get(today, [])
    events[today] = list(set(existing + new_items))
    return events


# ─────────────────────────────────────────────
# 新闻日期解析与过滤
# ─────────────────────────────────────────────

def _parse_news_date(time_str: str) -> Optional[datetime]:
    """尝试解析新闻时间字符串，返回 datetime 或 None"""
    if not time_str:
        return None
    s = time_str.strip()
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",   # RSS: Thu, 18 Mar 2026 14:30:00 +0000
        "%a, %d %b %Y %H:%M:%S %Z",   # RSS with TZ name (GMT)
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M",
        "%Y%m%d%H%M%S",
        "%d %b %Y %H:%M:%S %z",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, AttributeError):
            continue
    # email.utils 解析 RFC2822（feedparser标准格式）
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s)
    except Exception:
        pass
    # 最后尝试 dateutil（如已安装）
    try:
        from dateutil import parser as du
        return du.parse(s)
    except Exception:
        pass
    return None


def filter_fresh_news(news_items: list[dict], days: int = NEWS_FRESHNESS_DAYS) -> list[dict]:
    """
    过滤掉超过 days 天前的新闻。
    - 能解析日期：按日期过滤
    - 无法解析日期：标记为"日期未知"并保留，交由 LLM 根据内容判断
    """
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    cutoff_naive = cutoff.replace(tzinfo=None)
    fresh = []
    for item in news_items:
        raw_time = item.get("time", "")
        dt = _parse_news_date(raw_time)
        if dt is None:
            # 无法解析日期：标记后保留，让 LLM 凭内容判断
            item = dict(item)
            item["time"] = f"日期未知（原始：{raw_time[:30]}）" if raw_time else "日期未知"
            fresh.append(item)
            logger.debug(f"  [日期解析失败] {raw_time!r} → 保留，交LLM判断")
            continue
        dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
        if dt_naive >= cutoff_naive:
            fresh.append(item)
        else:
            logger.debug(f"  [过滤旧新闻] {dt_naive.date()} | {item.get('title','')[:40]}")
    return fresh


def filter_stale_financial_content(news_items: list[dict]) -> list[dict]:
    """
    Python层内容关键词过滤：拦截标题中明确属于陈旧报告期的财报新闻。
    逻辑与 _build_system_prompt 保持一致，作为双重保障。
    """
    import re
    now = datetime.now()
    year = now.year      # 2026
    month = now.month    # 3

    stale_patterns = []

    # ── 陈旧的年度业绩 ──────────────────────────────────────────────────────
    # 规则：年报只接受 prev_year（2025）；prev2_year（2024）及更早是旧的
    for stale_year in range(2020, year - 1):  # 2020~2024
        stale_patterns.append(
            re.compile(rf"{stale_year}.{{0,4}}(全年|年度|年报|年結|年绩|annual)", re.IGNORECASE)
        )

    # ── 陈旧的中期/半年/季报 ────────────────────────────────────────────────
    if month <= 6:
        # 现在是上半年：上一年H1/中期是旧的（2025H1在2025年8月披露，距今>6月）
        prev_year = year - 1  # 2025
        for pattern_str in [
            rf"{prev_year}.{{0,4}}(中期|半年|[Hh]1|上半年|interim)",
            rf"{prev_year}.{{0,2}}H1",
        ]:
            stale_patterns.append(re.compile(pattern_str, re.IGNORECASE))
        # prev2_year 及更早的任何中期都是旧的
        for stale_year in range(2020, year - 1):
            stale_patterns.append(
                re.compile(rf"{stale_year}.{{0,4}}(中期|半年|[Hh]1|上半年|interim)", re.IGNORECASE)
            )
    else:
        # 现在是下半年：2年前及更早的中期是旧的
        for stale_year in range(2020, year - 1):
            stale_patterns.append(
                re.compile(rf"{stale_year}.{{0,4}}(中期|半年|[Hh]1|上半年|interim)", re.IGNORECASE)
            )

    fresh = []
    for item in news_items:
        title = item.get("title", "") + " " + item.get("content", "")
        matched = False
        for pat in stale_patterns:
            if pat.search(title):
                logger.debug(f"  [内容过滤-陈旧财报] 匹配「{pat.pattern}」→ {item.get('title','')[:50]}")
                matched = True
                break
        if not matched:
            fresh.append(item)
    return fresh


def fmt_news_with_date(news_items: list[dict]) -> str:
    """将新闻列表格式化为带日期标签的文本（方便 LLM 判断新鲜度）"""
    lines = []
    for i, item in enumerate(news_items[:15], 1):
        raw_time = item.get("time", "")
        # 只取前16字符，避免时区信息太长
        date_tag = raw_time[:16] if raw_time else "日期未知"
        title = item.get("title", "")
        content = item.get("content", "")
        lines.append(f"[{i}][{date_tag}] {title} | {content}")
    return "\n".join(lines)


def extract_event_keywords(llm_output: str) -> list[str]:
    """从 LLM 输出中提取关键事件描述（用于写入 seen_events）"""
    keywords = []
    for line in llm_output.splitlines():
        line = line.strip()
        if line.startswith("🔸") and "：" in line:
            # 取公司名 + 第一条内容作为 key
            part = line.lstrip("🔸").strip()
            keywords.append(part[:80])  # 截断避免过长
    return keywords


# ─────────────────────────────────────────────
# LLM 客户端（DeepSeek / Kimi）
# ─────────────────────────────────────────────

def _call_llm(
    messages: list[dict],
    provider: str = "deepseek",
    model: str = None,
    max_tokens: int = 512,
    temperature: float = 0.3,
) -> str:
    """调用 OpenAI 兼容接口（DeepSeek / Kimi 均使用相同格式）"""
    import httpx

    if provider == "deepseek":
        api_key = os.environ["DEEPSEEK_API_KEY"]
        base_url = "https://api.deepseek.com/v1"
        model = model or "deepseek-chat"
    elif provider == "kimi":
        api_key = os.environ["KIMI_API_KEY"]
        base_url = "https://api.moonshot.cn/v1"
        model = model or "moonshot-v1-8k"
    else:
        raise ValueError(f"未知 provider: {provider}")

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def call_llm_with_fallback(
    messages: list[dict],
    max_tokens: int = 512,
    retry_wait: int = 60,
) -> tuple[str, str]:
    """
    先用 DeepSeek，限流(429)时等待 retry_wait 秒后切换 Kimi。
    返回 (output_text, provider_used)
    """
    # 尝试 DeepSeek
    try:
        output = _call_llm(messages, provider="deepseek", max_tokens=max_tokens)
        return output, "deepseek"
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "rate limit" in err_str.lower() or "limit" in err_str.lower():
            logger.warning(f"[LLM] DeepSeek 限流，等待 {retry_wait}s 后切换 Kimi...")
            time.sleep(retry_wait)
        else:
            logger.warning(f"[LLM] DeepSeek 失败: {e}，切换 Kimi")

    # 切换 Kimi
    try:
        output = _call_llm(messages, provider="kimi", max_tokens=max_tokens)
        return output, "kimi"
    except Exception as e:
        logger.error(f"[LLM] Kimi 也失败: {e}")
        raise


# ─────────────────────────────────────────────
# 单只股票提炼
# ─────────────────────────────────────────────

def refine_stock_news(
    company_name: str,
    news_items: list[dict],
    history_context: str,
    is_us: bool = False,
    today_str: str = "",
) -> Optional[str]:
    """
    返回格式化字符串如 "🔸腾讯：1）..."，或 None（无实质新闻）
    """
    if not news_items:
        return None

    # 第一层：按发布日期过滤
    fresh_items = filter_fresh_news(news_items, days=NEWS_FRESHNESS_DAYS)
    # 第二层：按内容关键词过滤陈旧财报期
    fresh_items = filter_stale_financial_content(fresh_items)
    if not fresh_items:
        logger.debug(f"  [{company_name}] 无近 {NEWS_FRESHNESS_DAYS} 天新鲜新闻，跳过")
        return None

    today_str = today_str or datetime.now().strftime("%Y-%m-%d")
    lang_note = "（以下为英文新闻，请翻译为繁體中文后提炼）" if is_us else ""

    # 构建带日期标签的新闻文本，帮助 LLM 判断新鲜度
    news_text = fmt_news_with_date(fresh_items)

    user_prompt = f"""今日日期：{today_str}
公司：{company_name}

[历史播报（近3天已播出事件，相同事实请勿重复输出）]
{history_context}

[待处理新闻]{lang_note}
（每条格式：[序号][发布日期] 标题 | 摘要，超过2天前的请直接跳过）
{news_text}

请按格式输出，无实质新闻输出 NO_NEWS。"""

    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": user_prompt},
    ]

    try:
        output, provider = call_llm_with_fallback(messages, max_tokens=300)
        logger.debug(f"  [{company_name}] LLM({provider}): {output[:60]}...")
        if "NO_NEWS" in output or not output.strip():
            return None
        return output.strip()
    except Exception as e:
        logger.error(f"[LLM/{company_name}] {e}")
        return None


# ─────────────────────────────────────────────
# 批量提炼所有个股
# ─────────────────────────────────────────────

def refine_all_stocks(all_news: dict, llm_interval: float = 1.0) -> list[str]:
    """
    all_news 结构: {"hk": {...}, "a": {...}, "us": {...}}
    返回: ["🔸腾讯：...", "🔸小米：...", ...]
    """
    seen_events = load_seen_events()
    seen_events = cleanup_old_events(seen_events)
    history_context = get_history_context(seen_events)
    today_str = datetime.now().strftime("%Y-%m-%d")

    outputs = []
    new_event_keywords = []

    for market, stocks in [("hk", all_news.get("hk", {})),
                            ("a",  all_news.get("a", {})),
                            ("us", all_news.get("us", {}))]:
        is_us = (market == "us")
        for code, info in stocks.items():
            name = info.get("name", code)
            news = info.get("news", [])
            if not news:
                logger.debug(f"  [{name}] 无新闻，跳过")
                continue

            logger.info(f"  提炼 [{name}]（{len(news)} 条，过滤前）...")
            result = refine_stock_news(name, news, history_context,
                                       is_us=is_us, today_str=today_str)
            if result:
                outputs.append(result)
                new_event_keywords.extend(extract_event_keywords(result))
            time.sleep(llm_interval)

    # 更新历史去重记录
    if new_event_keywords:
        seen_events = append_today_events(seen_events, new_event_keywords)
        save_seen_events(seen_events)
        logger.info(f"已写入 {len(new_event_keywords)} 条事件记录到 seen_events.json")

    return outputs
