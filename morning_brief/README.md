# 金融早报自动化系统

每日 7:30 HKT 自动抓取港股、A股、日股、汇率数据，结合 LLM 提炼个股新闻，推送 Telegram。

## 文件结构

```
morning_brief/
├── main.py                 # 主程序入口
├── fetchers/
│   ├── market_data.py      # 第一、二部分：指数 + 汇率
│   └── stock_news.py       # 第四部分：个股新闻
├── llm/
│   └── refiner.py          # LLM 提炼 + 历史去重
├── seen_events.json        # 历史播报记录（自动维护）
├── test_sources.py         # 数据源连通性测试
├── requirements.txt
├── README.md
├── logs/                   # 运行日志（自动创建）
└── output/                 # 早报输出（自动创建）
```

---

## Oracle Ubuntu 部署步骤

### 1. 连接服务器

```bash
ssh ubuntu@<your-oracle-ip>
```

### 2. 安装系统依赖

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git curl
```

### 3. 克隆 / 上传项目

```bash
cd /home/ubuntu
git clone <your-repo-url> morning_brief
# 或者用 scp 上传：
# scp -r ./morning_brief ubuntu@<ip>:/home/ubuntu/
```

### 4. 创建虚拟环境 & 安装依赖

```bash
cd /home/ubuntu/morning_brief
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. 配置环境变量

```bash
# 编辑 ~/.bashrc 或创建专用 .env 文件
cat >> ~/.bashrc << 'EOF'

# 金融早报环境变量
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxx"
export KIMI_API_KEY="sk-xxxxxxxxxxxx"          # 可选备用 LLM
export TELEGRAM_TOKEN="1234567890:AAxxxxxx"
export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"       # 群组ID或个人ID
EOF

source ~/.bashrc
```

> **获取 Telegram 参数：**
> - `TELEGRAM_TOKEN`：和 [@BotFather](https://t.me/BotFather) 创建 Bot 后获取
> - `TELEGRAM_CHAT_ID`：把 Bot 加入群组，然后访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` 查看 `chat.id`

### 6. 测试数据源连通性

```bash
cd /home/ubuntu/morning_brief
source venv/bin/activate
python test_sources.py
```

预期输出：所有必要项 ✔，可选项 ⚠ 不影响运行。

### 7. 手动测试完整运行

```bash
python main.py
```

检查：
- 控制台输出早报全文
- `output/YYYYMMDD.md` 文件生成
- Telegram 收到消息

### 8. 配置 Crontab

```bash
crontab -e
```

添加以下内容：

```cron
# 金融早报自动化
# 每天 UTC 23:30 运行（即 HKT 07:30，早于 8:00 完成推送）
# 使用虚拟环境的 Python，日志追加到 logs/cron.log
30 23 * * * cd /home/ubuntu/morning_brief && /home/ubuntu/morning_brief/venv/bin/python main.py >> logs/cron.log 2>&1
```

验证 crontab：

```bash
crontab -l
```

### 9. 查看日志

```bash
# 实时查看 cron 日志
tail -f /home/ubuntu/morning_brief/logs/cron.log

# 查看今日详细日志
cat /home/ubuntu/morning_brief/logs/$(date +%Y%m%d).log

# 查看历史早报
ls output/
cat output/20260319.md
```

---


## Telegram 人工补充指引（建议）

建议在独立输入频道按以下格式发消息，系统更稳定：

- **宏观补充**：带 `#宏观` 或 `#macro`
- **个股补充**：带股票标签（如 `#0700`、`#腾讯`、`#TSLA`）
- **IPO补充**：带 `#ipo` 或 `#新股`
- **行业数据提醒**：可带 `#宏观 #行业`，并在正文写清“指标名 + 时间范围 + 关注点”

推荐模板：

```text
#宏观 #行业
请补充：航运行业运价指数（过去24h）
关注点：是否明显上/下行，是否影响港股航运板块
```

```text
#0700
腾讯回购 120 万股，价格区间 371.2–378.6 港元，总额约 4.5 亿港元
```

```text
#0700
提醒核查：腾讯是否发布最新业绩（请搜索公告原文后输出）
```

说明：
- “提醒核查/请搜索”类文本会优先当作**搜索任务**处理，尽量避免原文直接上报。
- 人工输入建议一条一事，减少混合主题（宏观+个股+IPO写在同一条里）。

## 常见问题

### AKShare 接口超时 / 返回空数据

- 港股收盘后（约 16:00 HKT）数据最稳定；7:30 抓取的是前一交易日数据
- 若持续失败，早报会标注 `⚠️ 数据获取失败`，不影响其余部分推送

### DeepSeek 限流 (429)

- 脚本自动等待 60 秒后切换 Kimi
- 若两者均失败，个股板块会标注错误，行情数据仍正常推送

### Telegram 发送失败

- 检查 Bot 是否被踢出群组
- 确认 `TELEGRAM_CHAT_ID` 格式正确（群组 ID 为负数，如 `-100123456789`）

### yfinance 数据延迟

- 汇率、大宗商品取 `regularMarketPrice`（实时报价）
- 若美股盘前 7:30 HKT 对应美东 19:30（前一日收盘后），价格为收盘价

---

## 环境变量一览

| 变量 | 必须 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | ✅ | DeepSeek Chat API Key |
| `KIMI_API_KEY` | 可选 | Kimi/Moonshot 备用 LLM |
| `TELEGRAM_TOKEN` | ✅ | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | ✅ | 目标聊天 ID |

---

## WhatsApp 兼容说明

早报使用 `*加粗*` 语法（Telegram Markdown V1 格式），在 WhatsApp 中直接粘贴或转发即可正常显示加粗效果。如需通过 WhatsApp Business API 推送，将 `parse_mode` 去掉直接发送纯文本即可，`*号包裹*` 在 WhatsApp 中原生支持。

## 恒指及港股成交额的数据约定

- 按 Asia/Hong_Kong 的早报日期，使用 XHKG 交易日历确定前一个交易日（含香港假期）。即使盘中手动运行，也维持早报前一交易日口径。
- 恒指收盘取港交所 Main Board Daily Quotations 的 HANG SENG INDEX 下午收盘列；涨跌幅用同一行昨收计算。
- 港股成交额取同日主板与 GEM 日报的 Today's Turnover (HK$) 之和，除以 1e8 转为亿港元；不使用指数成交量、指数点位或成分股成交额。
- URL 日期和正文 DATE 必须匹配目标交易日。来源失败、过期、字段缺失时显示 N/A，不自动退到更早日期，也不由 LLM 填充。GEM 缺失时仍可保留已验证的恒指收盘，但不发布主板成交额作为全市场总额。
- 第一部分独立标注港股交易日期。来源 URL 保留在数据结果中；官方报表格式变更或半日市缺少下午收盘列时会明确缺失，需要核查。
- 更新部署时重新安装 requirements.txt（新增 exchange-calendars），再运行 `python -m unittest discover -s . -p test_hsi.py -v`。本测试不发送 Telegram。

测试样本来源（2026-09-04，截取报表头和市场摘要）：
https://www.hkex.com.hk/eng/stat/smstat/dayquot/d260904e.htm
https://www.hkex.com.hk/eng/stat/smstat/dayquot/GEM/e_G260904.htm
