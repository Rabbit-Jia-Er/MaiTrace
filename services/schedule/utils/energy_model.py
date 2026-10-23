"""时段能量基线模型。

按 24 小时时段映射能量值（0-100），用于注入时提示 LLM 当前 bot 的精神状态。
参考真实人类作息：凌晨低 / 上午高峰 / 午后微疲 / 下午次高峰 / 晚间放松 / 深夜撑不住。

使用方式::

    from .energy_model import describe_energy, get_energy_level
    level = get_energy_level(now.hour)           # 0~100
    desc = describe_energy(level)                # "状态不错" / "困了" ...

注入到 prompt 后让 LLM 自行决定回复语气。
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


# 24 小时能量曲线（参考真实人类作息）：
#   0-6   凌晨：10-25（深睡 / 浅睡 / 强行醒着也撑不住）
#   6-9   早晨：50-70（醒后清醒）
#   9-12  上午：75-88（高效时段）
#   12-14 午间：60-70（午饭后微疲）
#   14-18 下午：65-80（次高峰）
#   18-21 傍晚：60-75（晚饭后轻度疲劳）
#   21-23 晚间：40-55（放松，准备睡）
#   23-24 深夜：25-40（撑不住）
_ENERGY_TABLE: dict[int, int] = {
    0: 15, 1: 12, 2: 10, 3: 10, 4: 12, 5: 20,
    6: 50, 7: 60, 8: 70, 9: 80, 10: 85, 11: 88,
    12: 70, 13: 60, 14: 65, 15: 75, 16: 75, 17: 70,
    18: 65, 19: 70, 20: 65, 21: 55, 22: 45, 23: 30,
}


def get_energy_level(hour: int) -> int:
    """根据 24 小时制小时数返回能量值。

    Args:
        hour: 0-23 的小时数。超出范围会被 clamp 到 0/23。

    Returns:
        能量值（0-100），数字越大越精神。
    """
    clamped = max(0, min(23, int(hour)))
    return _ENERGY_TABLE[clamped]


def describe_energy(energy: int) -> str:
    """把数值能量值映射为简短自然语言描述。

    映射策略（5 档）：
        - ≥80  精神满满
        - 60~79 状态不错
        - 40~59 正常
        - 25~39 有点累
        - 10~24 困了
        - <10  快撑不住

    Args:
        energy: 0-100 的能量值。

    Returns:
        2-4 字的描述短语。
    """
    if energy >= 80:
        return "精神满满"
    if energy >= 60:
        return "状态不错"
    if energy >= 40:
        return "正常"
    if energy >= 25:
        return "有点累"
    if energy >= 10:
        return "困了"
    return "快撑不住"


def get_time_period(hour: int) -> str:
    """根据小时返回时段标签（用于碎碎念语料）。"""
    clamped = max(0, min(23, int(hour)))
    if clamped < 6:
        return "凌晨"
    if clamped < 9:
        return "早晨"
    if clamped < 12:
        return "上午"
    if clamped < 14:
        return "中午"
    if clamped < 18:
        return "下午"
    if clamped < 21:
        return "傍晚"
    if clamped < 23:
        return "晚上"
    return "深夜"
