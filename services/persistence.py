"""processed_list / processed_comments 持久化（带异步锁）。

文件位置：data/processed_list.json、data/processed_comments.json。
格式：dict[fid, list[comment_tid]]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_DATA_DIR = _PLUGIN_DIR / "data"
_PROCESSED_LIST_PATH = _DATA_DIR / "processed_list.json"
_PROCESSED_COMMENTS_PATH = _DATA_DIR / "processed_comments.json"

_processed_list_lock = asyncio.Lock()
_processed_comments_lock = asyncio.Lock()


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


async def load_processed_list() -> Dict[str, List[str]]:
    """加载已处理说说字典。文件不存在则返回空 dict。"""
    async with _processed_list_lock:
        if not _PROCESSED_LIST_PATH.exists():
            logger.info("processed_list 不存在，将创建新文件")
            return {}
        try:
            with _PROCESSED_LIST_PATH.open("r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as exc:
            logger.error("加载 processed_list 失败: %s", exc)
            return {}


async def save_processed_list(data: Dict[str, List[str]]) -> bool:
    """保存已处理说说字典。"""
    async with _processed_list_lock:
        try:
            _ensure_data_dir()
            with _PROCESSED_LIST_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.error("保存 processed_list 失败: %s", exc)
            return False


async def load_processed_comments() -> Dict[str, List[str]]:
    """加载已处理评论字典。"""
    async with _processed_comments_lock:
        if not _PROCESSED_COMMENTS_PATH.exists():
            logger.info("processed_comments 不存在，将创建新文件")
            return {}
        try:
            with _PROCESSED_COMMENTS_PATH.open("r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as exc:
            logger.error("加载 processed_comments 失败: %s", exc)
            return {}


async def save_processed_comments(data: Dict[str, List[str]]) -> bool:
    """保存已处理评论字典。"""
    async with _processed_comments_lock:
        try:
            _ensure_data_dir()
            with _PROCESSED_COMMENTS_PATH.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.error("保存 processed_comments 失败: %s", exc)
            return False


def get_cookie_file_path(uin: str) -> Path:
    """cookies-<uin>.json 的绝对路径。"""
    _ensure_data_dir()
    uin = (uin or "").lstrip("0") or "default"
    return _DATA_DIR / f"cookies-{uin}.json"


def get_qrcode_path() -> Path:
    """qrcode.png 的绝对路径。"""
    _ensure_data_dir()
    return _DATA_DIR / "qrcode.png"


def get_images_dir() -> Path:
    """生图缓存目录。"""
    images_dir = _PLUGIN_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir
