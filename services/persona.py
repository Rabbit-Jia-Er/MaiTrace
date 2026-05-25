"""统一的人格信息加载器。

发说说 / 读说说评论 / 自动回复 / 日记 / Routine 决策 全部共用。

主程序 ``[personality]`` 段 4 个字段：

- ``personality``：人格设定
- ``reply_style``：默认表达风格
- ``multiple_reply_style``：备用风格池
- ``multiple_probability``：按概率从池中替换默认 reply_style

加上 ``[bot]`` 的 ``nickname`` / ``alias_names``，再叠加 MaiTrace 自己的
``[persona]`` 扩展（self_description / use_multiple_reply_style），就是完整人格。

形象注入规则：

- ``self_description`` 非空 → 用 user 填的
- ``self_description`` 为空 → 自动从绘卷 ``mais_art_journal/[selfie].prompt_prefix`` 兜底
- 同时无论哪种情况，配图时都会用绘卷 ``[selfie].reference_image_path`` 走图生图
  （如果配了路径且文件存在）
"""

from __future__ import annotations

import logging
from ..utils import get_logger
import os
import random
from dataclasses import dataclass, field
from typing import Any, List, Tuple

from ..utils import (
    get_global,
    get_global_float,
    get_global_list,
    get_global_str,
    peel_envelope,
)

logger = get_logger(__name__)

# 麦麦绘卷的 plugin id（与 plugins/mais_art_journal/_manifest.json 对齐）
_ART_PLUGIN_ID = "1021143806.mais_art_journal"


@dataclass
class Persona:
    """一次性加载好的完整人格快照。

    每次调用 ``resolve_persona`` 会重新抽样 ``style``，所以同一插件实例
    多次调用得到的 ``style`` 可能不同（与主程序回复行为对齐）。
    """

    personality: str
    """主程序 personality.personality。第二人称设定。"""

    style: str
    """本次抽样后的 reply_style。可能是默认 reply_style，也可能来自 multiple_reply_style 池。"""

    default_style: str
    """主程序 personality.reply_style 原值（不参与抽样）。"""

    nickname: str
    """主程序 bot.nickname。"""

    alias_names: List[str] = field(default_factory=list)
    """主程序 bot.alias_names。"""

    self_description: str = ""
    """形象描述。user 填了用 user 的，否则用绘卷 [selfie].prompt_prefix。"""

    reference_image_path: str = ""
    """绘卷 [selfie].reference_image_path 解析后的绝对路径。空表示不可用 /
    未配置 / 文件不存在 / 绘卷未装。配图时传给绘卷 input_image_base64 走图生图。"""

    def system_prefix(self) -> str:
        """构造可直接插入 prompt 头部的"自我介绍"片段。

        如果都没有，返回空串。调用方拿到空串应当跳过该行（不要插一行空白）。
        """
        lines: List[str] = []
        if self.nickname:
            lines.append(f"我的名字是{self.nickname}")
        if self.alias_names:
            lines.append(f"（其他人也叫我：{'、'.join(self.alias_names)}）")
        if self.self_description:
            lines.append(self.self_description.rstrip("。.") + "。")
        return "\n".join(lines).strip()


# ============================================================
# 绘卷 selfie 兜底
# ============================================================


def _resolve_art_plugin_dir() -> str:
    """猜测绘卷插件目录的绝对路径。

    基于约定：MaiTrace 自己在 ``plugins/MaiTrace/``，绘卷在
    ``plugins/mais_art_journal/``（同级目录）。

    Returns:
        str: 绝对路径，如目录不存在返回空串。
    """
    here = os.path.dirname(os.path.abspath(__file__))  # .../plugins/MaiTrace/services
    plugins_dir = os.path.dirname(os.path.dirname(here))  # .../plugins
    candidate = os.path.join(plugins_dir, "mais_art_journal")
    return candidate if os.path.isdir(candidate) else ""


