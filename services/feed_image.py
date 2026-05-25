"""图片获取（AI 生图 + 表情包）。

AI 生图通过跨插件 API 调用麦麦绘卷（mais_art_journal）的 ``generate_image`` 公开 API。
调用时**始终传 ``selfie_mode=True``**，让绘卷走 selfie 流程：

- 形象 prompt 走绘卷自己的 ``[selfie].prompt_prefix``（**不被优化器改写**）
- 参考图走绘卷自己的 ``[selfie].reference_image_path``（自动 img2img；模型不支持时
  绘卷会用 ``silent_img2img_fallback=True`` 自动降级 txt2img）
- 我们只传 ``prompt`` 作为**场景描述**（说说正文），绘卷的
  ``SELFIE_SCENE_SYSTEM_PROMPT`` 只优化场景，明确禁止改写角色外观

变更说明（v3.1.4+）：

- 不再在 MaiTrace 这边拼 ``self_description + 场景`` 给绘卷
- ``persona.self_description`` 只影响文本 LLM（说说/评论/日记），不再注入生图 prompt
- ``persona.reference_image_path`` 不再被 MaiTrace 读，由绘卷自己管理
"""

from __future__ import annotations

import base64
import datetime
import logging
from ..utils import get_logger
import random
import uuid
from pathlib import Path
from typing import Optional

from ..utils import peel_envelope as _peel


logger = get_logger(__name__)


# 绘卷 API 命名空间（与 mais_art_journal/_manifest.json 的 id 对齐）
_ART_PLUGIN_ID = "1021143806.mais_art_journal"
_ART_GENERATE_IMAGE = f"{_ART_PLUGIN_ID}.generate_image"


# ============================================================
# 麦麦绘卷桥接：ctx.api.call("...generate_image", ...)
# ============================================================


async def generate_image_via_pic_plugin(
    plugin,
    image_prompt: str,
    pic_plugin_model: str,
    *,
    archive_dir: str = "",
    input_image_base64: str = "",
    selfie_style: str = "photo",
) -> Optional[bytes]:
    """通过麦麦绘卷生成一张图，直接返回 bytes。

    始终传 ``selfie_mode=True`` —— 让绘卷用 ``[selfie].prompt_prefix`` 作为角色外观、
    走 ``SELFIE_SCENE_SYSTEM_PROMPT`` 优化场景（明确禁止改写外观）。

    Args:
        plugin: MaiTracePlugin 实例。
        image_prompt: 场景描述（说说正文）。
        pic_plugin_model: 绘卷 ``models.<id>``。
        archive_dir: 非空时归档生成的图。
        input_image_base64: 参考图 base64（已由 collect_images_for_feed 读取并编码）。
            非空时绘卷自动走 img2img；模型不支持则 ``silent_img2img_fallback=True`` 降级 txt2img。
        selfie_style: ``standard`` / ``mirror`` / ``photo``。说说配图建议 ``photo``（第三人称）。

    Returns:
        Optional[bytes]: 成功返回图片 bytes；失败返回 ``None``。
    """
    if not image_prompt or not image_prompt.strip():
        logger.warning("image_prompt 为空，跳过 AI 生图")
        return None
    if not pic_plugin_model:
        logger.warning("未配置 image.pic_plugin_model，跳过 AI 生图")
        return None

    call_kwargs: dict = {
        "prompt": image_prompt,
        "model_id": pic_plugin_model,
        "selfie_mode": True,
        "selfie_style": selfie_style,
        "use_cache": False,
    }
    if input_image_base64:
        call_kwargs["input_image_base64"] = input_image_base64
        # strength 默认让绘卷决定（绘卷自己的 selfie 流程 strength=0.6）

    try:
        result = await plugin.ctx.api.call(_ART_GENERATE_IMAGE, **call_kwargs)
    except Exception as exc:
        logger.error("调用绘卷 generate_image 异常: %s", exc, exc_info=True)
        return None

    result = _peel(result)
    if not isinstance(result, dict):
        logger.warning("绘卷 generate_image 返回非 dict: %s", type(result).__name__)
        return None
    if not result.get("success"):
        logger.warning("绘卷 generate_image 失败: %s", result.get("error", "未知"))
        return None

    image_b64 = result.get("image_base64", "")
    if not image_b64:
        logger.warning("绘卷返回 success 但 image_base64 为空")
        return None

    try:
        img_bytes = base64.b64decode(image_b64)
    except Exception as exc:
        logger.error("解码绘卷返回的 base64 失败: %s", exc)
        return None

    logger.info(
        "绘卷生图成功 (model=%s, size=%s, style=%s, %s, %d bytes)",
        result.get("model_id", ""),
        result.get("size", ""),
        selfie_style,
        "img2img" if result.get("is_img2img") else "txt2img",
        len(img_bytes),
    )

    # 可选归档
    if archive_dir:
        try:
            Path(archive_dir).mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = uuid.uuid4().hex[:8]
            save_path = Path(archive_dir) / f"pic_plugin_{stamp}_{suffix}.png"
            with save_path.open("wb") as f:
                f.write(img_bytes)
            logger.info("归档生图副本: %s", save_path)
        except OSError as exc:
            logger.warning("归档失败（图片仍会发出）: %s", exc)

    return img_bytes


