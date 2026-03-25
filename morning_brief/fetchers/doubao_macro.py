"""
doubao_macro.py — 豆包（火山方舟）API 调用模块
用于抓取需要实时搜索的内容：中国宏观、全球宏观、港股今日招股、港股回购。

设计原则：
- 每个函数独立调用，固定 system prompt + user prompt，不依赖对话历史
- 避免对话界面的"模板漂移"问题
- 需要使用带联网搜索功能的 Bot（bot-xxx-xxx 格式的 model ID）
- 各函数互为独立，任一失败不影响其他

环境变量：
  ARK_API_KEY        火山方舟 API Key（必须）
  DOUBAO_BOT_MACRO   宏观+招股+回购通用 Bot ID，e.g. bot-20250101120000-xxxxx
                     若各功能使用不同 bot，可拆分为：
  DOUBAO_BOT_MACRO_CN     中国宏观 Bot ID（可选，优先于 DOUBAO_BOT_MACRO）
  DOUBAO_BOT_MACRO_GLOBAL 全球宏观 Bot ID（可选，优先于 DOUBAO_BOT_MACRO）
  DOUBAO_BOT_IPO          招股 Bot ID（可选，优先于 DOUBAO_BOT_MACRO）
  DOUBAO_BOT_BUYBACK      回购 Bot ID（可选，优先于 DOUBAO_BOT_MACRO）
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

任务：搜索过去24小时内发布的中国宏观要闻，精选对港股/A股有实质影响的事件，整理为结构化要点。
时间范围：严格限定为过去24小时内披露/发布的信息，不得引用更早的旧闻。

【輸出格式，嚴格遵守】
🔸中國宏觀（嚴格最多3條，寧少勿多）
1）[主題標題]
* [具體內容，保留關鍵數字和政策細節]
2）[主題標題]（如有）
* [具體內容]
3）[主題標題]（如有）
* [具體內容]

【優先選取，按重要性排序】
① 貨幣政策調整：LPR/MLF/逆回購**利率變動**、存準率調整、人民銀行重大表態
② 重磅經濟數據：CPI、PPI、PMI、進出口、GDP等統計局正式發布
③ 財政部/發改委/工信部**新出台**的重大政策（非例行表態）
④ 房地產、科技、能源等關鍵行業的重大政策轉向

【以下情況不計入，即使發生也不輸出】
✗ 央行日常例行逆回購操作（未調整利率或規模的常規操作）
✗ 官員在論壇/會議上的一般性表態（非新政策發布）
✗ 券商、機構的市場研判或年度展望
✗ 農業、農村、教育等對資本市場影響極低的政策

【約束】
- 使用繁體中文
- 數字保留具體值（如 3.0%、375億元）
- 每個主題下1-3個子要點
- 不輸出解釋性前言後語
- 若過去24小時內符合優先級的事件不足3條，如實輸出1-2條，不要湊數
- 過去24小時內無符合條件事件時，輸出「• 過去24小時暫無重要中國宏觀動態」"""

_USER_MACRO_CN = "今天是{date}，現在是北京時間07:30，請搜索過去24小時（昨日07:30至今）內發布的中國宏觀要聞，嚴格限定在此時間範圍，按格式輸出。"

# ── 全球宏观 ────────────────────────────────
_SYSTEM_MACRO_GLOBAL = """你是服务香港证券从业者的专业金融早报编辑。
每日早报发布时间：北京时间 07:30。

任务：搜索过去24小时内发布的全球宏观要闻，精选对港股/亚太市场有实质影响的事件，整理为结构化要点。
时间范围：严格限定为过去24小时内披露/发布的信息，不得引用更早的旧闻。

【輸出格式，嚴格遵守】
🔸全球宏觀（嚴格最多4條，寧少勿多）
1）[主題/機構名稱]
* [具體內容，含關鍵數字]
2）[主題/機構名稱]
* [具體內容]
（視重要性可增減，最多4條）

【優先選取，按重要性排序】
① 美聯儲：利率決議、FOMC聲明、主席記者會、官員重要表態及利率路徑預期變化
② 各地主要央行：歐央行、日央行、英央行等重大決策或政策立場轉變
③ 地緣政治：中美關係、關稅貿易摩擦、中東/俄烏等直接影響市場的重大事件
④ 美國/歐洲/日本重磅經濟數據正式發布：非農、CPI、PCE、PMI等

【以下情況不計入】
✗ 原油、黃金、工業金屬等商品的日常漲跌（價格數據已在早報第一部分呈現）
✗ 央行官員的例行、模糊表態（無新政策信息）
✗ 非主要市場的地區性事件（對港股影響極低）

【約束】
- 使用繁體中文
- 數字保留具體值（如 4.25%–4.50%、非農+27.5萬）
- 每個主題下1-3個子要點
- 不輸出解釋性前言後語
- 若符合條件的事件不足4條，如實輸出，不要湊數
- 過去24小時內無重要事件時，輸出「• 過去24小時暫無重要全球宏觀動態」"""

