"""
test_ipo_earnings.py — 独立测试 IPO 和业绩两个模块

用法:
  cd morning_brief
  python test_ipo_earnings.py [--ipo] [--earnings]
  （不带参数则两个都跑）
"""
import sys, os, argparse, logging
sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from datetime import datetime, timezone, timedelta
HKT = timezone(timedelta(hours=8))
now_hkt = datetime.now(HKT)

def test_ipo():
    print("\n" + "="*60)
    print("TEST: IPO 招股信息")
    print("="*60)

    # 1. 爬虫
    print("\n[1] 爬虫抓取...")
    try:
        from fetchers.ipo_fetcher import fetch_hk_ipo_today
        ipo_list = fetch_hk_ipo_today(today=now_hkt.date())
        print(f"    爬虫结果: {len(ipo_list)} 只")
        for s in ipo_list:
            print(f"    - {s.get('name')} ({s.get('code')}) {s.get('sub_start')}~{s.get('sub_end')}")
    except Exception as e:
        print(f"    爬虫失败: {e}")
        ipo_list = []

    # 2. 豆包
    print(f"\n[2] 豆包 ({'丰富模式' if ipo_list else '独立搜索模式'})...")
    try:
        from fetchers.doubao_macro import fetch_doubao_ipo, fmt_doubao_ipo_section_smart
        text = fetch_doubao_ipo(ipo_list=ipo_list or None, date_hkt=now_hkt)
        if text:
            section = fmt_doubao_ipo_section_smart(text, today_date=now_hkt.date())
            print("\n--- 豆包原始输出 ---")
            print(text[:800])
            print("\n--- 格式化输出 ---")
            print(section)
        else:
            print("    豆包返回空")
    except Exception as e:
        print(f"    豆包失败: {e}")
        import traceback; traceback.print_exc()


def test_earnings():
    print("\n" + "="*60)
    print("TEST: 业绩公告（watchlist 模式）")
    print("="*60)

    try:
        from fetchers.doubao_macro import fetch_doubao_earnings_watchlist, fmt_earnings_subsection
        from fetchers.stock_news import HK_STOCKS
        print(f"[1] watchlist: {len(HK_STOCKS)} 只股票")
        print("    前5只:", list(HK_STOCKS.items())[:5])

        print("\n[2] 豆包搜索过去48h业绩...")
        text = fetch_doubao_earnings_watchlist(HK_STOCKS, date_hkt=now_hkt)
        if text:
            section = fmt_earnings_subsection(text)
            print("\n--- 豆包原始输出 ---")
            print(text[:1000])
            print("\n--- 格式化输出 ---")
            print(section)
        else:
            print("    豆包返回空（watchlist内近48h无业绩，或调用失败）")
    except Exception as e:
        print(f"    失败: {e}")
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipo",      action="store_true")
    parser.add_argument("--earnings", action="store_true")
    args = parser.parse_args()

    run_all = not (args.ipo or args.earnings)
    if args.ipo      or run_all: test_ipo()
    if args.earnings or run_all: test_earnings()
