"""只在升级配置时搬迁旧字段，运行时配置统一使用当前结构。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Dict, Tuple


_FIELD_MOVES = (
    # v3.1 将配图从 send/models 拆出，文本模型设置归入 llm。
    *(("send", field, "image", field) for field in (
        "enable_image", "image_mode", "ai_probability", "image_number", "pic_plugin_model",
    )),
    ("models", "clear_image", "image", "clear_image"),
    *(("models", field, "llm", field) for field in (
        "text_model", "llm_timeout_seconds", "show_prompt",
    )),
    ("plugin", "napcat_token", "plugin", "http_token"),
    *((section, old, section, new) for section in ("read", "monitor") for old, new in (
        ("like_possibility", "like_probability"),
        ("comment_possibility", "comment_probability"),
    )),
    ("monitor", "self_readnum", "monitor", "self_read_number"),
)


def _matches_config_type(default: Any, value: Any) -> bool:
    """沿用当前宿主的类型规则，布尔值不能回填到数字字段。"""
    if default is None:
        return True
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, type(default))


def rebuild_upgrade_config(
    defaults: Mapping[str, Any], source: Mapping[str, Any],
) -> Dict[str, Any]:
    """按当前宿主的结构和类型规则生成升级快照。

    SDK 2.15.0 的重建函数不检查类型，不能用于识别宿主重建后的输入。
    迁移后的快照也遵循相同规则，避免重新引入宿主已舍弃的无效值。
    """
    rebuilt = deepcopy(dict(defaults))
    for key, value in source.items():
        if key not in rebuilt or key == "config_version":
            continue
        default = rebuilt[key]
        if isinstance(default, dict) and isinstance(value, Mapping):
            rebuilt[key] = rebuild_upgrade_config(default, value)
        elif _matches_config_type(default, value):
            rebuilt[key] = deepcopy(value)
    return rebuilt


def migrate_legacy_config(config_data: Mapping[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """保留用户已填写的新字段；只迁移仍然有用途的旧字段。"""
    config = deepcopy(dict(config_data))
    for old_section, old_field, new_section, new_field in _FIELD_MOVES:
        source = config.get(old_section)
        if not isinstance(source, dict) or old_field not in source:
            continue
        target = config.setdefault(new_section, {})
        if not isinstance(target, dict):
            raise ValueError(f"[{new_section}] 必须是配置对象")
        if new_field not in target:
            target[new_field] = source[old_field]
        del source[old_field]

    # 退役工具不再具有运行时配置；旧磁盘配置由插件在加载时归档。
    for section in ("diary", "diary_model", "models", "creation", "information", "social"):
        config.pop(section, None)
    routine = config.get("routine")
    if isinstance(routine, dict):
        for key in tuple(routine):
            if key.startswith("scheduled_"):
                del routine[key]
    messaging = config.get("messaging")
    if isinstance(messaging, dict):
        for key in ("private_debounce", "group_debounce", "text_wait_seconds", "image_wait_seconds",
                    "forwarded_wait_seconds", "max_wait_seconds", "recall_summary_enabled"):
            messaging.pop(key, None)
    environment = config.get("environment")
    if isinstance(environment, dict):
        for key in ("weather_enabled", "city", "refresh_minutes", "request_timeout_seconds"):
            environment.pop(key, None)
    life = config.get("life")
    if isinstance(life, dict) and isinstance(life.get("profiles"), list):
        for profile in life["profiles"]:
            if isinstance(profile, dict):
                profile.pop("initial_temperature", None)
    return config, config != config_data