_USER_MACRO_GLOBAL = "今天是{date}，現在是北京時間07:30，請搜索過去24小時（昨日07:30至今）內發布的全球宏觀要聞（美聯儲、各地央行、能源大宗、地緣政治），嚴格限定在此時間範圍，按格式輸出。"

# ── 港股今日招股 ─────────────────────────────
# Python 根据 DATES 元数据行判断首日/续期，分别格式化
_DETAIL_BLOCK_FORMAT = """\
📅 公司名（代碼.HK）
公司介紹：業務定位、核心優勢、市場地位（1-2句）
財務數據：最近完整財年營收、淨利潤、毛利率（如有最新季度/半年數據也列出）
• 招股期：起始日—截止日
• 全球發售：發售股數及港股/國際配比
• 發行價：定價X港元/股，每手X股
• 基石投資：投資者名稱及認購金額（有則填，無則省略此行）
• 獨家保薦：保薦人
• 定價日：xxxx；上市日：xxxx
• 募資用途：主要用途（1句）"""

# 豐富模式：DATES 行由系統提供，豆包只補充詳細信息
_SYSTEM_IPO = f"""你是服务香港证券从业者的专业金融早报编辑。
每日早报发布时间：北京时间 07:30。

任务：以下港股新股的代号和招股日期已由系统确认（DATES 行已提供），
请在每个 DATES 行后面补充详细信息。

【每只股票输出格式（DATES 行原样保留，不得修改）】
DATES: 02729|凱樂士科技|2026-03-16|2026-03-20
{_DETAIL_BLOCK_FORMAT}

【約束】
- DATES 行必須保持原樣，不得修改代碼或日期
- 找不到的字段填 N/A，嚴禁使用任何中括號占位符
- 使用繁體中文；數字保留具體值
- 不輸出解釋性前言後語"""

_USER_IPO = (
    "今天是{date}（北京時間07:30）。以下港股今日在認購期內（代號和日期已由系統確認）：\n\n"
    "{dates_block}\n\n"
    "請在每個 DATES 行後補充詳細信息，DATES 行保持原樣不修改。"
)

# 獨立搜索模式：爬蟲無數據時，豆包自行搜索（必須輸出 DATES 行以支持首日/續期判斷）
_SYSTEM_IPO_SEARCH = f"""你是服务香港证券从业者的专业金融早报编辑。
每日早报发布时间：北京时间 07:30。

任务：搜索今日（认购期包含今日）的港股新股认购（IPO申购）信息。

【搜索步骤】
1. 先搜索今日港股认购新股名单（包含首日及续期认购）
2. 对每只找到的新股，进一步搜索其招股书摘要、HKEX公告、港交所披露易及财经媒体（信报、经济日报、阿思达克），获取公司介绍、财务数据、发行价、每手股数、保荐人等详细信息

【每只股票输出格式（每块之间空行分隔，DATES 行必须在每块最前面）】
DATES: 股票代碼（5位含前導零）|公司名稱|認購起始日（YYYY-MM-DD）|認購截止日（YYYY-MM-DD）
{_DETAIL_BLOCK_FORMAT}

【約束】
- 每只股票必須以 DATES: 行開頭，格式嚴格為「DATES: 代碼|名稱|起始日|截止日」，日期格式 YYYY-MM-DD
- 必須搜尋每只股票的招股說明書或HKEX公告以填寫詳細字段，禁止不搜尋就填 N/A
- 若搜尋後確實找不到某字段，才填 N/A；嚴禁使用 [待查]、[TBD] 等占位符
- 若今日確實無新股認購，輸出：今日無港股新股認購
- 使用繁體中文；數字保留具體值
- 不輸出解釋性前言後語"""

