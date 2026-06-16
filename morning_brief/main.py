#!/usr/bin/env python3
"""
main.py — 金融早报自动化主程序
运行时间：每日 UTC 23:30（HKT 07:30）
"""
import os
import sys
import logging
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 确保 fetchers/llm 等子包可被直接导入
sys.path.insert(0, str(Path(__file__).parent))

# ── 日志配置 ──────────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f"{datetime.now().strftime('%Y%m%d')}.log",
                            encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# ── 输出目录 ──────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# HKT = UTC+8
HKT = timezone(timedelta(hours=8))


# ─────────────────────────────────────────────
# 格式化辅助
# ─────────────────────────────────────────────

def fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def fmt_num(val, decimals=2, fallback="N/A") -> str:
    if val is None:
        return fallback
    return f"{val:,.{decimals}f}"


def warn(label: str) -> str:
    return f"⚠️ {label}数据获取失败，请手动补充"


# ─────────────────────────────────────────────
# 早报正文生成
# ─────────────────────────────────────────────

def _prev_trading_day(dt: datetime) -> datetime:
    """前一个交易日（仅排除周六日，不处理节假日）"""
    d = dt - timedelta(days=1)
    while d.weekday() >= 5:   # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d


def build_brief(
    market_data: dict,
    stock_sections: list[str],
    now_hkt: datetime,
    macro_section: str = "",
    ipo_section: str = "",
    buyback_subsection: str = "",
) -> str:
    date_str = now_hkt.strftime("%Y-%m-%d")           # 今日：用于标题
    date_compact = now_hkt.strftime("%Y%m%d")
    market_date_str = _prev_trading_day(now_hkt).strftime("%Y-%m-%d")  # 前一交易日：用于第一部分

    hsi = market_data.get("hsi") or {}
    sb = market_data.get("southbound") or {}
    ash = market_data.get("a_share") or {}
    n225 = market_data.get("nikkei") or {}
    fx = market_data.get("fx") or {}

    # ── 第一部分：核心资金动态 ───────────────────────────────────────────────
    # 恒生指数
    hsi_close = fmt_num(hsi.get("close"))
    hsi_pct = fmt_pct(hsi.get("pct"))
    hsi_turnover = fmt_num(hsi.get("turnover_hkd_100m")) if hsi.get("turnover_hkd_100m") else "N/A"
    hsi_block = (
        f"1. 恒生指數（港股）\n"
        f"- 收盤價：{hsi_close} 點（{hsi_pct}）\n"
        f"- 成交額：{hsi_turnover} 億港元\n"
    )
    if hsi.get("error") and not hsi.get("close"):
        hsi_block = warn("恒生指數") + "\n"

    # 南向资金
    if sb.get("net_flow_hkd_100m") is not None:
        sb_dir = sb.get("direction", "")
        sb_val = fmt_num(sb["net_flow_hkd_100m"])
        sb_line = f"- 南向資金（北水）淨{sb_dir}：{sb_val} 億港元"
    else:
        sb_line = "- 南向資金（北水）：N/A"
    hsi_block += sb_line + "\n"

    # A股
    a_turnover = fmt_num(ash.get("total_turnover_trillion")) if ash.get("total_turnover_trillion") else "N/A"
    sh_close = fmt_num(ash.get("sh_close"))
    sh_pct = fmt_pct(ash.get("sh_pct"))
    sz_close = fmt_num(ash.get("sz_close"))
    sz_pct = fmt_pct(ash.get("sz_pct"))

    a_block = (
        f"2. A 股（滬深兩市）\n"
        f"- 總成交額：{a_turnover} 萬億元人民幣\n"
        f"- 上海綜合指數：{sh_close} 點（{sh_pct}）\n"
        f"- 深圳成分指數：{sz_close} 點（{sz_pct}）\n"
    )
    if ash.get("error") and not ash.get("sh_close"):
        a_block = warn("A股") + "\n"

    # 日经
    n_close = fmt_num(n225.get("close"))
    n_pct = fmt_pct(n225.get("pct"))
    n_vol = fmt_num(n225.get("volume_100m"), decimals=4) if n225.get("volume_100m") else "N/A"
    nikkei_block = (
        f"3. 日經225（日股）\n"
        f"- 收盤價：{n_close} 點（{n_pct}）\n"
        f"- 成交量：{n_vol} 億股\n"
    )
    if n225.get("error") and not n225.get("close"):
        nikkei_block = warn("日經225") + "\n"

    # ── 第二部分：关键汇率 ────────────────────────────────────────────────────
    def fxv(key, decimals=4):
        v = fx.get(key)
        return fmt_num(v, decimals) if v else "N/A"

    time_str = now_hkt.strftime("%H:%M")
    fx_block = f"""▶️二、*關鍵匯率*（截止{time_str}）
USD/JPY：{fxv("USD/JPY")}
USD/CHF：{fxv("USD/CHF")}
USD/CNH：{fxv("USD/CNH")}
CNH/HKD：{fxv("CNH/HKD", 6)}
XAU/USD：{fxv("XAU/USD", 2)}
DXY：{fxv("DXY")}
BTC/USD：{fxv("BTC/USD", 2)}
布倫特原油：{fxv("Brent", 2)}
WTI原油：{fxv("WTI", 2)}"""

    if not any(fx.get(k) for k in ["USD/JPY", "XAU/USD", "BTC/USD"]):
        fx_block = "▶️二、*關鍵匯率*\n" + warn("匯率")

    # ── 第四部分：个股动态 ────────────────────────────────────────────────────
    # 结构：回购 → 个股新闻
    section4_parts = ["▶️四、*個股動態*"]
    if buyback_subsection:
        section4_parts.append(buyback_subsection)
    has_prefix = bool(buyback_subsection)
    if stock_sections:
        if has_prefix:
            section4_parts.append("**個股新聞**")
        section4_parts.append("\n\n".join(stock_sections))
    else:
        section4_parts.append(warn("個股新聞"))
    stocks_block = "\n\n".join(section4_parts)

    # ── 第三部分：宏观 & 行业 ──────────────────────────────────────────────────
    macro_block = macro_section or "▶️三、*宏觀及行業動態*\n• 暫無數據"

    # ── 第五部分：今日招股 ────────────────────────────────────────────────────
    ipo_block = ipo_section or "▶️五、*今日招股（新股認購）*\n• 今日暫無新股認購"

    # ── 拼接完整早报 ──────────────────────────────────────────────────────────
    brief = f"""Good Morning, {date_compact} Daily Brief
▶️一、*{market_date_str}核心資金動態*
{hsi_block}{a_block}{nikkei_block}
{fx_block}

{macro_block}

{stocks_block}

{ipo_block}"""

    return brief.strip()


