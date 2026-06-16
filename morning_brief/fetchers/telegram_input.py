"""
telegram_input.py — 从 Telegram 频道读取人工精选输入
使用 Bot API getUpdates（方案B），无需额外 API 凭据

频道要求：将 Bot 设为频道管理员，频道发帖会以 channel_post 形式出现在 getUpdates。
支持消息格式：
  - 直接发布/粘贴的新闻文本（含 #标签 分类）
  - 转发消息（取 caption 或转发正文）
  - 推荐标签格式：港股用 #四位代码（#0700），美股用 #大写ticker（#NVDA），A股用 #六位代码
  - 兼容中文公司名标签（#騰訊/#腾讯），自动归一到代码，避免繁简重复
"""
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 存储上次处理的 update_id，避免重复处理同一条消息
OFFSET_FILE = Path(__file__).parent.parent / "telegram_input_offset.json"

# ── 分类标签集合 ────────────────────────────────────────────────────────────
MACRO_TAGS = {"#宏观", "#宏觀", "#macro", "#行业", "#行業", "#industry"}
IPO_TAGS = {"#ipo", "#新股", "#招股"}
STOCKS_GENERIC_TAGS = {"#个股", "#個股", "#stocks"}

# 港股代码：# + 4-5位数字
_HK_CODE_RE = re.compile(r"#(\d{4,5})\b")
# 美股 ticker：# + 2-5位大写字母（不在已知非股票标签内）
_US_TICKER_RE = re.compile(r"#([A-Z]{2,5})\b")
# 中文公司名标签：# + 2-8个汉字
_CN_NAME_RE = re.compile(r"#([\u4e00-\u9fff]{2,8})")

# 已知非公司标签（用于排除误识别）
_KNOWN_NON_COMPANY = MACRO_TAGS | IPO_TAGS | STOCKS_GENERIC_TAGS | {
    "#A股", "#港股", "#美股", "#行业", "#行業",
}

# ── 公司名 → 代码归一化 ──────────────────────────────────────────────────────
# 中文名（简体/繁体）→ 标准代码（港股4位、美股ticker、A股6位）
_CN_TO_CODE: dict[str, str] = {
    # 港股
    "騰訊": "0700", "腾讯": "0700",
    "阿里巴巴": "9988",
    "美團": "3690", "美团": "3690",
    "小米集團": "1810", "小米集团": "1810", "小米": "1810",
    "快手": "1024",
    "比亞迪": "1211", "比亚迪": "1211",
    "吉利汽車": "0175", "吉利汽车": "0175", "吉利": "0175",
    "中國移動": "0941", "中国移动": "0941",
    "港交所": "0388",
    "友邦保險": "1299", "友邦保险": "1299", "友邦": "1299",
    "中國平安": "2318", "中国平安": "2318", "平安": "2318",
    "招商銀行": "3968", "招商银行": "3968", "招行": "3968",
    "中國銀行": "3988", "中国银行": "3988",
    "農夫山泉": "9633", "农夫山泉": "9633",
    "寧德時代": "3750", "宁德时代": "3750", "寧德": "3750", "宁德": "3750",
    "攜程集團": "9961", "携程集团": "9961", "攜程": "9961", "携程": "9961",
    "百勝中國": "9987", "百胜中国": "9987",
    "安踏": "2020",
    "李寧": "2331", "李宁": "2331",
    "申洲國際": "2313", "申洲国际": "2313",
    "華潤啤酒": "0291", "华润啤酒": "0291",
    "華潤電力": "0836", "华润电力": "0836",
    "華潤置地": "1109", "华润置地": "1109",
    "中海外": "0688", "中國海外": "0688", "中国海外": "0688",
    "龍湖": "0960", "龙湖": "0960",
    "綠城": "3900", "绿城": "3900",
    "舜宇光學": "2382", "舜宇光学": "2382", "舜宇": "2382",
    "創科實業": "0669", "创科实业": "0669", "創科": "0669", "创科": "0669",
    "信義玻璃": "0868", "信义玻璃": "0868",
    "福耀玻璃": "3606",
    "中石油": "0857",
    "中遠海能": "1138", "中远海能": "1138",
    "港鐵": "0066", "港铁": "0066",
    "新東方": "9901", "新东方": "9901",
    "商湯": "0020", "商汤": "0020",
    "海爾智家": "6690", "海尔智家": "6690",
    "美的集團": "0300", "美的集团": "0300", "美的": "0300",
    "中國中免": "1880", "中国中免": "1880",
    # A股
    "茅台": "600519", "貴州茅台": "600519", "贵州茅台": "600519",
    # 美股
    "蘋果": "AAPL", "苹果": "AAPL",
    "微軟": "MSFT", "微软": "MSFT",
    "谷歌": "GOOGL",
    "亞馬遜": "AMZN", "亚马逊": "AMZN",
    "英偉達": "NVDA", "英伟达": "NVDA",
    "特斯拉": "TSLA",
    "英特爾": "INTC", "英特尔": "INTC",
    "Meta": "META",
}


