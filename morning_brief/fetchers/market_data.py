"""
market_data.py — 第一、二部分：指数 + 汇率数据抓取
"""
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _safe(fn, label: str):
    """执行 fn()，失败时返回 None 并记录警告"""
    try:
        return fn()
    except Exception as e:
        logger.warning(f"[{label}] 获取失败: {e}")
        return None


# ─────────────────────────────────────────────
# 第一部分：股票指数
# ─────────────────────────────────────────────

def fetch_hsi() -> dict:
    """恒生指数：收盘价、涨跌幅、成交额（亿港元）"""
    import akshare as ak
    result = {"close": None, "pct": None, "turnover_hkd_100m": None, "error": None}
    try:
        df = ak.stock_hk_index_daily_em(symbol="恒生指数")
        if df is None or df.empty:
            raise ValueError("空数据")
        row = df.iloc[-1]
        # 列名因版本而异，做兼容处理
        cols = {c.lower(): c for c in df.columns}
        close = float(row[cols.get("收盘", cols.get("close", list(cols.values())[1]))])
        prev_close_col = cols.get("昨收", cols.get("open", None))
        if prev_close_col and len(df) > 1:
            prev_close = float(df.iloc[-2][cols.get("收盘", list(cols.values())[1])])
            pct = (close - prev_close) / prev_close * 100
        else:
            pct = float(row.get("涨跌幅", row.get("pct_chg", 0)))

        # 成交额：原始单位可能是元，转换为亿港元
        turnover_col = cols.get("成交额", cols.get("amount", None))
        turnover = None
        if turnover_col:
            raw = float(row[turnover_col])
            # akshare 恒生指数成交额单位通常为「亿港元」
            # 若数值 > 1e8 则认为单位是元，需除以 1e8
            turnover = raw / 1e8 if raw > 1e8 else raw

        result.update({"close": round(close, 2), "pct": round(pct, 2),
                        "turnover_hkd_100m": round(turnover, 2) if turnover else None})
    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"[HSI] {e}")
    return result


def fetch_southbound_flow() -> dict:
    """南向资金（港股通）净流入，单位亿港元"""
    import akshare as ak
    result = {"net_flow_hkd_100m": None, "direction": None, "error": None}
    try:
        # 方法一：hsgt_fund_flow_summary_em
        df = ak.stock_hsgt_fund_flow_summary_em(indicator="南向资金")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            # 尝试常见列名
            for col in ["今日净流入（亿元）", "净流入（亿）", "当日净买入（亿元）", "净买入"]:
                if col in df.columns:
                    val = float(row[col])
                    result["net_flow_hkd_100m"] = abs(round(val, 2))
                    result["direction"] = "買入" if val >= 0 else "賣出"
                    return result
    except Exception as e:
        logger.debug(f"[Southbound-v1] {e}")

    try:
        # 方法二：stock_em_hsgt_north_net_flow_in（接口有南向选项）
        df = ak.stock_em_hsgt_north_net_flow_in(indicator="南向资金")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            for col in df.columns:
                if "净" in col or "flow" in col.lower():
                    val = float(row[col])
                    result["net_flow_hkd_100m"] = abs(round(val, 2))
                    result["direction"] = "買入" if val >= 0 else "賣出"
                    return result
    except Exception as e:
        logger.debug(f"[Southbound-v2] {e}")

    result["error"] = "南向资金数据获取失败"
    return result