# ─────────────────────────────────────────────
# Telegram 推送
# ─────────────────────────────────────────────

def _split_text(text: str, chunk_size: int) -> list[str]:
    """
    按行切割文本，保证每段不超过 chunk_size 字符。
    优先在空行处断开，次选在换行处断开。
    """
    lines = text.splitlines(keepends=True)
    chunks = []
    current = ""
    for line in lines:
        # 单行本身超长：强制按字符截断
        if len(line) > chunk_size:
            if current:
                chunks.append(current.rstrip())
                current = ""
            for i in range(0, len(line), chunk_size):
                chunks.append(line[i:i + chunk_size].rstrip())
            continue
        if len(current) + len(line) > chunk_size:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return [c for c in chunks if c.strip()]


def send_telegram(text: str, token: str, chat_id: str, chunk_size: int = 3800):
    """超 4000 字自动分段推送（保留 Markdown V1 格式）"""
    import httpx
    import time as _time

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_chunk(chunk: str):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = httpx.post(url, json=payload, timeout=30)
        # 若 Markdown 解析失败，退回纯文本重试
        if resp.status_code == 400 and "parse" in resp.text.lower():
            payload["parse_mode"] = ""
            resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    chunks = _split_text(text, chunk_size)
    logger.info(f"Telegram 推送：共 {len(chunks)} 段（总长 {len(text)} 字）")
    for i, chunk in enumerate(chunks, 1):
        try:
            send_chunk(chunk)
            logger.info(f"  第 {i}/{len(chunks)} 段发送成功")
            if i < len(chunks):
                import time
                time.sleep(1)
        except Exception as e:
            logger.error(f"  第 {i} 段发送失败: {e}")
            raise



# ─────────────────────────────────────────────
# 人工输入合并 / 搜索任务识别
# ─────────────────────────────────────────────

