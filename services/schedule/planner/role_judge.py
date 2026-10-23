"""角色裁判：通过 LLM 判断用户日程请求该如何处理。

供 ``ToolsService.update_schedule`` 使用：把当前日程 + 人设 + 用户描述
扔给 LLM，让它返回 ``{"decision": "today/future/reject", ...}``，调用方
据此决定是向当日空档插入约定、记录未来日期的约定，还是拒绝不变。

接口设计参考了 ``com_auto-planning_schedule/schedule_engine._decide_schedule_update``，
但走 SDK 的 ``ctx.llm.generate``。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio
import json
import logging
import re

from ..core.exceptions import LLMError
from ..commitments import DEFAULT_DURATION_MINUTES, infer_requested_date, validate_commitment_date, validate_duration
from ..llm import generate_text
from ..utils.llm_logger import log_llm_call
from .goal_manager import _normalize_commitment_date

logger = logging.getLogger(__name__)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")

_VALID_DECISIONS = {"today", "future", "reject"}


def _parse_json_loose(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 响应文本中提取首个 JSON 对象。"""
    text = (text or "").strip()
    for candidate in (text, *(m.group(1) for m in _JSON_BLOCK_RE.finditer(text))):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            continue
    match = _JSON_OBJECT_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _infer_future_date(raw_date: str, today_str: str) -> str:
    """LLM 未给出具体日期时，按明确日期、天数或星期推断。"""
    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    target = infer_requested_date(raw_date, today)
    if target is None:
        raise ValueError("未来约定日期不明确，请提供具体日期")
    return target.isoformat()


def _build_judge_prompt(
    *,
    persona: str,
    today_str: str,
    weekday: str,
    current_activities: List[Dict[str, Any]],
    description: str,
    current_time: str = "",
) -> str:
    """构造角色裁判 prompt。"""
    if current_activities:
        lines = []
        for act in current_activities:
            time_str = str(act.get("time", "") or "").strip() or "未指定时间"
            name = str(act.get("name", "") or "").strip()
            lines.append(f"- {time_str} {name}")
        current_text = "\n".join(lines)
    else:
        current_text = "暂无可靠日程"

    return (
        f"【角色人设】\n{persona or '未提供'}\n\n"
        f"【日期信息】\n今天是 {today_str}（{weekday}），现在 {current_time or '时间未提供'}\n"
        "可约今天或任何未来日期，不限制未来天数；过去日期不可预约。\n\n"
        f"【当前日程】\n{current_text}\n\n"
        f"【用户请求】\n{description}\n\n"
        "请扮演该角色，判断是否会接受这个日程相关请求。\n"
        "不要机械接受用户安排；如果不符合人设、时间冲突太强、语气像玩笑或没有明确计划，可以拒绝或不改日程。\n"
        "decision 只能是以下值之一：\n"
        "- today: 角色愿意在今天的空闲时段新增约定，不能移动、缩短或覆盖原活动\n"
        "- future: 角色接受任意未来日期的预约，只记录预约，不修改今天日程\n"
        "- reject: 角色没有接受，日程不变\n\n"
        "已有安排需要兑现，不为了讨好用户打乱作息；今天时间已过或冲突时拒绝并给出自然理由。"
        "未来日期不限制天数，日期不明确时不要擅自承诺。\n"
        "以 JSON 格式返回：\n"
        '{"decision": "today/future/reject", "date": "YYYY-MM-DD 或空", '
        '"time": "HH:MM 或空", "title": "简短标题", "notes": "备注", '
        '"reason": "角色判断理由", "raw_date": "用户原文中的日期描述", '
        '"duration_minutes": 30}\n'
        "duration_minutes 是 15 至 720 的整数，按请求估计；没有明确时长时用 30 分钟。\n"
        "日期必须根据【日期信息】中的真实日期推断。\n"
        "只输出 JSON，不要其他文字。"
    )


async def judge_schedule_request(
    plugin: Any,
    *,
    description: str,
    current_activities: List[Dict[str, Any]],
    persona: str,
    today_str: str,
    weekday: str,
    model: str,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    log_dir: Optional[Path] = None,
    log_enabled: bool = True,
    current_time: str = "",
) -> Optional[Dict[str, Any]]:
    """让 LLM 判断用户日程请求该如何处理。

    Args:
        plugin: 当前插件实例（用于 ``ctx.llm.generate``）。
        description: 用户的日程相关请求文本。
        current_activities: 今日日程活动列表（每项含 ``time`` / ``name``）。
        persona: 角色人设文本。
        today_str: 今日日期（``YYYY-MM-DD``）。
        weekday: 今日星期标签。
        model: 主程序 model_config 中的任务名。
        temperature: LLM 温度。
        max_tokens: LLM 最大 tokens。
        log_dir: LLM 调用归档目录；为 None 时不归档。
        log_enabled: 是否开启归档。

    Returns:
        解析后的判断 dict（含 ``decision`` 等字段）；LLM 或解析失败时抛出错误，
        调用方报告失败并保持原日程。
    """
    prompt = _build_judge_prompt(
        persona=persona,
        today_str=today_str,
        weekday=weekday,
        current_activities=current_activities,
        description=description,
        current_time=current_time,
    )

    try:
        result = await generate_text(
            plugin,
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning(f"角色裁判 LLM 调用失败: {exc}")
        if log_enabled and log_dir is not None:
            await asyncio.to_thread(log_llm_call, "role_decision", prompt, str(exc), model, False, log_dir)
        raise

    response_text = ""
    if isinstance(result, dict):
        response_text = str(result.get("response", "") or "").strip()
    success = bool(result.get("success", False)) if isinstance(result, dict) else False

    if log_enabled and log_dir is not None:
        await asyncio.to_thread(log_llm_call, "role_decision", prompt, response_text, model, success and bool(response_text), log_dir)

    if not success or not response_text:
        raise LLMError(f"角色裁判调用失败: {response_text or '模型返回空内容'}")

    parsed = _parse_json_loose(response_text)
    if not parsed:
        raise LLMError("角色裁判响应不是有效 JSON 对象")

    decision = str(parsed.get("decision", "") or "").strip().lower()
    if decision not in _VALID_DECISIONS:
        raise LLMError(f"角色裁判返回未知 decision: {decision}")
    parsed["decision"] = decision
    parsed.setdefault("title", description[:40])
    parsed.setdefault("date", "")
    parsed.setdefault("time", "")
    parsed.setdefault("notes", "")
    parsed.setdefault("reason", "")
    parsed.setdefault("raw_date", "")
    parsed.setdefault("duration_minutes", DEFAULT_DURATION_MINUTES)

    # 补全 future 分支的日期推断
    if parsed["decision"] == "future" and not str(parsed.get("date") or "").strip():
        parsed["date"] = _infer_future_date(str(parsed.get("raw_date") or description), today_str)
    if parsed["decision"] == "future":
        parsed["date"] = _normalize_commitment_date(str(parsed["date"]), datetime.strptime(today_str, "%Y-%m-%d"))
    if parsed["decision"] != "reject":
        target = datetime.strptime(str(parsed.get("date") or today_str), "%Y-%m-%d").date()
        validate_commitment_date(target, datetime.strptime(today_str, "%Y-%m-%d").date())
        parsed["duration_minutes"] = validate_duration(parsed["duration_minutes"])
    return parsed