_USER_IPO_SEARCH = "今天是{date}（北京時間07:30），請搜索今日（{month}月{day}日）仍在認購期的港股新股，按格式輸出（每只股票必須以 DATES: 行開頭）。包括首日招股和續期招股，請搜索全面，並對每只股票查詢招股書及HKEX公告以獲取完整資料。"

# ── 资金动态 ──────────────────────────────────
# cron 在 07:30 运行，港股/A股尚未开市，需查询最近一个交易日数据
_SYSTEM_MARKET_SUMMARY = """你是港股/A股市場數據助手。
任務：查詢最近一個交易日（非今日，因今日市場尚未開市）的以下市場數據。

【數據來源要求】
- 恒生指數收盤及成交額：必須來自港交所官方數據（hkex.com.hk）或彭博/路透社等主流財經終端，不得估算
- 南向資金：必須來自港交所北向/南向資金披露（hkex.com.hk/mutual-market）或東方財富，不得估算
- 滬深成交額：必須來自上海/深圳交易所官方數據或東方財富、Wind

【輸出格式，嚴格遵守，每行一個字段】
TRADING_DATE: YYYY-MM-DD
HSI_CLOSE: [收盤點數，數字，如 20500.00]
HSI_PCT: [漲跌幅，帶正負號，如 +1.25% 或 -0.88%]
HSI_TURNOVER_100M: [港股全日成交額，單位億港元，數字，正常範圍 500–5000，如 1250.5]
SB_DIRECTION: [南向資金方向，只填「流入」或「流出」]
SB_AMOUNT_100M: [南向資金淨額，絕對值，單位億港元，數字，正常範圍 10–1000，如 125.3。注意：若查到的原始數據單位是萬港元，請除以10,000換算為億港元]
A_TURNOVER_TRILLION: [滬深兩市合計成交額，單位萬億元人民幣，數字，正常範圍 0.8–3.0，如 1.25]

【約束】
- 只輸出上述7行，不輸出任何說明或前言後語
- 所有數字字段只填數字（不帶單位），單位已在字段名中標注
- TRADING_DATE 必須是最近一個有效交易日（排除今日、週末及公眾假期）
- 若查不到來自官方/主流財經終端的可靠數據，填 N/A，不得填入估算值"""

_USER_MARKET_SUMMARY = "今天是{date}（北京時間早上07:30，港股/A股尚未開市），請查詢最近一個交易日的恒生指數、港股成交額、南向資金（北水）、滬深兩市成交額，按格式輸出。"


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

    logger.info(f"[Doubao] endpoint={endpoint} model_prefix={model_id[:12]}... max_tokens={max_tokens} payload_keys={list(payload.keys())}")

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        if not resp.is_success:
            logger.error(f"[Doubao] {resp.status_code} body: {resp.text[:500]}")
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


def fetch_doubao_ipo(
    ipo_list: list[dict] = None,
    date_hkt: datetime = None,
) -> Optional[str]:
    """
    用豆包 API 获取/丰富港股新股认购信息。

    ipo_list 非空 → 丰富模式：DATES 行由爬虫数据预填，豆包只补内容
    ipo_list 为空/None → 独立搜索模式：豆包自行搜索，不输出 DATES 行
    返回格式化文本，或 None（失败时）。
    """
    bot_id = _get_bot_id("DOUBAO_BOT_IPO")
    if not bot_id:
        logger.debug("[DoubaoIPO] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")

    if ipo_list:
        # ── 丰富模式：DATES 行由爬虫提供，代码和日期不依赖豆包 ──
        dates_lines = []
        for s in ipo_list:
            code  = s.get("code", "").zfill(5)
            name  = s.get("name", "")
            start = s.get("sub_start", "")
            end   = s.get("sub_end", "")
            dates_lines.append(f"DATES: {code}|{name}|{start}|{end}")
        dates_block = "\n".join(dates_lines)
        system_prompt = _SYSTEM_IPO
        user_prompt   = _USER_IPO.format(date=date_str, dates_block=dates_block)
        logger.info(f"[DoubaoIPO] 丰富模式，{len(ipo_list)} 只")
    else:
        # ── 独立搜索模式：豆包自行搜索，要求输出 DATES 行（首日/续期判断依赖此）──
        month = str(date_hkt.month)
        day   = str(date_hkt.day)
        system_prompt = _SYSTEM_IPO_SEARCH
        user_prompt   = _USER_IPO_SEARCH.format(date=date_str, month=month, day=day)
        logger.info("[DoubaoIPO] 独立搜索模式（爬虫无数据）")

    try:
        output = _call_doubao(system_prompt, user_prompt, bot_id, max_tokens=4000)
        logger.info(f"[DoubaoIPO] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoIPO] 调用失败: {e}")
        return None