async def _get_art_selfie_info(ctx) -> Tuple[str, str]:
    """从麦麦绘卷读 selfie 配置。

    Returns:
        (prompt_prefix, reference_image_absolute_path)
        - prompt_prefix: 英文 / 中文形象前缀；空字符串=没有
        - reference_image_absolute_path: 已 resolve 的绝对路径；空字符串=没配置 /
          文件不存在 / 绘卷未安装

    绘卷未安装、selfie.enabled=false、prompt_prefix 为空 / reference_image_path
    为空或文件缺失，都会安全降级为空字符串。
    """
    try:
        cfg = await ctx.config.get_plugin(_ART_PLUGIN_ID)
    except Exception as exc:
        logger.debug("读绘卷配置失败（可能未安装）: %s", exc)
        return "", ""
    cfg = peel_envelope(cfg)
    if not isinstance(cfg, dict):
        return "", ""
    selfie = cfg.get("selfie") or {}
    if not isinstance(selfie, dict):
        return "", ""
    if not bool(selfie.get("enabled", True)):
        return "", ""

    prefix = str(selfie.get("prompt_prefix", "") or "").strip()

    # reference_image_path 处理（相对路径基于绘卷插件目录）
    raw_path = str(selfie.get("reference_image_path", "") or "").strip()
    abs_path = ""
    if raw_path:
        if os.path.isabs(raw_path):
            candidate = raw_path
        else:
            art_dir = _resolve_art_plugin_dir()
            candidate = os.path.join(art_dir, raw_path) if art_dir else ""
        if candidate and os.path.exists(candidate):
            abs_path = candidate
        else:
            logger.debug("绘卷 reference_image_path 解析失败或文件不存在: %s", raw_path)

    return prefix, abs_path


# ============================================================
# 主入口
# ============================================================


async def resolve_persona(plugin) -> Persona:
    """加载一次完整人格 + 绘卷形象信息。

    Args:
        plugin: MaiTracePlugin 实例（``plugin.ctx`` / ``plugin.config`` 都要用）。

    Returns:
        Persona: 含本次抽样 style + 形象描述 + 参考图路径。
    """
    ctx = plugin.ctx

    personality = await get_global_str(ctx, "personality.personality", "一个机器人")
    default_style = await get_global_str(ctx, "personality.reply_style", "")
    multiple_styles_raw = await get_global_list(ctx, "personality.multiple_reply_style")
    multiple_prob = await get_global_float(ctx, "personality.multiple_probability", 0.0)
    nickname = await get_global_str(ctx, "bot.nickname", "")
    alias_names_raw = await get_global_list(ctx, "bot.alias_names")

    multiple_styles = [str(s).strip() for s in multiple_styles_raw if str(s).strip()]
    alias_names = [str(n).strip() for n in alias_names_raw if str(n).strip()]

    # MaiTrace 自己的 [persona] 段
    cfg_persona = getattr(plugin.config, "persona", None)
    use_multiple = True
    self_desc_cfg = ""
    if cfg_persona is not None:
        use_multiple = bool(getattr(cfg_persona, "use_multiple_reply_style", True))
        self_desc_cfg = (getattr(cfg_persona, "self_description", "") or "").strip()

    # 风格抽样
    style = default_style
    if (
        use_multiple
        and multiple_styles
        and 0.0 < multiple_prob <= 1.0
        and random.random() < multiple_prob
    ):
        style = random.choice(multiple_styles)

    # 绘卷 selfie 信息（无开关、总是读）
    art_prefix, art_ref_path = await _get_art_selfie_info(ctx)

    # 形象描述：user 填了优先用 user 的，否则用绘卷 prompt_prefix
    self_description = self_desc_cfg if self_desc_cfg else art_prefix

    return Persona(
        personality=personality,
        style=style,
        default_style=default_style,
        nickname=nickname,
        alias_names=alias_names,
        self_description=self_description,
        reference_image_path=art_ref_path,
    )
