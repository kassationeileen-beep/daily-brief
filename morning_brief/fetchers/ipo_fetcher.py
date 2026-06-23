"""
ipo_fetcher.py — 第五部分：港股今日招股（新股认购）

主源：cpy.com.hk/hk/ipo_list.htm
  - 持牌证券商，每日更新，数据完整
  - 抓取 "新股认购" 表格，过滤今日仍在认购期内的新股
  - 返回: 股票代号、公司名称、认购价、截止日、每手股数等

备源：akshare stock_hk_ipo_em
  - 东方财富接口，数据完整但可能延迟
"""
import time
import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 主源：cpy.com.hk
# ─────────────────────────────────────────────

CPY_IPO_URL = "https://www.cpy.com.hk/hk/ipo_list.htm"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.7",
    "Referer": "https://www.cpy.com.hk/hk/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _parse_hk_date(s: str) -> Optional[date]:
    """
    解析多种港股日期格式：
      "24/03/2026", "2026-03-24", "24 Mar 2026", "24/3/26"
    """
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d/%m/%y",
                "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _fetch_cpy_ipo(today: date) -> list[dict]:
    """
    爬取 cpy.com.hk 的新股认购列表，返回今日认购中的记录。
    字段：code, name, price_hkd, sub_start, sub_end, lot_size, board_lot
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        logger.warning(f"[IPO-cpy] 缺少依赖 {e}，跳过")
        return []

    try:
        resp = requests.get(CPY_IPO_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        logger.warning(f"[IPO-cpy] 请求失败: {e}")
        return []

    # 优先用 lxml，没有则降级 html.parser
    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")

    # ── 定位表格 ──────────────────────────────────────────────
    # cpy.com.hk 的 IPO 表格通常以 class="ipo" 或 id 包含 "ipo" 标识
    # 若找不到，退回到页面中的第一个大表格
    table = (
        soup.find("table", id=lambda x: x and "ipo" in x.lower()) or
        soup.find("table", class_=lambda x: x and "ipo" in x.lower()) or
        soup.find("table", class_=lambda x: x and ("list" in x.lower() or "data" in x.lower())) or
        soup.find("table")  # 最终兜底
    )
    if not table:
        logger.warning("[IPO-cpy] 页面中未找到任何表格")
        return []

    # ── 解析表头 ──────────────────────────────────────────────
    headers_row = table.find("tr")
    if not headers_row:
        return []
    col_names = [th.get_text(strip=True) for th in headers_row.find_all(["th", "td"])]
    logger.debug(f"[IPO-cpy] 表头列名: {col_names}")

    # 列名映射（匹配中文关键词，容忍微小差异）
    def _find_col(keywords: list[str]) -> int:
        for i, name in enumerate(col_names):
            if any(kw in name for kw in keywords):
                return i
        return -1

    idx_name  = _find_col(["公司", "名稱", "名称", "股票名", "証券名"])
    idx_code  = _find_col(["代號", "代号", "股票代", "編號", "编号"])
    idx_price = _find_col(["招股價", "招股价", "發行價", "价格", "Price"])
    idx_start = _find_col(["認購開始", "认购开始", "開始", "起始", "Start"])
    idx_end   = _find_col(["認購截止", "认购截止", "截止", "到期", "End", "結束"])
    idx_lot   = _find_col(["每手", "手數", "手数", "Lot"])

    logger.debug(
        f"[IPO-cpy] 列索引: name={idx_name} code={idx_code} "
        f"price={idx_price} start={idx_start} end={idx_end} lot={idx_lot}"
    )

    # ── 解析数据行 ────────────────────────────────────────────
    results = []
    rows = table.find_all("tr")[1:]  # 跳过表头行

    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        txt = [c.get_text(strip=True) for c in cells]

        # 提取各字段（用 -1 表示未找到对应列时使用空字符串）
        def _get(idx: int) -> str:
            return txt[idx] if 0 <= idx < len(txt) else ""

        name  = _get(idx_name)  if idx_name  >= 0 else txt[0]
        code  = _get(idx_code)  if idx_code  >= 0 else ""
        price = _get(idx_price) if idx_price >= 0 else ""
        start_str = _get(idx_start) if idx_start >= 0 else ""
        end_str   = _get(idx_end)   if idx_end   >= 0 else ""
        lot   = _get(idx_lot)   if idx_lot   >= 0 else ""

        # 过滤无效行
        if not name:
            continue

        # 解析日期过滤今日
        start_dt = _parse_hk_date(start_str)
        end_dt   = _parse_hk_date(end_str)

        # 若无法解析日期，也保留（可能是模糊格式）
        if start_dt and end_dt:
            if not (start_dt <= today <= end_dt):
                continue  # 不在认购期内
        elif end_dt:
            if today > end_dt:
                continue  # 已截止

        # 整理股票代码（去掉前缀 "HK:" 等）
        code_clean = code.replace("HK:", "").replace(".HK", "").strip().lstrip("0")

        results.append({
            "code":      code_clean,
            "name":      name,
            "price_hkd": price,
            "sub_start": start_str,
            "sub_end":   end_str,
            "lot_size":  lot,
            "source":    "cpy.com.hk",
        })

    logger.info(f"[IPO-cpy] 今日认购中: {len(results)} 只")
    if not results:
        logger.debug(f"[IPO-cpy] 无结果，表头列名: {col_names}, 总行数: {len(rows)}")
    return results


# ─────────────────────────────────────────────
# 备源：akshare
# ─────────────────────────────────────────────

def _fetch_akshare_ipo(today: date) -> list[dict]:
    """
    用 akshare stock_hk_ipo_em 获取港股 IPO 数据（东方财富接口）。
    过滤：认购截止日 >= 今日，认购起始日 <= 今日。
    """
    try:
        import akshare as ak
    except ImportError:
        return []

    results = []
    try:
        df = ak.stock_hk_ipo_em()
        if df is None or df.empty:
            return []

        logger.info(f"[IPO-akshare] 列名: {list(df.columns)}, 共 {len(df)} 行")

        # 列名映射（兼容东方财富各版本）
        def _find(keywords):
            return next((c for c in df.columns if any(kw in c for kw in keywords)), None)

        name_col  = _find(["名称", "公司", "股票名", "证券名"])
        code_col  = _find(["代码", "代号", "编号"])
        price_col = _find(["招股价", "发行价", "价格", "价钱"])
        start_col = _find(["开始", "起始", "认购开始", "认购起"])
        end_col   = _find(["截止", "结束", "认购截止", "认购结"])
        lot_col   = _find(["每手", "手数", "手股"])

        logger.debug(f"[IPO-akshare] 列映射: name={name_col} code={code_col} "
                     f"price={price_col} start={start_col} end={end_col} lot={lot_col}")

        for _, row in df.iterrows():
            def _rv(col):
                return str(row[col]).strip() if col and col in df.columns else ""

            # 过滤认购期
            start_str = _rv(start_col)
            end_str   = _rv(end_col)
            start_dt  = _parse_hk_date(start_str)
            end_dt    = _parse_hk_date(end_str)

            if start_dt and end_dt:
                if not (start_dt <= today <= end_dt):
                    continue
            elif end_dt:
                if today > end_dt:
                    continue
            # 无法解析日期则保留（日期格式可能特殊）

            code_val = _rv(code_col).replace(".HK", "").replace("HK:", "").strip().lstrip("0")
            results.append({
                "code":      code_val,
                "name":      _rv(name_col),
                "price_hkd": _rv(price_col),
                "sub_start": start_str,
                "sub_end":   end_str,
                "lot_size":  _rv(lot_col),
                "source":    "akshare",
            })

    except Exception as e:
        logger.warning(f"[IPO-akshare] {e}")

    logger.info(f"[IPO-akshare] 今日认购中: {len(results)} 只")
    return results


# ─────────────────────────────────────────────
# 备源二：etnet.com.hk
# ─────────────────────────────────────────────

_ETNET_IPO_URL = "https://www.etnet.com.hk/www/tc/stocks/ipo.php"

def _fetch_etnet_ipo(today: date) -> list[dict]:
    """
    爬取 etnet.com.hk 的新股认购列表，抓取今日仍在认购期内的记录。
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        logger.warning(f"[IPO-etnet] 缺少依赖 {e}")
        return []

    try:
        resp = requests.get(_ETNET_IPO_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        logger.warning(f"[IPO-etnet] 请求失败: {e}")
        return []

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return []

    results = []
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers_row = rows[0]
        col_names = [th.get_text(strip=True) for th in headers_row.find_all(["th", "td"])]
        if not any(kw in " ".join(col_names) for kw in ["股票", "公司", "招股", "認購", "认购"]):
            continue

        logger.debug(f"[IPO-etnet] 表头: {col_names}")

        def _find_col(keywords):
            for i, name in enumerate(col_names):
                if any(kw in name for kw in keywords):
                    return i
            return -1

        idx_name  = _find_col(["公司", "名稱", "名称", "股票"])
        idx_code  = _find_col(["代號", "代号", "編號"])
        idx_price = _find_col(["招股價", "招股价", "發行", "Price"])
        idx_end   = _find_col(["截止", "結束", "到期"])
        idx_lot   = _find_col(["每手", "手數"])

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            txt = [c.get_text(strip=True) for c in cells]

            def _get(idx):
                return txt[idx] if 0 <= idx < len(txt) else ""

            name = _get(idx_name) if idx_name >= 0 else txt[0]
            if not name:
                continue

            end_str = _get(idx_end) if idx_end >= 0 else ""
            end_dt  = _parse_hk_date(end_str)
            if end_dt and today > end_dt:
                continue  # 已截止

            results.append({
                "code":      _get(idx_code).replace("HK:", "").strip().lstrip("0"),
                "name":      name,
                "price_hkd": _get(idx_price) if idx_price >= 0 else "N/A",
                "sub_start": "",
                "sub_end":   end_str,
                "lot_size":  _get(idx_lot) if idx_lot >= 0 else "",
                "source":    "etnet.com.hk",
            })
        break  # 找到第一个有效表就退出

    logger.info(f"[IPO-etnet] 今日认购中: {len(results)} 只")
    return results


# ─────────────────────────────────────────────
# 主源：Futu OpenD get_ipo_list
# ─────────────────────────────────────────────

def _fetch_futu_ipo(today: date) -> list[dict]:
    """
    通过 Futu OpenD SDK 获取港股 IPO 认购名单。
    过滤：apply_end_time >= today（认购期尚未结束）。
    返回字段与其他来源保持一致，name 为英文（由豆包搜索补充中文名）。
    需要 futu-api Python SDK 且 OpenD 在本地 11111 端口运行。
    """
    try:
        from futu import OpenQuoteContext, Market, RET_OK
    except ImportError:
        logger.debug("[IPO-futu] futu SDK 未安装，跳过")
        return []

    import os
    host = os.environ.get("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.environ.get("FUTU_OPEND_PORT", "11111"))
    ctx = None
    try:
        ctx = OpenQuoteContext(host=host, port=port)
        ret, data = ctx.get_ipo_list(Market.HK)
        if ret != RET_OK or data is None or data.empty:
            logger.warning(f"[IPO-futu] get_ipo_list 失败: ret={ret}")
            return []

        today_str = today.isoformat()
        results = []
        for _, row in data.iterrows():
            apply_end = str(row.get("apply_end_time", "") or "").strip()
            if not apply_end or apply_end == "N/A" or apply_end < today_str:
                continue  # 认购已截止

            code_raw = str(row.get("code", "")).replace("HK.", "")
            code_clean = code_raw.lstrip("0") or code_raw

            price_min = float(row.get("ipo_price_min") or 0)
            price_max = float(row.get("ipo_price_max") or 0)
            if price_min > 0 and price_max > 0:
                price_str = (f"{price_min:.2f}" if price_min == price_max
                             else f"{price_min:.2f}–{price_max:.2f}")
            else:
                price_str = "N/A"

            lot = row.get("lot_size")
            results.append({
                "code":      code_clean,
                "name":      str(row.get("name", "")).strip(),
                "price_hkd": price_str,
                "sub_start": "",        # Futu 不提供认购开始日，由豆包搜索补充
                "sub_end":   apply_end,
                "lot_size":  str(int(lot)) if lot and str(lot) != "N/A" else "",
                "source":    "futu",
            })

        logger.info(f"[IPO-futu] 今日认购中: {len(results)} 只")
        return results

    except Exception as e:
        logger.warning(f"[IPO-futu] {e}")
        return []
    finally:
        if ctx:
            try:
                ctx.close()
            except Exception:
                pass


# ─────────────────────────────────────────────
# 统一入口
# ─────────────────────────────────────────────

def fetch_hk_ipo_today(today: Optional[date] = None) -> list[dict]:
    """
    获取今日港股认购新股列表。
    主: Futu OpenD → 备1: cpy.com.hk → 备2: akshare → 备3: etnet.com.hk
    """
    if today is None:
        from datetime import timezone, timedelta
        HKT = timezone(timedelta(hours=8))
        today = datetime.now(HKT).date()

    logger.info(f"[IPO] 抓取今日 ({today}) 认购新股...")

    # 主源：Futu OpenD（最准确，直接来自交易所数据）
    ipo_list = _fetch_futu_ipo(today)
    if ipo_list:
        return ipo_list

    logger.info("[IPO] Futu 主源失败，尝试 cpy 备源")
    ipo_list = _fetch_cpy_ipo(today)
    if ipo_list:
        return ipo_list

    logger.info("[IPO] cpy 备源失败，尝试 akshare 备源")
    ipo_list = _fetch_akshare_ipo(today)
    if ipo_list:
        return ipo_list

    logger.info("[IPO] akshare 备源失败，尝试 etnet 备源")
    ipo_list = _fetch_etnet_ipo(today)
    return ipo_list


def fmt_ipo_section(ipo_list: list[dict]) -> str:
    """
    格式化为早报第五部分文本块。
    示例输出：
      ▶️五、*今日招股（新股認購）*
      • 1234 星海科技  招股價：3.00 港元  截止：24/03/2026  每手：1,000 股
    """
    if not ipo_list:
        return "▶️五、*今日招股（新股認購）*\n• 今日暫無新股認購"

    lines = ["▶️五、*今日招股（新股認購）*"]
    for item in ipo_list:
        code  = item.get("code", "")
        name  = item.get("name", "")
        price = item.get("price_hkd", "N/A")
        end   = item.get("sub_end", "N/A")
        lot   = item.get("lot_size", "")

        lot_str = f"  每手：{lot} 股" if lot else ""
        lines.append(
            f"• {code} {name}  招股價：{price} 港元  "
            f"截止日：{end}{lot_str}"
        )
    return "\n".join(lines)