def fetch_doubao_market_summary(date_hkt: datetime = None) -> Optional[str]:
    """
    用豆包 API 查询最近一个交易日的港股/A股资金动态摘要。
    cron 在 07:30 运行，今日市场未开盘，固定查询最近一个已收盘的交易日。
    返回原始 KEY: VALUE 文本（供 parse_market_summary 解析），或 None。
    """
    bot_id = _get_bot_id("DOUBAO_BOT_MARKET", fallback_env="DOUBAO_BOT_MACRO")
    if not bot_id:
        logger.debug("[DoubaoMarket] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")
    user_prompt = _USER_MARKET_SUMMARY.format(date=date_str)

    try:
        output = _call_doubao(_SYSTEM_MARKET_SUMMARY, user_prompt, bot_id, max_tokens=400)
        logger.info(f"[DoubaoMarket] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoMarket] 调用失败: {e}")
        return None


# ─────────────────────────────────────────────
# 资金动态解析
# ─────────────────────────────────────────────

def parse_market_summary(raw_text: str) -> dict:
    """
    解析豆包返回的资金动态 KEY: VALUE 文本，返回与 market_data 兼容的 dict：
    {
      "hsi":        {"close": float, "pct": float, "turnover_hkd_100m": float},
      "southbound": {"net_flow_hkd_100m": float, "direction": "买入"|"卖出"},
      "a_share":    {"total_turnover_trillion": float},
      "trading_date": "2026-03-19",
    }
    缺失/无效字段置为 None，不抛异常。
    """
    def _parse_float(s: str) -> Optional[float]:
        if not s or s.strip().upper() in ("N/A", "NAN", ""):
            return None
        # 去掉 %、+ 号等
        s = s.strip().lstrip("+").rstrip("%").replace(",", "")
        try:
            result = float(s)
            import math
            return None if math.isnan(result) else result
        except ValueError:
            return None

    kv = {}
    for line in raw_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            kv[key.strip()] = val.strip()

    direction_raw = kv.get("SB_DIRECTION", "")
    if "流入" in direction_raw:
        sb_direction = "买入"
    elif "流出" in direction_raw:
        sb_direction = "卖出"
    else:
        sb_direction = None

    # 南向资金单位自动修正：正常值 < 2000 亿港元/天，若返回 > 5000 说明单位是万港元
    sb_amount_raw = _parse_float(kv.get("SB_AMOUNT_100M"))
    if sb_amount_raw is not None and sb_amount_raw > 5000:
        logger.warning(
            f"[DoubaoMarket] SB_AMOUNT={sb_amount_raw} 异常大，疑为万港元，自动÷10000"
        )
        sb_amount_raw = round(sb_amount_raw / 10000, 2)

    return {
        "hsi": {
            "close":               _parse_float(kv.get("HSI_CLOSE")),
            "pct":                 _parse_float(kv.get("HSI_PCT")),
            "turnover_hkd_100m":   _parse_float(kv.get("HSI_TURNOVER_100M")),
        },
        "southbound": {
            "net_flow_hkd_100m":   sb_amount_raw,
            "direction":           sb_direction,
        },
        "a_share": {
            "total_turnover_trillion": _parse_float(kv.get("A_TURNOVER_TRILLION")),
        },
        "trading_date": kv.get("TRADING_DATE", ""),
    }


# ─────────────────────────────────────────────
# IPO 首日 vs 续期格式化
# ─────────────────────────────────────────────

