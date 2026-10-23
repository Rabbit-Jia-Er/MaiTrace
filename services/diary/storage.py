"""旧版聊天日记 JSON 的只读访问。

文件布局（相对于 SDK data_dir）：
    diaries/YYYY-MM-DD_HHMMSS.json
    diary_index.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio
import json
import os

from ...utils import get_logger
from ...utils.date import format_date_str

logger = get_logger(__name__)


class DiaryStorage:
    """只读取已有日记文件，不生成或发布新聊天日记。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir) / "diaries"
        self.index_file = Path(data_dir) / "diary_index.json"


    async def get_diary(self, date: str) -> Optional[Dict[str, Any]]:
        """指定日期最新一条日记。"""
        diaries = await self.get_diaries_by_date(date)
        return max(diaries, key=lambda d: d.get("generation_time", 0)) if diaries else None

    async def get_diaries_by_date(self, date: str) -> List[Dict[str, Any]]:
        """指定日期所有日记，按生成时间升序。"""
        return await asyncio.to_thread(self._get_diaries_by_date, date)

    def _get_diaries_by_date(self, date: str) -> List[Dict[str, Any]]:
        try:
            if not self.data_dir.exists():
                return []
            prefix = f"{format_date_str(date)}_"
            results: List[Dict[str, Any]] = []
            for filename in os.listdir(self.data_dir):
                if filename.startswith(prefix) and filename.endswith(".json"):
                    file_path = self.data_dir / filename
                    try:
                        with file_path.open("r", encoding="utf-8") as f:
                            results.append(json.load(f))
                    except Exception as exc:
                        logger.warning("读日记 %s 失败: %s", filename, exc)
            results.sort(key=lambda d: d.get("generation_time", 0))
            return results
        except Exception as exc:
            logger.error("读日期日记失败: %s", exc, exc_info=True)
            return []

    async def list_diaries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """最近 limit 条（0 = 不限），按生成时间降序。"""
        return await asyncio.to_thread(self._list_diaries, limit)

    def _list_diaries(self, limit: int) -> List[Dict[str, Any]]:
        try:
            if not self.data_dir.exists():
                return []
            results: List[Dict[str, Any]] = []
            for filename in os.listdir(self.data_dir):
                if not filename.endswith(".json"):
                    continue
                try:
                    with (self.data_dir / filename).open("r", encoding="utf-8") as f:
                        results.append(json.load(f))
                except Exception as exc:
                    logger.warning("读日记 %s 失败: %s", filename, exc)
            results.sort(key=lambda d: d.get("generation_time", 0), reverse=True)
            return results[:limit] if limit > 0 else results
        except Exception as exc:
            logger.error("列日记失败: %s", exc, exc_info=True)
            return []
