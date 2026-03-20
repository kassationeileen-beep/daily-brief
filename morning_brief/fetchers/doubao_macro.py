"""
doubao_macro.py — 豆包（火山方舟）API 调用模块
用于抓取需要实时搜索的内容：中国宏观、全球宏观、港股今日招股。

设计原则：
- 每个函数独立调用，固定 system prompt + user prompt，不依赖对话历史
- 避免对话界面的"模板漂移"问题
- 需要使用带联网搜索功能的 Bot（bot-xxx-xxx 格式的 model ID）
- 三个函数互为独立，任一失败不影响其他

环境变量：
  ARK_API_KEY        火山方舟 API Key（必须）
  DOUBAO_BOT_MACRO   宏观+招股用的 Bot ID，e.g. bot-20250101120000-xxxxx
                     若宏观和招股使用不同 bot，可拆分为：
  DOUBAO_BOT_MACRO_CN     中国宏观 Bot ID（可选，优先于 DOUBAO_BOT_MACRO）
  DOUBAO_BOT_MACRO_GLOBAL 全球宏观 Bot ID（可选，优先于 DOUBAO_BOT_MACRO）
  DOUBAO_BOT_IPO          招股 Bot ID（可选，优先于 DOUBAO_BOT_MACRO）
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# API 常量
# ─────────────────────────────────────────────

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ARK_BOT_ENDPOINT = f"{ARK_BASE_URL}/bots/chat/completions"
ARK_CHAT_ENDPOINT = f"{ARK_BASE_URL}/chat/completions"

# 默认超时（秒）：联网搜索比普通 LLM 慢，给足时间
DEFAULT_TIMEOUT = 90


# ─────────────────────────────────────────────
# 固定 Prompt 定义
# ─────────────────────────────────────────────

# ── 中国宏观 ────────────────────────────────
_SYSTEM_MACRO_CN = """你是服务香港证券从业者的专业金融早报编辑。
每日早报发布时间：北京时间 07:30。

任务：搜索今日中国宏观要闻，整理为结构化要点。

【输出格式，严格遵守】
🔸中國宏觀（3條左右）
1）[主題標題]
* [具體內容，保留關鍵數字和政策細節]
2）[主題標題]
* [具體內容]
3）[主題標題]（如有）
* [具體內容]

【重點關注，按優先級】
- 貨幣政策：LPR、MLF、逆回購利率、存準率、人民銀行公告
- 財政政策：財政部、發改委、工信部重大政策發布
- 經濟數據：CPI、PPI、PMI、進出口、GDP等統計局數據
- 流動性：央行公開市場操作規模、資金面
- 關鍵行業：房地產、科技、能源重大政策動向

【約束】
- 使用繁體中文
- 數字保留具體值（如 3.0%、375億元）
- 每個主題下1-3個子要點
- 不輸出解釋性前言後語
- 不輸出「暫無重要事件」等佔位語句；若真無重要事件，輸出「• 今日暫無重要中國宏觀動態」"""

_USER_MACRO_CN = "今天是{date}（北京時間早上），請搜索今日中國宏觀要聞，按格式輸出。"

# ── 全球宏观 ────────────────────────────────
_SYSTEM_MACRO_GLOBAL = """你是服务香港证券从业者的专业金融早报编辑。
每日早报发布时间：北京时间 07:30。

任务：搜索今日全球宏观要闻，整理为结构化要点。

【输出格式，严格遵守】
🔸全球宏觀（3-5條）
1）[主題/機構名稱]
* [具體內容，含關鍵數字]
2）[主題/機構名稱]
* [具體內容]
（視重要性可增減，3-5條）

【重點關注，按優先級】
- 美聯儲：利率決議、FOMC聲明、官員講話及市場隱含預期
- 各地央行：歐央行、日央行、英央行等重大決策或表態
- 能源與大宗商品：原油（WTI/布蘭特）、工業金屬、黃金的價格驅動因素
- 地緣政治：中東、俄烏、全球貿易摩擦等影響市場的事件
- 美國/歐洲/日本重磅經濟數據：非農、CPI、PMI等

【約束】
- 使用繁體中文
- 數字保留具體值（如 3.5%–3.75%、WTI $72/桶）
- 每個主題下1-3個子要點
- 不輸出解釋性前言後語
- 若真無重要事件，輸出「• 今日暫無重要全球宏觀動態」"""

_USER_MACRO_GLOBAL = "今天是{date}（北京時間早上），請搜索今日全球宏觀要聞（美聯儲、各地央行、能源大宗、地緣政治），按格式輸出。"

# ── 港股今日招股 ─────────────────────────────
_SYSTEM_IPO = """你是服务香港证券从业者的专业金融早报编辑。
每日早报发布时间：北京时间 07:30。

任务：搜索今日（认购期包含今日）的港股新股认购（IPO申购）信息，输出详细介绍。

【输出格式，严格遵守——每只新股输出以下结构】
📅 今日（{month}月{day}日）港股招股：[公司名]（[股票代碼].HK）
公司介紹：[業務定位、核心優勢、市場地位，1-2句]
財務數據：[最近完整財年營收、淨利潤、毛利率；若有最新季度/半年數據也列出，並標明同比趨勢]
• 招股期：[開始日期]—[截止日期]
• 全球發售：[發售股數及港股/國際配比]
• 發行價：定價[X]港元/股，每手[X]股
• 基石投資：[基石投資者名稱]，認購金額：[金額]（如有）
• 獨家保薦：[保薦人]（如有多家列出）
• 定價日：[日期]；上市日：[日期]
• 募資用途：[主要用途，1句]

