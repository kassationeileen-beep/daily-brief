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
    manual_items: list[str] = None,
) -> Optional[str]:
    """
    返回格式化字符串如 "🔸腾讯：1）..."，或 None（无实质新闻）。
    manual_items: 人工精选输入，优先级最高，跳过重要性过滤，全部保留。
    """
    manual_items = manual_items or []

    if not news_items and not manual_items:
        return None

    # 第一层：按发布日期过滤（仅对自动抓取）
    fresh_items = filter_fresh_news(news_items, days=NEWS_FRESHNESS_DAYS)
    # 第二层：按内容关键词过滤陈旧财报期（仅对自动抓取）
    fresh_items = filter_stale_financial_content(fresh_items)

    # 无自动新闻 + 无人工输入 → 跳过
    if not fresh_items and not manual_items:
        logger.debug(f"  [{company_name}] 无近 {NEWS_FRESHNESS_DAYS} 天新鲜新闻，跳过")
        return None

    today_str = today_str or datetime.now().strftime("%Y-%m-%d")
    lang_note = "（以下自动抓取为英文新闻，请翻译为繁體中文后提炼）" if is_us else ""

    # 人工输入段落（若有）
    manual_section = ""
    if manual_items:
        manual_lines = "\n".join(f"- {item.strip()}" for item in manual_items)
        manual_section = (
            f"\n[人工輸入（用戶已篩選，優先級最高，全部保留，無需判斷重要性）]\n"
            f"{manual_lines}\n"
        )

    # 自动抓取段落
    news_text = fmt_news_with_date(fresh_items) if fresh_items else "（無自動抓取新聞）"

    user_prompt = f"""今日日期：{today_str}
公司：{company_name}

[歷史播報（近3天已播出事件，相同事實請勿重複輸出）]
{history_context}
{manual_section}
[自動抓取新聞]{lang_note}
（每條格式：[序號][發布日期] 標題 | 摘要，超過2天前的請直接跳過）
{news_text}

輸出規則：
- 人工輸入條目：全部保留並輸出，不做重要性過濾
- 自動抓取條目：正常過濾，若與人工輸入描述同一事件，丟棄自動抓取版本
請按格式輸出，無實質新聞輸出 NO_NEWS。"""

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

def refine_all_stocks(
    all_news: dict,
    llm_interval: float = 1.0,
    manual_stock_items: dict = None,
) -> list[str]:
    """
    all_news 结构: {"hk": {...}, "a": {...}, "us": {...}}
    manual_stock_items: {"腾讯"/"0700"/"TSLA": ["文本1", ...], ...}
      - 对 watchlist 内的公司：人工输入与自动抓取合并，人工优先
      - 对 watchlist 外的公司：仅人工输入，直接进 LLM，无自动新闻
    返回: ["🔸腾讯：...", "🔸小米：...", ...]
    """
    manual_stock_items = manual_stock_items or {}

    seen_events = load_seen_events()
    seen_events = cleanup_old_events(seen_events)
    history_context = get_history_context(seen_events)
    today_str = datetime.now().strftime("%Y-%m-%d")

    outputs = []
    new_event_keywords = []
    processed_manual_keys: set[str] = set()

    for market, stocks in [("hk", all_news.get("hk", {})),
                            ("a",  all_news.get("a", {})),
                            ("us", all_news.get("us", {}))]:
        is_us = (market == "us")
        for code, info in stocks.items():
            name = info.get("name", code)
            news = info.get("news", [])

            # 查找人工输入：按公司名或代码匹配，去重保留顺序
            manual = list(dict.fromkeys(
                manual_stock_items.get(name, []) + manual_stock_items.get(code, [])
            ))
            processed_manual_keys.add(name)
            processed_manual_keys.add(code)

            if not news and not manual:
                logger.debug(f"  [{name}] 无新闻，跳过")
                continue

            logger.info(
                f"  提炼 [{name}]（自动{len(news)}条 + 人工{len(manual)}条）..."
            )
            result = refine_stock_news(
                name, news, history_context,
                is_us=is_us, today_str=today_str,
                manual_items=manual,
            )
            if result:
                outputs.append(result)
                new_event_keywords.extend(extract_event_keywords(result))
            time.sleep(llm_interval)

    # 处理 watchlist 外的人工输入公司
    extra = {k: v for k, v in manual_stock_items.items()
             if k not in processed_manual_keys and v}
    for company, manual in extra.items():
        manual = list(dict.fromkeys(manual))  # 去重
        logger.info(f"  提炼 [{company}]（人工输入，watchlist外，{len(manual)}条）...")
        result = refine_stock_news(
            company, [], history_context,
            is_us=False, today_str=today_str,
            manual_items=manual,
        )
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


# ─────────────────────────────────────────────
# 第三部分：宏观 & 行业新闻摘要
# ─────────────────────────────────────────────

