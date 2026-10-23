"""将 MaiTrace 自身旧目录的数据复制到 SDK 数据目录。"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Set, Tuple
import json
import os
import shutil
import sqlite3
import tempfile

from .persistence import write_json


MIGRATION_MARKER = ".maitrace-data-v6.json"
_DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def _files(directory: Path, data_dir: Path) -> Iterator[Path]:
    """只遍历实际旧目录，跳过嵌套的 SDK 目标，拒绝链接引入外部数据。"""
    if directory.resolve() == data_dir:
        return
    if directory.is_symlink() or directory.is_junction():
        raise ValueError(f"旧数据目录包含链接，请先处理: {directory}")
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink() or entry.is_junction():
            raise ValueError(f"旧数据目录包含链接，请先处理: {entry}")
        if entry.is_dir():
            yield from _files(entry, data_dir)
        elif entry.is_file():
            yield entry


def _sources(plugin_dir: Path, data_dir: Path) -> Iterator[Tuple[Path, Path]]:
    for old_dir, prefix in ((plugin_dir / "data", Path()), (plugin_dir / "images", Path("images"))):
        if old_dir.exists():
            for source in _files(old_dir, data_dir):
                relative = prefix / source.relative_to(old_dir)
                if relative.name == MIGRATION_MARKER:
                    continue
                # WAL/SHM/回滚日志不能独立复制；通过 SQLite backup 生成一致快照。
                if source.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                yield source, relative
    # 更早版本把去重文件放在插件根目录，把 Cookie 和二维码放在 qzone/。
    for filename in ("processed_list.json", "processed_comments.json", "cookie_stats.json", "qrcode.png"):
        source = plugin_dir / filename
        if source.exists():
            yield source, Path(filename)
    for old_dir in (plugin_dir, plugin_dir / "qzone"):
        if not old_dir.exists():
            continue
        for source in sorted(old_dir.glob("cookies-*.json")):
            yield source, Path(source.name)
    qrcode = plugin_dir / "qzone" / "qrcode.png"
    if qrcode.exists():
        yield qrcode, Path(qrcode.name)


def _copy_file(source: Path, target: Path) -> bool:
    """先写完整临时文件；目标已存在时保留它，失败不留下半个目标文件。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        if source.suffix.lower() in _DATABASE_SUFFIXES:
            # mode=ro 禁止创建或改写旧库，仍读取已提交的 WAL 数据。
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as old:
                with closing(sqlite3.connect(str(temporary), timeout=5)) as new:
                    old.backup(new)
                    new.execute("PRAGMA journal_mode=DELETE")
                    result = new.execute("PRAGMA integrity_check").fetchall()
                    if result != [("ok",)]:
                        raise ValueError(f"SQLite 快照校验失败: {source}: {result}")
        else:
            shutil.copy2(source, temporary)
        # 同目录硬链接原子发布完整快照，在 Windows/Linux 上都不会覆盖已有目标。
        try:
            os.link(temporary, target)
        except FileExistsError:
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def migrate_plugin_data(plugin_dir: Path, data_dir: Path) -> int:
    """全部成功后标记完成，失败可重试；源文件和目标已有文件均保留。"""
    plugin_dir, data_dir = plugin_dir.resolve(), data_dir.resolve()
    marker = data_dir / MIGRATION_MARKER
    created: List[Path] = []
    try:
        if marker.exists():
            with marker.open("r", encoding="utf-8") as stream:
                completed = json.load(stream)
            if not isinstance(completed, dict) or completed.get("version") != 6:
                raise ValueError(f"数据迁移标记无效: {marker}")
            return 0
        seen: Set[Path] = set()
        for source, relative in _sources(plugin_dir, data_dir):
            if not source.resolve().is_relative_to(plugin_dir) or source.is_symlink():
                raise ValueError(f"旧数据文件不属于当前插件: {source}")
            target = data_dir / relative
            if not target.resolve().is_relative_to(data_dir):
                raise ValueError(f"数据迁移目标不在 SDK 数据目录: {target}")
            if target in seen or target.exists():
                if target.exists() and not target.is_file():
                    raise ValueError(f"数据迁移目标不是文件: {target}")
                continue
            seen.add(target)
            if _copy_file(source, target):
                created.append(target)
        write_json(marker, {"version": 6, "completed_at": datetime.now(timezone.utc).isoformat(), "copied": len(created)})
        return len(created)
    except Exception as exc:
        # 未完成迁移时只撤销本次创建的副本，避免下次因同名文件跳过不完整的数据。
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise RuntimeError(f"MaiTrace 数据迁移失败: {exc}") from exc
