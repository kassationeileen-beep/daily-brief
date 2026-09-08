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

def _hsi_session(now_hkt=None):
    """Morning brief always reports the last HK session before its local date."""
    from datetime import timezone
    import exchange_calendars as xcals
    hkt = timezone(timedelta(hours=8))
    now = now_hkt or datetime.now(hkt)
    if now.tzinfo is None:
        now = now.replace(tzinfo=hkt)
    day = now.astimezone(hkt).date() - timedelta(days=1)
    return xcals.get_calendar("XHKG").date_to_session(day, direction="previous").date()


def _parse_hkex_daily(html, expected_date, board):
    """Read only dated market highlights; currency is explicitly HK dollars."""
    import re
    from html import unescape
    text = unescape(re.sub(r"<[^>]+>", "", html))
    date_match = re.search(r"DATE:\s*(\d{2})\s+([A-Z]{3})\s+(\d{4})", text)
    if not date_match:
        raise ValueError("HKEX report date missing")
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    day, month, year = date_match.groups()
    actual = datetime(int(year), months.index(month) + 1, int(day)).date()
    if actual != expected_date:
        raise ValueError(f"HKEX stale/mismatched report: {actual}, expected {expected_date}")
    marker = "MAIN BOARD AND TRADING ONLY STOCKS" if board == "main" else "GROWTH ENTERPRISE MARKET"
    if marker not in text:
        raise ValueError("HKEX wrong board")
    amount = re.search(r"Today's Turnover:\s*\(HK\$\):\s*([\d,]+)(?=\s)", text)
    out = {"turnover_hkd": int(amount[1].replace(",", "")) if amount else None}
    if board == "main":
        # Columns: morning close, afternoon close, previous close, change, %change.
        row = re.search(r"HANG SENG INDEX[ \t]+([^\r\n]+)", text)
        if not row:
            raise ValueError("HKEX HSI row missing")
        cols = row[1].split()
        if len(cols) != 5:
            raise ValueError("HKEX HSI column layout changed")
        close, prev = float(cols[1].replace(",", "")), float(cols[2].replace(",", ""))
        import math
        if not all(math.isfinite(v) and v > 0 for v in (close, prev)):
            raise ValueError("HKEX invalid closing price")
        out.update(close=round(close, 2), pct=round((close / prev - 1) * 100, 2))
    return out


def _get_hkex_report(url):
    import requests
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    return response.text


def fetch_hsi(now_hkt=None) -> dict:
    """Dated HKEX close and Main Board + GEM turnover. Never estimate or backdate."""
    result = {"close": None, "pct": None, "turnover_hkd_100m": None,
              "trade_date": None, "source": "HKEX daily quotations", "error": None}
    try:
        day = _hsi_session(now_hkt)
        result["trade_date"] = day.isoformat()
        base = "https://www.hkex.com.hk/eng/stat/smstat/dayquot/"
        main_url = base + f"d{day:%y%m%d}e.htm"
        gem_url = base + f"GEM/e_G{day:%y%m%d}.htm"
        result.update(close_source=main_url, turnover_sources=[main_url, gem_url])
        main = _parse_hkex_daily(_get_hkex_report(main_url), day, "main")
        result.update(close=main["close"], pct=main["pct"])
        gem = _parse_hkex_daily(_get_hkex_report(gem_url), day, "gem")
        if main["turnover_hkd"] is None or gem["turnover_hkd"] is None:
            raise ValueError("HKEX market turnover missing; cannot publish partial total")
        result["turnover_hkd_100m"] = round(
            (main["turnover_hkd"] + gem["turnover_hkd"]) / 1e8, 2)
        logger.info("[HSI-HKEX] %s close=%s turnover=%s 亿港元 (Main Board + GEM)",
                    day, result["close"], result["turnover_hkd_100m"])
    except Exception as exc:
        result["error"] = str(exc)
        logger.warning("[HSI-HKEX] %s", exc)
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

    # 上证综指（顺带捕获最近交易日，供成交额按同日查询）
    ref_date = None  # 形如 '20260716'
    try:
        df = _timed(lambda: ak.stock_zh_index_daily(symbol="sh000001"), "SH")
        if df is not None and not df.empty:
            row = df.iloc[-1]
            close = float(row["close"])
            prev = float(df.iloc[-2]["close"]) if len(df) > 1 else close
            result["sh_close"] = round(close, 2)
            result["sh_pct"] = round((close - prev) / prev * 100, 2)
            try:
                ref_date = str(row["date"]).replace("-", "")[:8]
            except Exception:
                ref_date = None
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

    # 主：沪深交易所官方每日概况相加（stock_sse_deal_daily + stock_szse_summary）
    # 用 ref_date（上证 daily 最近交易日）查询，保证成交额与收盘价同一交易日。
    # 注：legu 接口 2026 年起已移除成交额字段，仅剩涨跌家数，故弃用。
    try:
        sh_amt = None  # 万亿元
        sz_amt = None
        if ref_date:
            # 上交所：股票 成交金额，单位亿元
            try:
                dfs = _timed(lambda: ak.stock_sse_deal_daily(date=ref_date), "A-turnover-SSE")
                if dfs is not None and not dfs.empty and "股票" in dfs.columns:
                    hit = dfs[dfs["单日情况"] == "成交金额"]
                    if not hit.empty:
                        sh_amt = float(hit["股票"].iloc[0]) / 1e4  # 亿元 → 万亿
            except Exception as e:
                logger.warning(f"[A-turnover-SSE] {e}")
            # 深交所：证券类别==股票 成交金额，单位元
            try:
                dfz = _timed(lambda: ak.stock_szse_summary(date=ref_date), "A-turnover-SZSE")
                if dfz is not None and not dfz.empty:
                    hit = dfz[dfz["证券类别"] == "股票"]
                    if not hit.empty:
                        sz_amt = float(hit["成交金额"].iloc[0]) / 1e12  # 元 → 万亿
            except Exception as e:
                logger.warning(f"[A-turnover-SZSE] {e}")
        if sh_amt is not None and sz_amt is not None:
            result["total_turnover_trillion"] = round(sh_amt + sz_amt, 4)
            logger.info(
                f"[A-turnover] 沪 {sh_amt:.4f} + 深 {sz_amt:.4f} "
                f"= {result['total_turnover_trillion']} 万亿 ({ref_date})"
            )
    except Exception as e:
        logger.warning(f"[A-turnover] 官方接口失败: {e}")

    # 备：全量 spot 成交额求和（慢但兜底）
    if result["total_turnover_trillion"] is None:
        try:
            df = _timed(lambda: ak.stock_zh_a_spot_em(), "A-turnover-spot")
            if df is not None and not df.empty:
                for col in ["成交额", "amount", "总成交额"]:
                    if col in df.columns:
                        total = df[col].sum()
                        if total > 0:
                            result["total_turnover_trillion"] = round(total / 1e12, 4)
                            logger.info(f"[A-turnover-spot] {result['total_turnover_trillion']} 万亿")
                        break
        except Exception as e2:
            errors.append(f"A股成交额: {e2}")
            logger.warning(f"[A-turnover] 官方+spot 均失败: {e2}")

    if errors:
        result["error"] = "; ".join(errors)
    return result


