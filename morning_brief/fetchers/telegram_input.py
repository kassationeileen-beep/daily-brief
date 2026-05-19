"""
telegram_input.py — 从 Telegram 频道读取人工精选输入
使用 Bot API getUpdates（方案B），无需额外 API 凭据

频道要求：将 Bot 设为频道管理员，频道发帖会以 channel_post 形式出现在 getUpdates。
支持消息格式：
  - 直接发布/粘贴的新闻文本（含 #标签 分类）
  - 转发消息（取 caption 或转发正文）
  - 支持 #宏观/#macro、#ipo/#新股、#0700/#腾讯/#TSLA 等标签
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

        # 港股代码 #0700 → "0700"
        for m in _HK_CODE_RE.findall(line):
            company_tags.append(m)
            if section not in ("macro", "ipo"):
                section = "stocks"

        # 美股 ticker
        for m in _US_TICKER_RE.findall(line):
            full = f"#{m}"
            if full not in _KNOWN_NON_COMPANY:
                company_tags.append(m)
                if section not in ("macro", "ipo"):
                    section = "stocks"

        # 中文公司名标签
        for m in _CN_NAME_RE.findall(line):
            full = f"#{m}"
            if full not in _KNOWN_NON_COMPANY:
                company_tags.append(m)
                if section not in ("macro", "ipo"):
                    section = "stocks"

        # 泛个股标签（无具体公司名 → unclassified）
        for t in STOCKS_GENERIC_TAGS:
            if t.lower() in lower and section not in ("macro", "ipo", "stocks"):
                section = "unclassified"

    # 清理正文：删除纯标签行，保留内容行
    content_lines = []
    for line in lines:
        # 去掉行内所有 #tag 后检查是否还有实质内容
        without_tags = re.sub(r"#[\w\u4e00-\u9fff]+", "", line).strip()
        if without_tags:
            content_lines.append(line.strip())
    content = "\n".join(content_lines).strip() or text

    company = company_tags[0] if company_tags else None

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
