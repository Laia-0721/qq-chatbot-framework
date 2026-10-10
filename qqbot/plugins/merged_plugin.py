"""QQ 聊天机器人合并插件。

包含四个功能模块：
  * AI 聊天 / 识图     —— 群聊需白名单且以触发前缀开头，私聊直接触发
  * 菠萝复读            —— 指定群内发送 🍍 即复读
  * 打卡签到 / 排行榜    —— 以 data/signin.json 持久化
  * 天气查询            —— 和风天气 + 城市映射，带限流

所有可配置项均从 .env 读取（见项目根目录 .env.example 与 docs/配置说明.md）。
本文件不含任何真实密钥、群号或个人信息，未配置的功能会自动静默不触发。
"""

import json
import os
import re
import time
import asyncio
import base64
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import aiohttp
from nonebot import on_message, on_regex, get_driver
from nonebot.rule import Rule, to_me
from nonebot.adapters.onebot.v11 import (
    Bot, MessageEvent, GroupMessageEvent,
    PrivateMessageEvent, Message, MessageSegment
)
from nonebot.log import logger

# ==========================================================================
#                            配置区（全部留空，请在 .env 中填写）
# ==========================================================================
# 从 NoneBot 驱动配置读取，.env / .env.prod 中的同名变量会被自动注入。
_config = get_driver().config


def _env_str(name: str, default: str = "") -> str:
    """读取字符串配置，未配置时返回默认值。"""
    value = getattr(_config, name, None)
    if value is None:
        return default
    return str(value).strip()


def _env_int(name: str, default: int = 0) -> int:
    """读取整数配置，解析失败时返回默认值。"""
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"配置项 {name} 不是合法整数：{raw!r}，已使用默认值 {default}")
        return default


def _env_int_list(name: str) -> List[int]:
    """读取逗号分隔的整数列表配置，例如 GROUP_WHITELIST=123456,789012。"""
    raw = _env_str(name)
    if not raw:
        return []
    result: List[int] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            logger.warning(f"配置项 {name} 中的 {item!r} 不是合法群号，已跳过")
    return result


# --- 天气（和风天气）---
WEATHER_API_KEY = _env_str("QWEATHER_APIKEY")          # 和风天气 API Key
WEATHER_API_HOST = _env_str("QWEATHER_HOST")           # 和风天气开发者专属 API Host

# --- AI 对话（DeepSeek）---
DEEPSEEK_API_KEY = _env_str("DEEPSEEK_APIKEY")         # DeepSeek API Key
DEEPSEEK_MODEL = _env_str("DEEPSEEK_MODEL", "deepseek-flash")  # 多模态模型
DEEPSEEK_API_URL = _env_str(
    "DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat/completions"
)

# --- 功能白名单 / 触发规则 ---
# 留空表示该功能不触发，避免误响应任意群聊。
SPECIFIC_GROUP_ID = _env_int("PINEAPPLE_GROUP_ID")              # 菠萝功能群号
AI_CHAT_WHITELIST_GROUPS = _env_int_list("AI_CHAT_GROUPS")      # AI 聊天白名单群号
AI_CHAT_PREFIX = _env_str("AI_CHAT_PREFIX", "..")               # 群聊 AI 触发前缀
PRIVATE_UNLIMITED_PREFIX = _env_str("PRIVATE_UNLIMITED_PREFIX", "$")  # 私聊取消字数限制前缀
SINGLE_MESSAGE_MAX_LEN = _env_int("SINGLE_MESSAGE_MAX_LEN", 500)      # 单条消息最大字符数
# ==========================================================================

WEATHER_API_URL = (
    f"https://{WEATHER_API_HOST}/v7/weather/now" if WEATHER_API_HOST else ""
)

# ---------- 基于插件文件位置定位项目根目录 ----------
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PLUGIN_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
SIGNIN_FILE = os.path.join(DATA_DIR, "signin.json")
CITY_MAPPING_FILE = os.path.join(DATA_DIR, "city_mapping.json")
NICKNAME_CACHE_FILE = os.path.join(DATA_DIR, "nickname_cache.json")
AI_PROMPT_FILE = os.path.join(DATA_DIR, "ai_prompt.json")

os.makedirs(DATA_DIR, exist_ok=True)