# ============================================================
# 表情包：ctx.emoji.get_by_description
# ============================================================


async def get_emoji_image(plugin, description: str) -> Optional[bytes]:
    """通过 ctx.emoji 取一张表情包（base64 → bytes）。"""
    try:
        result = await plugin.ctx.emoji.get_by_description(description=description)
    except Exception as exc:
        logger.warning("ctx.emoji.get_by_description 异常: %s", exc)
        return None
    result = _peel(result)
    if not result:
        return None
    # 可能形态：tuple(base64, description, scene) 或 dict {"base64": ..., ...}
    if isinstance(result, tuple):
        b64 = result[0] if len(result) >= 1 else ""
    elif isinstance(result, dict):
        b64 = result.get("base64", "")
    else:
        b64 = ""
    if not b64:
        return None
    try:
        return base64.b64decode(b64)
    except Exception as exc:
        logger.warning("解码表情包 base64 失败: %s", exc)
        return None


# ============================================================
# 集中入口：根据配置决定 AI / emoji / random
# ============================================================


async def collect_images_for_feed(
    plugin,
    message: str,
    *,
    reference_image_path: str = "",
) -> list[bytes]:
    """按 ``image.*`` 配置收集图片，直接返回 bytes 列表。

    Args:
        plugin: MaiTracePlugin 实例。
        message: 说说正文。AI 路径下作为场景描述传给绘卷；emoji 路径下作为表情包
            匹配 description 使用。
        reference_image_path: 参考图绝对路径（来自 ``persona.reference_image_path``，
            通常源自绘卷 ``[selfie].reference_image_path``）。非空且文件存在时读取
            转 base64 传给绘卷 ``input_image_base64`` 走图生图；模型不支持时
            绘卷自动降级 txt2img。

    Returns:
        list[bytes]: 生成的图片 bytes 列表。AI 生图失败 / 表情包匹配失败时该项
        会被跳过；返回空列表代表本次发纯文本。
    """
    images: list[bytes] = []

    if not plugin.config.image.enable_image:
        return images

    image_mode = plugin.config.image.image_mode
    image_number = max(1, min(4, plugin.config.image.image_number))
    ai_probability = max(0.0, min(1.0, plugin.config.image.ai_probability))
    selfie_style = plugin.config.image.selfie_style

    if image_mode == "only_ai":
        use_ai = True
    elif image_mode == "only_emoji":
        use_ai = False
    else:
        use_ai = random.random() < ai_probability

    if use_ai:
        pic_plugin_model = plugin.config.image.pic_plugin_model
        if not pic_plugin_model:
            logger.warning("未配置 image.pic_plugin_model，无法生 AI 图，将发纯文本")
            return images

        # 加载参考图（如果有）→ base64
        ref_b64 = ""
        if reference_image_path:
            try:
                with open(reference_image_path, "rb") as f:
                    ref_b64 = base64.b64encode(f.read()).decode("ascii")
                logger.info(
                    "使用绘卷参考图走图生图: %s (%d bytes)",
                    reference_image_path, len(ref_b64),
                )
            except OSError as exc:
                logger.warning(
                    "读取参考图失败 %s: %s（降级纯文生图）",
                    reference_image_path, exc,
                )

        logger.info(
            "使用绘卷生成配图: model=%s style=%s mode=%s scene=%s",
            pic_plugin_model,
            selfie_style,
            "img2img" if ref_b64 else "txt2img",
            message[:80],
        )

        # clear_image=false 时归档历史副本，true 时不落盘
        archive_dir = ""
        if not plugin.config.image.clear_image:
            from .persistence import get_images_dir
            archive_dir = str(get_images_dir())

        # 按 image_number 多次调用（绘卷 generate_image 一次返回一张图）
        for _ in range(image_number):
            img_bytes = await generate_image_via_pic_plugin(
                plugin, message, pic_plugin_model,
                archive_dir=archive_dir,
                input_image_base64=ref_b64,
                selfie_style=selfie_style,
            )
            if img_bytes:
                images.append(img_bytes)
    else:
        for _ in range(image_number):
            img = await get_emoji_image(plugin, message)
            if img:
                images.append(img)

    return images