def fetch_a_share_indices() -> dict:
    """上证综指、深证成指、A股总成交额"""
    import akshare as ak
    result = {
        "sh_close": None, "sh_pct": None,
        "sz_close": None, "sz_pct": None,
        "total_turnover_trillion": None,
        "error": None
    }
    errors = []

    # 上证综指
    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            close = float(row["close"])
            prev = float(df.iloc[-2]["close"]) if len(df) > 1 else close
            result["sh_close"] = round(close, 2)
            result["sh_pct"] = round((close - prev) / prev * 100, 2)
    except Exception as e:
        errors.append(f"上证: {e}")
        logger.warning(f"[SH] {e}")

    # 深证成指
    try:
        df = ak.stock_zh_index_daily(symbol="sz399001")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            close = float(row["close"])
            prev = float(df.iloc[-2]["close"]) if len(df) > 1 else close
            result["sz_close"] = round(close, 2)
            result["sz_pct"] = round((close - prev) / prev * 100, 2)
    except Exception as e:
        errors.append(f"深证: {e}")
        logger.warning(f"[SZ] {e}")

    # A股总成交额（汇总 stock_zh_a_spot_em 的成交额列）
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            # 找成交额列
            for col in ["成交额", "amount", "总成交额"]:
                if col in df.columns:
                    total = df[col].sum()
                    # 单位：元 → 万亿
                    result["total_turnover_trillion"] = round(total / 1e12, 2)
                    break
    except Exception as e:
        errors.append(f"A股成交额: {e}")
        logger.warning(f"[A-turnover] {e}")

    if errors:
        result["error"] = "; ".join(errors)
    return result


def fetch_nikkei225() -> dict:
    """日经225：收盘价、涨跌幅、成交量（亿股）"""
    import yfinance as yf
    result = {"close": None, "pct": None, "volume_100m": None, "error": None}
    try:
        tk = yf.Ticker("^N225")
        hist = tk.history(period="5d")
        if hist.empty:
            raise ValueError("空数据")
        row = hist.iloc[-1]
        close = float(row["Close"])
        prev = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else close
        pct = (close - prev) / prev * 100
        volume = float(row["Volume"])
        result.update({
            "close": round(close, 2),
            "pct": round(pct, 2),
            "volume_100m": round(volume / 1e8, 4)
        })
    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"[N225] {e}")
    return result


# ─────────────────────────────────────────────
# 第二部分：汇率 & 大宗商品
# ─────────────────────────────────────────────

def _yf_price(ticker: str) -> Optional[float]:
    """取 yfinance 最新价（regularMarketPrice 优先）"""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    info = tk.info
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if price:
        return float(price)
    # fallback: 最近1日历史
    hist = tk.history(period="2d")
    if not hist.empty:
        return float(hist["Close"].iloc[-1])
    raise ValueError(f"无法取得 {ticker} 价格")


def fetch_fx_and_commodities() -> dict:
    """汇率、贵金属、原油、BTC、DXY"""
    tickers = {
        "USD/JPY":  "USDJPY=X",
        "USD/CHF":  "USDCHF=X",
        "USD/CNH":  "USDCNH=X",
        "USD/HKD":  "HKD=X",
        "XAU/USD":  "GC=F",
        "DXY":      "DX-Y.NYB",
        "BTC/USD":  "BTC-USD",
        "Brent":    "BZ=F",
        "WTI":      "CL=F",
    }
    result = {k: None for k in tickers}
    result["CNH/HKD"] = None
    errors = []

    for name, sym in tickers.items():
        try:
            price = _yf_price(sym)
            result[name] = round(price, 4) if price else None
        except Exception as e:
            errors.append(f"{name}: {e}")
            logger.warning(f"[FX/{name}] {e}")
        time.sleep(0.3)  # 礼貌延迟

    # CNH/HKD = USD/HKD ÷ USD/CNH
    try:
        if result["USD/HKD"] and result["USD/CNH"]:
            result["CNH/HKD"] = round(result["USD/HKD"] / result["USD/CNH"], 6)
    except Exception as e:
        errors.append(f"CNH/HKD计算: {e}")

    if errors:
        result["_errors"] = errors
    return result


# ─────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────

def fetch_all_market_data() -> dict:
    """抓取所有行情数据，返回结构化字典"""
    logger.info("开始抓取市场数据...")
    data = {}

    data["hsi"] = _safe(fetch_hsi, "HSI")
    data["southbound"] = _safe(fetch_southbound_flow, "南向资金")
    data["a_share"] = _safe(fetch_a_share_indices, "A股")
    data["nikkei"] = _safe(fetch_nikkei225, "日经225")

    logger.info("抓取汇率数据...")
    data["fx"] = _safe(fetch_fx_and_commodities, "FX")

    logger.info("市场数据抓取完成")
    return data
