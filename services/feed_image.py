"""图片获取（AI 生图 + 表情包）。

AI 生图通过跨插件 API 调用麦麦绘卷（mais_art_journal）的 ``generate_image`` 公开 API。
每张 AI 配图随机选择自拍或普通画图，失败重试沿用本张的选择：

- 形象 prompt 走绘卷自己的 ``[selfie].prompt_prefix``（**不被优化器改写**）
- 参考图和角色设定交给绘卷处理；MaiTrace 可透传已配置的参考图内容
- 我们只传 ``prompt`` 作为**场景描述**（说说正文），绘卷的
  ``SELFIE_SCENE_SYSTEM_PROMPT`` 只优化场景，明确禁止改写角色外观
- 普通画图传 ``selfie_mode=False``，只画本次内容，不注入角色参考图或穿着

变更说明（v3.1.4+）：

- 不再在 MaiTrace 这边拼 ``self_description + 场景`` 给绘卷
- ``persona.self_description`` 只影响文本 LLM（说说/评论/日记），不再注入生图 prompt
- 按日程传活动、服装与自拍模式；必需配图失败会阻止空间发布
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio
import base64
import binascii
import datetime
import random
import uuid

from ..utils import get_logger


logger = get_logger(__name__)


# 绘卷 API 命名空间（与 mais_art_journal/_manifest.json 的 id 对齐）
_ART_PLUGIN_ID = "1021143806.mais_art_journal"
_ART_GENERATE_IMAGE = f"{_ART_PLUGIN_ID}.generate_image"
IMAGE_RPC_TIMEOUT_MS = 185_000


class ImageGenerationError(RuntimeError):
    """AI 配图未完整生成；调用方必须在发布 QQ 空间之前处理。"""


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
    activity_type: str = "",
    activity_description: str = "",
    outfit: str = "",
    selfie_mode: bool = True,
    activity_context: Optional[Dict[str, str]] = None,
) -> bytes:
    """通过麦麦绘卷生成一张图，直接返回 bytes。

    自拍使用绘卷的固定形象；普通画图交给绘卷的常规生成流程。

    Args:
        plugin: MaiTracePlugin 实例。
        image_prompt: 场景描述（说说正文）。
        pic_plugin_model: 绘卷 ``models.<id>``。
        archive_dir: 非空时归档生成的图。
        input_image_base64: 参考图 base64（已由 collect_images_for_feed 读取并编码）。
            非空时由绘卷按目标模型的参考图能力处理。
        selfie_style: ``auto`` / ``standard`` / ``mirror`` / ``photo``。

    Returns:
        bytes: 成功返回图片内容；失败抛出 ImageGenerationError。
    """
    if not image_prompt or not image_prompt.strip():
        raise ImageGenerationError("图片场景为空")
    if not pic_plugin_model:
        raise ImageGenerationError("未配置 image.pic_plugin_model")

    call_kwargs: Dict[str, Any] = {
        "prompt": image_prompt,
        "model_id": pic_plugin_model,
        "selfie_mode": selfie_mode,
        "use_cache": False,
        "activity_description": activity_description,
    }
    if selfie_mode:
        call_kwargs.update(selfie_style=selfie_style, activity_type=activity_type, outfit=outfit)
        # 绘卷用真实地点、时段与细化场景决定光照和服装，不重新回调日程。
        call_kwargs.update({key: value for key, value in (activity_context or {}).items()
                            if key in {"activity_location", "activity_time_window", "activity_as_of",
                                       "activity_timezone", "activity_scene"} and value})
    if selfie_mode and input_image_base64:
        call_kwargs["input_image_base64"] = input_image_base64
        # 参考强度由绘卷根据换装需求和具体后端处理。

    try:
        # 两跳独立计时：绘卷组件 180 秒，调用方留 5 秒传输和结果处理余量。
        result = await plugin.ctx.call_capability(
            "api.call", timeout_ms=IMAGE_RPC_TIMEOUT_MS,
            api_name=_ART_GENERATE_IMAGE, version="1", args=call_kwargs,
        )
    except Exception as exc:
        raise ImageGenerationError(f"调用绘卷 generate_image 失败: {exc}") from exc

    if not isinstance(result, dict):
        raise ImageGenerationError(f"绘卷返回类型错误: {type(result).__name__}")
    if not result.get("success"):
        raise ImageGenerationError(f"绘卷生成失败: {result.get('error', '未提供原因')}")

    image_b64 = result.get("image_base64", "")
    if not image_b64:
        raise ImageGenerationError("绘卷返回成功但图片为空")

    try:
        img_bytes = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ImageGenerationError("绘卷返回的图片不是有效 base64") from exc
    if not img_bytes:
        raise ImageGenerationError("绘卷返回的图片内容为空")

    logger.info(
        "绘卷生图成功 (model=%s, size=%s, style=%s, %s, %d bytes)",
        result.get("model_id", ""),
        result.get("size", ""),
        result.get("selfie_style", selfie_style),
        "img2img" if result.get("is_img2img") else "txt2img",
        len(img_bytes),
    )

    # 可选归档
    if archive_dir:
        try:
            save_path = await asyncio.to_thread(_archive_image, Path(archive_dir), img_bytes)
            logger.info("归档生图副本: %s", save_path)
        except OSError as exc:
            logger.warning("归档失败（图片仍会发出）: %s", exc)

    return img_bytes


def _archive_image(directory: Path, image: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    path = directory / f"pic_plugin_{stamp}_{suffix}.png"
    path.write_bytes(image)
    return path


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
    if not result:
        return None
    # 当前 SDK 返回表情对象或 None，图片位于 base64 字段。
    if not isinstance(result, dict):
        raise RuntimeError("表情包接口返回格式异常，预期为表情对象")
    if result.get("success") is False:
        raise RuntimeError(f"表情包查询失败: {result.get('error', '未知错误')}")
    b64 = result.get("base64", "")
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
    activity_type: str = "",
    activity_description: str = "",
    outfit: str = "",
    activity_context: Optional[Dict[str, str]] = None,
) -> List[bytes]:
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
        List[bytes]: 全部生成成功的图片。require_image 开启时 AI 配图失败抛错；
        表情包没有匹配结果时允许空列表。
    """
    images: List[bytes] = []

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
            if plugin.config.image.require_image:
                raise ImageGenerationError("未配置 image.pic_plugin_model，未发送说说")
            logger.warning("未配置 AI 模型，按 require_image=false 发布无图说说")
            return images

        # 每张的生成模式先选定，显式重试不会把自拍变成普通图。
        content_mode = plugin.config.image.ai_content_mode
        selfie_modes = [content_mode == "selfie" or (content_mode == "random" and random.random() < 0.5)
                        for _ in range(image_number)]
        # 只有选中自拍才读取角色参考图，普通画图不受参考图路径影响。
        ref_b64 = ""
        if reference_image_path and any(selfie_modes):
            try:
                reference = await asyncio.to_thread(Path(reference_image_path).read_bytes)
                if not reference:
                    raise ImageGenerationError("角色参考图为空，未发送说说")
                ref_b64 = base64.b64encode(reference).decode("ascii")
                logger.info(
                    "使用绘卷参考图走图生图: %s (%d bytes)",
                    reference_image_path, len(ref_b64),
                )
            except OSError as exc:
                raise ImageGenerationError(f"读取参考图失败: {reference_image_path}: {exc}") from exc

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
            archive_dir = str(plugin.persistence.get_images_dir())

        # 按 image_number 多次调用（绘卷 generate_image 一次返回一张图）
        for image_index, is_selfie in enumerate(selfie_modes):
            for attempt in range(plugin.config.image.image_retry + 1):
                try:
                    img_bytes = await generate_image_via_pic_plugin(
                        plugin, message, pic_plugin_model,
                        archive_dir=archive_dir,
                        input_image_base64=ref_b64 if is_selfie else "",
                        selfie_style=selfie_style,
                        activity_type=activity_type,
                        activity_description=activity_description,
                        outfit=outfit,
                        selfie_mode=is_selfie,
                        activity_context=activity_context,
                    )
                except ImageGenerationError as exc:
                    if attempt < plugin.config.image.image_retry:
                        logger.warning("第 %d 张配图失败，5 秒后重试（%d/%d）: %s",
                                       image_index + 1, attempt + 1, plugin.config.image.image_retry, exc)
                        await asyncio.sleep(5)
                        continue
                    if plugin.config.image.require_image:
                        raise ImageGenerationError(f"第 {image_index + 1} 张配图生成失败: {exc}") from exc
                    logger.warning("AI 配图失败，按 require_image=false 继续: %s", exc)
                else:
                    images.append(img_bytes)
                break
    else:
        for _ in range(image_number):
            img = await get_emoji_image(plugin, message)
            if img:
                images.append(img)

    return images
