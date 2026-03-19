#!/usr/bin/env python3
"""
test_sources.py — 各数据源连通性测试
运行方式：python test_sources.py
部署后快速验证环境用
"""
import sys
import time
import traceback
from datetime import datetime

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg):
    print(f"  {GREEN}✔ {msg}{RESET}")


def fail(msg):
    print(f"  {RED}✘ {msg}{RESET}")


def warn(msg):
    print(f"  {YELLOW}⚠ {msg}{RESET}")


def section(title):
    print(f"\n{BOLD}{'─'*50}{RESET}")
    print(f"{BOLD}▶ {title}{RESET}")
    print(f"{BOLD}{'─'*50}{RESET}")


results = []


def test(name, fn):
    try:
        result = fn()
        ok(f"{name}: {result}")
        results.append((name, True, str(result)))
        return result
    except Exception as e:
        fail(f"{name}: {e}")
        results.append((name, False, str(e)))
        return None


# ─────────────────────────────────────────────
# 1. 依赖包检查
# ─────────────────────────────────────────────
section("1. 依赖包导入")

def check_import(pkg):
    __import__(pkg)
    return "OK"

for pkg in ["akshare", "yfinance", "feedparser", "httpx", "requests"]:
    test(f"import {pkg}", lambda p=pkg: check_import(p))


# ─────────────────────────────────────────────
# 2. 环境变量检查
# ─────────────────────────────────────────────
section("2. 环境变量")
import os

env_vars = ["DEEPSEEK_API_KEY", "KIMI_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
for var in env_vars:
    val = os.environ.get(var, "")
    if val:
        masked = val[:4] + "****" + val[-4:] if len(val) > 8 else "****"
        ok(f"{var} = {masked}")
        results.append((var, True, masked))
    else:
        warn(f"{var} 未设置（可选或需手动配置）")
        results.append((var, None, "未设置"))


# ─────────────────────────────────────────────
# 3. AKShare 数据源
# ─────────────────────────────────────────────
section("3. AKShare 数据源")
import akshare as ak

def test_hsi():
    df = ak.stock_hk_index_daily_em(symbol="恒生指数")
    assert df is not None and not df.empty, "空数据"
    row = df.iloc[-1]
    return f"{len(df)} 行，最新: {row.iloc[1]:.2f}"

test("恒生指数 (stock_hk_index_daily_em)", test_hsi)
time.sleep(1)

def test_sh():
    df = ak.stock_zh_index_daily(symbol="sh000001")
    assert not df.empty
    return f"{len(df)} 行，最新收盘: {df.iloc[-1]['close']:.2f}"

test("上证综指 (stock_zh_index_daily sh000001)", test_sh)
time.sleep(1)

def test_sz():
    df = ak.stock_zh_index_daily(symbol="sz399001")
    assert not df.empty
    return f"{len(df)} 行，最新收盘: {df.iloc[-1]['close']:.2f}"

test("深证成指 (stock_zh_index_daily sz399001)", test_sz)
time.sleep(1)

def test_a_spot():
    df = ak.stock_zh_a_spot_em()
    assert not df.empty
    # 找成交额列
    amount_col = next((c for c in ["成交额", "amount"] if c in df.columns), None)
    if amount_col:
        total = df[amount_col].sum()
        return f"{len(df)} 只，总成交额 {total/1e12:.2f} 万亿"
    return f"{len(df)} 只（成交额列未找到: {list(df.columns[:5])}）"

test("A股成交额 (stock_zh_a_spot_em)", test_a_spot)
time.sleep(1)

def test_southbound():
    df = ak.stock_hsgt_fund_flow_summary_em(indicator="南向资金")
    assert df is not None and not df.empty
    return f"{len(df)} 行，列: {list(df.columns)}"

test("南向资金 (stock_hsgt_fund_flow_summary_em)", test_southbound)
time.sleep(1)

def test_a_news():
    df = ak.stock_news_em(symbol="600519")
    assert df is not None and not df.empty
    return f"{len(df)} 条新闻"

test("A股新闻-贵州茅台 (stock_news_em 600519)", test_a_news)


# ─────────────────────────────────────────────
# 4. yfinance 数据源
# ─────────────────────────────────────────────
section("4. yfinance 数据源")
import yfinance as yf

def test_yf(ticker, label):
    tk = yf.Ticker(ticker)
    info = tk.info
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if not price:
        hist = tk.history(period="2d")
        price = float(hist["Close"].iloc[-1]) if not hist.empty else None
    assert price, f"无法获取价格"
    return f"{price:.4f}"

for ticker, label in [
    ("^N225", "日经225"),
    ("USDJPY=X", "USD/JPY"),
    ("USDCHF=X", "USD/CHF"),
    ("USDCNH=X", "USD/CNH"),
    ("HKD=X", "USD/HKD"),
    ("GC=F", "黄金 XAU/USD"),
    ("DX-Y.NYB", "DXY"),
    ("BTC-USD", "BTC/USD"),
    ("BZ=F", "布伦特原油"),
    ("CL=F", "WTI原油"),
]:
    test(f"{label} ({ticker})", lambda t=ticker, l=label: test_yf(t, l))
    time.sleep(0.5)


# ─────────────────────────────────────────────
# 5. 东方财富港股新闻 API
# ─────────────────────────────────────────────
section("5. 东方财富港股新闻 API")
import requests

def test_hk_news(code="0700", name="腾讯"):
    url = "https://np-listapi.eastmoney.com/comm/web/getListInfo"
    params = {
        "client": "web", "type": "1",
        "mTypeAndCode": f"116.{code}",
        "pageSize": "5", "pageIndex": "1", "callback": "",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", {}).get("list", []) or []
    return f"{name}({code}): {len(items)} 条新闻"

test("港股新闻-腾讯 (东方财富 np-listapi)", test_hk_news)
time.sleep(1)

def test_hk_news_9988():
    return test_hk_news("9988", "阿里巴巴")

test("港股新闻-阿里巴巴 (东方财富)", test_hk_news_9988)


# ─────────────────────────────────────────────
# 6. Yahoo Finance RSS（feedparser）
# ─────────────────────────────────────────────
section("6. Yahoo Finance RSS (feedparser)")
import feedparser

def test_yahoo_rss(ticker="AAPL"):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
    feed = feedparser.parse(url)
    entries = feed.entries
    assert len(entries) > 0, "无条目"
    return f"{ticker}: {len(entries)} 条, 最新: {entries[0].get('title','')[:40]}..."

for ticker in ["AAPL", "NVDA", "TSLA"]:
    test(f"Yahoo RSS {ticker}", lambda t=ticker: test_yahoo_rss(t))
    time.sleep(0.5)


# ─────────────────────────────────────────────
# 7. LLM API（DeepSeek）
# ─────────────────────────────────────────────
section("7. LLM API (DeepSeek)")

def test_deepseek():
    import httpx
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未设置")
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "回复数字1"}],
        "max_tokens": 10,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30) as client:
        resp = client.post("https://api.deepseek.com/v1/chat/completions",
                           json=payload, headers=headers)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return f"响应: {content[:30]}"