def _normalize_company(raw: str) -> str:
    """归一化 company tag 到标准代码。
    - 数字串：港股补到4位，A股补到6位
    - 英文 ticker：保持大写（正则已保证）
    - 中文名：反查 _CN_TO_CODE，查不到保留原值
    """
    if raw.isdigit():
        stripped = raw.lstrip("0") or "0"
        return stripped.zfill(4) if len(stripped) <= 4 else stripped.zfill(6)
    return _CN_TO_CODE.get(raw, raw)


# ── Offset 管理 ──────────────────────────────────────────────────────────────

def _load_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("last_update_id", 0)
        except Exception:
            pass
    return 0


def _save_offset(update_id: int):
    OFFSET_FILE.write_text(
        json.dumps({"last_update_id": update_id}), encoding="utf-8"
    )


# ── Bot API 调用 ──────────────────────────────────────────────────────────────

def _get_updates(token: str, offset: int = 0, limit: int = 100) -> list[dict]:
    """调用 Telegram Bot API getUpdates，返回 update 列表"""
    try:
        import httpx
    except ImportError:
        logger.warning("[TelegramInput] 缺少 httpx 依赖")
        return []

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params: dict = {"limit": limit, "timeout": 0}
    if offset:
        params["offset"] = offset
    try:
        resp = httpx.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
        logger.warning(f"[TelegramInput] API error: {data.get('description')}")
    except Exception as e:
        logger.warning(f"[TelegramInput] getUpdates 请求失败: {e}")
    return []


# ── Update 字段提取 ───────────────────────────────────────────────────────────

def _get_chat_id(update: dict) -> Optional[str]:
    for key in ("channel_post", "message"):
        msg = update.get(key)
        if msg:
            return str(msg.get("chat", {}).get("id", ""))
    return None


def _get_msg_date(update: dict) -> Optional[datetime]:
    for key in ("channel_post", "message"):
        msg = update.get(key)
        if msg and msg.get("date"):
            return datetime.fromtimestamp(msg["date"], tz=timezone.utc)
    return None


def _extract_text(update: dict) -> Optional[str]:
    """提取消息正文（含转发消息的原文和 caption）"""
    for key in ("channel_post", "message"):
        msg = update.get(key)
        if not msg:
            continue
        text = (msg.get("text") or msg.get("caption") or "").strip()
        return text or None
    return None


def _matches_channel(chat_id: str, target: str) -> bool:
    """
    宽松匹配频道 ID。
    Telegram 频道 ID 通常为 -1001234567890，用户可能只提供 1234567890。
    只要数字部分（去掉前缀 -100）相同即匹配。
    """
    c = str(chat_id).lstrip("-")
    t = str(target).lstrip("-")
    # 去掉 100 前缀（超级群组/频道格式）
    c_short = c[3:] if c.startswith("100") and len(c) > 10 else c
    t_short = t[3:] if t.startswith("100") and len(t) > 10 else t
    return c == t or c_short == t_short or c == t_short or t == c_short


# ── 消息解析 ─────────────────────────────────────────────────────────────────

