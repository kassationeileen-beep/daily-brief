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

def _fetch_hsi_futu() -> dict:
    """恒生指数：富途 OpenD HK.800000 快照（收盘价 + 涨跌幅 + 成交额一次原子取得）。
    update_time 为港股收盘时刻（16:00），收盘后到次日开盘前快照保持上一交易日收盘，
    无时区选行/盘中歧义。turnover 为恒指成分股口径成交额（港元）。
    """
    import os
    out = {"close": None, "pct": None, "turnover_hkd_100m": None, "trade_date": None}
    ctx = None
    try:
        from futu import OpenQuoteContext, RET_OK
        host = os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
        port = int(os.environ.get("FUTU_OPEND_PORT", "11111"))
        ctx = OpenQuoteContext(host=host, port=port)
        ret, data = ctx.get_market_snapshot(["HK.800000"])
        if ret != RET_OK or data is None or data.empty:
            logger.warning(f"[HSI-futu] 快照失败: {data if ret != RET_OK else '空'}")
            return out
        r = data.iloc[0]
        close = float(r["last_price"])
        prev  = float(r["prev_close_price"])
        turnover = float(r["turnover"])
        if close > 0:
            out["close"] = round(close, 2)
            if prev > 0:
                out["pct"] = round((close - prev) / prev * 100, 2)
        if turnover > 0:
            out["turnover_hkd_100m"] = round(turnover / 1e8, 2)
        # update_time 形如 "2026-09-28 16:00:00"，取日期部分
        upd = str(r.get("update_time", "") or "")
        if len(upd) >= 10:
            out["trade_date"] = upd[:10]
        logger.info(
            f"[HSI-futu] 收盘: {out['close']} ({out['pct']:+.2f}%) "
            f"成交额: {out['turnover_hkd_100m']} 亿港元 日期: {out['trade_date']}"
        )
    except Exception as e:
        logger.warning(f"[HSI-futu] {e}")
    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass
    return out


# ── 数值合理性校验（拒绝垃圾数据流入早报）────────────────────────────────────

def _hsi_close_ok(v) -> bool:
    """恒指收盘价合理区间：5000–100000 点"""
    return v is not None and 5000 < v < 100000


def _hsi_turnover_ok(v) -> bool:
    """恒指成交额合理区间：200–30000 亿港元/日（历史极值均在区间内）"""
    return v is not None and 200 <= v <= 30000


def _fetch_hsi_tencent() -> dict:
    """腾讯行情 qt.gtimg.cn/q=r_hkHSI（境外直连，无需 key）。
    收盘价 + 昨收 + 成交额（万港元）+ 行情时间一次原子取得，口径为恒指成分股成交额，
    与富途主源一致。收盘后到次日开盘前保持上一交易日数据，与 07:30 早报场景匹配。
    """
    import requests
    out = {"close": None, "pct": None, "turnover_hkd_100m": None, "trade_date": None}
    resp = requests.get("https://qt.gtimg.cn/q=r_hkHSI", timeout=10,
                        headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="ignore")
    payload = text.split('="', 1)[1].rstrip('";\n ')
    f = payload.split("~")
    # f[3]=最新价 f[4]=昨收 f[6]=成交额(万港元) f[30]=行情时间 f[31]=涨跌 f[32]=涨跌%
    close, prev = float(f[3]), float(f[4])
    if close > 0:
        out["close"] = round(close, 2)
        if prev > 0:
            out["pct"] = round((close - prev) / prev * 100, 2)
    amount_wan = float(f[6])
    if amount_wan > 0:
        out["turnover_hkd_100m"] = round(amount_wan / 1e4, 2)  # 万港元 → 亿港元
    ts = f[30].strip()  # 形如 "2026/09/28 18:31:27"
    if len(ts) >= 10:
        out["trade_date"] = ts[:10].replace("/", "-")
    logger.info(
        f"[HSI-tencent] 收盘: {out['close']} ({out['pct']:+.2f}%) "
        f"成交额: {out['turnover_hkd_100m']} 亿港元 日期: {out['trade_date']}"
    )
    return out