def fmt_doubao_ipo_section_smart(doubao_ipo_text: str, today_date=None) -> str:
    """
    根据豆包 IPO 原始输出，按招股首日/续期分别格式化：
    - 首日（sub_start == today）：保留完整详细信息块
    - 续期（sub_start < today）：只输出简短提醒
      "正在招股：\n公司名（XXXXX.HK）：M月D日—M月D日"

    豆包输出每只 IPO 以 DATES 行开头：
    DATES: 02729|凯乐士科技|2026-03-16|2026-03-19
    后接详细信息块。
    """
    from datetime import date as date_type
    import re

    if not doubao_ipo_text:
        return "▶️五、*今日招股（新股認購）*\n• 今日暫無新股認購"
    if doubao_ipo_text.strip() == "今日無港股新股認購":
        return "▶️五、*今日招股（新股認購）*\n• 今日暫無新股認購"

    if today_date is None:
        HKT = timezone(timedelta(hours=8))
        today_date = datetime.now(HKT).date()

    def _parse_date(s: str):
        """容错日期解析：用正则提取 YYYY-MM-DD，忽略豆包附加的括注或格式噪声"""
        s = s.strip()
        m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
        if m:
            try:
                return date_type.fromisoformat(m.group(1))
            except ValueError:
                pass
        m2 = re.search(r'(\d{4})[/.](\d{1,2})[/.](\d{1,2})', s)
        if m2:
            try:
                return date_type(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            except ValueError:
                pass
        return None

    # 按 DATES: 行分割各 IPO 块
    # 每个块的格式：DATES: ...\n...详细信息...
    blocks = re.split(r'(?=^DATES:)', doubao_ipo_text, flags=re.MULTILINE)

    first_day_blocks = []
    ongoing_lines = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # 提取 DATES 行（兼容全角竖线｜和半角|）
        dates_match = re.match(r'^DATES:\s*(.+)$', block, re.MULTILINE)
        if not dates_match:
            # 没有 DATES 行（豆包未遵守格式），按首日处理保留完整
            first_day_blocks.append(block)
            continue

        dates_line = dates_match.group(1).strip()
        # 统一全角竖线为半角
        dates_line = dates_line.replace('｜', '|')
        parts = [p.strip() for p in dates_line.split("|")]

        # 解析 code, name, sub_start, sub_end
        code = parts[0].zfill(5) if len(parts) > 0 else ""
        name = parts[1] if len(parts) > 1 else ""
        sub_start_str = parts[2] if len(parts) > 2 else ""
        sub_end_str = parts[3] if len(parts) > 3 else ""

        # 去掉 DATES 行，保留后面的详细文本
        detail_text = re.sub(r'^DATES:.*\n?', '', block, count=1, flags=re.MULTILINE).strip()

        # 容错日期解析
        sub_start = _parse_date(sub_start_str)
        sub_end = _parse_date(sub_end_str)

        is_first_day = (sub_start is not None and sub_start == today_date)

        if is_first_day:
            first_day_blocks.append(detail_text)
        else:
            # 续期：构造简短提醒
            # 防御：跳过豆包用占位符填充的无效条目
            if "[" in code or "[" in name or not name.strip():
                logger.debug(f"[IPO-fmt] 跳过占位符条目: code={code!r} name={name!r}")
                continue
            if sub_start and sub_end:
                date_range = (f"{sub_start.month}月{sub_start.day}日"
                              f"—{sub_end.month}月{sub_end.day}日")
            elif "[" not in sub_start_str and "[" not in sub_end_str and (sub_start_str or sub_end_str):
                date_range = f"{sub_start_str}—{sub_end_str}"
            else:
                logger.debug(f"[IPO-fmt] 跳过日期缺失条目: {name} start={sub_start_str!r} end={sub_end_str!r}")
                continue
            display_code = code if code else ""
            ongoing_lines.append(
                f"{name}（{display_code}.HK）：{date_range}"
            )

    # 拼装最终输出
    parts_out = ["▶️五、*今日招股（新股認購）*"]
    if first_day_blocks:
        parts_out.append("\n\n".join(first_day_blocks))
    if ongoing_lines:
        parts_out.append("正在招股：\n" + "\n".join(ongoing_lines))
    if not first_day_blocks and not ongoing_lines:
        parts_out.append("• 今日暫無新股認購")

    return "\n\n".join(parts_out)


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


# ─────────────────────────────────────────────
# 回购查询 Prompt
# ─────────────────────────────────────────────

_SYSTEM_BUYBACK = """你是港股回購數據整理助手。
任務：搜索港交所過去{hours}小時內披露的所有股票回購（Stock Repurchase）記錄。
數據來源：港交所官網（hkex.com.hk）回購統計頁面。

【輸出格式，嚴格遵守】
每只有回購記錄的股票佔一行，字段用"|"分隔：
股票代碼（4位含前導零）|公司名（中文）|回購股數（X.XX萬股）|價格範圍（X.XX–X.XX港元）|回購金額（XXXX.XX萬港元）

示例：
0669|創科實業|30.00萬股|106.70–107.90港元|3219.81萬港元
0700|騰訊|250.00萬股|430.00–435.00港元|108500.00萬港元

【約束】
- 只輸出數據行，不輸出任何標題、說明、前言後語
- 股票代碼必須是4位（如 0700，不是 700）
- 若過去{hours}小時內無任何回購記錄，輸出：NO_BUYBACK"""

_USER_BUYBACK = "今天是{date}，請搜索港交所過去{hours}小時內（即{date}及前一日）披露的所有港股回購記錄，按格式輸出。"

# 百胜中国专用 prompt（48h + 同时搜索 HK 和 US 代码）
_SYSTEM_BUYBACK_YUMCHINA = """你是港股回購數據整理助手。
任務：搜索百勝中國（港股代碼：09987.HK，美股代碼：YUMC）過去48小時內的股票回購披露記錄。
百勝中國因美股上市慣例，回購通常在T+2日披露，請重點搜索港交所及SEC/美股相關公告。

【輸出格式，嚴格遵守】
9987|百勝中國|回購股數（X.XX萬股）|價格範圍（X.XX–X.XX港元 或 XX.XX–XX.XX美元）|回購金額（XXXX.XX萬港元 或 XXX.XX萬美元）

【約束】
- 只輸出數據行，不輸出任何標題、說明、前言後語
- 若過去48小時內無百勝中國回購記錄，輸出：NO_BUYBACK"""

_USER_BUYBACK_YUMCHINA = "今天是{date}，請搜索百勝中國（09987.HK / YUMC）過去48小時內的回購披露記錄，按格式輸出。"


# ─────────────────────────────────────────────
# 回购查询函数
# ─────────────────────────────────────────────

def fetch_doubao_buybacks(date_hkt: datetime = None) -> Optional[str]:
    """
    用豆包 API 搜索过去 24h 港交所所有股票回购记录。
    返回原始管道分隔文本（供 parse_buyback_lines 解析），或 None（失败/无权限）。
    """
    bot_id = _get_bot_id("DOUBAO_BOT_BUYBACK")
    if not bot_id:
        logger.debug("[DoubaobuybackS] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")
    hours = 24
    system_prompt = _SYSTEM_BUYBACK.replace("{hours}", str(hours))
    user_prompt = _USER_BUYBACK.format(date=date_str, hours=hours)

    try:
        output = _call_doubao(system_prompt, user_prompt, bot_id, max_tokens=600)
        logger.info(f"[DoubaoByback] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoByback] 调用失败: {e}")
        return None


def fetch_doubao_buyback_yumchina(date_hkt: datetime = None) -> Optional[str]:
    """
    百胜中国专用：48h 窗口，兼顾 HK/US 双重披露。
    返回原始管道分隔文本，或 None。
    """
    bot_id = _get_bot_id("DOUBAO_BOT_BUYBACK")
    if not bot_id:
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")
    user_prompt = _USER_BUYBACK_YUMCHINA.format(date=date_str)

    try:
        output = _call_doubao(_SYSTEM_BUYBACK_YUMCHINA, user_prompt, bot_id, max_tokens=200)
        logger.info(f"[DoubaoByback-YumChina] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoByback-YumChina] 调用失败: {e}")
        return None


# ─────────────────────────────────────────────
# 回购数据解析 & Watchlist 匹配
# ─────────────────────────────────────────────

def parse_buyback_lines(raw_text: str) -> list[dict]:
    """
    解析豆包输出的管道分隔回购记录。
    每行格式：0669|创科实业|30.00万股|106.70–107.90港元|3219.81万港元
    返回: [{"code": "0669", "name": "创科实业", "shares": "30.00万股",
             "price": "106.70–107.90港元", "amount": "3219.81万港元"}, ...]
    """
    if not raw_text or raw_text.strip() == "NO_BUYBACK":
        return []

    results = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line == "NO_BUYBACK":
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        results.append({
            "code":   parts[0].lstrip("0").zfill(4),  # 标准化为4位
            "name":   parts[1] if len(parts) > 1 else "",
            "shares": parts[2] if len(parts) > 2 else "",
            "price":  parts[3] if len(parts) > 3 else "",
            "amount": parts[4] if len(parts) > 4 else "",
        })
    return results


def match_watchlist_buybacks(
    buyback_items: list[dict],
    hk_watchlist: dict,
) -> list[dict]:
    """
    将豆包返回的回购记录与 HK watchlist（{code: name}）做匹配，
    只保留 watchlist 中的股票。匹配优先用股票代码（4位），
    其次用 watchlist 名称与豆包返回名称的模糊比较。

    返回匹配到的记录列表（含 watchlist_name 字段，使用 watchlist 中的规范名称）。
    """
    matched = []
    seen_codes = set()

    # 构建辅助映射：{标准化4位code: watchlist_name}
    watchlist_normalized = {
        code.lstrip("0").zfill(4): name
        for code, name in hk_watchlist.items()
    }

    for item in buyback_items:
        code = item.get("code", "").lstrip("0").zfill(4)
        if not code:
            continue

        if code in watchlist_normalized and code not in seen_codes:
            matched.append({
                **item,
                "code": code,
                "watchlist_name": watchlist_normalized[code],
            })
            seen_codes.add(code)

    return matched


def fmt_buyback_subsection(matched_items: list[dict]) -> str:
    """
    格式化回购子段落。
    示例输出：
      **回購**
      創科實業（00669.HK）
      * 回購：30.00 萬股
      * 價格：106.70–107.90 港元
      * 金額：3219.81 萬港元
    """
    if not matched_items:
        return ""

    lines = ["**回購**"]
    for item in matched_items:
        code_display = item["code"].zfill(5)          # 港股5位显示惯例（如 00669）
        name = item.get("watchlist_name") or item.get("name", "")
        lines.append(f"{name}（{code_display}.HK）")

        shares = item.get("shares", "")
        price  = item.get("price", "")
        amount = item.get("amount", "")

        if shares:
            lines.append(f"* 回購：{shares}")
        if price:
            lines.append(f"* 價格：{price}")
        if amount:
            lines.append(f"* 金額：{amount}")
        lines.append("")  # 空行分隔不同股票

    return "\n".join(lines).rstrip()


# ─────────────────────────────────────────────
# 业绩公告查询（watchlist 模式 + 详情模式）
# ─────────────────────────────────────────────

_EARNINGS_OUTPUT_FORMAT = """\
公司名（代碼.HK）｜業績期（如「2025年全年業績」）

▸ 財務數據
• 收入：X.XX 億 港元/人民幣（同比 +/-X%）
• 毛利率：X.X%（同比 +/-X ppts）（如有）
• EBITDA/經營利潤：X.XX 億（同比 +/-X%）（如有）
• 淨利潤：X.XX 億 港元/人民幣（同比 +/-X%）
• 每股盈利（EPS）：X.XX 港元/人民幣（如有）

▸ 業務更新
• 核心業務板塊表現（1-3個要點，含具體數字）
• 重要戰略/產品/市場動向

▸ 其他
• 末期息/中期息：每股 X.XX 港元 或 不派息
• 股息合計（全年）：X.XX 港元（如有）
• 回購/特別派息：如有則說明"""

# ── watchlist 模式：豆包自行判断哪些有业绩 ──────────────────
_SYSTEM_EARNINGS_WATCHLIST = f"""你是服务香港证券从业者的专业金融早报编辑。
每日早报发布时间：北京时间 07:30（过去48小时 = 前日07:30至今）。

任务：从以下港股 watchlist 中，搜索过去48小时内发布了业绩公告（年報、中報、盈利預警）的公司，
仅对有实际业绩发布的公司输出三段式要点。

【輸出格式，每只股票一塊，中間用「---」分隔】
{_EARNINGS_OUTPUT_FORMAT}

【約束】
- 只輸出過去48小時內確實有業績公告的公司，沒有業績的公司一律跳過（不輸出任何行）
- 數字必須來自公告原文，無數據填 N/A，不得估算
- 若 watchlist 中所有公司均無近48小時業績，輸出：近48小時watchlist內無業績公告
- 使用繁體中文；不輸出解釋性前言後語"""

_USER_EARNINGS_WATCHLIST = (
    "今天是{date}（北京時間07:30）。請從以下港股 watchlist 中，"
    "搜索過去48小時內（{prev_date} 07:30 至今）發布業績公告的公司，按格式輸出：\n\n"
    "{watchlist}"
)

# ── 详情模式（Finnhub 触发备用）──────────────────────────────
_SYSTEM_EARNINGS_DETAIL = f"""你是服务香港证券从业者的专业金融早报编辑。
任務：搜索以下港股公司近日（過去24小時內）發布的業績公告，按三段式格式輸出要點。

【輸出格式，每只股票一塊，中間用「---」分隔】
{_EARNINGS_OUTPUT_FORMAT}

【約束】
- 使用繁體中文
- 數字必須來自公告原文，無數據填 N/A，不得估算
- 若某只股票確實無近24小時業績公告，跳過該股票不輸出
- 不輸出任何說明性前言後語"""

_USER_EARNINGS_DETAIL = (
    "今天是{date}（北京時間早上07:30），"
    "以下港股公司近日發布了業績公告，"
    "請搜索各公司官方業績公告原文並按格式輸出：\n{companies}"
)


def fetch_doubao_earnings_watchlist(
    watchlist: dict,
    date_hkt: datetime = None,
) -> Optional[str]:
    """
    将完整 watchlist 喂给豆包，让其自行判断哪些股票过去48h有业绩并生成三段式要点。

    watchlist: {code: name} 字典（来自 HK_STOCKS）
    返回格式化文本，或 None（失败/无业绩时）。
    """
    if not watchlist:
        return None

    bot_id = _get_bot_id("DOUBAO_BOT_EARNINGS", fallback_env="DOUBAO_BOT_MACRO")
    if not bot_id:
        logger.debug("[DoubaoEarnings] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str     = date_hkt.strftime("%Y年%m月%d日")
    prev_date_str = (date_hkt - timedelta(days=2)).strftime("%Y年%m月%d日")
    watchlist_str = "\n".join(
        f"- {name}（{code.zfill(4)}.HK）"
        for code, name in watchlist.items()
    )
    user_prompt = _USER_EARNINGS_WATCHLIST.format(
        date=date_str,
        prev_date=prev_date_str,
        watchlist=watchlist_str,
    )

    try:
        # watchlist ~46 只，有业绩的通常 3-10 只，每只约 250 tokens
        output = _call_doubao(
            _SYSTEM_EARNINGS_WATCHLIST, user_prompt, bot_id,
            max_tokens=2500,
        )
        # 无业绩时返回约定短语，视为 None
        if "無業績公告" in output or "无业绩" in output or "近48小時" in output:
            logger.info("[DoubaoEarnings-WL] watchlist 内近48h无业绩")
            return None
        logger.info(f"[DoubaoEarnings-WL] 成功，{len(output)} 字")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoEarnings-WL] 调用失败: {e}")
        return None


def fetch_doubao_earnings_detail(
    triggered_stocks: list[dict],
    date_hkt: datetime = None,
) -> Optional[str]:
    """
    对指定股票列表（Finnhub 触发）用豆包搜索业绩详情（备用路径）。

    triggered_stocks: [{"code": "0291", "name": "華潤啤酒", ...}, ...]
    返回格式化文本，或 None（失败时）。
    """
    if not triggered_stocks:
        return None

    bot_id = _get_bot_id("DOUBAO_BOT_EARNINGS", fallback_env="DOUBAO_BOT_MACRO")
    if not bot_id:
        logger.debug("[DoubaoEarnings] 未配置 Bot ID，跳过")
        return None

    if date_hkt is None:
        HKT = timezone(timedelta(hours=8))
        date_hkt = datetime.now(HKT)

    date_str = date_hkt.strftime("%Y年%m月%d日")
    companies = "\n".join(
        f"- {s['name']}（{s['code'].zfill(4)}.HK）"
        for s in triggered_stocks
    )
    user_prompt = _USER_EARNINGS_DETAIL.format(date=date_str, companies=companies)

    try:
        output = _call_doubao(
            _SYSTEM_EARNINGS_DETAIL, user_prompt, bot_id,
            max_tokens=1500,
        )
        logger.info(f"[DoubaoEarnings] 成功，{len(output)} 字，涉及 {len(triggered_stocks)} 只")
        return output
    except Exception as e:
        logger.warning(f"[DoubaoEarnings] 调用失败: {e}")
        return None


def fmt_earnings_subsection(earnings_detail: str) -> str:
    """将豆包返回的业绩详情包装为子段落"""
    if not earnings_detail:
        return ""
    return f"**今日業績公告**\n{earnings_detail}"