def _parse_message(text: str) -> dict:
    """
    解析单条消息文本，识别标签并分类。
    返回:
      {
        "section": "macro" | "ipo" | "stocks" | "unclassified",
        "company": str | None,   # 仅 stocks 时填入
        "content": str,          # 清洗后的正文
      }
    """
    text = text.strip()
    lines = text.splitlines()

    section = None
    company_tags: list[str] = []

    # 扫描所有行的标签
    for line in lines:
        lower = line.lower()

        # 宏观
        for t in MACRO_TAGS:
            if t.lower() in lower:
                section = "macro"

        # IPO
        for t in IPO_TAGS:
            if t.lower() in lower:
                section = "ipo"

        # 港股代码 #0700 / #00700 → 归一化4位
        for m in _HK_CODE_RE.findall(line):
            company_tags.append(_normalize_company(m))
            if section not in ("macro", "ipo"):
                section = "stocks"

        # 美股 ticker
        for m in _US_TICKER_RE.findall(line):
            full = f"#{m}"
            if full not in _KNOWN_NON_COMPANY:
                company_tags.append(m)
                if section not in ("macro", "ipo"):
                    section = "stocks"

        # 中文公司名标签 → 反查代码，简繁通吃
        for m in _CN_NAME_RE.findall(line):
            full = f"#{m}"
            if full not in _KNOWN_NON_COMPANY:
                company_tags.append(_normalize_company(m))
                if section not in ("macro", "ipo"):
                    section = "stocks"

        # 泛个股标签（无具体公司名 → unclassified）
        for t in STOCKS_GENERIC_TAGS:
            if t.lower() in lower and section not in ("macro", "ipo", "stocks"):
                section = "unclassified"

    # 清理正文：删除纯标签行，保留内容行
    content_lines = []
    for line in lines:
        without_tags = re.sub(r"#[\w\u4e00-\u9fff]+", "", line).strip()
        if without_tags:
            content_lines.append(line.strip())
    content = "\n".join(content_lines).strip() or text

    # 去重：同一条消息里多个标签可能归一到同一代码（如 #0700 #騰訊）
    seen_tags: set[str] = set()
    deduped_tags: list[str] = []
    for tag in company_tags:
        if tag not in seen_tags:
            seen_tags.add(tag)
            deduped_tags.append(tag)
    company = deduped_tags[0] if deduped_tags else None

    return {
        "section": section or "unclassified",
        "company": company,
        "content": content,
    }


# ── 主入口 ────────────────────────────────────────────────────────────────────

def fetch_manual_inputs(
    token: str,
    channel_id: str,
    lookback_hours: int = 24,
) -> dict:
    """
    读取目标频道过去 lookback_hours 小时内的所有消息。

    返回 ManualInputBundle:
    {
        "macro":        ["宏观文本1", ...],
        "stocks":       {"腾讯": ["文本1"], "0700": ["文本"], ...},
        "ipo":          ["IPO文本1", ...],
        "unclassified": ["未分类文本1", ...],
    }
    """
    bundle: dict = {"macro": [], "stocks": {}, "ipo": [], "unclassified": []}

    offset = _load_offset()
    # 若已有 offset，从下一条开始取（同时向 Telegram 确认已处理之前的 update）
    updates = _get_updates(token, offset=offset + 1 if offset else 0)

    if not updates:
        logger.info("[TelegramInput] 无新 updates，跳过人工输入")
        return bundle

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    new_max_id = offset
    matched = 0

    for upd in updates:
        upd_id = upd.get("update_id", 0)
        new_max_id = max(new_max_id, upd_id)

        # 过滤：只处理目标频道
        chat_id = _get_chat_id(upd)
        if not chat_id or not _matches_channel(chat_id, channel_id):
            continue

        # 过滤：24h 时间窗口
        msg_dt = _get_msg_date(upd)
        if msg_dt and msg_dt < cutoff:
            logger.debug(f"[TelegramInput] 消息超出24h窗口，跳过")
            continue

        text = _extract_text(upd)
        if not text:
            continue

        parsed = _parse_message(text)
        section = parsed["section"]
        content = parsed["content"]
        company = parsed["company"]

        if section == "macro":
            bundle["macro"].append(content)
        elif section == "ipo":
            bundle["ipo"].append(content)
        elif section == "stocks":
            key = company or "未知公司"
            bundle["stocks"].setdefault(key, []).append(content)
        else:
            bundle["unclassified"].append(content)

        matched += 1
        logger.info(f"[TelegramInput] [{section}] {content[:60].replace(chr(10), ' ')}...")

    # 保存最新 offset（确认已处理）
    if new_max_id > offset:
        _save_offset(new_max_id)

    logger.info(
        f"[TelegramInput] 读取完毕 共{matched}条有效消息 "
        f"| 宏观{len(bundle['macro'])} 个股{len(bundle['stocks'])}家 "
        f"IPO{len(bundle['ipo'])} 未分类{len(bundle['unclassified'])}"
    )
    return bundle
