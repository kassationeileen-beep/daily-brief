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
    """恒生指数：收盘价、涨跌幅、成交额（亿港元）
    主：yfinance ^HSI（海外服务器稳定）
    备：akshare stock_hk_index_daily_em（需能访问东方财富）
    """
    result = {"close": None, "pct": None, "turnover_hkd_100m": None, "error": None}

    # 主：yfinance
    try:
        import yfinance as yf
        tk = yf.Ticker("^HSI")
        hist = tk.history(period="5d")
        if hist is not None and not hist.empty:
            row = hist.iloc[-1]
            close = float(row["Close"])
            prev = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else close
            pct = (close - prev) / prev * 100
            volume = float(row.get("Volume", 0))
            # yfinance HSI Volume 单位为手(lot)，成交额需从info取或标注N/A
            info = tk.info
            turnover = None
            # regularMarketVolume * 某均价估算，或直接标注获取不到
            result.update({
                "close": round(close, 2),
                "pct": round(pct, 2),
                "turnover_hkd_100m": turnover,
            })
            logger.debug(f"[HSI] yfinance 成功: {close:.2f}")
            # 成交额尝试从 akshare 补充
    except Exception as e:
        logger.debug(f"[HSI-yfinance] {e}")

    # 补充成交额 / 备用收盘：akshare（可能超时）
    try:
        import akshare as ak
        df = ak.stock_hk_index_daily_em(symbol="恒生指数")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            cols = {c.lower(): c for c in df.columns}
            # 若 yfinance 未取到收盘价则用 akshare
            if result["close"] is None:
                close_col = cols.get("收盘", cols.get("close", list(cols.values())[1]))
                close = float(row[close_col])
                prev = float(df.iloc[-2][close_col]) if len(df) > 1 else close
                result["close"] = round(close, 2)
                result["pct"] = round((close - prev) / prev * 100, 2)
            # 成交额
            turnover_col = cols.get("成交额", cols.get("amount", None))
            if turnover_col:
                raw = float(row[turnover_col])
                result["turnover_hkd_100m"] = round(raw / 1e8 if raw > 1e8 else raw, 2)
    except Exception as e:
        logger.debug(f"[HSI-akshare] {e}（海外IP访问东方财富超时属正常）")
        if result["close"] is None:
            result["error"] = str(e)

    return result


def fetch_southbound_flow() -> dict:
    """南向资金（港股通）净流入，单位亿港元"""
    import akshare as ak
    result = {"net_flow_hkd_100m": None, "direction": None, "error": None}

    # 方法一：stock_hsgt_fund_flow_summary_em（无参数，返回沪深港通汇总）
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and not df.empty:
            logger.debug(f"[Southbound-v1] 列名: {list(df.columns)}, 行数: {len(df)}")
            # 找包含"南"的行（南向资金行）
            south_row = None
            for col in df.columns:
                mask = df[col].astype(str).str.contains("南", na=False)
                if mask.any():
                    south_row = df[mask].iloc[0]
                    break
            if south_row is None:
                south_row = df.iloc[-1]  # fallback 用最后一行
            # 找净流入列
            for col in ["今日净流入（亿元）", "净流入（亿）", "当日净买入（亿元）",
                        "净买入（亿元）", "当日净流入", "净流入"]:
                if col in df.columns:
                    val = float(south_row[col])
                    result["net_flow_hkd_100m"] = abs(round(val, 2))
                    result["direction"] = "買入" if val >= 0 else "賣出"
                    return result
            # 找不到已知列名，打印列名帮助调试
            logger.warning(f"[Southbound] 未找到净流入列，实际列名: {list(df.columns)}")
    except Exception as e:
        logger.debug(f"[Southbound-v1] {e}")

    # 方法二：stock_hsgt_hist_em 历史数据取最新一天
    try:
        df = ak.stock_hsgt_hist_em(symbol="南向资金")
        if df is not None and not df.empty:
            logger.debug(f"[Southbound-v2] 列名: {list(df.columns)}")
            row = df.iloc[-1]
            for col in df.columns:
                if "净" in str(col):
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

    # A股总成交额：先用 stock_zh_a_spot_em，超时则用交易所日报接口
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            for col in ["成交额", "amount", "总成交额"]:
                if col in df.columns:
                    total = df[col].sum()
                    result["total_turnover_trillion"] = round(total / 1e12, 2)
                    break
    except Exception as e:
        logger.debug(f"[A-turnover-v1] {e}（尝试备用接口）")
        # 备用：用沪深两市指数的 stock_zh_index_daily 无法得到总成交额
        # 改用 stock_market_activity_legu（乐股市场总览）
        try:
            df2 = ak.stock_market_activity_legu()
            if df2 is not None and not df2.empty:
                logger.debug(f"[A-turnover-v2] 列名: {list(df2.columns)}")
                for col in df2.columns:
                    if "成交" in str(col) and "额" in str(col):
                        val = float(str(df2[col].iloc[0]).replace(",", "").replace("万亿", ""))
                        result["total_turnover_trillion"] = round(val, 2)
                        break
        except Exception as e2:
            errors.append(f"A股成交额: {e2}")
            logger.warning(f"[A-turnover] 两种方式均失败: {e2}")

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