def fetch_nikkei225() -> dict:
    """日经225：收盘价、涨跌幅。
    主：日本经济新闻社（Nikkei Inc.）官方指数日线 CSV（指数编制方，最权威）
    备1：akshare index_global_spot_em（东财全球指数实时快照）
    备2：yfinance ^N225（Volume 固定为0，Futu 亦不支持 JP 指数）
    成交量无权威免费源，volume_100m 固定为 None。
    """
    result = {"close": None, "pct": None, "volume_100m": None, "error": None}

    # ── 主：Nikkei Inc. 官方指数日线 CSV ──────────────────────────────────────
    # 日经225 由日本经济新闻社编制并发布，此 CSV 是权威原始来源。
    # 列序：日期, 終値(收盘), 始値, 高値, 安値。文件为 Shift-JIS，数据行均 ASCII。
    # 取最后一行即最近交易日官方收盘：收盘后到次日东京开盘(9:00 JST=8:00 HKT)前，
    # 始终是上一交易日收盘，避免实时快照接口在东京已开盘后取到当日盘中价的风险。
    try:
        import requests, re
        url = "https://indexes.nikkei.co.jp/nkave/historical/nikkei_stock_average_daily_jp.csv"
        resp = _timed(
            lambda: requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}),
            "N225-official",
        )
        resp.raise_for_status()
        text = resp.content.decode("shift_jis", errors="ignore")
        rows = []
        for line in text.splitlines():
            m = re.match(r'^"(\d{4}/\d{2}/\d{2})","([\d.]+)"', line)
            if m:
                rows.append((m.group(1), float(m.group(2))))
        if len(rows) >= 2:
            date_str, close = rows[-1]
            prev = rows[-2][1]
            if close > 0 and prev > 0:
                pct = (close - prev) / prev * 100
                result.update({"close": round(close, 2), "pct": round(pct, 2)})
                logger.info(f"[N225-official] 收盘: {close:.2f} ({pct:+.2f}%) 日期: {date_str}")
                return result
        logger.warning("[N225-official] CSV 行数不足或解析失败")
    except Exception as e:
        logger.warning(f"[N225-official] {e}")

    # ── 备1：akshare 东财全球指数实时快照 ─────────────────────────────────────
    try:
        import akshare as ak
        df = ak.index_global_spot_em()
        row = df[df["代码"] == "N225"]
        if not row.empty:
            r = row.iloc[0]
            close = float(r["最新价"])
            prev  = float(r["昨收价"])
            pct   = float(r["涨跌幅"])
            logger.info(f"[N225-akshare] 收盘: {close:.2f} ({pct:+.2f}%) 更新: {r.get('最新行情时间','')}")
            result.update({"close": round(close, 2), "pct": round(pct, 2)})
            return result
    except Exception as e:
        logger.warning(f"[N225-akshare] {e}")

    # 备：yfinance（显式日期范围避免缓存错误）
    try:
        import yfinance as yf
        from datetime import date as date_type, timedelta as td
        tk = yf.Ticker("^N225")
        end   = (date_type.today() + td(days=1)).strftime("%Y-%m-%d")
        start = (date_type.today() - td(days=14)).strftime("%Y-%m-%d")
        hist = tk.history(start=start, end=end)
        if hist.empty:
            raise ValueError("空数据")
        row  = hist.iloc[-1]
        close = float(row["Close"])
        prev  = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else close
        pct   = (close - prev) / prev * 100
        logger.info(f"[N225-yf] 收盘: {close:.2f} ({pct:+.2f}%)")
        result.update({
            "close": round(close, 2),
            "pct": round(pct, 2),
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
