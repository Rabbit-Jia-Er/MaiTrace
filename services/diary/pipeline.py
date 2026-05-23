"""日记 pipeline：消息抓取 → 时间线 → prompt → LLM → 截断 → 落库 → 发布。"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from ...utils import (
    MAX_DIARY_LENGTH,
    TOKEN_LIMIT_50K,
    estimate_tokens,
    peel_envelope,
    smart_truncate,
    truncate_by_tokens,
)
from ...utils.date import date_with_weather
from ..cookie import renew_cookies
from ..llm_runner import LLMRunner
from ..qzone_api import create_qzone_api
from .fetcher import MessageFetcher, _resolve_session_id
from .prompts import build_custom_prompt, build_diary_prompt, build_qqzone_prompt
from .storage import DiaryStorage
from .timeline import TimelineBuilder, weather_by_emotion

logger = logging.getLogger(__name__)


async def _get_global(ctx, key: str, default: str = "") -> str:
    try:
        value = await ctx.config.get(key, default)
    except Exception as exc:
        logger.warning("ctx.config.get(%s) 异常: %s", key, exc)
        return default
    value = peel_envelope(value)
    if isinstance(value, dict):
        value = value.get("value", default)
    return str(value or default)


async def _resolve_personality(ctx) -> Dict[str, str]:
    return {
        "core": await _get_global(ctx, "personality.personality", "是一个机器人助手"),
        "style": await _get_global(ctx, "personality.reply_style", ""),
        "nickname": await _get_global(ctx, "bot.nickname", ""),
    }


def _parse_target_chats(s: str) -> List[str]:
    """配置中 target_chats 是多行字符串，按行拆。"""
    if not s:
        return []
    return [line.strip() for line in s.replace("\r", "").split("\n") if line.strip()]


class DiaryPipeline:
    """日记主流程编排。"""

    def __init__(self, plugin) -> None:
        self._plugin = plugin
        self._ctx = plugin.ctx
        self._storage = DiaryStorage()
        self._fetcher = MessageFetcher(plugin.ctx)
        self._llm = LLMRunner(
            plugin.ctx,
            plugin.config.llm.text_model,
            timeout=plugin.config.llm.llm_timeout_seconds,
        )

    @property
    def storage(self) -> DiaryStorage:
        return self._storage

    # ===== 公开入口 =====

    async def generate_for_date(
        self,
        date: str,
        *,
        group_id: str = "",
        ignore_filter: bool = False,
    ) -> Tuple[bool, str]:
        """生成指定日期日记。group_id 非空时仅取该群消息。"""
        start_time, end_time = self._time_window(date)
        target_chats = _parse_target_chats(self._plugin.config.diary.target_chats)

        # 决定取哪些消息
        if group_id:
            sid = await _resolve_session_id(self._ctx, group_id=group_id)
            if sid:
                messages = await self._fetcher.fetch_for_chats([sid], start_time, end_time)
            else:
                messages = await self._fetcher.fetch_all(start_time, end_time)
        elif ignore_filter:
            messages = await self._fetcher.fetch_all(start_time, end_time)
        else:
            messages = await self._fetcher.fetch_with_filter(
                self._plugin.config.diary.filter_mode,
                target_chats,
                start_time,
                end_time,
            )

        min_per_chat = self._plugin.config.diary.min_messages_per_chat
        if min_per_chat > 0:
            before = len(messages)
            messages = MessageFetcher.filter_min_messages_per_chat(messages, min_per_chat)
            if before != len(messages):
                logger.info("min_messages_per_chat=%d 过滤后消息 %d → %d", min_per_chat, before, len(messages))

        min_count = self._plugin.config.diary.min_message_count
        if len(messages) < min_count:
            return False, f"消息数量不足({len(messages)}/{min_count})"

        return await self._generate_from_messages(date, messages)

    async def generate_and_publish_for_today(self) -> Tuple[bool, str]:
        """定时任务用：今天的日记 → 生成 + 发布。"""
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        success, result = await self.generate_for_date(today)
        if not success:
            return success, result
        published = await self.publish_to_qzone(today, result)
        logger.info(
            "定时日记 %s 生成完成（%d 字），QQ 空间发布 %s",
            today, len(result), "成功" if published else "失败",
        )
        return True, result

    # ===== 内部 =====

    def _time_window(self, date: str) -> Tuple[float, float]:
        date_obj = datetime.datetime.strptime(date, "%Y-%m-%d")
        start = date_obj.timestamp()
        now = datetime.datetime.now()
        if now.strftime("%Y-%m-%d") == date:
            end = now.timestamp()
        else:
            end = (date_obj + datetime.timedelta(days=1)).timestamp()
        return start, end

    async def _generate_from_messages(self, date: str, messages: List[Dict[str, Any]]) -> Tuple[bool, str]:
        try:
            personality = await _resolve_personality(self._ctx)
            bot_qq = await _get_global(self._ctx, "bot.qq_account", "")

            timeline_builder = TimelineBuilder(bot_qq_account=bot_qq)
            timeline = timeline_builder.build(messages)

            if estimate_tokens(timeline) > TOKEN_LIMIT_50K:
                timeline = truncate_by_tokens(timeline, TOKEN_LIMIT_50K)

            weather = weather_by_emotion(messages)
            date_str = date_with_weather(date, weather)

            cfg = self._plugin.config.diary
            min_wc = self._normalize_int(cfg.min_word_count, default=250, lo=20, hi=MAX_DIARY_LENGTH)
            max_wc = self._normalize_int(cfg.max_word_count, default=350, lo=20, hi=MAX_DIARY_LENGTH)
            if max_wc < min_wc:
                max_wc = min_wc
            target_length = random.randint(min_wc, max_wc)

            prompt = self._compose_prompt(
                date=date,
                timeline=timeline,
                date_str=date_str,
                target_length=target_length,
                personality=personality,
            )

            if self._plugin.config.llm.show_prompt:
                logger.info("日记 prompt（前 500 字）: %s", prompt[:500])

            content = await self._call_model(prompt, timeline)
            if not content:
                await self._save_failed(date, weather, "模型返回空内容")
                return False, "模型生成日记失败（返回空）"

            if len(content) > max_wc:
                content = smart_truncate(content, max_wc)

            await self._storage.save_diary({
                "date": date,
                "diary_content": content,
                "word_count": len(content),
                "generation_time": time.time(),
                "weather": weather,
                "bot_messages": timeline_builder.stats["bot_messages"],
                "user_messages": timeline_builder.stats["user_messages"],
                "is_published_qzone": False,
                "qzone_publish_time": None,
                "status": "生成成功",
                "error_message": "",
            })
            return True, content
        except Exception as exc:
            logger.error("生成日记失败: %s", exc, exc_info=True)
            await self._save_failed(date, "阴", str(exc))
            return False, f"生成日记时出错: {exc}"

    def _compose_prompt(
        self,
        *,
        date: str,
        timeline: str,
        date_str: str,
        target_length: int,
        personality: Dict[str, str],
    ) -> str:
        cfg = self._plugin.config.diary
        style = cfg.style
        personality_desc = personality["core"]
        name = personality.get("nickname", "")

        if style == "custom":
            ctx = {
                "date": date,
                "timeline": timeline,
                "date_with_weather": date_str,
                "target_length": str(target_length),
                "personality_desc": personality_desc,
                "style": personality.get("style", ""),
                "name": name,
            }
            try:
                return build_custom_prompt(cfg.custom_prompt, ctx)
            except ValueError as exc:
                logger.warning("custom_prompt 失败，降级 diary 模板: %s", exc)
                style = "diary"

        if style == "qqzone":
            return build_qqzone_prompt(
                date=date, timeline=timeline, date_with_weather=date_str,
                target_length=target_length, personality_desc=personality_desc,
                style_desc=personality.get("style", ""), name=name,
            )
        return build_diary_prompt(
            date=date, timeline=timeline, date_with_weather=date_str,
            target_length=target_length, personality_desc=personality_desc,
            style_desc=personality.get("style", ""), name=name,
        )

    async def _call_model(self, prompt: str, timeline: str) -> str:
        cfg_custom = self._plugin.config.diary_model
        if cfg_custom.use_custom_model:
            return await self._generate_with_custom_model(prompt)
        # 系统模型路径
        if estimate_tokens(timeline) > TOKEN_LIMIT_50K:
            truncated = truncate_by_tokens(timeline, TOKEN_LIMIT_50K)
            prompt = prompt.replace(timeline, truncated)
        success, text = await self._llm.generate(prompt, temperature=0.7, max_tokens=4096)
        return text if success else ""

    async def _generate_with_custom_model(self, prompt: str) -> str:
        cfg = self._plugin.config.diary_model
        if not cfg.api_key or cfg.api_key in ("your-rinko-key-here", "sk-your-siliconflow-key-here"):
            logger.error("自定义模型 API key 未配置")
            return ""
        try:
            async with AsyncOpenAI(base_url=cfg.api_url, api_key=cfg.api_key) as client:
                completion = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=cfg.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=cfg.temperature,
                    ),
                    timeout=cfg.api_timeout,
                )
            if not completion.choices:
                return ""
            return (completion.choices[0].message.content or "").strip()
        except asyncio.TimeoutError:
            logger.error("自定义模型超时 (%ds)", cfg.api_timeout)
            return ""
        except Exception as exc:
            logger.error("自定义模型调用异常: %s", exc, exc_info=True)
            return ""

    async def _save_failed(self, date: str, weather: str, error_message: str) -> None:
        try:
            await self._storage.save_diary({
                "date": date,
                "diary_content": "",
                "word_count": 0,
                "generation_time": time.time(),
                "weather": weather,
                "bot_messages": 0,
                "user_messages": 0,
                "is_published_qzone": False,
                "qzone_publish_time": None,
                "status": "报错:生成失败",
                "error_message": f"原因:{error_message}",
            })
        except Exception as exc:
            logger.error("保存失败记录出错: %s", exc)

    @staticmethod
    def _normalize_int(value: Any, *, default: int, lo: int, hi: int) -> int:
        if not isinstance(value, int):
            return default
        if value < lo:
            return lo
        if value > hi:
            return hi
        return value

    # ===== 发布到 QQ 空间 =====

    async def publish_to_qzone(self, date: str, content: str) -> bool:
        """通过 services/cookie + services/qzone_api 发布日记到空间。"""
        bot_qq = await _get_global(self._ctx, "bot.qq_account", "")
        if not bot_qq:
            return False

        await renew_cookies(
            self._ctx,
            host=self._plugin.config.plugin.http_host,
            port=self._plugin.config.plugin.http_port,
            napcat_token=self._plugin.config.plugin.napcat_token,
            uin=bot_qq,
            methods=list(self._plugin.config.plugin.cookie_methods),
        )

        qzone = create_qzone_api(bot_qq)
        if qzone is None:
            await self._mark_publish_failed(date, "创建 QzoneAPI 失败")
            return False

        try:
            tid = await qzone.publish_emotion(content, [])
        except Exception as exc:
            logger.error("发布空间异常: %s", exc, exc_info=True)
            await self._mark_publish_failed(date, str(exc))
            return False

        await self._mark_publish_success(date, bool(tid))
        return bool(tid)

    async def _mark_publish_success(self, date: str, ok: bool) -> None:
        diary = await self._storage.get_diary(date)
        if not diary:
            return
        if ok:
            diary["is_published_qzone"] = True
            diary["qzone_publish_time"] = time.time()
            diary["status"] = "一切正常"
            diary["error_message"] = ""
        else:
            diary["is_published_qzone"] = False
            diary["status"] = "报错:发说说失败"
            diary["error_message"] = "原因:QQ 空间发布失败，可能 cookie 过期或网络问题"
        await self._storage.save_diary(diary)

    async def _mark_publish_failed(self, date: str, reason: str) -> None:
        diary = await self._storage.get_diary(date)
        if not diary:
            return
        diary["is_published_qzone"] = False
        diary["status"] = "报错:发说说失败"
        diary["error_message"] = f"原因:{reason}"
        await self._storage.save_diary(diary)