# ---------- 城市映射加载 ----------
def load_city_mapping() -> Dict[str, str]:
    if os.path.exists(CITY_MAPPING_FILE):
        try:
            with open(CITY_MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载城市映射失败: {e}")
    base_mapping = {"北京": "101010100", "上海": "101020100", "临沂": "101120901"}
    logger.warning("未找到完整城市映射文件，使用内置基础映射。")
    return base_mapping

CITY_ID_MAP = load_city_mapping()
def get_location_id(city_name: str) -> Optional[str]:
    return CITY_ID_MAP.get(city_name)

# ---------- 打卡数据管理 ----------
class SignInManager:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data: Dict[str, Dict] = {}
        self._lock = asyncio.Lock()
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"加载打卡数据失败: {e}")
                self.data = {}
        else:
            self.data = {}

    async def save(self):
        async with self._lock:
            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"保存打卡数据失败: {e}")

    async def sign_in(self, user_id: str) -> Tuple[bool, str]:
        today = datetime.now().date().isoformat()
        user_data = self.data.get(user_id, {"total_days": 0, "consecutive_days": 0, "last_date": None})
        last_date = user_data.get("last_date")
        if last_date == today:
            return False, "今天已经打过卡啦，明天再来吧~"
        if last_date:
            last_dt = datetime.fromisoformat(last_date).date()
            today_dt = datetime.now().date()
            if (today_dt - last_dt).days == 1:
                consecutive = user_data["consecutive_days"] + 1
            else:
                consecutive = 1
        else:
            consecutive = 1
        user_data["total_days"] += 1
        user_data["consecutive_days"] = consecutive
        user_data["last_date"] = today
        self.data[user_id] = user_data
        await self.save()
        return True, f"打卡成功！总打卡 {user_data['total_days']} 天，连续 {consecutive} 天。"

    def get_rank(self) -> List[Tuple[str, int, int]]:
        sorted_users = sorted(self.data.items(), key=lambda x: x[1]["total_days"], reverse=True)
        return [(uid, info["total_days"], info["consecutive_days"]) for uid, info in sorted_users]

sign_manager = SignInManager(SIGNIN_FILE)

# ---------- 昵称缓存管理 ----------
class NicknameCache:
    def __init__(self, cache_file: str):
        self.cache_file = cache_file
        self.data = self.load()

    def load(self) -> Dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    async def save(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存昵称缓存失败: {e}")

    async def get_nickname(self, bot: Bot, group_id: int, user_id: str) -> str:
        if user_id in self.data:
            return self.data[user_id]
        try:
            member_info = await bot.get_group_member_info(group_id=group_id, user_id=int(user_id))
            nickname = member_info.get("card") or member_info.get("nickname") or str(user_id)
        except Exception:
            try:
                stranger_info = await bot.get_stranger_info(user_id=int(user_id))
                nickname = stranger_info.get("nickname", str(user_id))
            except Exception as e:
                logger.warning(f"获取用户 {user_id} 昵称失败: {e}")
                nickname = str(user_id)
        self.data[user_id] = nickname
        await self.save()
        return nickname

nickname_cache = NicknameCache(NICKNAME_CACHE_FILE)

# ---------- AI 人设管理 ----------
class AIPromptManager:
    def __init__(self, file_path: str):
        self.file_path = file_path
        # 人设内容留空，请在 data/ai_prompt.json 中填写，详见 docs/配置说明.md
        self.data = {"default": "", "presets": {}}
        self.load()

    def load(self):
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"加载 AI 预设失败: {e}")

    def get_prompt(self, name: str = None) -> str:
        if name and name in self.data.get("presets", {}):
            return self.data["presets"][name]
        return self.data.get("default", "")

    def list_presets(self) -> List[str]:
        return [k for k in self.data.get("presets", {}).keys() if k]

    @property
    def special_user_id(self) -> str:
        return str(self.data.get("special_user_id", ""))

    @property
    def special_user_preset(self) -> str:
        return self.data.get("special_user_preset", "default")

    @property
    def developer_name(self) -> str:
        return str(self.data.get("developer_name", ""))

    @property
    def restricted_presets(self) -> list:
        return [p for p in self.data.get("restricted_presets", []) if p]

ai_prompt = AIPromptManager(AI_PROMPT_FILE)
user_persona: Dict[str, str] = {}

