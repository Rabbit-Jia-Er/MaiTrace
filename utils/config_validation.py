"""配置输入校验，独立于配置包，避免日程与根配置之间的循环导入。"""

from __future__ import annotations

from string import Formatter
from typing import List, Set
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def qq_id(value: str, *, allow_empty: bool = False) -> str:
    value = value.strip()
    if allow_empty and not value:
        return value
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("QQ 号必须是非空的半角数字字符串")
    return value


def qq_list(values: List[str]) -> List[str]:
    return list(dict.fromkeys(qq_id(value) for value in values))


def chat_references(values: List[str], *, proactive: bool = False, material: bool = False) -> List[str]:
    """只检查格式，保留裸会话 ID 和其他平台已有的有效引用。"""
    normalized = []
    for raw in values:
        value = raw.strip()
        if not value:
            raise ValueError("会话名单条目不可留空；不用时请删除条目")
        if value.startswith("session:") and value[len("session:"):].strip():
            normalized.append(value)
            continue
        parts = value.split(":")
        expected = 2 if material else 3
        if len(parts) == expected and parts[-2] in ("group", "private") and (material or parts[0]):
            if material or parts[0] == "qq":
                qq_id(parts[-1])
            elif not parts[-1].strip():
                raise ValueError("会话目标 ID 不可留空")
            normalized.append(value)
            continue
        if not material and ":" not in value and not (proactive and value.lower() == "all"):
            normalized.append("all" if value.lower() == "all" else value)
            continue
        example = "group:123 / private:456 / session:真实ID" if material else "qq:group:123 / qq:private:456 / session:真实ID"
        raise ValueError(f"会话引用格式错误：{value}；支持 {example}")
    return list(dict.fromkeys(normalized))


def model_task(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("模型任务名不可留空；填写主程序已有任务，如 replyer 或 utils")
    return value


def clock_time(value: str) -> str:
    """将有效时间规范化为两位 HH:MM。"""
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(part.isascii() and part.isdecimal() for part in parts):
        raise ValueError("时间必须为 HH:MM")
    hour, minute = map(int, parts)
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("时间必须为有效的 HH:MM")
    return f"{hour:02d}:{minute:02d}"


def timezone_name(value: str) -> str:
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("时区必须为有效的 IANA 名称，如 Asia/Shanghai 或 UTC") from exc
    return value


def prompt_template(value: str, placeholders: Set[str]) -> str:
    """仅接受已提供的占位符；JSON 等字面花括号需写成 {{ 和 }}。"""
    try:
        for _, name, spec, conversion in Formatter().parse(value):
            if name is None:
                continue
            if name not in placeholders or spec or conversion:
                raise ValueError(f"不支持占位符 {{{name}}}；支持：{', '.join(sorted(placeholders))}")
    except ValueError as exc:
        raise ValueError(f"提示词模板格式错误：{exc}；字面花括号请写成 {{{{ 和 }}}}") from exc
    return value
