"""刷空间 + 自动评论 + 自动回复评论 + 回复他人空间中对 bot 评论的回复（多层链式）。

由 RoutineRunner.browse 调用 FeedMonitor.check_feeds。
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import random
from typing import Any

from ..utils import peel_envelope
from ..utils.time_window import is_in_silent_period
from .cookie import renew_cookies
from .feed_read import make_qzone_api
from .llm_runner import LLMRunner
from .persistence import (
    load_processed_comments,
    load_processed_list,
    save_processed_comments,
    save_processed_list,
)
from .prompts import build_comment_prompt, build_reply_prompt, build_reply_to_reply_prompt
from .qzone_api import QzoneAPI

logger = logging.getLogger(__name__)


async def _get_global_str(ctx, key: str, default: str = "") -> str:
    try:
        value = await ctx.config.get(key, default)
    except Exception as exc:
        logger.warning("ctx.config.get(%s) 异常: %s", key, exc)
        return default
    value = peel_envelope(value)
    if isinstance(value, dict):
        value = value.get("value", default)
    return str(value or default)


async def _get_impression(plugin, user_id: str) -> str:
    if not user_id:
        return "无印象"
    try:
        result = await plugin.ctx.db.get(
            model_name="PersonInfo",
            filters={"user_id": str(user_id)},
        )
    except Exception as exc:
        logger.debug("db.get PersonInfo 异常: %s", exc)
        return "无印象"
    result = peel_envelope(result)
    rows = result if isinstance(result, list) else (result.get("rows") if isinstance(result, dict) else [])
    if not rows:
        return "无印象"
    return str(rows[0].get("memory_points") or "无印象")


def _passes_read_list(target_qq: str, read_list: list[str], list_type: str) -> bool:
    """根据 monitor.read_list_type 决定是否处理该 QQ。"""
    in_list = str(target_qq) in [str(q) for q in (read_list or [])]
    if list_type == "whitelist":
        return in_list
    if list_type == "blacklist":
        return not in_list
    return True


class FeedMonitor:
    """刷空间 + 评论/点赞 + 回复评论（含多层链式回复）。"""

    def __init__(self, plugin):
        self.plugin = plugin

    async def check_feeds(
        self,
        processed_list: dict[str, list[str]],
        processed_comments: dict[str, list[str]],
    ) -> tuple[bool, str]:
        """主入口：扫一轮空间，按配置点赞/评论/回复。"""
        cfg_monitor = self.plugin.config.monitor

        # 静默时段
        is_silent, allow_like, allow_comment = is_in_silent_period(
            cfg_monitor.silent_hours,
            cfg_monitor.like_during_silent,
            cfg_monitor.comment_during_silent,
        )
        if is_silent and not allow_like and not allow_comment:
            logger.info("静默时段且不允许点赞/评论，跳过本轮刷空间")
            return True, "静默期跳过"

        # cookie
        qq_account = await _get_global_str(self.plugin.ctx, "bot.qq_account", "")
        if not qq_account:
            return False, "未读到 bot.qq_account"

        ok = await renew_cookies(
            self.plugin.ctx,
            host=self.plugin.config.plugin.http_host,
            port=self.plugin.config.plugin.http_port,
            napcat_token=self.plugin.config.plugin.napcat_token,
            uin=qq_account,
            methods=list(self.plugin.config.plugin.cookie_methods),
        )
        if not ok:
            return False, "更新 cookies 失败"

        # QzoneAPI
        qzone = await make_qzone_api(self.plugin)
        if qzone is None:
            return False, "创建 QzoneAPI 失败"

        # 拉说说列表
        try:
            feeds_list = await qzone.monitor_get_list(cfg_monitor.self_readnum)
        except Exception as exc:
            logger.error("monitor_get_list 异常: %s", exc, exc_info=True)
            return False, f"获取说说列表失败: {exc}"

        if not feeds_list:
            logger.info("未读取到新说说")
            return True, "no feeds"

        bot_personality = await _get_global_str(self.plugin.ctx, "personality.personality", "一个机器人")
        bot_expression = await _get_global_str(self.plugin.ctx, "personality.reply_style", "内容积极向上")
        runner = LLMRunner(self.plugin.ctx, self.plugin.config.models.text_model)
        show_prompt = self.plugin.config.models.show_prompt

        # 逐条处理
        for feed in feeds_list:
            if not isinstance(feed, dict) or feed.get("error"):
                continue

            await asyncio.sleep(3 + random.random())

            content = feed.get("content", "")
            for image in (feed.get("images") or []):
                content = content + image
            fid = feed["tid"]
            target_qq = str(feed.get("target_qq", ""))
            rt_con = feed.get("rt_con", "")
            comments_list = feed.get("comments") or []
            created_time = feed.get("created_time", "未知时间")

            # 名单过滤
            if not _passes_read_list(target_qq, list(cfg_monitor.read_list), cfg_monitor.read_list_type):
                logger.info("跳过名单策略外的 QQ: %s", target_qq)
                continue

            # 自己的说说 → 回复评论
            if target_qq == qq_account:
                if not cfg_monitor.enable_auto_reply:
                    continue
                await self._reply_self_feed_comments(
                    qzone, fid, target_qq, content, comments_list, qq_account,
                    runner, bot_personality, bot_expression, processed_comments,
                    show_prompt=show_prompt,
                )
                continue

            # 他人的说说 → 评论 / 点赞 / 链式回复
            if fid in processed_list:
                # 已处理过这条说说，检查链式回复
                if not allow_comment or not comments_list:
                    continue
                await self._reply_others_to_bot_comment(
                    qzone, fid, target_qq, content, comments_list, qq_account,
                    runner, bot_personality, bot_expression, processed_comments,
                    show_prompt=show_prompt,
                )
                continue

            # 未处理过 → 评论 + 点赞
            await self._comment_and_like(
                qzone, fid, target_qq, content, rt_con, created_time,
                runner, bot_personality, bot_expression,
                allow_like=allow_like, allow_comment=allow_comment,
                show_prompt=show_prompt,
            )

            # 标记已处理
            processed_list[fid] = []
            while len(processed_list) > cfg_monitor.processed_feeds_cache_size:
                oldest = next(iter(processed_list))
                processed_list.pop(oldest)
            await save_processed_list(processed_list)

        return True, "ok"

    # ===== 子流程：评论 + 点赞他人说说 =====

    async def _comment_and_like(
        self,
        qzone: QzoneAPI,
        fid: str,
        target_qq: str,
        content: str,
        rt_con: str,
        created_time: str,
        runner: LLMRunner,
        bot_personality: str,
        bot_expression: str,
        *,
        allow_like: bool,
        allow_comment: bool,
        show_prompt: bool,
    ) -> None:
        cfg_monitor = self.plugin.config.monitor
        impression = await _get_impression(self.plugin, target_qq)

        # 评论
        if allow_comment and random.random() <= cfg_monitor.comment_possibility:
            prompt = build_comment_prompt(
                self.plugin, target_qq, content, created_time,
                bot_personality, bot_expression, impression, rt_con,
            )
            if show_prompt:
                logger.info("评论 prompt: %s", prompt)
            success, comment_text = await runner.generate(prompt, temperature=0.3)
            if success and comment_text:
                ok = await qzone.comment(fid, target_qq, comment_text)
                if ok:
                    logger.info("成功评论 %s: %s", target_qq, comment_text)
                else:
                    logger.warning("评论失败 %s", target_qq)
            else:
                logger.warning("生成评论失败: %s", comment_text)
        else:
            logger.info("静默期或按概率跳过评论")

        # 点赞
        if allow_like and random.random() <= cfg_monitor.like_possibility:
            ok = await qzone.like(fid, target_qq)
            if ok:
                logger.info("点赞成功 %s: %s", target_qq, content[:20])
            else:
                logger.warning("点赞失败 %s", target_qq)
        else:
            logger.info("静默期或按概率跳过点赞")

    # ===== 子流程：回复自己说说下的评论 =====

    async def _reply_self_feed_comments(
        self,
        qzone: QzoneAPI,
        fid: str,
        target_qq: str,  # = qq_account
        content: str,
        comments_list: list[dict[str, Any]],
        qq_account: str,
        runner: LLMRunner,
        bot_personality: str,
        bot_expression: str,
        processed_comments: dict[str, list[str]],
        *,
        show_prompt: bool,
    ) -> None:
        # 待回复 = 非自己评论 + 未处理过
        already = processed_comments.get(fid, [])
        to_reply: list[dict[str, Any]] = []
        for c in comments_list:
            c_qq = c.get("qq_account", "")
            try:
                same = int(c_qq) == int(qq_account)
            except (TypeError, ValueError):
                same = str(c_qq) == str(qq_account)
            if same:
                continue
            if c["comment_tid"] not in already:
                to_reply.append(c)

        if not to_reply:
            return

        cfg_monitor = self.plugin.config.monitor
        for comment in to_reply:
            comment_qq = comment.get("qq_account", "")
            impression = await _get_impression(self.plugin, comment_qq)

            prompt = build_reply_prompt(
                self.plugin,
                nickname=comment.get("nickname", ""),
                content=content,
                comment_content=comment.get("content", ""),
                created_time=comment.get("created_time", ""),
                bot_personality=bot_personality,
                bot_expression=bot_expression,
                impression=impression,
            )
            if show_prompt:
                logger.info("回复评论 prompt: %s", prompt)
            success, reply_text = await runner.generate(prompt, temperature=0.3)
            if not success or not reply_text:
                logger.warning("生成回复失败: %s", reply_text)
                continue

            ok = await qzone.reply(
                fid, target_qq, comment.get("nickname", ""),
                reply_text, comment["comment_tid"],
            )
            if not ok:
                logger.warning("回复评论失败: %s", comment.get("content"))
                continue
            logger.info("回复评论成功: %s", reply_text)
            processed_comments.setdefault(fid, []).append(comment["comment_tid"])
            while len(processed_comments) > cfg_monitor.processed_comments_cache_size:
                oldest = next(iter(processed_comments))
                processed_comments.pop(oldest)
            await save_processed_comments(processed_comments)
            await asyncio.sleep(5 + random.random() * 5)

    # ===== 子流程：回复他人空间中对 bot 评论的回复（多层链式） =====

    async def _reply_others_to_bot_comment(
        self,
        qzone: QzoneAPI,
        fid: str,
        target_qq: str,  # 说说主人
        content: str,
        comments_list: list[dict[str, Any]],
        qq_account: str,
        runner: LLMRunner,
        bot_personality: str,
        bot_expression: str,
        processed_comments: dict[str, list[str]],
        *,
        show_prompt: bool,
    ) -> None:
        # 1) 找出 bot 在这条说说下发过的所有评论的 comment_tid（含顶级+子评论）
        bot_comment_tids: dict[Any, str] = {}
        for c in comments_list:
            if str(c.get("qq_account", "")) == str(qq_account):
                bot_comment_tids[c["comment_tid"]] = c.get("content", "")
        if not bot_comment_tids:
            return

        # 2) 找出"回复了 bot 评论的新子评论"
        already = processed_comments.get(fid, [])
        replies_to_bot: list[dict[str, Any]] = []
        for c in comments_list:
            if (
                c.get("parent_tid") in bot_comment_tids
                and str(c.get("qq_account", "")) != str(qq_account)
                and c["comment_tid"] not in already
            ):
                replies_to_bot.append(c)
        if not replies_to_bot:
            return

        cfg_monitor = self.plugin.config.monitor
        for reply_c in replies_to_bot:
            bot_original = bot_comment_tids.get(reply_c["parent_tid"], "")
            impression = await _get_impression(self.plugin, reply_c.get("qq_account", ""))

            prompt = build_reply_to_reply_prompt(
                self.plugin,
                nickname=reply_c.get("nickname", ""),
                content=content,
                bot_comment=bot_original,
                reply_content=reply_c.get("content", ""),
                created_time=reply_c.get("created_time", ""),
                bot_personality=bot_personality,
                bot_expression=bot_expression,
                impression=impression,
            )
            if show_prompt:
                logger.info("回复链式 prompt: %s", prompt)
            success, reply_text = await runner.generate(prompt, temperature=0.3)
            if not success or not reply_text:
                logger.warning("生成回复（链式）失败: %s", reply_text)
                continue

            ok = await qzone.reply(
                fid, reply_c.get("qq_account", ""),
                reply_c.get("nickname", ""), reply_text,
                reply_c["comment_tid"], host_uin=target_qq,
            )
            if not ok:
                logger.warning("回复（链式）失败: %s", reply_c.get("content"))
                continue
            logger.info("回复（链式）成功: %s", reply_text)
            processed_comments.setdefault(fid, []).append(reply_c["comment_tid"])
            while len(processed_comments) > cfg_monitor.processed_comments_cache_size:
                oldest = next(iter(processed_comments))
                processed_comments.pop(oldest)
            await save_processed_comments(processed_comments)
            await asyncio.sleep(5 + random.random() * 5)


async def run_browse_once(plugin) -> tuple[bool, str]:
    """便捷入口：加载 processed_* → check_feeds → 保存。RoutineRunner 调用此。"""
    processed_list = await load_processed_list()
    processed_comments = await load_processed_comments()
    monitor = FeedMonitor(plugin)
    try:
        success, msg = await monitor.check_feeds(processed_list, processed_comments)
    finally:
        await save_processed_list(processed_list)
        await save_processed_comments(processed_comments)
    return success, msg