# ---------- AI 聊天上下文管理 ----------
class ChatContextManager:
    def __init__(self, max_turns: int = 10):
        self.max_turns = max_turns
        self.contexts: Dict[str, List[Dict[str, str]]] = {}

    def _key(self, event: MessageEvent) -> str:
        if isinstance(event, GroupMessageEvent):
            return f"g{event.group_id}_u{event.user_id}"
        return f"private_u{event.user_id}"

    def get(self, event: MessageEvent) -> List[Dict[str, str]]:
        return self.contexts.get(self._key(event), [])

    def append(self, event: MessageEvent, role: str, content: str):
        key = self._key(event)
        if key not in self.contexts:
            self.contexts[key] = []
        self.contexts[key].append({"role": role, "content": content})
        if len(self.contexts[key]) > self.max_turns * 2:
            self.contexts[key] = self.contexts[key][-(self.max_turns * 2):]

    def clear(self, event: MessageEvent):
        key = self._key(event)
        self.contexts.pop(key, None)

chat_context = ChatContextManager(max_turns=10)

# ---------- 菠萝功能 ----------
def is_pineapple_event(bot: Bot, event: MessageEvent) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    # 未配置群号（0）时该功能不触发
    if not SPECIFIC_GROUP_ID or event.group_id != SPECIFIC_GROUP_ID:
        return False
    if event.message.extract_plain_text().strip() != "🍍":
        return False
    if event.user_id == int(bot.self_id):
        return False
    return True

pineapple_matcher = on_message(rule=is_pineapple_event, priority=10, block=False)

@pineapple_matcher.handle()
async def handle_pineapple(bot: Bot, event: GroupMessageEvent):
    try:
        await bot.send(event, Message("🍍"))
    except Exception as e:
        logger.error(f"发送🍍失败: {e}")

# ---------- 打卡签到 ----------
sign_matcher = on_regex(r"^\s*(打卡|签到)\s*$", rule=to_me(), priority=5, block=True)

@sign_matcher.handle()
async def handle_sign_in(bot: Bot, event: MessageEvent):
    user_id = str(event.user_id)
    success, msg = await sign_manager.sign_in(user_id)
    await bot.send(event, Message(msg))

# ---------- 排行榜 ----------
rank_matcher = on_regex(r"^\s*(签到榜|打卡榜|签到排行)\s*$", rule=to_me(), priority=5, block=True)

@rank_matcher.handle()
async def handle_rank(bot: Bot, event: MessageEvent):
    rank_data = sign_manager.get_rank()
    if not rank_data:
        await bot.send(event, "暂无打卡记录")
        return
    current_user_id = str(event.user_id)
    current_rank = None
    for idx, (uid, _, _) in enumerate(rank_data, start=1):
        if uid == current_user_id:
            current_rank = idx
            break
    top10 = rank_data[:10]
    if isinstance(event, GroupMessageEvent):
        tasks = [nickname_cache.get_nickname(bot, event.group_id, uid) for uid, _, _ in top10]
        nicknames = await asyncio.gather(*tasks)
    else:
        nicknames = [uid for uid, _, _ in top10]
    lines = ["🏆 打卡排行榜 🏆"]
    for i, ((uid, total, consecutive), nick) in enumerate(zip(top10, nicknames), start=1):
        line = f"{i}. {nick}  总打卡:{total}天  连续:{consecutive}天"
        if current_user_id == uid:
            line += " (我)"
        lines.append(line)
    if current_rank and current_rank > 10:
        lines.append("...")
        current_total = rank_data[current_rank - 1][1]
        current_consecutive = rank_data[current_rank - 1][2]
        if isinstance(event, GroupMessageEvent):
            current_nick = await nickname_cache.get_nickname(bot, event.group_id, current_user_id)
        else:
            current_nick = current_user_id
        lines.append(f"{current_rank}. {current_nick}  总打卡:{current_total}天  连续:{current_consecutive}天 (我)")
    await bot.send(event, Message("\n".join(lines)))

# ---------- 天气查询 ----------
class RateLimiter:
    def __init__(self, window_seconds=3600):
        self.window = window_seconds
        self.records: Dict[str, float] = {}

    def is_allowed(self, user_id: str, city: str) -> bool:
        key = f"{user_id}:{city}"
        now = time.time()
        last = self.records.get(key, 0)
        if now - last < self.window:
            return False
        self.records[key] = now
        return True

rate_limiter = RateLimiter()

