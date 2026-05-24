"""权限检查（白/黑名单 + 命令管理员）。"""

from __future__ import annotations

import logging
from ..utils import get_logger
from typing import Literal

logger = get_logger(__name__)

Section = Literal["send", "read"]


def check_permission(config, qq_account: str, section: Section) -> bool:
    """检查 qq_account 是否对 section（send/read）有权限。

    用于 @Tool（LLM 触发）的权限检查。

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


def is_admin(config, qq_account: str) -> bool:
    """检查 qq_account 是否在 [plugin].admin_qq 列表中。

    用于 /zn 命令权限：所有命令（含 help / v / debug / 主题）都要求调用者是管理员。
    与 ``check_permission`` 分开 —— 后者控制 @Tool（LLM 触发）的权限。

    Args:
        config: MaiTracePluginConfig 实例。
        qq_account: 待检查的 QQ 号。

    Returns:
        在 admin_qq 列表中返回 True；列表为空 / qq_account 为空时返回 False。
    """
    if not qq_account:
        return False
    admin_list = [str(q).strip() for q in (config.plugin.admin_qq or []) if str(q).strip()]
    return str(qq_account) in admin_list