def _fetch_hsi_sina() -> dict:
    """新浪行情 hq.sinajs.cn/list=rt_hkHSI（需带 Referer，境外可访问）。
    收盘价 + 昨收 + 成交额（千港元）+ 日期一次原子取得，口径为恒指成分股成交额。
    """
    import requests
    out = {"close": None, "pct": None, "turnover_hkd_100m": None, "trade_date": None}
    resp = requests.get(
        "https://hq.sinajs.cn/list=rt_hkHSI", timeout=10,
        headers={"Referer": "https://finance.sina.com.cn",
                 "User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="ignore")
    payload = text.split('="', 1)[1].rstrip('";\n ')
    f = payload.split(",")
    # f[2]=今开 f[3]=昨收 f[6]=最新价 f[11]=成交额(千港元) f[17]=日期 f[18]=时间
    close, prev = float(f[6]), float(f[3])
    if close > 0:
        out["close"] = round(close, 2)
        if prev > 0:
            out["pct"] = round((close - prev) / prev * 100, 2)
    amount_qian = float(f[11])
    if amount_qian > 0:
        out["turnover_hkd_100m"] = round(amount_qian / 1e5, 2)  # 千港元 → 亿港元
    d = f[17].strip().replace("/", "-")
    out["trade_date"] = d or None
    logger.info(
        f"[HSI-sina] 收盘: {out['close']} ({out['pct']:+.2f}%) "
        f"成交额: {out['turnover_hkd_100m']} 亿港元 日期: {out['trade_date']}"
    )
    return out


def fetch_hsi() -> dict:
    """恒生指数：收盘价、涨跌幅、成交额（亿港元）、交易日
    数据源优先级（收盘价与成交额同源原子取得，口径统一为恒指成分股成交额）：
    1. 富途 OpenD HK.800000 快照（最稳）
    2. 腾讯行情 qt.gtimg.cn/q=r_hkHSI（境外直连，收盘+成交额+时间一次取得）
    3. 新浪行情 hq.sinajs.cn/list=rt_hkHSI
    4. akshare index_global_spot_em（仅收盘价兜底，无成交额；东财接口境外常超时）
    5. yfinance ^HSI（仅收盘价兜底。绝不用其 Volume 估算成交额：
       Volume 是成分股股数，乘指数点位量纲错误，实测高估 ~250 倍）
    每个字段入库前经过合理性校验；trade_date 记录数据所属交易日供早报标题使用。
    """
    result = {"close": None, "pct": None, "turnover_hkd_100m": None,
              "trade_date": None, "error": None}

    def _fill(src: dict, label: str):
        """逐字段填充：只接受通过合理性校验的值"""
        if not src:
            return
        if result["close"] is None and _hsi_close_ok(src.get("close")):
            result["close"] = src["close"]
            result["pct"] = src.get("pct")
        elif src.get("close") is not None and not _hsi_close_ok(src.get("close")):
            logger.warning(f"[HSI-{label}] 收盘价 {src.get('close')} 超出合理区间，丢弃")
        if result["turnover_hkd_100m"] is None and _hsi_turnover_ok(src.get("turnover_hkd_100m")):
            result["turnover_hkd_100m"] = src["turnover_hkd_100m"]
        elif src.get("turnover_hkd_100m") is not None and not _hsi_turnover_ok(src.get("turnover_hkd_100m")):
            logger.warning(f"[HSI-{label}] 成交额 {src.get('turnover_hkd_100m')} 亿港元超出合理区间，丢弃")
        if result["trade_date"] is None and src.get("trade_date"):
            result["trade_date"] = src["trade_date"]

    # ── 1. 富途 OpenD HK.800000 ───────────────────────────────────────────────
    _fill(_fetch_hsi_futu(), "futu")

    # ── 2. 腾讯行情（收盘价+成交额原子取得）────────────────────────────────────
    if result["close"] is None or result["turnover_hkd_100m"] is None:
        try:
            _fill(_fetch_hsi_tencent(), "tencent")
        except Exception as e:
            logger.warning(f"[HSI-tencent] 失败: {e}")

    # ── 3. 新浪行情（收盘价+成交额原子取得）────────────────────────────────────
    if result["close"] is None or result["turnover_hkd_100m"] is None:
        try:
            _fill(_fetch_hsi_sina(), "sina")
        except Exception as e:
            logger.warning(f"[HSI-sina] 失败: {e}")

    # ── 4. akshare index_global_spot_em（仅收盘价兜底，无成交额）────────────────
    if result["close"] is None:
        try:
            import akshare as ak
            df = _timed(lambda: ak.index_global_spot_em(), "HSI-spot")
            if df is not None and not df.empty:
                row = df[df["代码"] == "HSI"]
                if not row.empty:
                    r = row.iloc[0]
                    close = float(r["最新价"])
                    pct   = float(r["涨跌幅"])
                    if _hsi_close_ok(close):
                        result["close"] = round(close, 2)
                        result["pct"] = round(pct, 2)
                        logger.info(
                            f"[HSI-spot] 收盘: {close:.2f} ({pct:+.2f}%) "
                            f"更新: {r.get('最新行情时间','')}"
                        )
                if result["close"] is None:
                    logger.warning("[HSI-spot] 未取到有效 HSI 行")
        except Exception as e:
            logger.warning(f"[HSI-spot] 失败: {e}")

    # ── 5. yfinance ^HSI（仅收盘价兜底；禁用 Volume 估算成交额）─────────────────
    if result["close"] is None:
        try:
            import yfinance as yf
            tk = yf.Ticker("^HSI")
            hist = tk.history(period="5d")
            if hist is not None and not hist.empty:
                row = hist.iloc[-1]
                close = float(row["Close"])
                prev = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else close
                pct = (close - prev) / prev * 100
                if _hsi_close_ok(close):
                    result.update({"close": round(close, 2), "pct": round(pct, 2)})
                    if result["trade_date"] is None:
                        result["trade_date"] = str(hist.index[-1].date())
                    logger.info(f"[HSI-yfinance] 收盘: {close:.2f} 日期: {result['trade_date']}")
        except Exception as e:
            logger.warning(f"[HSI-yfinance] {e}")
            if result["close"] is None:
                result["error"] = str(e)

    if result["close"] is None and result["error"] is None:
        result["error"] = "HSI 收盘价所有数据源均失败"
    if result["turnover_hkd_100m"] is None:
        logger.warning("[HSI] 成交额所有数据源均失败，早报将显示 N/A")

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


def _n225_close_ok(v) -> bool:
    """日经225 收盘价合理区间：5000–200000 点"""
    return v is not None and 5000 < v < 200000


def _n225_vol_ok(v) -> bool:
    """日经225 成交量合理区间：0.5–200 亿股/日（东证 Prime 全市场约 10–40 亿股，
    成分股口径只会更小，区间放宽以容忍长期结构变化）"""
    return v is not None and 0.5 <= v <= 200


def _fetch_n225_futu() -> dict:
    """日经225：富途 OpenD 快照（JP.N225）。
    moomoo/futu 前端有日经225指数行情（含成交量），数据经由同一 OpenD 协议，
    与 HSI 的 HK.800000 同理。volume 为成分股合计股数，/1e8 得亿股。
    注意：futu 官方文档的行情品类表仅列出 JP 股票/ETF；若当前 OpenD 版本
    不支持 JP 指数快照（ret != RET_OK），静默降级，成交量维持 None。
    """
    import os
    out = {"close": None, "pct": None, "volume_100m": None}
    ctx = None
    try:
        from futu import OpenQuoteContext, RET_OK
        host = os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
        port = int(os.environ.get("FUTU_OPEND_PORT", "11111"))
        ctx = OpenQuoteContext(host=host, port=port)
        for code in ("JP.N225", "JP.NKY"):
            ret, data = ctx.get_market_snapshot([code])
            if ret != RET_OK or data is None or data.empty:
                logger.warning(f"[N225-futu] {code} 快照失败: ret={ret}")
                continue
            r = data.iloc[0]
            close = float(r["last_price"])
            prev  = float(r["prev_close_price"])
            vol   = float(r["volume"]) if "volume" in data.columns else 0.0
            if close > 0:
                out["close"] = round(close, 2)
                if prev > 0:
                    out["pct"] = round((close - prev) / prev * 100, 2)
            if vol > 0:
                out["volume_100m"] = round(vol / 1e8, 2)
            logger.info(
                f"[N225-futu] {code} 收盘: {out['close']} ({out['pct']:+.2f}%) "
                f"成交量: {out['volume_100m']} 亿股 更新: {r.get('update_time','')}"
            )
            return out
    except Exception as e:
        logger.warning(f"[N225-futu] {e}")
    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass
    return out


def fetch_nikkei225() -> dict:
    """日经225：收盘价、涨跌幅、成交量（亿股）。
    收盘价主：日本经济新闻社（Nikkei Inc.）官方指数日线 CSV（指数编制方，最权威）
    收盘价备：富途 OpenD JP.N225 → akshare index_global_spot_em → yfinance ^N225
    成交量：富途 OpenD JP.N225 快照（moomoo/futu 前端同源；OpenD 不支持 JP 指数时
    降级为 None）。yfinance ^N225 的 Volume 延迟一天且口径可疑，不使用。
    """
    result = {"close": None, "pct": None, "volume_100m": None, "error": None}

    # 富途快照先行：一次连接同时提供收盘价备源 + 成交量主源
    futu = _fetch_n225_futu()

    # ── 收盘价主：Nikkei Inc. 官方指数日线 CSV ────────────────────────────────
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
        else:
            logger.warning("[N225-official] CSV 行数不足或解析失败")
    except Exception as e:
        logger.warning(f"[N225-official] {e}")

    # ── 收盘价备1：富途 OpenD JP.N225（与成交量同源）──────────────────────────
    if result["close"] is None and _n225_close_ok(futu.get("close")):
        result["close"], result["pct"] = futu["close"], futu["pct"]
        logger.info(f"[N225] 收盘价采用富途: {result['close']}")

    # ── 收盘价备2：akshare 东财全球指数实时快照 ───────────────────────────────
    if result["close"] is None:
        try:
            import akshare as ak
            df = ak.index_global_spot_em()
            row = df[df["代码"] == "N225"]
            if not row.empty:
                r = row.iloc[0]
                close = float(r["最新价"])
                pct   = float(r["涨跌幅"])
                if _n225_close_ok(close):
                    logger.info(f"[N225-akshare] 收盘: {close:.2f} ({pct:+.2f}%) 更新: {r.get('最新行情时间','')}")
                    result.update({"close": round(close, 2), "pct": round(pct, 2)})
        except Exception as e:
            logger.warning(f"[N225-akshare] {e}")

    # ── 收盘价备3：yfinance（显式日期范围避免缓存错误）────────────────────────
    if result["close"] is None:
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
            if _n225_close_ok(close):
                logger.info(f"[N225-yf] 收盘: {close:.2f} ({pct:+.2f}%)")
                result.update({"close": round(close, 2), "pct": round(pct, 2)})
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"[N225] {e}")

    # ── 成交量：富途快照（校验后入库）────────────────────────────────────────
    if _n225_vol_ok(futu.get("volume_100m")):
        result["volume_100m"] = futu["volume_100m"]
    elif futu.get("volume_100m") is not None:
        logger.warning(f"[N225-futu] 成交量 {futu['volume_100m']} 亿股超出合理区间，丢弃")

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

def fetch_us_indices(now_hkt: Optional[datetime] = None) -> dict:
    """美股日线：仅取香港报告日之前的交易日，逐指数保留真实日期。

    适用于香港早间日报；节假日回退到最近有效收盘，不用指数成交量估算资金流。
    """
    from datetime import timezone
    from math import isfinite

    cutoff = (now_hkt or datetime.now(timezone(timedelta(hours=8)))).date()
    result = {}
    for key, symbol in {"sp500": "^GSPC", "nasdaq": "^IXIC"}.items():
        item = {"close": None, "pct": None, "trade_date": None, "error": None}
        result[key] = item
        try:
            import yfinance as yf
            hist = yf.Ticker(symbol).history(
                start=(cutoff - timedelta(days=30)).isoformat(),
                end=cutoff.isoformat(), auto_adjust=False, timeout=15,
            )
            if hist is None or hist.empty:
                raise ValueError("无日线数据")
            hist = hist.sort_index()
            hist = hist[hist.index.date < cutoff]
            closes = hist["Close"].dropna()
            closes = closes[closes.map(lambda v: isfinite(float(v)) and float(v) > 0)]
            if closes.empty:
                raise ValueError("无有效收盘价")
            item["close"] = round(float(closes.iloc[-1]), 2)
            item["trade_date"] = closes.index[-1].date().isoformat()
            if len(closes) >= 2:
                item["pct"] = round((float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1) * 100, 2)
        except Exception as e:
            item["error"] = str(e)
            logger.warning(f"[US/{symbol}] 获取失败: {e}")
    return result


def fetch_all_market_data(now_hkt: Optional[datetime] = None) -> dict:
    """抓取所有行情数据，返回结构化字典"""
    logger.info("开始抓取市场数据...")
    data = {}

    data["hsi"] = _safe(fetch_hsi, "HSI")
    data["southbound"] = _safe(fetch_southbound_flow, "南向资金")
    data["a_share"] = _safe(fetch_a_share_indices, "A股")
    data["nikkei"] = _safe(fetch_nikkei225, "日经225")
    data["us"] = _safe(lambda: fetch_us_indices(now_hkt), "美股指数")

    logger.info("抓取汇率数据...")
    data["fx"] = _safe(fetch_fx_and_commodities, "FX")

    logger.info("市场数据抓取完成")
    return data