【若今日有多只新股，逐一輸出，中間用空行分隔】
【若今日無新股認購，輸出：今日無港股新股認購】
【約束】
- 使用繁體中文
- 數字保留具體值
- 不輸出解釋性前言後語"""

_USER_IPO = "今天是{date}，請搜索今日（{month}月{day}日）仍在認購期的港股新股（IPO申購）信息，按格式輸出。"


# ─────────────────────────────────────────────
# 核心 API 调用
# ─────────────────────────────────────────────

def _call_doubao(
    system_prompt: str,
    user_prompt: str,
    model_id: str,
    max_tokens: int = 800,
    temperature: float = 0.2,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """
    调用火山方舟 API（支持 bot-xxx 和 ep-xxx 两种 model ID）。
    bot-xxx：使用 /bots/chat/completions 端点（支持联网搜索）
    ep-xxx / 其他：使用 /chat/completions 端点
    """
    try:
        import httpx
    except ImportError:
        raise RuntimeError("缺少 httpx 依赖，请 pip install httpx")

    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        raise RuntimeError("未设置环境变量 ARK_API_KEY")

    # 根据 model_id 格式选择端点
    endpoint = ARK_BOT_ENDPOINT if model_id.startswith("bot-") else ARK_CHAT_ENDPOINT

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _get_bot_id(env_var: str, fallback_env: str = "DOUBAO_BOT_MACRO") -> Optional[str]:
    """按优先级读取 Bot ID：专用变量 > 通用变量"""
    return os.environ.get(env_var) or os.environ.get(fallback_env)


# ─────────────────────────────────────────────
# 三个独立查询函数
# ─────────────────────────────────────────────

def fetch_doubao_macro_cn(date_hkt: datetime = None) -> Optional[str]:
    """
    用豆包 API 获取今日中国宏观要闻。
    返回格式化文本（以 🔸中國宏觀 开头），或 None（失败时）。
    """
    bot_id = _get_bot_id("DOUBAO_BOT_MACRO_CN")
    if not bot_id:
        logger.debug("[DoubaoMacro-CN] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")
    user_prompt = _USER_MACRO_CN.format(date=date_str)

    try:
        output = _call_doubao(_SYSTEM_MACRO_CN, user_prompt, bot_id, max_tokens=600)
        logger.info(f"[DoubaoMacro-CN] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoMacro-CN] 调用失败: {e}")
        return None


def fetch_doubao_macro_global(date_hkt: datetime = None) -> Optional[str]:
    """
    用豆包 API 获取今日全球宏观要闻。
    返回格式化文本（以 🔸全球宏觀 开头），或 None（失败时）。
    """
    bot_id = _get_bot_id("DOUBAO_BOT_MACRO_GLOBAL")
    if not bot_id:
        logger.debug("[DoubaoMacro-Global] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")
    user_prompt = _USER_MACRO_GLOBAL.format(date=date_str)

    try:
        output = _call_doubao(_SYSTEM_MACRO_GLOBAL, user_prompt, bot_id, max_tokens=800)
        logger.info(f"[DoubaoMacro-Global] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoMacro-Global] 调用失败: {e}")
        return None


def fetch_doubao_ipo(date_hkt: datetime = None) -> Optional[str]:
    """
    用豆包 API 获取今日港股新股认购详细信息。
    返回格式化文本（含公司介绍/财务数据/招股细节），或 None（失败时）。
    """
    bot_id = _get_bot_id("DOUBAO_BOT_IPO")
    if not bot_id:
        logger.debug("[DoubaoIPO] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")
    month = str(date_hkt.month)
    day = str(date_hkt.day)

    # system prompt 中的 {month}/{day} 占位符（用于标题格式）
    system_prompt = _SYSTEM_IPO.replace("{month}", month).replace("{day}", day)
    user_prompt = _USER_IPO.format(date=date_str, month=month, day=day)

    try:
        output = _call_doubao(system_prompt, user_prompt, bot_id, max_tokens=1000)
        logger.info(f"[DoubaoIPO] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoIPO] 调用失败: {e}")
        return None


# ─────────────────────────────────────────────
# 组合宏观段落
# ─────────────────────────────────────────────

def build_doubao_macro_section(
    cn_text: Optional[str],
    global_text: Optional[str],
    date_hkt: datetime = None,
) -> Optional[str]:
    """
    将中国宏观 + 全球宏观合并为第三部分文本块。
    两者都为 None 时返回 None（表示豆包全部失败，交由调用方降级）。
    """
    if not cn_text and not global_text:
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_label = f"{date_hkt.month}月{date_hkt.day}日"
    header = f"▶️三、*{date_label} 宏觀要聞*"

    parts = [header]
    if cn_text:
        parts.append(cn_text)
    else:
        parts.append("🔸中國宏觀\n• ⚠️ 數據獲取失敗，請手動補充")

    if global_text:
        parts.append(global_text)
    else:
        parts.append("🔸全球宏觀\n• ⚠️ 數據獲取失敗，請手動補充")

    return "\n\n".join(parts)


# ─────────────────────────────────────────────
# 格式化招股段落
# ─────────────────────────────────────────────

def fmt_doubao_ipo_section(doubao_ipo_text: str) -> str:
    """将豆包 IPO 文本包装为第五部分标题块"""
    return f"▶️五、*今日招股（新股認購）*\n{doubao_ipo_text}"
