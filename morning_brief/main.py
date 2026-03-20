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
    # 结构：回购子段落（如有）在前，个股新闻在后
    section4_parts = ["▶️四、*個股動態*"]
    if buyback_subsection:
        section4_parts.append(buyback_subsection)
    if stock_sections:
        if buyback_subsection:
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
                hsi = market_data.setdefault("hsi", {})
                if hsi.get("close") is None and db_market["hsi"].get("close") is not None:
                    hsi["close"] = db_market["hsi"]["close"]
                    logger.info(f"  [DoubaoMarket] 补充 HSI close: {hsi['close']}")
                if hsi.get("pct") is None and db_market["hsi"].get("pct") is not None:
                    hsi["pct"] = db_market["hsi"]["pct"]
                if hsi.get("turnover_hkd_100m") is None and db_market["hsi"].get("turnover_hkd_100m") is not None:
                    hsi["turnover_hkd_100m"] = db_market["hsi"]["turnover_hkd_100m"]
                    logger.info(f"  [DoubaoMarket] 补充 HSI 成交额: {hsi['turnover_hkd_100m']} 亿")

                sb = market_data.setdefault("southbound", {})
                if sb.get("net_flow_hkd_100m") is None and db_market["southbound"].get("net_flow_hkd_100m") is not None:
                    sb["net_flow_hkd_100m"] = db_market["southbound"]["net_flow_hkd_100m"]
                    sb["direction"] = db_market["southbound"]["direction"] or ""
                    logger.info(f"  [DoubaoMarket] 补充北水: {sb['direction']} {sb['net_flow_hkd_100m']} 亿")

                ash = market_data.setdefault("a_share", {})
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
        # 降级：RSS 抓取 + LLM 提炼
        logger.info("Step 2a (降级): 抓取宏观 RSS + LLM 提炼")
        try:
            from fetchers.macro_news import fetch_macro_news
            macro_news_items = fetch_macro_news()
        except Exception as e:
            logger.error(f"宏观新闻抓取失败: {e}")
            macro_news_items = []
        try:
            from llm.refiner import refine_macro_news
            macro_section = refine_macro_news(macro_news_items)
        except Exception as e:
            logger.error(f"宏观LLM提炼失败: {e}")
            macro_section = "▶️三、*宏觀及行業動態*\n• ⚠️ LLM提炼失败，请手动补充"

    # ── Step 2b: 今日招股 ─────────────────────────────────────────────────────
    # 豆包优先（信息更丰富）→ 爬虫备用
    ipo_section = None
    doubao_ipo_available = bool(os.environ.get("ARK_API_KEY") and (
        os.environ.get("DOUBAO_BOT_IPO") or os.environ.get("DOUBAO_BOT_MACRO")
    ))

    if doubao_ipo_available:
        logger.info("Step 2b: 豆包 API 拉取今日招股信息")
        try:
            from fetchers.doubao_macro import fetch_doubao_ipo, fmt_doubao_ipo_section_smart
            doubao_ipo_text = fetch_doubao_ipo(date_hkt=now_hkt)
            if doubao_ipo_text:
                ipo_section = fmt_doubao_ipo_section_smart(doubao_ipo_text, today_date=now_hkt.date())
                logger.info("[DoubaoIPO] 招股信息生成成功（首日完整/续期提醒），跳过爬虫方案")
            else:
                logger.warning("[DoubaoIPO] 豆包 IPO 失败，降级到爬虫")
        except Exception as e:
            logger.error(f"豆包招股模块异常: {e}，降级到爬虫")

    if ipo_section is None:
        logger.info("Step 2b (降级): 爬虫抓取今日招股")
        try:
            from fetchers.ipo_fetcher import fetch_hk_ipo_today, fmt_ipo_section
            ipo_list = fetch_hk_ipo_today(today=now_hkt.date())
            ipo_section = fmt_ipo_section(ipo_list)
        except Exception as e:
            logger.error(f"今日招股抓取失败: {e}")
            ipo_section = "▶️五、*今日招股（新股認購）*\n• ⚠️ 數據獲取失敗，請手動補充"

    # ── Step 2c: 豆包回购查询 ─────────────────────────────────────────────────
    # 豆包搜索全港 24h 回购 → Python 匹配 watchlist → 格式化子段落
    # 百胜中国（T+2 披露惯例）单独 48h 查询，合并进结果
    buyback_subsection = ""
    doubao_buyback_available = bool(os.environ.get("ARK_API_KEY") and (
        os.environ.get("DOUBAO_BOT_BUYBACK") or os.environ.get("DOUBAO_BOT_MACRO")
    ))

    if doubao_buyback_available:
        logger.info("Step 2c: 豆包 API 查询港股回购（24h 全市场 + 百胜中国 48h）")
        try:
            from fetchers.doubao_macro import (
                fetch_doubao_buybacks,
                fetch_doubao_buyback_yumchina,
                parse_buyback_lines,
                match_watchlist_buybacks,
                fmt_buyback_subsection,
            )
            from fetchers.stock_news import HK_STOCKS

            # 24h 全市场回购
            raw_buybacks = fetch_doubao_buybacks(date_hkt=now_hkt)
            all_items = parse_buyback_lines(raw_buybacks or "")

            # 百胜中国 48h 专用查询，合并（去重）
            raw_yum = fetch_doubao_buyback_yumchina(date_hkt=now_hkt)
            yum_items = parse_buyback_lines(raw_yum or "")
            existing_codes = {item["code"] for item in all_items}
            for item in yum_items:
                if item["code"] not in existing_codes:
                    all_items.append(item)
                    existing_codes.add(item["code"])

            # 匹配 watchlist，格式化
            matched = match_watchlist_buybacks(all_items, HK_STOCKS)
            buyback_subsection = fmt_buyback_subsection(matched)
            if matched:
                logger.info(f"[DoubaoByback] watchlist 命中 {len(matched)} 只：{[m['watchlist_name'] for m in matched]}")
            else:
                logger.info("[DoubaoByback] watchlist 内今日无回购")
        except Exception as e:
            logger.error(f"豆包回购模块异常: {e}")

    # ── Step 2d: 抓取个股新闻 ─────────────────────────────────────────────────
    logger.info("Step 2d: 抓取个股新闻")
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
        stock_sections = refine_all_stocks(all_news, llm_interval=1.0)
    except Exception as e:
        logger.error(f"LLM 提炼失败: {e}")
        stock_sections = [f"⚠️ LLM提炼失败: {e}"]

    # ── Step 4: 生成早报 ──────────────────────────────────────────────────────
    logger.info("Step 4: 生成早报文本")
    brief_text = build_brief(
        market_data, stock_sections, now_hkt,
        macro_section=macro_section,
        ipo_section=ipo_section,
        buyback_subsection=buyback_subsection,
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
