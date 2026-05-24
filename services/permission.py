"""权限检查（白/黑名单）。"""

from __future__ import annotations

import logging
from ..utils import get_logger
from typing import Literal

logger = get_logger(__name__)

Section = Literal["send", "read"]


def check_permission(config, qq_account: str, section: Section) -> bool:
    """检查 qq_account 是否对 section（send/read）有权限。

    Args:
        config: MaiTracePluginConfig 实例（plugin.config）。
        qq_account: 待检查的 QQ 号。
        section: "send" 或 "read"。

    Returns:
        是否有权限。permission_type 错误返回 False。
    """
    if not qq_account:
        return False
    sec = getattr(config, section, None)
    if sec is None:
        logger.error("无效的 section: %s", section)
        return False

    perm_list = [str(q) for q in (sec.permission or [])]
    qq = str(qq_account)
    if sec.permission_type == "whitelist":
        return qq in perm_list
    if sec.permission_type == "blacklist":
        return qq not in perm_list
    logger.error("permission_type 错误: %s", sec.permission_type)
    return False
