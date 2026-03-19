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
