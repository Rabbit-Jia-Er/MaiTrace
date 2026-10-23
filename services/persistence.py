"""实例持有的空间持久化服务，所有文件位于 SDK 分配的数据目录。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List
import asyncio
import hashlib
import json
import os
import tempfile
import time

from ..utils import get_logger

logger = get_logger(__name__)


def write_json(path: Path, data: Any) -> None:
    """同目录临时文件替换，避免中断时留下半个 JSON。在线程中调用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _archive_legacy_config(data_dir: Path, content: bytes) -> None:
    """在线程中原样归档配置；原子发布完整文件且不覆盖已有备份。"""
    directory = data_dir / "legacy"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"config-{hashlib.sha256(content).hexdigest()[:16]}.toml"
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError("旧配置归档内容与同名文件不一致")
        return
    descriptor, filename = tempfile.mkstemp(prefix=".config-", dir=directory)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != content:
                raise ValueError("旧配置归档内容与同名文件不一致")
    finally:
        temporary.unlink(missing_ok=True)


def queue_legacy_config_archive(data_dir: Path, content: bytes) -> Future[None]:
    """同步配置回调仅提交已读字节；禁用插件被丢弃后线程仍完成归档。"""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maitrace-config-archive")
    try:
        future = executor.submit(_archive_legacy_config, Path(data_dir), content)
    finally:
        # 不等待磁盘写入，不占用 Runner 事件循环；解释器正常退出会等待工作线程。
        executor.shutdown(wait=False)

    def report_failure(completed: Future[None]) -> None:
        error = completed.exception()
        if error is not None:
            logger.error("旧配置归档失败: %s", error, exc_info=(type(error), error, error.__traceback__))

    future.add_done_callback(report_failure)
    return future


