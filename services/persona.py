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

- 文本路径（说说/评论/日记 prompt）：``self_description`` 非空 → 用 user 填的；
  为空 → 自动从绘卷 ``mais_art_journal/[selfie].prompt_prefix`` 兜底
- 图像路径（配图）：调绘卷 ``generate_image`` 传 ``selfie_mode=True``，由绘卷自己
  用 ``[selfie].prompt_prefix`` 和 ``[selfie].reference_image_path`` 处理。
  这套独立链路在 ``services/feed_image.py``，本模块不参与。
"""

from __future__ import annotations

import logging
from ..utils import get_logger
import random
from dataclasses import dataclass, field
from typing import List

from ..utils import (
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
    """形象描述（仅注入文本 LLM）。user 填了用 user 的，否则用绘卷 [selfie].prompt_prefix 兜底。"""

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
# 绘卷 selfie 兜底（仅用于文本 prompt）
# ============================================================


async def _get_art_selfie_prefix(ctx) -> str:
    """从麦麦绘卷读 ``[selfie].prompt_prefix``。

    绘卷未安装 / selfie.enabled=false / prompt_prefix 为空 → 返回空串。

    Note:
        参考图（``reference_image_path``）由绘卷自己在 ``selfie_mode=True`` 流程中读取，
        本插件不再读它，因此这里只返回 ``prompt_prefix``。
    """
    try:
        cfg = await ctx.config.get_plugin(_ART_PLUGIN_ID)
    except Exception as exc:
        logger.debug("读绘卷配置失败（可能未安装）: %s", exc)
        return ""
    cfg = peel_envelope(cfg)
    if not isinstance(cfg, dict):
        return ""
    selfie = cfg.get("selfie") or {}
    if not isinstance(selfie, dict):
        return ""
    if not bool(selfie.get("enabled", True)):
        return ""
    return str(selfie.get("prompt_prefix", "") or "").strip()


# ============================================================
# 主入口
# ============================================================


async def resolve_persona(plugin) -> Persona:
    """加载一次完整人格 + 形象兜底。

    Args:
        plugin: MaiTracePlugin 实例（``plugin.ctx`` / ``plugin.config`` 都要用）。

    Returns:
        Persona: 含本次抽样 style + 形象描述。
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

    # 形象描述：user 填了优先用 user 的，否则用绘卷 prompt_prefix 兜底
    if self_desc_cfg:
        self_description = self_desc_cfg
    else:
        self_description = await _get_art_selfie_prefix(ctx)

    return Persona(
        personality=personality,
        style=style,
        default_style=default_style,
        nickname=nickname,
        alias_names=alias_names,
        self_description=self_description,
    )
