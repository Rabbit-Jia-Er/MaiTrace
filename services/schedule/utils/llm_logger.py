"""LLM 调用归档。

把每次 LLM 调用的 prompt + 响应 + 模型 + 成功状态写入独立文件，方便事后
排查 prompt / 响应质量问题。生产环境可通过配置开关 ``llm_log_enabled``
快速关闭。

文件命名：``{ok|fail}_<call_type>_<timestamp>.txt``，例如
``ok_schedule_generation_20260525_103045_123456.txt``。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_CALL_TYPE = re.compile(r"[^a-zA-Z0-9_.\-]")


def _sanitize(call_type: str) -> str:
    """把 call_type 过滤成安全的文件名片段。"""
    cleaned = _SAFE_CALL_TYPE.sub("_", str(call_type).strip())
    return cleaned or "unknown"


def log_llm_call(
    call_type: str,
    prompt: str,
    response: str,
    model: str,
    success: bool,
    log_dir: Path,
) -> None:
    """把一次 LLM 调用写入 ``log_dir`` 下的独立文件。

    Args:
        call_type: 调用类型（``schedule_generation`` / ``role_decision`` 等）。
        prompt: 完整 prompt 文本。
        response: LLM 响应文本。
        model: 使用的模型 / 任务名。
        success: 调用是否成功。
        log_dir: 目标日志目录；不存在会被创建。

    Raises:
        Never — 任何 OSError 都被静默吞掉，避免影响主业务流程。
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        status = "ok" if success else "fail"
        filename = f"{status}_{_sanitize(call_type)}_{timestamp}.txt"
        target = log_dir / filename
        content = (
            f"=== MODEL ===\n{model or 'default'}\n\n"
            f"=== SUCCESS ===\n{success}\n\n"
            f"=== PROMPT ===\n{prompt}\n\n"
            f"=== RESPONSE ===\n{response}"
        )
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.debug(f"LLM 日志写入失败（已忽略）: {exc}")


def cleanup_old_logs(log_dir: Path, retention_days: int) -> int:
    """删除超过保留期的 LLM 日志文件。

    Args:
        log_dir: 日志目录。
        retention_days: 保留天数（必须 ≥ 1，否则按 1 处理）。

    Returns:
        被删除的文件数；目录不存在返回 0。
    """
    if not log_dir.is_dir():
        return 0
    retention_days = max(1, int(retention_days))
    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted = 0
    for path in log_dir.iterdir():
        if path.suffix != ".txt":
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                deleted += 1
        except OSError:
            continue
    if deleted:
        logger.debug(f"清理了 {deleted} 个过期 LLM 日志")
    return deleted
