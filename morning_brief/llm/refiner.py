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

SYSTEM_PROMPT = """你是专业金融早报编辑。将原始财经新闻提炼为机构投资者早报格式。

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
- 已在[历史播报]中出现的事件（核心事实相同即为重复，不看标题措辞）

【处理规则】
1. 去重：同一事件多渠道报道只保留信息最全版本
2. 提炼：每条压缩为1-2句，保留关键数字，删除来源标签和修饰语
3. 无实质新闻则静默跳过，不输出该公司
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
) -> Optional[str]:
    """
    返回格式化字符串如 "🔸腾讯：1）..."，或 None（无实质新闻）
    """
    if not news_items:
        return None

    # 构建新闻文本
    news_text = "\n".join(
        f"[{i+1}] 标题: {item.get('title', '')} | 内容: {item.get('content', '')}"
        for i, item in enumerate(news_items[:15])
    )
    lang_note = "（以下为英文新闻，请翻译为繁體中文后提炼）" if is_us else ""

    user_prompt = f"""公司：{company_name}

[历史播报]
{history_context}

[最新新闻]{lang_note}
{news_text}

请按格式输出，无实质新闻输出 NO_NEWS。"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
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

            logger.info(f"  提炼 [{name}]（{len(news)} 条新闻）...")
            result = refine_stock_news(name, news, history_context, is_us=is_us)
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