test("DeepSeek Chat API", test_deepseek)


section("7b. LLM API (Kimi/Moonshot) [可选]")

def test_kimi():
    import httpx
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        raise ValueError("KIMI_API_KEY 未设置（备用LLM，可选）")
    payload = {
        "model": "moonshot-v1-8k",
        "messages": [{"role": "user", "content": "回复数字1"}],
        "max_tokens": 10,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30) as client:
        resp = client.post("https://api.moonshot.cn/v1/chat/completions",
                           json=payload, headers=headers)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    return f"响应: {content[:30]}"

test("Kimi/Moonshot API", test_kimi)


# ─────────────────────────────────────────────
# 8. Telegram 推送
# ─────────────────────────────────────────────
section("8. Telegram Bot API")

def test_telegram():
    import httpx
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID 未设置")
    url = f"https://api.telegram.org/bot{token}/getMe"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    bot_name = data["result"].get("username", "unknown")
    return f"Bot: @{bot_name}"

test("Telegram getMe", test_telegram)


# ─────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────
section("汇总")

total = len(results)
passed = sum(1 for _, s, _ in results if s is True)
failed = sum(1 for _, s, _ in results if s is False)
optional = sum(1 for _, s, _ in results if s is None)

print(f"\n  总计: {total} 项")
print(f"  {GREEN}通过: {passed}{RESET}")
print(f"  {RED}失败: {failed}{RESET}")
print(f"  {YELLOW}未配置(可选): {optional}{RESET}")

if failed > 0:
    print(f"\n{RED}失败项目:{RESET}")
    for name, status, msg in results:
        if status is False:
            print(f"  - {name}: {msg}")

print(f"\n{'='*50}")
if failed == 0:
    print(f"{GREEN}{BOLD}✔ 所有必要数据源连通正常，可以部署！{RESET}")
else:
    print(f"{RED}{BOLD}✘ 有 {failed} 个数据源异常，请检查后再部署。{RESET}")
print(f"{'='*50}\n")

sys.exit(0 if failed == 0 else 1)
