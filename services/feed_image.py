"""图片获取（AI 生图 + 表情包）。"""

from __future__ import annotations

import base64
import datetime
import logging
import os
import random
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image

from ..utils import peel_envelope as _peel


logger = logging.getLogger(__name__)


# ============================================================
# 麦麦绘卷 (mais_art_journal) 桥接：调对方的 generate_image_standalone
# ============================================================


async def generate_image_via_pic_plugin(
    plugin,
    image_prompt: str,
    image_dir: str,
    pic_plugin_model: str,
) -> bool:
    """通过麦麦绘卷生成图片，保存到 image_dir 下。"""
    try:
        from plugins.mais_art_journal.core.api_clients import generate_image_standalone
    except ImportError:
        logger.warning("麦麦绘卷未安装，无法生图")
        return False

    # 读麦麦绘卷的配置
    try:
        # 新 SDK：通过 ctx.api 调对方插件的配置访问？目前更稳的是直接读 component_registry
        from src.plugin_system.core import component_registry
        from src.plugin_system.apis import config_api
        pic_config = component_registry.get_plugin_config("mais_art_journal")
        if not pic_config:
            logger.warning("未找到麦麦绘卷配置")
            return False
        model_prefix = f"models.{pic_plugin_model}"
        model_config: dict = {}
        for key in (
            "base_url", "api_key", "format", "model", "default_size", "seed",
            "guidance_scale", "num_inference_steps", "watermark",
            "custom_prompt_add", "negative_prompt_add", "artist", "support_img2img",
        ):
            val = config_api.get_plugin_config(pic_config, f"{model_prefix}.{key}", None)
            if val is not None:
                model_config[key] = val
        if not model_config.get("base_url") or not model_config.get("model"):
            logger.warning("麦麦绘卷模型 %s 配置不完整", pic_plugin_model)
            return False

        # 注入 selfie 参考图
        try:
            bot_appearance = config_api.get_plugin_config(pic_config, "selfie.prompt_prefix", "")
            reference_image_path = (config_api.get_plugin_config(pic_config, "selfie.reference_image_path", "") or "").strip()
        except Exception:
            bot_appearance = ""
            reference_image_path = ""

        if bot_appearance:
            image_prompt = f"{bot_appearance}, {image_prompt}"

        reference_image_b64: Optional[str] = None
        strength: Optional[float] = None
        if reference_image_path:
            try:
                if not os.path.isabs(reference_image_path):
                    art_plugin_dir = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "mais_art_journal",
                    )
                    reference_image_path = os.path.join(art_plugin_dir, reference_image_path)
                if os.path.exists(reference_image_path):
                    with open(reference_image_path, "rb") as f:
                        reference_image_b64 = base64.b64encode(f.read()).decode("utf-8")
                    if model_config.get("support_img2img", True):
                        strength = 0.6
                    else:
                        reference_image_b64 = None
            except Exception as exc:
                logger.debug("加载自拍参考图失败: %s", exc)

        size = model_config.get("default_size", "1024x1024")
        success, image_data = await generate_image_standalone(
            prompt=image_prompt,
            model_config=model_config,
            size=size,
            negative_prompt=None,
            strength=strength,
            input_image_base64=reference_image_b64,
            max_retries=2,
        )
        if not success or not image_data:
            logger.warning("pic_plugin 生图失败: %s", image_data)
            return False

        Path(image_dir).mkdir(parents=True, exist_ok=True)
        save_path = Path(image_dir) / "pic_plugin_0.png"
        if image_data.startswith("http"):
            async with httpx.AsyncClient() as client:
                img_response = await client.get(image_data, timeout=60.0)
                img_response.raise_for_status()
                image = Image.open(BytesIO(img_response.content))
                image.save(save_path)
        else:
            img_bytes = base64.b64decode(image_data)
            image = Image.open(BytesIO(img_bytes))
            image.save(save_path)

        logger.info("pic_plugin 生成图片已保存: %s", save_path)
        return True
    except Exception as exc:
        logger.error("pic_plugin 生图异常: %s", exc, exc_info=True)
        return False


async def optimize_image_prompt(plugin, message: str, personality: str) -> Optional[str]:
    """生成图片描述：优先 PromptOptimizer，失败回退到 LLM 自身。"""
    # 优先：麦麦绘卷的 PromptOptimizer
    try:
        from plugins.mais_art_journal.core.utils import PromptOptimizer
        optimizer = PromptOptimizer(log_prefix="[MaiTrace.feed_image]")
        success, image_prompt = await optimizer.optimize(message)
        if success and image_prompt:
            return image_prompt
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("PromptOptimizer 异常: %s", exc)

    # 兜底：自己走 ctx.llm
    from .prompts import build_image_prompt
    from .llm_runner import LLMRunner
    runner = LLMRunner(
        plugin.ctx,
        plugin.config.llm.text_model,
        timeout=plugin.config.llm.llm_timeout_seconds,
    )
    prompt = build_image_prompt(plugin, message, personality)
    success, image_prompt = await runner.generate(prompt, temperature=0.3)
    if success and image_prompt:
        return image_prompt
    return None


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
    personality: str,
) -> tuple[list[bytes], list[str]]:
    """根据 send.* 配置收集图片。

    Returns:
        (image_bytes_list, generated_file_paths) — 后者用于上传后清理本地文件。
    """
    images: list[bytes] = []
    done_paths: list[str] = []

    if not plugin.config.image.enable_image:
        return images, done_paths

    image_mode = plugin.config.image.image_mode
    image_number = max(1, min(4, plugin.config.image.image_number))
    ai_probability = max(0.0, min(1.0, plugin.config.image.ai_probability))

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
            return images, done_paths

        image_prompt = await optimize_image_prompt(plugin, message, personality)
        if not image_prompt:
            logger.warning("生成图片提示词失败，将发纯文本")
            return images, done_paths

        from .persistence import get_images_dir
        image_dir = str(get_images_dir())
        logger.info("使用 pic_plugin 生成配图: %s", image_prompt)
        ok = await generate_image_via_pic_plugin(plugin, image_prompt, image_dir, pic_plugin_model)
        if not ok:
            return images, done_paths

        # 把所有未处理图片读出来 + 重命名为 done_*
        all_files = [f for f in os.listdir(image_dir) if os.path.isfile(os.path.join(image_dir, f))]
        unprocessed = sorted(f for f in all_files if not f.startswith("done_"))
        for image_file in unprocessed:
            full = os.path.join(image_dir, image_file)
            with open(full, "rb") as f:
                images.append(f.read())
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            new_path = os.path.join(image_dir, f"done_{stamp}_{image_file}")
            os.rename(full, new_path)
            done_paths.append(new_path)
    else:
        for _ in range(image_number):
            img = await get_emoji_image(plugin, message)
            if img:
                images.append(img)

    return images, done_paths


def cleanup_done_paths(plugin, done_paths: list[str]) -> None:
    """根据 image.clear_image 配置决定是否删除已上传的图片。"""
    if not plugin.config.image.clear_image:
        return
    for path in done_paths:
        try:
            os.remove(path)
            logger.info("已删除图片: %s", path)
        except OSError as exc:
            logger.warning("删除图片失败 %s: %s", path, exc)
