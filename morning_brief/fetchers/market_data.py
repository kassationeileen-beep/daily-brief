"""
market_data.py — 第一、二部分：指数 + 汇率数据抓取
"""
import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_AKSHARE_TIMEOUT = 20  # seconds per akshare call


def _timed(fn, label: str, timeout: int = _AKSHARE_TIMEOUT):
    """在独立线程中运行 fn()，超时则立即抛出 TimeoutError（不等线程结束）"""
    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(fn)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        ex.shutdown(wait=False)
        raise TimeoutError(f"{label} 请求超时 ({timeout}s)")
    finally:
        ex.shutdown(wait=False)


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
    收盘价主：yfinance ^HSI（海外服务器稳定）
    成交额主：akshare stock_hk_index_daily_em（需能访问东方财富，海外IP可能超时）
    成交额备：yfinance regularMarketVolume × 近似均价（粗估，误差约5-15%）
    """
    result = {"close": None, "pct": None, "turnover_hkd_100m": None, "error": None}

    # ── 收盘价：yfinance ──────────────────────────────────────────────────────
    yf_avg_price = None   # 用于备用成交额估算
    try:
        import yfinance as yf
        tk = yf.Ticker("^HSI")
        hist = tk.history(period="5d")
        if hist is not None and not hist.empty:
            row = hist.iloc[-1]
            close = float(row["Close"])
            prev = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else close
            pct = (close - prev) / prev * 100
            yf_avg_price = (float(row["High"]) + float(row["Low"])) / 2
            result.update({"close": round(close, 2), "pct": round(pct, 2)})
            logger.debug(f"[HSI] yfinance 收盘: {close:.2f}")
    except Exception as e:
        logger.warning(f"[HSI-yfinance] {e}")

    # ── 成交额主：akshare stock_hk_index_daily_em ─────────────────────────────
    # 注：海外IP访问东方财富可能超时，失败时自动降级到备用方案
    try:
        import akshare as ak
        df = _timed(lambda: ak.stock_hk_index_daily_em(symbol="恒生指数"), "HSI-akshare")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            logger.debug(f"[HSI-akshare] 列名: {list(df.columns)}")
            # 若 yfinance 未取到收盘价则补充
            if result["close"] is None:
                for cname in ["收盘", "close", "Close"]:
                    if cname in df.columns:
                        close = float(row[cname])
                        prev = float(df.iloc[-2][cname]) if len(df) > 1 else close
                        result["close"] = round(close, 2)
                        result["pct"] = round((close - prev) / prev * 100, 2)
                        break
            # 成交额
            for cname in ["成交额", "amount", "Amount", "turnover", "Turnover"]:
                if cname in df.columns:
                    raw = float(row[cname])
                    # akshare 恒生指数 成交额单位通常为亿港元（raw ≈ 1000-2000）
                    # 若取到原始元值（raw > 1e10），则除以1e8换算
                    result["turnover_hkd_100m"] = round(raw / 1e8 if raw > 1e10 else raw, 2)
                    logger.debug(f"[HSI-akshare] 成交额 raw={raw} → {result['turnover_hkd_100m']} 亿港元")
                    break
    except Exception as e:
        logger.warning(f"[HSI-akshare] 失败（海外IP访问东方财富超时属正常）: {e}")
        if result["close"] is None:
            result["error"] = str(e)

    # ── 成交额备2：爬 HKEX 官方市场统计页（新加坡等境外IP可访问）──────────────
    if result["turnover_hkd_100m"] is None:
        try:
            import requests
            from bs4 import BeautifulSoup
            # HKEX 每日市场统计摘要页（无需登录，境外可访问）
            hkex_url = "https://www.hkex.com.hk/eng/market/sec_tradinfo/secstat/mktsum.htm"
            resp = requests.get(hkex_url, timeout=12, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.hkex.com.hk/",
            })
            soup = BeautifulSoup(resp.text, "lxml")
            # 找包含"Main Board Turnover"或"总成交额"的表格行
            for tag in soup.find_all(string=lambda t: t and (
                "Main Board Turnover" in t or "Turnover" in t
            )):
                parent = tag.find_parent("tr")
                if not parent:
                    continue
                cells = parent.find_all("td")
                for cell in cells:
                    txt = cell.get_text(strip=True).replace(",", "").replace("HK$", "")
                    try:
                        val = float(txt)
                        if 1e10 < val < 1e14:   # 合理成交额范围（元）
                            result["turnover_hkd_100m"] = round(val / 1e8, 2)
                            logger.info(f"[HSI-HKEX] 成交额: {result['turnover_hkd_100m']} 亿港元")
                            break
                    except ValueError:
                        continue
                if result["turnover_hkd_100m"] is not None:
                    break
        except Exception as e:
            logger.warning(f"[HSI-HKEX] 爬取失败: {e}")

    return result


def fetch_southbound_flow() -> dict:
    """南向资金（港股通）净流入，单位亿港元"""
    import akshare as ak
    result = {"net_flow_hkd_100m": None, "direction": None, "error": None}

    def _normalize_sb(val: float, label: str) -> float:
        """南向资金单位校验：正常范围 10–2000 亿港元/天，> 5000 视为万元自动换算"""
        if abs(val) > 5000:
            logger.warning(f"[{label}] 数值 {val:.2f} 异常大，疑为万元，÷10000 换算为亿元")
            val = val / 10000
        return val

    # 方法一：stock_hsgt_fund_min_em 分时数据（push2 实时推送端点，最可靠）
    # 列：日期, 时间, 港股通(沪), 港股通(深), 南向资金（累计净流入，亿元）
    # 2024-08-19 交易所更改披露机制后，此端点仍可正常访问
    try:
        df = _timed(lambda: ak.stock_hsgt_fund_min_em(symbol="南向资金"), "Southbound-min")
        if df is not None and not df.empty:
            row = df.iloc[-1]  # 取最新分钟（当日最新累计值）
            val = float(row["南向资金"])
            if val != 0:  # 0 可能是未开市，跳过
                val = _normalize_sb(val, "Southbound-min")
                result["net_flow_hkd_100m"] = abs(round(val, 2))
                result["direction"] = "買入" if val >= 0 else "賣出"
                logger.info(f"[Southbound-min] 南向净流入: {val:.2f} 亿元 ({row['日期']} {row['时间']})")
                return result
    except Exception as e:
        logger.debug(f"[Southbound-min] {e}")

    # 方法二：stock_hsgt_fund_flow_summary_em（无参数），过滤南向资金行
    # 注：该函数无 indicator 参数，返回沪深港通所有方向数据，需按板块过滤
    try:
        df = _timed(lambda: ak.stock_hsgt_fund_flow_summary_em(), "Southbound-v1")
        if df is not None and not df.empty:
            logger.debug(f"[Southbound-v1] 板块值: {df['板块'].unique().tolist()}")
            # 过滤南向：港股通沪 + 港股通深（板块列包含"港股通"或"南向"）
            south = df[df["板块"].str.contains("港股通|南向", na=False)]
            if not south.empty:
                val = float(south["成交净买额"].sum())  # 已转换为亿元
                val = _normalize_sb(val, "Southbound-v1")
                result["net_flow_hkd_100m"] = abs(round(val, 2))
                result["direction"] = "買入" if val >= 0 else "賣出"
                logger.info(f"[Southbound-v1] 南向净买额: {val:.2f} 亿元")
                return result
    except Exception as e:
        logger.debug(f"[Southbound-v1] {e}")

    # 方法三：stock_hsgt_hist_em 历史数据最新一行
    try:
        df = _timed(lambda: ak.stock_hsgt_hist_em(symbol="南向资金"), "Southbound-v2")
        if df is not None and not df.empty:
            row = df.iloc[-1]  # 已按日期升序排列
            val = float(row["当日成交净买额"])  # 单位亿元
            val = _normalize_sb(val, "Southbound-v2")
            result["net_flow_hkd_100m"] = abs(round(val, 2))
            result["direction"] = "買入" if val >= 0 else "賣出"
            logger.info(f"[Southbound-v2] 南向净买额: {val:.2f} 亿元 (日期: {row['日期']})")
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
        df = _timed(lambda: ak.stock_zh_index_daily(symbol="sh000001"), "SH")
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
        df = _timed(lambda: ak.stock_zh_index_daily(symbol="sz399001"), "SZ")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            close = float(row["close"])
            prev = float(df.iloc[-2]["close"]) if len(df) > 1 else close
            result["sz_close"] = round(close, 2)
            result["sz_pct"] = round((close - prev) / prev * 100, 2)
    except Exception as e:
        errors.append(f"深证: {e}")
        logger.warning(f"[SZ] {e}")

    # A股总成交额
    # 主：stock_market_activity_legu（单次请求，轻量）
    # 备：stock_zh_a_spot_em（全量下载5000+股，慢但可靠）
    def _parse_turnover_str(s: str) -> Optional[float]:
        """解析'1.23万亿'/'12345亿'/'1.23e12'(元)等格式，统一返回万亿人民币"""
        s = str(s).replace(",", "").strip()
        try:
            if "万亿" in s:
                return round(float(s.replace("万亿", "").strip()), 4)
            elif "亿" in s:
                return round(float(s.replace("亿", "").strip()) / 10000, 4)
            else:
                val = float(s)
                if val > 1e11:      # 原始元值
                    return round(val / 1e12, 4)
                elif val > 1e7:     # 亿元值
                    return round(val / 10000, 4)
                else:               # 已经是万亿
                    return round(val, 4)
        except (ValueError, TypeError):
            return None

    try:
        df2 = _timed(lambda: ak.stock_market_activity_legu(), "A-turnover-legu")
        if df2 is not None and not df2.empty:
            logger.debug(f"[A-turnover-legu] 列名: {list(df2.columns)}, 数据:\n{df2.to_string()}")
            # 遍历所有列和行，寻找包含"成交"+"额"的字段
            found = False
            for col in df2.columns:
                if "成交" in str(col) and "额" in str(col):
                    val = _parse_turnover_str(df2[col].iloc[0])
                    if val is not None:
                        result["total_turnover_trillion"] = val
                        found = True
                        break
            # 若列名中没有，尝试从值列中匹配（宽表转长表格式）
            if not found:
                for _, row2 in df2.iterrows():
                    for col in df2.columns:
                        cell = str(row2.get(col, ""))
                        if "成交额" in cell or "沪深成交" in cell:
                            # 找同行或下一列的数值
                            cols_list = list(df2.columns)
                            idx = cols_list.index(col)
                            if idx + 1 < len(cols_list):
                                val = _parse_turnover_str(row2[cols_list[idx + 1]])
                                if val is not None:
                                    result["total_turnover_trillion"] = val
                                    found = True
                                    break
                    if found:
                        break
    except Exception as e:
        logger.warning(f"[A-turnover-legu] {e}，尝试备用接口")
        try:
            df = _timed(lambda: ak.stock_zh_a_spot_em(), "A-turnover-spot")
            if df is not None and not df.empty:
                logger.debug(f"[A-turnover-spot] 列名: {list(df.columns)}")
                for col in ["成交额", "amount", "总成交额"]:
                    if col in df.columns:
                        total = df[col].sum()
                        result["total_turnover_trillion"] = round(total / 1e12, 4)
                        break
        except Exception as e2:
            errors.append(f"A股成交额: {e2}")
            logger.warning(f"[A-turnover] 两种方式均失败: {e2}")

    if errors:
        result["error"] = "; ".join(errors)
    return result


def fetch_nikkei225() -> dict:
    """日经225：收盘价、涨跌幅、成交量（亿股）
    注：yfinance ^N225 Volume 通常为0，此时 volume_100m 保留为 None（供手动补充）
    """
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
        volume = float(row.get("Volume", 0) or 0)
        result.update({
            "close": round(close, 2),
            "pct": round(pct, 2),
            "volume_100m": round(volume / 1e8, 4) if volume > 0 else None,
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