EARNINGS_KEYWORDS = (
    "业绩", "業績", "财报", "財報", "年报", "年報", "中报", "中報",
    "季报", "季報", "results", "earnings", "annual", "interim",
)
SEARCH_INTENT_KEYWORDS = (
    "查", "搜", "搜索", "核查", "看看", "留意", "关注", "關注", "提醒",
    "是否", "有没有", "有无", "有冇", "可能", "应该", "應該", "發", "发",
)


def _is_earnings_search_request(text: str) -> bool:
    """识别“请去查业绩”类人工提醒，避免把提醒原文直接写入日报。"""
    lowered = (text or "").lower()
    has_earnings = any(keyword.lower() in lowered for keyword in EARNINGS_KEYWORDS)
    has_search_intent = any(keyword.lower() in lowered for keyword in SEARCH_INTENT_KEYWORDS)
    return has_earnings and has_search_intent


def _extract_earnings_requests(manual_stock_items: dict) -> tuple[dict, list[dict]]:
    """
    从人工个股输入中拆出业绩搜索请求。

    返回：
    - cleaned_manual：继续交给个股 LLM 的人工事实；
    - earnings_requests：需要触发豆包业绩搜索的公司列表。
    """
    cleaned_manual: dict = {}
    requests: list[dict] = []
    seen_companies: set[str] = set()

    for company, items in (manual_stock_items or {}).items():
        keep_items = []
        for item in items or []:
            if _is_earnings_search_request(item):
                company_key = str(company).strip()
                if company_key and company_key not in seen_companies:
                    requests.append({"code": company_key, "name": company_key, "source": "telegram"})
                    seen_companies.add(company_key)
                logger.info(f"[ManualInput] 将人工业绩提醒转为搜索任务: {company_key}")
            else:
                keep_items.append(item)
        if keep_items:
            cleaned_manual[company] = keep_items

    return cleaned_manual, requests


def _merge_manual_ipo_section(ipo_section: str, manual_items: list[str]) -> str:
    """把 Telegram 人工 IPO 输入强制合并进第五部分，避免读入后被主流程忽略。"""
    manual_items = [item.strip() for item in (manual_items or []) if item and item.strip()]
    if not manual_items:
        return ipo_section

    manual_block = "人工補充（Telegram）：\n" + "\n".join(
        f"• {item}" for item in manual_items
    )

    empty_markers = ("• 今日暫無新股認購", "• 今日暂无新股认购")
    if ipo_section and any(marker in ipo_section for marker in empty_markers):
        header = ipo_section.splitlines()[0]
        return f"{header}\n{manual_block}"

    if ipo_section:
        return f"{ipo_section.rstrip()}\n\n{manual_block}"
    return f"▶️五、*今日招股（新股認購）*\n{manual_block}"

# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

