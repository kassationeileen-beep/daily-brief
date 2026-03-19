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

def build_brief(market_data: dict, stock_sections: list[str], now_hkt: datetime) -> str:
    date_str = now_hkt.strftime("%Y-%m-%d")
    date_compact = now_hkt.strftime("%Y%m%d")

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
    sb_val = fmt_num(sb.get("net_flow_hkd_100m")) if sb.get("net_flow_hkd_100m") else "N/A"
    sb_dir = sb.get("direction", "N/A")
    if sb.get("error") and not sb.get("net_flow_hkd_100m"):
        sb_line = warn("南向資金")
    else:
        sb_line = f"- 南向資金（北水）淨{sb_dir}：{sb_val} 億港元"
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
        f"- 成交量：{n_vol}億股\n"
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
    if stock_sections:
        stocks_block = "▶️四、*個股動態*\n" + "\n".join(stock_sections)
    else:
        stocks_block = "▶️四、*個股動態*\n" + warn("個股新聞")

    # ── 拼接完整早报 ──────────────────────────────────────────────────────────
    brief = f"""Good Morning, {date_compact} Daily Brief
▶️一、*{date_str}核心資金動態*
{hsi_block}{a_block}{nikkei_block}
{fx_block}

{stocks_block}"""

    return brief.strip()


# ─────────────────────────────────────────────
# Telegram 推送
# ─────────────────────────────────────────────

def send_telegram(text: str, token: str, chat_id: str, chunk_size: int = 3800):
    """超 4000 字自动分段推送（保留 Markdown V1 格式）"""
    import httpx

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_chunk(chunk: str):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = httpx.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # 按段落分割，每段不超过 chunk_size
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > chunk_size:
            if current:
                chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para).strip() if current else para

    if current:
        chunks.append(current.strip())

    logger.info(f"Telegram 推送：共 {len(chunks)} 段")
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
    logger.info("Step 1: 抓取市场数据")
    try:
        from fetchers.market_data import fetch_all_market_data
        market_data = fetch_all_market_data()
    except Exception as e:
        logger.error(f"市场数据模块失败: {e}")
        market_data = {}

    # ── Step 2: 抓取个股新闻 ──────────────────────────────────────────────────
    logger.info("Step 2: 抓取个股新闻")
    try:
        from fetchers.stock_news import fetch_all_stock_news
        all_news = fetch_all_stock_news(request_interval=1.5)
    except Exception as e:
        logger.error(f"个股新闻模块失败: {e}")
        all_news = {}

    # ── Step 3: LLM 提炼 ──────────────────────────────────────────────────────
    logger.info("Step 3: LLM 提炼个股动态")
    try:
        from llm.refiner import refine_all_stocks
        stock_sections = refine_all_stocks(all_news, llm_interval=1.0)
    except Exception as e:
        logger.error(f"LLM 提炼失败: {e}")
        stock_sections = [f"⚠️ LLM提炼失败: {e}"]

    # ── Step 4: 生成早报 ──────────────────────────────────────────────────────
    logger.info("Step 4: 生成早报文本")
    brief_text = build_brief(market_data, stock_sections, now_hkt)

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
