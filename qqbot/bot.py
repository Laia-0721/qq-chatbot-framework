"""QQ 机器人入口。

启动流程：
  1. nonebot.init() 读取 .env / .env.prod 配置
  2. 注册 OneBot V11 适配器（对接 NapCat）
  3. 加载 plugins/ 目录下的本地插件
  4. 加载第三方视频解析插件
  5. 启动驱动

注意：请使用 `python bot.py` 启动，不要使用 `nb run`
（`nb run` 不会执行本文件）。
"""

import nonebot
from nonebot.adapters.onebot import V11Adapter as OneBotAdapter

# 初始化 NoneBot（只需调用一次）
nonebot.init()

# 获取驱动并注册 OneBot 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

# 加载自定义插件目录
from nonebot.plugin import load_plugins

load_plugins("plugins")

# 加载第三方插件（nonebot-plugin-parser，用于 B 站等视频链接解析）
# 若不需要视频解析功能，可注释掉以下两行
try:
    nonebot.load_plugin("nonebot_plugin_parser")
except Exception as e:  # noqa: BLE001
    from nonebot.log import logger

    logger.warning(f"视频解析插件 nonebot_plugin_parser 加载失败，已跳过：{e}")

# 运行 NoneBot
nonebot.run()