_MACRO_SYSTEM_PROMPT = """你是一名服务香港证券从业者的资深财经编辑。
你的任务是从提供的英文/中文新闻列表中，提取出对港股/A股投资者最重要的宏观经济和行业动态，
输出一份简洁的中文要点摘要。

输出要求：
- 输出3-5条要点，每条以"• "开头
- 每条不超过60个中文字
- 优先关注：美联储/央行动向、中国经济数据、贸易/关税政策、能源/大宗商品、港股相关监管政策
- 使用繁体中文
- 如无重要事件，输出"• 暫無重要宏觀動態"
- 不要添加标题行，直接输出要点列表"""


def refine_macro_news(
    news_items: list[dict],
    max_items: int = 20,
    manual_items: list[str] = None,
) -> str:
    """
    将宏观新闻列表 → LLM → 3-5条中文要点摘要。
    manual_items: 人工精选宏观内容，优先级最高，全部保留，不经重要性过滤。
    返回格式化的第三部分文本块，或降级纯文本摘要。
    """
    manual_items = manual_items or []

    if not news_items and not manual_items:
        return "▶️三、*宏觀及行業動態*\n• 暫無重要宏觀動態"

    items_to_use = news_items[:max_items]
    news_text = "\n".join(
        f"[{i+1}][{item.get('source','')}] {item.get('title','')} | {item.get('content','')[:150]}"
        for i, item in enumerate(items_to_use)
    )

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    # 人工精选段落
    manual_section = ""
    if manual_items:
        manual_lines = "\n".join(f"- {item.strip()}" for item in manual_items)
        manual_section = (
            f"[人工精選（用戶已篩選，全部保留，優先顯示，不做重要性過濾）]\n"
            f"{manual_lines}\n\n"
        )

    user_content = (
        f"今日日期：{date_str}\n\n"
        + manual_section
        + f"[自動抓取新聞（共{len(items_to_use)}條，作為補充）]\n"
        + news_text
        + "\n\n輸出規則：\n"
        + "1. 人工精選條目必須全部輸出（不過濾、不省略）\n"
        + "2. 自動抓取條目：正常過濾，若與人工精選描述同一事件，丟棄自動抓取版本\n"
        + f"3. 合併後輸出3-{max(6, len(manual_items) + 3)}條要點"
        + "（繁體中文，每條以'• '開頭）："
    )

    messages = [
        {"role": "system", "content": _MACRO_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        output, provider = call_llm_with_fallback(messages, max_tokens=500)
        logger.info(f"[MacroRefine] LLM({provider}) 生成宏观摘要 {len(output)} 字")
        return f"▶️三、*宏觀及行業動態*\n{output}"
    except Exception as e:
        logger.error(f"[MacroRefine] LLM 失败: {e}，降级输出标题列表")
        fallback_lines = [f"• [精選] {item[:60]}" for item in manual_items[:3]]
        fallback_lines += [f"• {item['title'][:60]}" for item in items_to_use[:max(0, 5 - len(fallback_lines))]]
        return "▶️三、*宏觀及行業動態*\n" + "\n".join(fallback_lines)


def classify_unclassified_items(items: list[str]) -> dict:
    """
    用 LLM 对无标签的人工输入消息进行分类。
    返回: {"macro": [...], "stocks": {"公司名": [...]}, "ipo": [...]}
    """
    if not items:
        return {"macro": [], "stocks": {}, "ipo": []}

    import re as _re

    items_text = "\n".join(f"[{i+1}] {item.strip()}" for i, item in enumerate(items))

    messages = [
        {
            "role": "system",
            "content": (
                "你是财经新闻分类助手。将每条新闻归类为以下三类之一：\n"
                "- macro：宏观经济、央行政策、汇率、大宗商品、行业政策\n"
                "- stocks：个股动态（需识别公司名称）\n"
                "- ipo：新股、招股、IPO\n\n"
                "严格输出JSON，格式如下（不加任何说明文字）：\n"
                '{"macro": ["原文1"], "stocks": {"公司名": ["原文"]}, "ipo": ["原文"]}'
            ),
        },
        {
            "role": "user",
            "content": f"以下是待分类的新闻条目：\n{items_text}\n\n请输出JSON分类结果：",
        },
    ]

    try:
        output, provider = call_llm_with_fallback(messages, max_tokens=600)
        # 提取 JSON 部分（防止 LLM 在前后加说明文字）
        json_match = _re.search(r'\{.*\}', output, _re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            macro_cnt = len(result.get("macro", []))
            stock_cnt = len(result.get("stocks", {}))
            ipo_cnt = len(result.get("ipo", []))
            logger.info(
                f"[ManualClassify] LLM({provider}) 分类完成: "
                f"宏观{macro_cnt}条 个股{stock_cnt}家 IPO{ipo_cnt}条"
            )
            return {
                "macro": result.get("macro", []),
                "stocks": result.get("stocks", {}),
                "ipo": result.get("ipo", []),
            }
    except Exception as e:
        logger.error(f"[ManualClassify] 分类失败: {e}，未分类条目全部归入宏观")

    # 降级：全部归入宏观（保守处理，确保不丢失）
    return {"macro": items, "stocks": {}, "ipo": []}
