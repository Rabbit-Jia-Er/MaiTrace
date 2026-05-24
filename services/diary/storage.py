"""日记 JSON 文件存储。

文件布局：
    data/diaries/YYYY-MM-DD_HHMMSS.json
    data/diary_index.json
"""

from __future__ import annotations

import datetime
import json
import logging
from ...utils import get_logger
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...utils.date import format_date_str

logger = get_logger(__name__)


class DiaryStorage:
    """JSON 文件日记存储管理器。"""

    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent.parent  # plugins/MaiTrace/
        self.data_dir = base / "data" / "diaries"
        self.index_file = base / "data" / "diary_index.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_file.parent.mkdir(parents=True, exist_ok=True)

    async def save_diary(self, diary_data: Dict[str, Any]) -> bool:
        """保存日记。文件名 YYYY-MM-DD_HHMMSS.json。"""
        try:
            date = diary_data["date"]
            generation_time = diary_data.get("generation_time", time.time())
            timestamp = datetime.datetime.fromtimestamp(generation_time)
            filename = f"{format_date_str(date)}_{timestamp.strftime('%H%M%S')}.json"
            file_path = self.data_dir / filename
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(diary_data, f, ensure_ascii=False, indent=2)
            await self._update_index()
            return True
        except Exception as exc:
            logger.error("保存日记失败: %s", exc, exc_info=True)
            return False

    async def get_diary(self, date: str) -> Optional[Dict[str, Any]]:
        """指定日期最新一条日记。"""
        diaries = await self.get_diaries_by_date(date)
        return max(diaries, key=lambda d: d.get("generation_time", 0)) if diaries else None

    async def get_diaries_by_date(self, date: str) -> List[Dict[str, Any]]:
        """指定日期所有日记，按生成时间升序。"""
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

    async def get_stats(self) -> Dict[str, Any]:
        try:
            diaries = await self.list_diaries(limit=0)
            if not diaries:
                return {"total_count": 0, "total_words": 0, "avg_words": 0, "latest_date": "无"}
            total_count = len(diaries)
            total_words = sum(d.get("word_count", 0) for d in diaries)
            avg_words = total_words // total_count
            latest_date = max(diaries, key=lambda d: d.get("generation_time", 0)).get("date", "无")
            return {
                "total_count": total_count,
                "total_words": total_words,
                "avg_words": avg_words,
                "latest_date": latest_date,
            }
        except Exception as exc:
            logger.error("get_stats 失败: %s", exc, exc_info=True)
            return {"total_count": 0, "total_words": 0, "avg_words": 0, "latest_date": "无"}

    async def _update_index(self) -> None:
        try:
            index_data: Dict[str, Any] = {
                "last_update": time.time(),
                "total_diaries": 0,
                "success_count": 0,
                "failed_count": 0,
            }
            if not self.data_dir.exists():
                with self.index_file.open("w", encoding="utf-8") as f:
                    json.dump(index_data, f, ensure_ascii=False, indent=2)
                return

            success = failed = 0
            for filename in os.listdir(self.data_dir):
                if not filename.endswith(".json"):
                    continue
                try:
                    with (self.data_dir / filename).open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("is_published_qzone"):
                        success += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
            index_data.update({
                "success_count": success,
                "failed_count": failed,
                "total_diaries": success + failed,
            })
            with self.index_file.open("w", encoding="utf-8") as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("更新索引失败: %s", exc, exc_info=True)