class PluginPersistence:
    """文件路径、锁及 Cookie 状态均由当前插件实例持有。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._processed_list_lock = asyncio.Lock()
        self._processed_comments_lock = asyncio.Lock()
        self._cookie_stats_lock = asyncio.Lock()
        self._activity_lock = asyncio.Lock()
        self._migration_lock = asyncio.Lock()
        self.cookie_state: Dict[str, Any] = {
            "last_save_time": 0.0, "last_method": "", "last_error": "",
            "uin": "", "adapter_id": "",
        }

    def _load_mapping(self, filename: str) -> Dict:
        path = self.data_dir / filename
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError(f"{filename} 必须是 JSON 对象")
            return data
        except FileNotFoundError:
            return {}

    async def _run_io(self, operation: Callable[..., Any], *args: Any) -> Any:
        # 取消等待不会停止线程；线程完成前保留外层文件锁，防止重载后的写入被旧任务覆盖。
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def migrate_legacy_data(self, plugin_dir: Path) -> int:
        """初始化业务存储前迁移旧文件；取消或重载时等待复制线程退出。"""
        from .data_migration import migrate_plugin_data

        def migrate() -> int:
            # 当前 Runner 的冷配置校验没有上下文；这些原件即使已有 v6 迁移标记也要带入。
            staged = plugin_dir / "data" / "legacy"
            if staged.is_symlink() or staged.is_junction():
                raise ValueError(f"旧配置归档目录包含链接: {staged}")
            for source in staged.glob("config-*.toml"):
                if source.is_symlink() or source.is_junction() or not source.is_file():
                    raise ValueError(f"旧配置归档不是普通文件: {source}")
                _archive_legacy_config(self.data_dir, source.read_bytes())
            return migrate_plugin_data(plugin_dir, self.data_dir)

        async with self._migration_lock:
            return await self._run_io(migrate)

    async def archive_legacy_config(self, content: bytes) -> None:
        """原样保留升级前的配置；按内容命名，同一份快照只归档一次。"""
        async with self._migration_lock:
            await self._run_io(_archive_legacy_config, self.data_dir, content)

    async def load_processed_list(self) -> Dict[str, List[str]]:
        """加载已处理说说字典。文件不存在则返回空 dict。"""
        async with self._processed_list_lock:
            return await self._run_io(self._load_mapping, "processed_list.json")

    async def save_processed_list(self, data: Dict[str, List[str]]) -> bool:
        """保存已处理说说字典。"""
        async with self._processed_list_lock:
            await self._run_io(write_json, self.data_dir / "processed_list.json", data)
        return True

    async def load_processed_comments(self) -> Dict[str, List[str]]:
        """加载已处理评论字典。"""
        async with self._processed_comments_lock:
            return await self._run_io(self._load_mapping, "processed_comments.json")

    async def save_processed_comments(self, data: Dict[str, List[str]]) -> bool:
        """保存已处理评论字典。"""
        async with self._processed_comments_lock:
            await self._run_io(write_json, self.data_dir / "processed_comments.json", data)
        return True

    async def load_cookie_stats(self) -> Dict[str, Dict[str, float]]:
        """统计损坏时按配置顺序尝试，不阻断登录。"""
        async with self._cookie_stats_lock:
            try:
                return await self._run_io(self._load_mapping, "cookie_stats.json")
            except (OSError, ValueError) as exc:
                logger.warning("加载 cookie_stats 失败（按原顺序继续）: %s", exc)
                return {}

    async def record_cookie_attempt(self, method: str, success: bool) -> None:
        """记录一次 Cookie 获取尝试结果。"""
        if not method:
            return
        async with self._cookie_stats_lock:
            await self._run_io(self._record_cookie_attempt, method, success)

    def _record_cookie_attempt(self, method: str, success: bool) -> None:
        try:
            data = self._load_mapping("cookie_stats.json")
        except (OSError, ValueError):
            data = {}
        entry = data.get(method) or {"success": 0, "failure": 0, "last_success_ts": 0.0}
        if success:
            entry["success"] = int(entry.get("success", 0)) + 1
            entry["last_success_ts"] = time.time()
        else:
            entry["failure"] = int(entry.get("failure", 0)) + 1
        data[method] = entry
        try:
            write_json(self.data_dir / "cookie_stats.json", data)
        except OSError as exc:
            logger.warning("保存 cookie_stats 失败: %s", exc)

    def get_cookie_file_path(self, uin: str) -> Path:
        """cookies-<uin>.json 的路径；取路径不执行磁盘写入。"""
        uin = (uin or "").lstrip("0") or "default"
        if uin != "default" and not uin.isdecimal():
            raise ValueError("Cookie 账号必须是 QQ 号码")
        return self.data_dir / f"cookies-{uin}.json"

    def _load_qzone_activity(self) -> List[Dict[str, Any]]:
        try:
            with (self.data_dir / "qzone_activity.json").open("r", encoding="utf-8") as stream:
                entries = json.load(stream)
        except FileNotFoundError:
            return []
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise ValueError("qzone_activity.json 必须是活动对象列表")
        return entries[-500:]

    def _record_qzone_activity(self, entry: Dict[str, Any]) -> None:
        entries = self._load_qzone_activity()
        entries.append(entry)
        write_json(self.data_dir / "qzone_activity.json", entries[-500:])

    async def record_qzone_activity(self, kind: str, **fields: Any) -> bool:
        """成功动作的有界历史；落盘失败只告警，不能把已成功的网络动作改成失败。"""
        try:
            if kind not in {"publish", "comment", "skip_comment", "reply"}:
                raise ValueError(f"未知空间活动类型: {kind}")
            entry: Dict[str, Any] = {
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
            }
            for key in ("target_qq", "target_name", "fid", "comment_tid", "feed", "comment", "text"):
                text = str(fields.get(key) or "").strip()
                if text:
                    entry[key] = text[:1000]
            async with self._activity_lock:
                await self._run_io(self._record_qzone_activity, entry)
            return True
        except Exception as exc:
            logger.warning("记录空间活动失败: %s", exc)
            return False

    async def recent_qzone_activity(self, *, keyword: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        """跨会话查询当前插件自己的空间活动，读取失败由工具明确报告。"""
        async with self._activity_lock:
            entries = await self._run_io(self._load_qzone_activity)
        needle = keyword.strip().casefold()
        return [entry for entry in reversed(entries)
                if not needle or any(needle in str(value).casefold() for value in entry.values())][:max(1, min(50, limit))]

    def get_qrcode_path(self) -> Path:
        return self.data_dir / "qrcode.png"

    def get_images_dir(self) -> Path:
        return self.data_dir / "images"