def main():
    now_utc = datetime.now(timezone.utc)
    now_hkt = now_utc.astimezone(HKT)
    logger.info(f"=== 金融早报启动 {now_hkt.strftime('%Y-%m-%d %H:%M %Z')} ===")

    # 必须环境变量检查
    required_envs = ["DEEPSEEK_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [e for e in required_envs if not os.environ.get(e)]
    if missing:
        logger.error(f"缺少环境变量: {missing}")
        sys.exit(1)

    # ── Step 0: 读取 Telegram 人工精选输入 ───────────────────────────────────
    # 需配置 TELEGRAM_INPUT_CHANNEL_ID（与输出频道独立的输入频道）
    # 若未配置则跳过，完全依赖自动抓取
    manual_bundle: dict = {"macro": [], "stocks": {}, "ipo": [], "unclassified": []}
    input_channel_id = os.environ.get("TELEGRAM_INPUT_CHANNEL_ID")
    if input_channel_id:
        logger.info("Step 0: 读取 Telegram 人工精选输入")
        try:
            from fetchers.telegram_input import fetch_manual_inputs
            input_bot_token = os.environ.get("TELEGRAM_INPUT_BOT_TOKEN") or os.environ["TELEGRAM_TOKEN"]
            raw_bundle = fetch_manual_inputs(
                token=input_bot_token,
                channel_id=input_channel_id,
            )
            # 未分类条目交 LLM 自动分类
            if raw_bundle.get("unclassified"):
                logger.info(
                    f"Step 0: LLM 分类 {len(raw_bundle['unclassified'])} 条未打标签消息"
                )
                from llm.refiner import classify_unclassified_items
                classified = classify_unclassified_items(raw_bundle["unclassified"])
                raw_bundle["macro"].extend(classified.get("macro", []))
                raw_bundle["ipo"].extend(classified.get("ipo", []))
                for company, items in classified.get("stocks", {}).items():
                    raw_bundle["stocks"].setdefault(company, []).extend(items)
            manual_bundle = raw_bundle
            logger.info(
                f"Step 0 完成: 宏观{len(manual_bundle['macro'])}条 "
                f"个股{len(manual_bundle['stocks'])}家 "
                f"IPO{len(manual_bundle['ipo'])}条"
            )
        except Exception as e:
            logger.warning(f"Step 0: 人工输入读取失败: {e}，继续使用纯自动抓取")
    else:
        logger.info("Step 0: 未配置 TELEGRAM_INPUT_CHANNEL_ID，跳过人工输入")

    # 将“查业绩/是否发业绩”这类人工提醒转为后续搜索任务，避免原文直出日报
    manual_stock_items, manual_earnings_requests = _extract_earnings_requests(
        manual_bundle.get("stocks") or {}
    )
    manual_bundle["stocks"] = manual_stock_items
    if manual_earnings_requests:
        logger.info(
            f"Step 0: 识别到 {len(manual_earnings_requests)} 条人工业绩搜索任务"
        )

    # ── Step 1: 抓取市场数据 ──────────────────────────────────────────────────
    logger.info("Step 1: 抓取市场数据（结构化 scraper）")
    try:
        from fetchers.market_data import fetch_all_market_data
        market_data = fetch_all_market_data()
    except Exception as e:
        logger.error(f"市场数据模块失败: {e}")
        market_data = {}

    # ── Step 1b: 豆包补充资金动态（填补 scraper 缺失字段）─────────────────────
    # 补充字段：HSI 成交额、北水净流向、A股总成交额
    # 只在对应字段为空时补充，不覆盖 scraper 已获取的数据
    doubao_market_available = bool(os.environ.get("ARK_API_KEY") and (
        os.environ.get("DOUBAO_BOT_MARKET") or os.environ.get("DOUBAO_BOT_MACRO")
    ))
    if doubao_market_available:
        logger.info("Step 1b: 豆包补充资金动态")
        try:
            from fetchers.doubao_macro import fetch_doubao_market_summary, parse_market_summary
            raw_summary = fetch_doubao_market_summary(date_hkt=now_hkt)
            if raw_summary:
                db_market = parse_market_summary(raw_summary)
                # 逐字段补充：只填 scraper 未获取到的
                # 注意：_safe 失败时返回 None，setdefault 对已存在的 None 值无效，
                # 须用 "or {}" 确保始终有可操作的 dict
                hsi = market_data.get("hsi") or {}
                market_data["hsi"] = hsi
                if hsi.get("close") is None and db_market["hsi"].get("close") is not None:
                    hsi["close"] = db_market["hsi"]["close"]
                    logger.info(f"  [DoubaoMarket] 补充 HSI close: {hsi['close']}")
                if hsi.get("pct") is None and db_market["hsi"].get("pct") is not None:
                    hsi["pct"] = db_market["hsi"]["pct"]
                if hsi.get("turnover_hkd_100m") is None and db_market["hsi"].get("turnover_hkd_100m") is not None:
                    hsi["turnover_hkd_100m"] = db_market["hsi"]["turnover_hkd_100m"]
                    logger.info(f"  [DoubaoMarket] 补充 HSI 成交额: {hsi['turnover_hkd_100m']} 亿")

                sb = market_data.get("southbound") or {}
                market_data["southbound"] = sb
                if sb.get("net_flow_hkd_100m") is None and db_market["southbound"].get("net_flow_hkd_100m") is not None:
                    sb["net_flow_hkd_100m"] = db_market["southbound"]["net_flow_hkd_100m"]
                    sb["direction"] = db_market["southbound"]["direction"] or ""
                    logger.info(f"  [DoubaoMarket] 补充北水: {sb['direction']} {sb['net_flow_hkd_100m']} 亿")

                ash = market_data.get("a_share") or {}
                market_data["a_share"] = ash
                if ash.get("total_turnover_trillion") is None and db_market["a_share"].get("total_turnover_trillion") is not None:
                    ash["total_turnover_trillion"] = db_market["a_share"]["total_turnover_trillion"]
                    logger.info(f"  [DoubaoMarket] 补充A股成交额: {ash['total_turnover_trillion']} 万亿")
        except Exception as e:
            logger.warning(f"豆包资金动态补充失败: {e}，使用 scraper 原始数据")

    # ── Step 2a: 豆包宏观（优先） ─────────────────────────────────────────────
    # 若配置了 ARK_API_KEY + DOUBAO_BOT_MACRO*，则用豆包直接生成宏观段落；
    # 否则（或豆包失败）降级到 RSS + LLM 方案。
    macro_section = None
    doubao_available = bool(os.environ.get("ARK_API_KEY") and (
        os.environ.get("DOUBAO_BOT_MACRO") or
        os.environ.get("DOUBAO_BOT_MACRO_CN") or
        os.environ.get("DOUBAO_BOT_MACRO_GLOBAL")
    ))

    if doubao_available:
        logger.info("Step 2a: 豆包 API 拉取宏观要闻（中国 + 全球）")
        try:
            from fetchers.doubao_macro import (
                fetch_doubao_macro_cn,
                fetch_doubao_macro_global,
                build_doubao_macro_section,
            )
            cn_text = fetch_doubao_macro_cn(date_hkt=now_hkt)
            global_text = fetch_doubao_macro_global(date_hkt=now_hkt)
            macro_section = build_doubao_macro_section(cn_text, global_text, date_hkt=now_hkt)
            if macro_section:
                logger.info("[DoubaoMacro] 宏观段落生成成功，跳过 RSS+LLM 方案")
            else:
                logger.warning("[DoubaoMacro] 中国/全球宏观均失败，降级到 RSS+LLM")
        except Exception as e:
            logger.error(f"豆包宏观模块异常: {e}，降级到 RSS+LLM")

    if macro_section is None:
        # 降级：RSS 抓取 + LLM 提炼（含人工精选注入）
        logger.info("Step 2a (降级): 抓取宏观 RSS + LLM 提炼")
        try:
            from fetchers.macro_news import fetch_macro_news
            macro_news_items = fetch_macro_news()
        except Exception as e:
            logger.error(f"宏观新闻抓取失败: {e}")
            macro_news_items = []
        try:
            from llm.refiner import refine_macro_news
            macro_section = refine_macro_news(
                macro_news_items,
                manual_items=manual_bundle.get("macro"),
            )
        except Exception as e:
            logger.error(f"宏观LLM提炼失败: {e}")
            macro_section = "▶️三、*宏觀及行業動態*\n• ⚠️ LLM提炼失败，请手动补充"
    elif manual_bundle.get("macro"):
        # Doubao 已生成宏观段落，但仍有人工精选 → 追加到段落顶部
        logger.info("Step 2a: 将人工精选宏观注入 Doubao 生成的段落")
        try:
            from llm.refiner import refine_macro_news
            # 仅用人工条目调用一次 LLM 格式化，再前插到 Doubao 结果
            manual_only_section = refine_macro_news(
                [],
                manual_items=manual_bundle.get("macro"),
            )
            # manual_only_section 格式: "▶️三、*...*\n• ..."
            # 取 Doubao 段落的 header 行保留，bullets 合并
            doubao_bullets = "\n".join(macro_section.splitlines()[1:])
            manual_bullets = "\n".join(manual_only_section.splitlines()[1:])
            header = macro_section.splitlines()[0]
            macro_section = f"{header}\n{manual_bullets}\n{doubao_bullets}"
        except Exception as e:
            logger.warning(f"人工宏观注入失败: {e}，保留 Doubao 原始结果")

    # ── Step 2b: 今日招股 ─────────────────────────────────────────────────────
    # 豆包优先（信息更丰富）→ 爬虫备用
    ipo_section = None
    doubao_ipo_available = bool(os.environ.get("ARK_API_KEY") and (
        os.environ.get("DOUBAO_BOT_IPO") or os.environ.get("DOUBAO_BOT_MACRO")
    ))

    # Step 2b: 招股信息
    # 流程：先尝试爬虫（提供结构化代码+日期）→ 豆包丰富内容
    #        爬虫无数据 → 豆包独立搜索（不输出 DATES 行，避免 [待查]）
    #        豆包也不可用 → 爬虫简版兜底
    logger.info("Step 2b: 抓取今日招股基础数据（爬虫）")
    ipo_list_scraped: list[dict] = []
    try:
        from fetchers.ipo_fetcher import fetch_hk_ipo_today, fmt_ipo_section
        ipo_list_scraped = fetch_hk_ipo_today(today=now_hkt.date())
    except Exception as e:
        logger.warning(f"今日招股爬虫失败: {e}")

    if doubao_ipo_available:
        # ipo_list_scraped 有数据→丰富模式；无数据→独立搜索模式（两者都传给同一函数）
        logger.info(
            f"Step 2b: 豆包{'丰富 ' + str(len(ipo_list_scraped)) + ' 只' if ipo_list_scraped else '独立搜索'}招股信息"
        )
        try:
            from fetchers.doubao_macro import fetch_doubao_ipo, fmt_doubao_ipo_section_smart
            doubao_ipo_text = fetch_doubao_ipo(
                ipo_list=ipo_list_scraped or None,   # None→独立搜索；有数据→丰富模式
                date_hkt=now_hkt,
            )
            if doubao_ipo_text:
                ipo_section = fmt_doubao_ipo_section_smart(doubao_ipo_text, today_date=now_hkt.date())
                logger.info("[DoubaoIPO] 招股信息生成成功")
            else:
                logger.warning("[DoubaoIPO] 豆包返回空，降级")
        except Exception as e:
            logger.error(f"豆包招股模块异常: {e}")

    if ipo_section is None:
        # 兜底：爬虫简版（无详细介绍）或错误提示
        ipo_section = (
            fmt_ipo_section(ipo_list_scraped)
            if ipo_list_scraped
            else "▶️五、*今日招股（新股認購）*\n• ⚠️ 數據獲取失敗，請手動補充"
        )

    if manual_bundle.get("ipo"):
        logger.info(f"Step 2b: 合并人工 IPO 输入 {len(manual_bundle['ipo'])} 条")
        ipo_section = _merge_manual_ipo_section(ipo_section, manual_bundle.get("ipo"))

    # Step 2c: 回购查询已移除（不在日报中独立汇报）
    buyback_subsection = “”

    # Step 2d: 业绩查询已移除（不在日报中独立汇报）
    earnings_subsection = “”

    # ── Step 2e: 抓取个股新闻 ─────────────────────────────────────────────────
    logger.info("Step 2e: 抓取个股新闻")
    try:
        from fetchers.stock_news import fetch_all_stock_news
        all_news = fetch_all_stock_news(request_interval=1.5)
    except Exception as e:
        logger.error(f"个股新闻模块失败: {e}")
        all_news = {}

    # ── Step 3: LLM 提炼个股动态 ──────────────────────────────────────────────
    logger.info("Step 3: LLM 提炼个股动态")
    try:
        from llm.refiner import refine_all_stocks
        stock_sections = refine_all_stocks(
            all_news,
            llm_interval=1.0,
            manual_stock_items=manual_bundle.get("stocks"),
        )
    except Exception as e:
        logger.error(f"LLM 提炼失败: {e}")
        stock_sections = [f"⚠️ LLM提炼失败: {e}"]

    # ── Step 4: 生成早报 ──────────────────────────────────────────────────────
    logger.info("Step 4: 生成早报文本")
    stock_prefix_subsection = "\n\n".join(
        part for part in [earnings_subsection, buyback_subsection] if part
    )
    brief_text = build_brief(
        market_data, stock_sections, now_hkt,
        macro_section=macro_section,
        ipo_section=ipo_section,
        buyback_subsection=stock_prefix_subsection,
    )

    # ── Step 5: 保存到文件 ────────────────────────────────────────────────────
    out_file = OUTPUT_DIR / f"{now_hkt.strftime('%Y%m%d')}.md"
    out_file.write_text(brief_text, encoding="utf-8")
    logger.info(f"早报已保存: {out_file}")

    # ── Step 6: 推送 Telegram ─────────────────────────────────────────────────
    logger.info("Step 6: 推送 Telegram")
    token = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    try:
        send_telegram(brief_text, token, chat_id)
        logger.info("Telegram 推送完成")
    except Exception as e:
        logger.error(f"Telegram 推送失败: {e}")

    logger.info("=== 早报流程结束 ===")
    print("\n" + "=" * 60)
    print(brief_text)
    print("=" * 60)


if __name__ == "__main__":
    main()