async def fetch_weather_by_city_name(city_name: str) -> Optional[str]:
    location_id = get_location_id(city_name)
    if not location_id:
        return None
    if not WEATHER_API_URL or not WEATHER_API_KEY:
        logger.warning("未配置 QWEATHER_APIKEY / QWEATHER_HOST，天气功能不可用。")
        return None
    params = {"location": location_id, "key": WEATHER_API_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(WEATHER_API_URL, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if data.get("code") == "200":
                    now = data["now"]
                    return f"{city_name}天气：{now['text']}，温度 {now['temp']}℃，体感 {now['feelsLike']}℃，湿度 {now['humidity']}%"
                return None
    except Exception as e:
        logger.error(f"天气API异常: {e}")
        return None

def extract_cities(text: str) -> List[str]:
    clean = re.sub(r'天气|查询|？|!|！|。|，|,', '', text).strip()
    if not clean:
        return []
    parts = re.split(r'[ ,，、]+', clean)
    stopwords = {"的", "和", "与", "跟", "及", "还有", "以及"}
    return [p for p in parts if p and p not in stopwords]

weather_matcher = on_regex(r"天气", rule=to_me(), priority=5, block=True)

@weather_matcher.handle()
async def handle_weather(bot: Bot, event: MessageEvent):
    raw_text = event.get_plaintext().strip()
    cities = extract_cities(raw_text)
    if not cities:
        await bot.send(event, "请提供城市名，例如：北京天气")
        return
    user_id = str(event.user_id)
    limited_cities, allowed_cities = [], []
    for city in cities:
        if rate_limiter.is_allowed(user_id, city):
            allowed_cities.append(city)
        else:
            limited_cities.append(city)
    if limited_cities:
        await bot.send(event, f"您查询 {'、'.join(limited_cities)} 过于频繁，请一小时后重试。")
    if not allowed_cities:
        return
    tasks = [fetch_weather_by_city_name(city) for city in allowed_cities]
    results = await asyncio.gather(*tasks)
    reply_parts = []
    for city, result in zip(allowed_cities, results):
        reply_parts.append(result if result else f"未能查询到 {city} 的天气")
    if reply_parts:
        await bot.send(event, Message("\n".join(reply_parts)))

# ============================================================
#                    AI 聊天功能（含识图）
# ============================================================

async def download_image_as_base64(url: str) -> Optional[str]:
    """下载图片并转换为Base64编码字符串"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning(f"下载图片失败，HTTP {resp.status}: {url}")
                    return None
                image_data = await resp.read()
                return base64.b64encode(image_data).decode("utf-8")
    except asyncio.TimeoutError:
        logger.error(f"下载图片超时: {url}")
        return None
    except Exception as e:
        logger.error(f"下载图片异常: {e}")
        return None


async def call_deepseek_multimodal_raw(messages: List[Dict]) -> str:
    """接收完整 messages 的多模态调用，用于带上下文和图片的场景"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": 4096,
        "temperature": 0.8
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                DEEPSEEK_API_URL, headers=headers, json=payload, timeout=120
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"DeepSeek 多模态 API HTTP错误 {resp.status}: {error_text[:200]}")
                    return "抱歉，我暂时看不清这张图片……稍后再试试？"
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        logger.error("DeepSeek 多模态 API 请求超时")
        return "图片有点大，处理超时了……请稍后再试~"
    except Exception as e:
        logger.error(f"DeepSeek 多模态 API 异常: {e}")
        return "出了点小问题……我马上检查一下！"

async def call_deepseek_text(messages: List[Dict]) -> str:
    """纯文本调用（原有逻辑）"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "stream": False,
        "max_tokens": 4096,
        "temperature": 0.8
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                DEEPSEEK_API_URL, headers=headers, json=payload, timeout=60
            ) as resp:
                if resp.status != 200:
                    logger.error(f"DeepSeek API HTTP错误: {resp.status}")
                    return "抱歉，我暂时联系不上服务器……稍后再试试？"
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        logger.error("DeepSeek API 请求超时")
        return "唔……信号不太好，等一下再叫我好吗？"
    except Exception as e:
        logger.error(f"DeepSeek API 异常: {e}")
        return "出了点小问题……我马上检查一下！"


def _split_text_by_length(text: str, max_len: int = SINGLE_MESSAGE_MAX_LEN) -> List[str]:
    """把长文本按段落/句子边界切分成多段，每段不超过 max_len"""
    if len(text) <= max_len:
        return [text]

    parts = []
    remaining = text
    while len(remaining) > max_len:
        # 优先按段落边界切
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut < max_len // 2:
            # 其次按换行
            cut = remaining.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            # 再按中文标点
            for punct in ["。", "！", "？", "；", "…"]:
                idx = remaining.rfind(punct, 0, max_len)
                if idx > cut:
                    cut = idx + 1  # 包含标点
        if cut < max_len // 2:
            # 最后按英文标点或空格
            for punct in [".", "!", "?", ";", " "]:
                idx = remaining.rfind(punct, 0, max_len)
                if idx > cut:
                    cut = idx + 1
        if cut < max_len // 2:
            # 实在找不到边界就硬切
            cut = max_len
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    if remaining:
        parts.append(remaining)
    return parts
def _sanitize_text(text: str) -> str:
    """净化文本，移除 QQ 无法解析的特殊字符和 Markdown 语法"""
    if not text:
        return text

    import re as _re

    text = _re.sub(r'```[\s\S]*?```', '', text)
    text = _re.sub(r'`[^`\n]*`', '', text)
    text = text.replace("**", "").replace("__", "").replace("~~", "")
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            line = stripped.lstrip("#").lstrip()
        if line.lstrip().startswith(("- ", "* ", "+ ")):
            line = line.lstrip()[2:]
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    text = _re.sub(r'\|[\s\-:|]+\|', '', text)
    text = _re.sub(r'\[([^\]]*)\]\([^\)]*\)', r'\1', text)
    text = _re.sub(r'https?://\S+', '', text)
    text = text.replace("[", "［").replace("]", "］")
    text = _re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060\ufeff]', '', text)
    text = _re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
    text = _re.sub(r'[\uFE00-\uFE0F]', '', text)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

async def send_split_messages(bot: Bot, event: MessageEvent, reply: str, unlimited: bool = False):
    """发送回复。
    - unlimited=False：按 || 拆分成多条短消息
    - unlimited=True：不按 || 拆分，把 || 转成换行，超长时按段落切分
    """
    # 发送前先净化，避免特殊字符导致 QQ 无法解析
    reply = _sanitize_text(reply)
    logger.info(f"[发送调试] 长度={len(reply)}，前200字={reply[:200]!r}")

    if unlimited:
        reply = reply.replace("||", "\n").strip()
        parts = _split_text_by_length(reply)
        for idx, part in enumerate(parts):
            if isinstance(event, GroupMessageEvent) and idx == 0:
                msg = MessageSegment.at(event.user_id) + MessageSegment.text(" " + part)
            else:
                msg = MessageSegment.text(part)
            await bot.send(event, msg)
            if idx < len(parts) - 1:
                await asyncio.sleep(0.5)
        return

    # 普通模式：按 || 拆分
    parts = [p.strip() for p in reply.split("||") if p.strip()]
    if not parts:
        parts = ["……嗯？"]
    for idx, part in enumerate(parts):
        if isinstance(event, GroupMessageEvent) and idx == 0:
            msg = MessageSegment.at(event.user_id) + MessageSegment.text(" " + part)
        else:
            msg = MessageSegment.text(part)
        await bot.send(event, msg)
        if idx < len(parts) - 1:
            await asyncio.sleep(0.8)

async def is_ai_chat_trigger(bot: Bot, event: MessageEvent) -> bool:
    """AI 聊天触发规则：群聊需白名单 + 前缀开头；私聊直接触发"""
    if isinstance(event, GroupMessageEvent):
        if not AI_CHAT_WHITELIST_GROUPS or event.group_id not in AI_CHAT_WHITELIST_GROUPS:
            return False
        if not AI_CHAT_PREFIX:
            return False
        return event.message.extract_plain_text().strip().startswith(AI_CHAT_PREFIX)
    if isinstance(event, PrivateMessageEvent):
        return True
    return False


ai_chat = on_message(rule=Rule(is_ai_chat_trigger), priority=99, block=False)


@ai_chat.handle()
async def handle_ai_chat(bot: Bot, event: MessageEvent):
    text = event.message.extract_plain_text().strip()
    if isinstance(event, GroupMessageEvent) and AI_CHAT_PREFIX:
        text = text[len(AI_CHAT_PREFIX):].strip()

    # 私聊 + 特殊符号开头 → 取消字数限制
    unlimited = False
    if isinstance(event, PrivateMessageEvent) and PRIVATE_UNLIMITED_PREFIX:
        for p in PRIVATE_UNLIMITED_PREFIX:
            if text.startswith(p):
                unlimited = True
                text = text[len(p):].strip()
                break

    # 提取图片 URL
    image_url = None
    for segment in event.message:
        if segment.type == "image":
            image_url = segment.data.get("url") or segment.data.get("file")
            break

    if not text and not image_url:
        return

    user_key = f"{event.group_id}_{event.user_id}" if isinstance(event, GroupMessageEvent) else f"p_{event.user_id}"
    is_special = bool(ai_prompt.special_user_id) and str(event.user_id) == ai_prompt.special_user_id

    # 纯文本管理命令（仅文本，且非 unlimited 模式时生效）
    if not image_url and not unlimited:
        if text.lower() in ("clear", "重置", "清除"):
            chat_context.clear(event)
            await bot.send(event, Message("嗯，聊天记录清空了呢~"))
            return

        if text.startswith("切换 "):
            persona_name = text[3:].strip()
            if persona_name in ai_prompt.restricted_presets and not is_special:
                await bot.send(event, Message("这个……是只属于 #开发者 的哦，抱歉不能给你切换。"))
                return
            if persona_name in ai_prompt.list_presets():
                user_persona[user_key] = persona_name
                chat_context.clear(event)
                await bot.send(event, Message(f"好~ 现在开始用「{persona_name}」的方式聊天。"))
            else:
                available = "、".join(ai_prompt.list_presets())
                await bot.send(event, Message(f"没有找到那个人设呢。可用的有：{available}"))
            return

        if text in ("查看人设", "当前人设"):
            current = user_persona.get(user_key, "default")
            await bot.send(event, Message(f"现在用的是「{current}」哦。"))
            return

    logger.info(f"AI 聊天触发，用户{event.user_id}，特殊={is_special}，无限制={unlimited}，内容：{text[:50]}")

    # 选择基础人设
    if is_special and not user_persona.get(user_key):
        system_prompt = ai_prompt.get_prompt(ai_prompt.special_user_preset)
    else:
        system_prompt = ai_prompt.get_prompt(user_persona.get(user_key))

    # ---- 动态身份指令 ----
    # 人设提示词内容留空，请在 data/ai_prompt.json 中填写。
    # 仅当该文件里配置了 developer_name 时，才会追加以下身份约束，
    # 否则不注入任何内容，避免把残缺的提示词发给模型。
    dev_name = ai_prompt.developer_name
    if dev_name and is_special:
        # 当前对话者被确认为开发者本人时的身份指令（提示词见 docs/配置说明.md）
        system_prompt += ""
    elif dev_name:
        # 当前对话者不是开发者时的防冒充指令（提示词见 docs/配置说明.md）
        system_prompt += ""

    # 无限制模式：追加取消字数限制的指令
    if unlimited:
        system_prompt += (
            "\n\n【本次回复的特殊指令】\n"
            "本次对话取消了字数限制，你可以详尽、完整、充分地回答。\n"
            "不需要用 || 分隔消息，直接输出一段完整的回复即可。\n"
            "如果内容确实很长，可以用换行分段，但不要拆成多条消息。\n"
            "尽量优先以纯文本形式输出。"
        )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(chat_context.get(event))
    messages.append({"role": "user", "content": text if text else "（发送了一张图片）"})

    # 有图片走多模态，否则走文本
    if image_url:
        image_b64 = await download_image_as_base64(image_url)
        if not image_b64:
            await bot.send(event, Message("图片下载失败了，请重新发送试试~"))
            return
        messages[-1] = {
            "role": "user",
            "content": [
                {"type": "text", "text": text if text else "请描述这张图片。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        }
        reply = await call_deepseek_multimodal_raw(messages)
    else:
        reply = await call_deepseek_text(messages)

    chat_context.append(event, "user", text if text else "（发送了一张图片）")
    chat_context.append(event, "assistant", reply)

    await send_split_messages(bot, event, reply, unlimited=unlimited)

# ---------- 启动信息 ----------
@get_driver().on_startup
async def startup():
    logger.info("合并插件已启动（AI聊天+识图 + 菠萝 + 打卡 + 天气）")
    if not DEEPSEEK_API_KEY:
        logger.warning("未配置 DEEPSEEK_APIKEY，AI 聊天与识图不可用。")
    if not AI_CHAT_WHITELIST_GROUPS:
        logger.warning("未配置 AI_CHAT_GROUPS，群聊 AI 聊天不会触发（私聊不受影响）。")
    if not SPECIFIC_GROUP_ID:
        logger.warning("未配置 PINEAPPLE_GROUP_ID，菠萝复读不会触发。")
    if not (WEATHER_API_KEY and WEATHER_API_HOST):
        logger.warning("未配置 QWEATHER_APIKEY / QWEATHER_HOST，天气查询不可用。")
