# QQ 群聊机器人框架

基于 [NoneBot2](https://nonebot.dev/) + [NapCat](https://github.com/NapNeko/NapCatQQ) 的 QQ 群聊机器人，开箱即用的功能框架。

> 本仓库已**完整脱敏**：不含任何真实密钥、QQ 号、群号、服务器路径或聊天数据。所有配置项默认留空，按 [配置说明](docs/配置说明.md) 填写后即可运行。

## 功能

| 功能 | 说明 |
| --- | --- |
| **AI 聊天** | 群聊白名单 + 前缀触发，私聊直接对话；支持多套人设切换；带 10 轮上下文记忆 |
| **图片识别** | 与 AI 聊天同时发送图片即可识图（DeepSeek 多模态） |
| **长文本回复** | 自动按段落/标点把长回复切分成多条消息，避免超出 QQ 单条长度 |
| **文本净化** | 自动移除 Markdown 语法与 QQ 无法解析的特殊字符 |
| **打卡签到** | `打卡` / `签到`，统计总天数与连续天数，JSON 持久化 |
| **排行榜** | `签到榜`，展示前 10 名并标注自己的排名（含群昵称解析） |
| **天气查询** | 和风天气，支持一次查询多城市，带 1 小时限流；内置城市映射 |
| **菠萝复读** | 指定群内发送 `🍍` 即复读，纯粹的群内互动小功能 |
| **视频解析** | 借助 `nonebot-plugin-parser` 解析 B 站等平台视频链接（可选） |

## 项目结构

```
.
├── qqbot/                          # 机器人（NoneBot2）
│   ├── bot.py                      # 入口，务必用它启动
│   ├── pyproject.toml              # 依赖声明
│   ├── .env.example                # 配置模板 → 复制为 .env
│   ├── .env.prod                   # 驱动配置
│   ├── qqbot.service               # systemd 部署单元
│   ├── generate_city_mapping.py    # 生成城市映射（可选）
│   ├── plugins/
│   │   └── merged_plugin.py        # 全部业务逻辑
│   └── data/
│       └── ai_prompt.json          # AI 人设（留空，需自行填写）
├── napcat/                         # QQ 协议端
│   ├── docker-compose.yml
│   └── config/                     # NapCat 配置（文件名需带真实 QQ 号）
└── docs/
    └── 配置说明.md                  # 完整配置文档
```

## 快速开始

```bash
git clone https://github.com/Laia-0721/qq-chatbot-framework.git
cd qq-chatbot-framework

# 1) 启动 QQ 协议端
cd napcat
docker compose up -d
docker compose logs -f              # 扫码登录 QQ

# 2) 把配置文件名改成自己的 QQ 号（否则 NapCat 会忽略这些配置）
cd config
mv onebot11_ACCOUNT.json onebot11_<你的QQ号>.json
mv napcat_ACCOUNT.json   napcat_<你的QQ号>.json
mv napcat_protocol_ACCOUNT.json napcat_protocol_<你的QQ号>.json

# 3) 启动机器人
cd ../../qqbot
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e .
cp .env.example .env                # 填写 token、API Key、群号
python bot.py
```

完整配置项说明见 **[docs/配置说明.md](docs/配置说明.md)**。

## 环境要求

- Python **3.10+**
- Docker（用于运行 NapCat）
- 可选：`ffmpeg` + `yt-dlp`（仅在需要视频解析时）

## 两个最容易踩的坑

1. **token 必须两端一致**：`qqbot/.env` 的 `ONEBOT_ACCESS_TOKEN` 与
   `napcat/config/onebot11_<QQ号>.json` 的 `token` 要逐字符相同，否则连接被拒绝、机器人毫无反应。
2. **必须用 `python bot.py` 启动**：`nb run` 不会执行 `bot.py`，因而不会注册 OneBot 适配器。

> 另外，`AI_CHAT_GROUPS` 默认为空，**群聊不会触发 AI 聊天**（私聊不受影响）。这是刻意设计，
> 避免机器人误响应你所在的所有群。测试时先填一个群号。

## 配置一览

所有配置集中在 `qqbot/.env`，未配置的功能会自动禁用并在启动日志中提示：

```dotenv
ONEBOT_ACCESS_TOKEN=      # 与 NapCat 一致（必填）
DEEPSEEK_APIKEY=          # AI 聊天与识图
QWEATHER_APIKEY=          # 天气
QWEATHER_HOST=            # 天气（专属 Host）
AI_CHAT_GROUPS=           # 群聊 AI 白名单，逗号分隔
PINEAPPLE_GROUP_ID=       # 菠萝功能群号
```

## 说明

- 本项目仅供学习与个人使用。使用自动化机器人操作 QQ 账号存在风控风险，请自行评估。
- 未配置任一密钥时程序仍可正常启动，仅对应功能不触发。
