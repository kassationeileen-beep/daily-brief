"""
test_scrapers.py — 测试港交所各数据源的可访问性和数据质量

用法（在 Oracle 服务器上跑）：
  cd morning_brief
  source venv/bin/activate
  python test_scrapers.py [--ipo] [--results] [--buyback] [--all]
"""
import sys, os, argparse, logging, requests
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from datetime import date, datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
today = datetime.now(HKT).date()
from_3d = (today - timedelta(days=3)).strftime("%Y%m%d")
to_today = today.strftime("%Y%m%d")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-HK,zh-TW;q=0.9,en;q=0.7",
    "Referer": "https://www.hkexnews.hk/",
}


# ─────────────────────────────────────────────
# IPO 源测试
# ─────────────────────────────────────────────

def test_ipo_sources():
    print("\n" + "=" * 60)
    print(f"TEST: IPO 数据源  今日={today}")
    print("=" * 60)

    # ── 1. 现有主源 cpy.com.hk ──
    print("\n[IPO-1] cpy.com.hk")
    try:
        resp = requests.get("https://www.cpy.com.hk/hk/ipo_list.htm", headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        print(f"  找到 {len(tables)} 个表格")
        if tables:
            first = tables[0]
            rows = first.find_all("tr")
            print(f"  第一个表格 {len(rows)} 行")
            for row in rows[:3]:
                print(f"    {[td.get_text(strip=True) for td in row.find_all(['td','th'])]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 2. hkexnews 招股章程（类型 = 'A2'/'Prospectus'）──
    # hkexnews 的 doctype 过滤：招股书通常是 category=8 (New Listing) 或搜索关键词
    print("\n[IPO-2] hkexnews.hk — 搜索关键词 'Prospectus' (近7天)")
    try:
        url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
        from_7d = (today - timedelta(days=7)).strftime("%Y%m%d")
        params = {
            "lang": "EN",
            "sdatefrom": from_7d,
            "sdateto": to_today,
            "statype": "",
            "category": "8",   # New Listing / IPO
            "scode": "",
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.find_all("tr")
        count = 0
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                title = max((c.get_text(strip=True) for c in cells), key=len)
                if any(kw in title.lower() for kw in ["prospectus", "listing document", "招股"]):
                    date_cell = cells[0].get_text(strip=True)
                    # 尝试提取股票代码
                    code = ""
                    for c in cells:
                        txt = c.get_text(strip=True)
                        if txt.isdigit() and len(txt) <= 5:
                            code = txt
                            break
                    print(f"  {date_cell} | code={code or 'N/A'} | {title[:60]}")
                    count += 1
        if count == 0:
            print("  无匹配结果（可能 category=8 过滤方式不对）")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 3. hkexnews 直接用 URL 参数按公告分类搜索 ──
    print("\n[IPO-3] hkexnews.hk — category=1 (Listing/IPO相关) 关键词搜索")
    try:
        url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
        from_7d = (today - timedelta(days=7)).strftime("%Y%m%d")
        params = {
            "lang": "EN",
            "sdatefrom": from_7d,
            "sdateto": to_today,
            "statype": "",
            "category": "1",
            "scode": "",
            "keyword": "prospectus",
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        # 检查返回 HTML 的结构
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # 看看有没有结果表格
        result_divs = soup.find_all(class_=lambda c: c and "result" in c.lower())
        print(f"  含 'result' class 的元素: {len(result_divs)}")
        rows = soup.find_all("tr")
        print(f"  总 <tr> 数: {len(rows)}")
        for row in rows[:5]:
            cells = row.find_all("td")
            if cells:
                print(f"    {[c.get_text(strip=True)[:30] for c in cells[:4]]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 4. AASTOCKS IPO 列表 ──
    print("\n[IPO-4] AASTOCKS IPO 页面")
    try:
        url = "https://www.aastocks.com/tc/stocks/ipo/upcoming.aspx"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        print(f"  找到 {len(tables)} 个表格")
        for i, t in enumerate(tables[:3]):
            rows = t.find_all("tr")
            print(f"  table[{i}]: {len(rows)} 行")
            for row in rows[:3]:
                cells = row.find_all(["td", "th"])
                print(f"    {[c.get_text(strip=True)[:25] for c in cells[:5]]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 5. etnet.com.hk IPO（现有备源2）──
    print("\n[IPO-5] etnet.com.hk IPO（现有备源2）")
    try:
        url = "https://www.etnet.com.hk/www/tc/stocks/ipo.php"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        print(f"  找到 {len(tables)} 个表格")
        for i, t in enumerate(tables[:3]):
            rows = t.find_all("tr")
            if len(rows) < 2:
                continue
            cols = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])]
            print(f"  table[{i}] 表头: {cols}")
            for row in rows[1:3]:
                cells = row.find_all(["td", "th"])
                print(f"    {[c.get_text(strip=True)[:25] for c in cells[:6]]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 6. HKEX 新上市简报 JSON ──
    print("\n[IPO-6] HKEX 新上市 API（非公开，尝试）")
    try_urls = [
        "https://www.hkex.com.hk/eng/invest/securities/ipo/documents/ipoCalendar.json",
        "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Listing/ipoCalendar.json",
        "https://www.hkex.com.hk/eng/invest/securities/ipo/",
    ]
    for url in try_urls:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            print(f"  {url}\n  → status={resp.status_code}, len={len(resp.text)}, content-type={resp.headers.get('content-type','')}")
            if resp.status_code == 200 and len(resp.text) < 2000:
                print(f"  内容: {resp.text[:300]}")
        except Exception as e:
            print(f"  {url}\n  → FAIL: {e}")


# ─────────────────────────────────────────────
# 业绩公告源测试
# ─────────────────────────────────────────────

def test_results_sources():
    print("\n" + "=" * 60)
    print(f"TEST: 业绩公告数据源  范围={from_3d}~{to_today}")
    print("=" * 60)

    # ── 1. hkexnews 按公告类型搜索（全市场）──
    # hkexnews 的分类编号：Annual Results = A21, Interim Results = A22
    # statype 参数实际值需要探索
    for label, category, statype in [
        ("Annual Results (category=9)", "9", ""),
        ("Interim Results (category=10)", "10", ""),
        ("Results (no filter)", "0", ""),
    ]:
        print(f"\n[Results-1/{label}]")
        try:
            url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
            params = {
                "lang": "EN",
                "sdatefrom": from_3d,
                "sdateto": to_today,
                "statype": statype,
                "category": category,
                "scode": "",
            }
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
            print(f"  status={resp.status_code}, len={len(resp.text)}")
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.find_all("tr")
            print(f"  总 <tr> 数: {len(rows)}")
            count = 0
            for row in rows[:20]:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    title = max((c.get_text(strip=True) for c in cells), key=len)
                    if any(kw in title.lower() for kw in ["result", "annual", "interim", "profit", "業績", "年報", "中報"]):
                        date_cell = cells[0].get_text(strip=True)
                        print(f"  {date_cell} | {title[:70]}")
                        count += 1
            if count == 0:
                print("  无业绩公告匹配")
        except Exception as e:
            print(f"  FAIL: {e}")

    # ── 2. 针对 watchlist 中特定股票搜索（代码+结果关键词）──
    print("\n[Results-2] hkexnews 针对特定股票（0700 腾讯）近3天业绩公告")
    try:
        url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
        params = {
            "lang": "EN",
            "scode": "00700",
            "sdatefrom": from_3d,
            "sdateto": to_today,
            "statype": "",
            "category": "0",
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.find_all("tr")
        print(f"  总 <tr> 数: {len(rows)}")
        for row in rows[:10]:
            cells = row.find_all("td")
            if len(cells) >= 2:
                title = max((c.get_text(strip=True) for c in cells), key=len)
                date_cell = cells[0].get_text(strip=True)
                if title:
                    print(f"  {date_cell} | {title[:60]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 3. 全市场搜索关键词 "annual results" ──
    print("\n[Results-3] hkexnews 全市场关键词 'annual results' 近3天")
    try:
        url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
        params = {
            "lang": "EN",
            "scode": "",
            "sdatefrom": from_3d,
            "sdateto": to_today,
            "statype": "",
            "category": "0",
            "keyword": "annual results",
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.find_all("tr")
        count = 0
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                title = max((c.get_text(strip=True) for c in cells), key=len)
                if "result" in title.lower():
                    date_cell = cells[0].get_text(strip=True)
                    # 提取股票代码
                    code = next((c.get_text(strip=True) for c in cells if c.get_text(strip=True).isdigit() and len(c.get_text(strip=True)) <= 5), "N/A")
                    print(f"  {date_cell} | {code} | {title[:60]}")
                    count += 1
                    if count >= 10:
                        break
        if count == 0:
            print("  无结果（keyword 参数可能不被支持）")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 4. HKEX Open API（结构化数据）──
    print("\n[Results-4] HKEX Open Data API（结构化，需要探索端点）")
    try_urls = [
        f"https://www.hkex.com.hk/eng/invest/securities/ipo/documents/companySearch.aspx",
        f"https://data.hkex.com/apidoc/v1/security/results",
    ]
    for url in try_urls:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            print(f"  {url}\n  → status={resp.status_code}, len={len(resp.text)}")
            if resp.status_code == 200 and len(resp.text) < 1000:
                print(f"  内容: {resp.text[:300]}")
        except Exception as e:
            print(f"  {url}\n  → FAIL: {e}")

    # ── 5. akshare 港股业绩日历（检查接口是否可用）──
    print("\n[Results-5] akshare 港股业绩相关接口")
    try:
        import akshare as ak
        import inspect
        # 列出含 hk 的业绩相关函数
        funcs = [name for name in dir(ak) if "hk" in name.lower() and any(
            kw in name.lower() for kw in ["earn", "result", "report", "financ", "calendar"]
        )]
        print(f"  akshare HK 业绩相关函数: {funcs}")
        if "stock_hk_financials_report_em" in funcs or not funcs:
            # 尝试东方财富 HK 财报接口
            print("  尝试 stock_financial_analysis_indicator_ths (A股，作对比)")
    except ImportError:
        print("  akshare 未安装")
    except Exception as e:
        print(f"  FAIL: {e}")


# ─────────────────────────────────────────────
# 回购公告源测试（验证现有逻辑是否有效）
# ─────────────────────────────────────────────

def test_buyback_sources():
    print("\n" + "=" * 60)
    print(f"TEST: 回购公告数据源  范围={from_3d}~{to_today}")
    print("=" * 60)

    # ── 1. 现有逻辑：hkexnews + 关键词过滤（以 0669 创科实业为例）──
    print("\n[Buyback-1] 现有逻辑：hkexnews 0669 创科实业 近3天")
    try:
        from fetchers.stock_news import fetch_hk_buyback_announcements
        results = fetch_hk_buyback_announcements("0669", "創科實業", days=3)
        print(f"  返回 {len(results)} 条")
        for r in results:
            print(f"  {r['time']} | {r['title'][:60]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 2. 全市场回购：hkexnews 搜索 "repurchase" 关键词 ──
    print("\n[Buyback-2] hkexnews 全市场 'repurchase' 近3天")
    try:
        url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
        params = {
            "lang": "EN",
            "scode": "",
            "sdatefrom": from_3d,
            "sdateto": to_today,
            "statype": "",
            "category": "0",
        }
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        print(f"  status={resp.status_code}, len={len(resp.text)}")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.find_all("tr")
        count = 0
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            title = max((c.get_text(strip=True) for c in cells), key=len)
            if any(kw.lower() in title.lower() for kw in ["repurchase", "buyback", "monthly return"]):
                date_cell = cells[0].get_text(strip=True)
                code = next((c.get_text(strip=True) for c in cells
                             if c.get_text(strip=True).isdigit() and len(c.get_text(strip=True)) <= 5), "N/A")
                print(f"  {date_cell} | {code} | {title[:60]}")
                count += 1
                if count >= 10:
                    print("  (只显示前10条)")
                    break
        if count == 0:
            print("  无匹配（hkexnews 搜索结构可能不含关键词过滤）")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── 3. HKEX 官方回购统计 ──
    print("\n[Buyback-3] HKEX 官方回购统计 API（探索）")
    try_urls = [
        f"https://www.hkex.com.hk/eng/stat/smstat/repurchase/rep{today.strftime('%y%m')}_e.htm",
        f"https://www.hkex.com.hk/eng/stat/smstat/repurchase/rep{today.strftime('%Y%m%d')}_e.htm",
        "https://www.hkex.com.hk/eng/stat/smstat/repurchase/",
    ]
    for url in try_urls:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            print(f"  {url}\n  → status={resp.status_code}, len={len(resp.text)}")
            if resp.status_code == 200 and len(resp.text) > 100:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, "html.parser")
                tables = soup.find_all("table")
                print(f"  找到 {len(tables)} 个表格")
                if tables:
                    rows = tables[0].find_all("tr")
                    for row in rows[:4]:
                        cells = row.find_all(["td", "th"])
                        print(f"    {[c.get_text(strip=True)[:20] for c in cells[:5]]}")
        except Exception as e:
            print(f"  {url}\n  → FAIL: {e}")


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipo",     action="store_true", help="测试 IPO 数据源")
    parser.add_argument("--results", action="store_true", help="测试业绩公告数据源")
    parser.add_argument("--buyback", action="store_true", help="测试回购公告数据源")
    parser.add_argument("--all",     action="store_true", help="全部测试")
    args = parser.parse_args()

    run_all = not (args.ipo or args.results or args.buyback)
    if args.all or run_all or args.ipo:     test_ipo_sources()
    if args.all or run_all or args.results: test_results_sources()
    if args.all or run_all or args.buyback: test_buyback_sources()
