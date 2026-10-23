"""旧配置和旧数据升级回归；只读宿主源码，所有文件操作在临时目录。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, Mock, patch
import ast
import asyncio
import hashlib
import json
import sqlite3
import sys
import threading
import tomllib

import pytest
import tomlkit
from maibot_sdk.config import rebuild_plugin_config_data
from maibot_sdk.context import PluginContext, PluginPaths

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from MaiTrace.handlers.commands import dispatch_zn
from MaiTrace.plugin import MaiTracePlugin
from MaiTrace.services import data_migration, persistence
from MaiTrace.services.diary.storage import DiaryStorage
from MaiTrace.services.life import LifeEngine, LifeStore
from MaiTrace.services.life.config import LifeUserProfile
from MaiTrace.services.schedule import ScheduleEngine
from MaiTrace.services.schedule.planner.goal_manager import GoalStatus
from MaiTrace.services.persistence import PluginPersistence
from MaiTrace.utils import get_global


class LocalPlugin(MaiTracePlugin):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory

    @property
    def plugin_dir(self) -> Path:
        return self.directory


def _host_rebuild_config(defaults: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    """执行当前宿主真实的配置重建函数，避免导入宿主时初始化日志和数据库。"""
    path = Path(__file__).resolve().parents[3] / "src/plugin_runtime/runner/runner_main.py"
    if not path.is_file():
        pytest.skip("当前安装未提供宿主配置重建源码")
    names = {
        "_deep_copy_plugin_config_value", "_deep_copy_plugin_config_mapping",
        "_is_plugin_config_value_type_compatible", "_overlay_plugin_config_fields",
        "rebuild_plugin_config_data",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    namespace = {"Any": Any, "Dict": Dict, "Mapping": Mapping}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["rebuild_plugin_config_data"](defaults, source)


@pytest.fixture(autouse=True)
def finish_config_archive_workers(tmp_path: Path):
    """临时目录移除前等待全部归档，线程失败直接使测试失败。"""
    del tmp_path
    pending = []
    original = persistence.queue_legacy_config_archive

    def queue(*args):
        future = original(*args)
        pending.append(future)
        return future

    with patch("MaiTrace.plugin.queue_legacy_config_archive", side_effect=queue):
        yield
    for future in pending:
        future.result(timeout=5)


def _host_config_writer():
    """只执行当前宿主的 TOML 写入/禁用判断，不初始化宿主运行时。"""
    path = Path(__file__).resolve().parents[3] / "src/plugin_runtime/runner/runner_main.py"
    if not path.is_file():
        pytest.skip("当前安装未提供宿主配置写入源码")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    runner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginRunner")
    names = {"_merge_plugin_config_document", "_has_extra_config_keys", "_save_plugin_config", "_is_plugin_enabled"}
    runner.body = [node for node in runner.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in runner.body} == names
    namespace = {
        "Any": Any, "Dict": Dict, "Mapping": Mapping, "Optional": Optional,
        "Path": Path, "tomlkit": tomlkit, "logger": Mock(),
    }
    exec(compile(ast.Module(body=[runner], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["PluginRunner"]


def test_legacy_fields_move_before_sdk_validation_and_keep_new_values() -> None:
    plugin = MaiTracePlugin()
    source = {
        "plugin": {"config_version": "3.0.0", "napcat_token": "old-token"},
        "send": {"enable_image": False, "image_number": 3, "history_number": 8},
        "image": {"image_number": 2},
        "models": {"text_model": "utils", "clear_image": False},
        "read": {"like_possibility": 0.2, "comment_possibility": 0.3},
        "monitor": {"like_possibility": 0.4, "comment_possibility": 0.5, "self_readnum": 9},
        "diary_model": {"api_timeout": 420, "api_key": "obsolete-secret"},
    }
    original = deepcopy(source)
    normalized, changed = plugin.normalize_plugin_config(source)
    assert source == original
    assert changed and normalized["plugin"]["config_version"] == plugin.build_default_config()["plugin"]["config_version"]
    assert normalized["plugin"]["http_token"] == "old-token"
    assert normalized["image"]["enable_image"] is False
    assert normalized["image"]["image_number"] == 2
    assert normalized["image"]["clear_image"] is False
    assert normalized["send"]["history_number"] == 8
    assert normalized["llm"]["text_model"] == "utils"
    assert normalized["read"]["like_probability"] == 0.2
    assert normalized["read"]["comment_probability"] == 0.3
    assert normalized["monitor"]["like_probability"] == 0.4
    assert normalized["monitor"]["comment_probability"] == 0.5
    assert normalized["monitor"]["self_read_number"] == 9
    assert "diary" not in normalized
    assert "obsolete-secret" not in repr(normalized)
    assert "models" not in normalized and "diary_model" not in normalized
    assert plugin.normalize_plugin_config(normalized) == (normalized, False)


def test_current_http_token_takes_precedence_and_chat_diary_config_is_removed() -> None:
    normalized, _ = MaiTracePlugin().normalize_plugin_config({
        "plugin": {"config_version": "3.1.0", "napcat_token": "old", "http_token": "current"},
        "diary": {"llm_timeout_seconds": 600}, "diary_model": {"api_timeout": 420},
    })
    assert "diary" not in normalized and "diary_model" not in normalized
    assert normalized["plugin"]["http_token"] == "current"


def test_host_pruning_preserves_moved_values_without_writing_source(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    content = b'[plugin]\nconfig_version = "3.0.0"\nnapcat_token = "saved"\n[send]\nenable_image = false\n[models]\ntext_model = "utils"\n[diary_model]\napi_timeout = 420\napi_key = "obsolete"\n'
    config_file.write_bytes(content)
    plugin = LocalPlugin(tmp_path)
    defaults = plugin.get_default_config()
    source = {"plugin": {"config_version": "3.0.0", "napcat_token": "saved"},
              "send": {"enable_image": False}, "models": {"text_model": "utils"},
              "diary_model": {"api_timeout": 420, "api_key": "obsolete"}}
    rebuilt = _host_rebuild_config(defaults, source)
    assert "diary_model" not in rebuilt
    assert "diary" not in rebuilt
    normalized, changed = plugin.normalize_plugin_config(rebuilt)
    assert changed and normalized["plugin"]["http_token"] == "saved"
    assert normalized["image"]["enable_image"] is False
    assert normalized["llm"]["text_model"] == "utils"
    assert "diary" not in normalized
    assert config_file.read_bytes() == content
    plugin.set_plugin_config(normalized)
    assert plugin.config.plugin.http_token == "saved"


@pytest.mark.parametrize("section, field, value", [
    ("plugin", "http_port", "9999"),
    ("plugin", "admin_qq", '"123"'),
    ("send", "history_number", "true"),
    ("send", "history_number", "8.0"),
    ("routine", "post_enabled", '"false"'),
    ("image", "ai_probability", "true"),
    ("persona", "self_description", "123"),
    ("life", "initial_energy", '"invalid"'),
])
def test_host_type_pruning_keeps_other_legacy_settings(
    tmp_path: Path, section: str, field: str, value: str,
) -> None:
    content = (
        '[plugin]\nconfig_version="3.0.0"\nnapcat_token="saved"\n'
        '[send]\nenable_image=false\n'
        '[models]\ntext_model="utils"\n'
        '[diary_model]\napi_timeout=420\n'
    )
    header = f"[{section}]\n"
    if header in content:
        content = content.replace(header, header + f"{field}={value}\n")
    else:
        content += header + f"{field}={value}\n"
    config_file = tmp_path / "config.toml"
    config_file.write_text(content, encoding="utf-8")
    source = tomllib.loads(content)
    plugin = LocalPlugin(tmp_path)
    defaults = plugin.get_default_config()
    rebuilt = _host_rebuild_config(defaults, source)
    assert rebuilt[section][field] == defaults[section][field]
    normalized, changed = plugin.normalize_plugin_config(rebuilt)
    assert changed
    assert normalized["plugin"]["http_token"] == "saved"
    assert normalized["image"]["enable_image"] is False
    assert normalized["llm"]["text_model"] == "utils"
    # 已填写的新字段优先；类型无效时沿用宿主重建后的默认值。
    assert "diary" not in normalized
    assert normalized[section][field] == defaults[section][field]
    assert config_file.read_text(encoding="utf-8") == content
    assert plugin.normalize_plugin_config(normalized) == (normalized, False)


def test_pending_disk_upgrade_does_not_override_webui_edits(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[plugin]\nconfig_version="3.1.0"\n[send]\nenable_image=false\n', encoding="utf-8",
    )
    plugin = LocalPlugin(tmp_path)
    edited = rebuild_plugin_config_data(plugin.get_default_config(), {
        "plugin": {"config_version": "3.1.0"}, "send": {"enable_image": False},
    })
    edited["image"]["enable_image"] = True
    normalized, _ = plugin.normalize_plugin_config(edited)
    assert normalized["image"]["enable_image"] is True


def test_copy_old_layouts_preserves_sources_existing_targets_and_marker(tmp_path: Path) -> None:
    plugin_dir, destination = tmp_path / "plugin", tmp_path / "sdk-data"
    files = {
        "data/processed_list.json": b'{"from": ["data"]}',
        "processed_list.json": b'{"from": ["root"]}',
        "processed_comments.json": b'{"comment": ["id"]}',
        "data/diaries/2026-10-21_120000.json": '{"diary_content":"旧日记"}'.encode("utf-8"),
        "data/diary_index.json": b'{"entries": []}',
        "qzone/cookies-123.json": b'{"p_skey": "saved"}',
        "qzone/qrcode.png": b"qrcode",
        "images/old.png": b"saved-image",
        "data/cookie_stats.json": b'{"old": {"success": 3}}',
    }
    for relative, content in files.items():
        path = plugin_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    destination.mkdir()
    (destination / "cookie_stats.json").write_bytes(b'{"current": {}}')
    copied = data_migration.migrate_plugin_data(plugin_dir, destination)
    assert copied == 7
    assert (destination / "processed_list.json").read_bytes() == files["data/processed_list.json"]
    assert (destination / "processed_comments.json").read_bytes() == files["processed_comments.json"]
    assert (destination / "diaries/2026-10-21_120000.json").read_bytes() == files["data/diaries/2026-10-21_120000.json"]
    assert (destination / "cookies-123.json").read_bytes() == files["qzone/cookies-123.json"]
    assert (destination / "qrcode.png").read_bytes() == b"qrcode"
    assert (destination / "images/old.png").read_bytes() == b"saved-image"
    assert (destination / "cookie_stats.json").read_bytes() == b'{"current": {}}'
    assert all((plugin_dir / relative).read_bytes() == content for relative, content in files.items())
    assert json.loads((destination / data_migration.MIGRATION_MARKER).read_text(encoding="utf-8"))["version"] == 6
    (plugin_dir / "data/late.json").write_bytes(b"late")
    assert data_migration.migrate_plugin_data(plugin_dir, destination) == 0
    assert not (destination / "late.json").exists()


def test_sqlite_backup_includes_committed_wal_without_copying_sidecars(tmp_path: Path) -> None:
    plugin_dir, destination = tmp_path / "plugin", tmp_path / "sdk-data"
    source = plugin_dir / "data/schedule/goals.db"
    source.parent.mkdir(parents=True)
    old = sqlite3.connect(str(source))
    try:
        old.execute("PRAGMA journal_mode=WAL")
        old.execute("PRAGMA wal_autocheckpoint=0")
        old.execute("CREATE TABLE saved(value TEXT)")
        old.execute("INSERT INTO saved VALUES ('committed-in-wal')")
        old.commit()
        assert source.with_name("goals.db-wal").stat().st_size > 0
        assert data_migration.migrate_plugin_data(plugin_dir, destination) == 1
        new = sqlite3.connect(str(destination / "schedule/goals.db"))
        try:
            assert new.execute("SELECT value FROM saved").fetchall() == [("committed-in-wal",)]
            assert new.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        finally:
            new.close()
        assert old.execute("SELECT value FROM saved").fetchall() == [("committed-in-wal",)]
        assert not (destination / "schedule/goals.db-wal").exists()
        assert not (destination / "schedule/goals.db-shm").exists()
    finally:
        old.close()


def test_failed_copy_rolls_back_only_new_files_and_can_retry(tmp_path: Path) -> None:
    plugin_dir, destination = tmp_path / "plugin", tmp_path / "sdk-data"
    source_dir = plugin_dir / "data"
    source_dir.mkdir(parents=True)
    (source_dir / "a.json").write_bytes(b"preserved")
    (source_dir / "z.db").write_bytes(b"broken-database")
    destination.mkdir()
    (destination / "existing.json").write_bytes(b"current")
    with pytest.raises(RuntimeError, match="数据迁移失败"):
        data_migration.migrate_plugin_data(plugin_dir, destination)
    assert not (destination / "a.json").exists()
    assert not (destination / "z.db").exists()
    assert not (destination / data_migration.MIGRATION_MARKER).exists()
    assert (destination / "existing.json").read_bytes() == b"current"
    assert (source_dir / "a.json").read_bytes() == b"preserved"
    assert (source_dir / "z.db").read_bytes() == b"broken-database"
    (source_dir / "z.db").unlink()
    connection = sqlite3.connect(str(source_dir / "z.db"))
    connection.execute("CREATE TABLE restored(id INTEGER)")
    connection.close()
    assert data_migration.migrate_plugin_data(plugin_dir, destination) == 2


def test_marker_write_failure_rolls_back_and_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir, destination = tmp_path / "plugin", tmp_path / "sdk-data"
    (plugin_dir / "data").mkdir(parents=True)
    (plugin_dir / "data/saved.json").write_bytes(b"saved")
    write = data_migration.write_json
    monkeypatch.setattr(data_migration, "write_json", Mock(side_effect=OSError("marker failed")))
    with pytest.raises(RuntimeError, match="marker failed"):
        data_migration.migrate_plugin_data(plugin_dir, destination)
    assert list(destination.iterdir()) == []
    monkeypatch.setattr(data_migration, "write_json", write)
    assert data_migration.migrate_plugin_data(plugin_dir, destination) == 1


def test_existing_sdk_database_is_preserved(tmp_path: Path) -> None:
    plugin_dir, destination = tmp_path / "plugin", tmp_path / "sdk-data"
    (plugin_dir / "data").mkdir(parents=True)
    destination.mkdir()
    (plugin_dir / "data/goals.db").write_bytes(b"obsolete-broken-db")
    (destination / "goals.db").write_bytes(b"current-db")
    assert data_migration.migrate_plugin_data(plugin_dir, destination) == 0
    assert (destination / "goals.db").read_bytes() == b"current-db"


def test_cancelled_start_waits_for_migration_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def copying(plugin_dir: Path, destination: Path) -> int:
        entered.set()
        assert release.wait(timeout=5)
        finished.set()
        return 0

    monkeypatch.setattr(data_migration, "migrate_plugin_data", copying)

    async def scenario() -> None:
        store = PluginPersistence(tmp_path)
        task = asyncio.create_task(store.migrate_legacy_data(tmp_path))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(scenario())


def test_global_config_preserves_payload_and_reports_host_failure() -> None:
    from maibot_sdk.context import PluginContext

    async def rpc(method: str, plugin_id: str, payload: Dict[str, Any]) -> Any:
        if payload["args"]["key"] == "failed":
            return {"success": False, "error": "host unavailable"}
        return {"success": True, "value": {"data": "payload", "value": "nested"}}

    async def scenario() -> None:
        ctx = PluginContext("Rabbit-Jia-Er.MaiTrace", rpc_call=rpc)
        assert await get_global(ctx, "example") == {"data": "payload", "value": "nested"}
        with pytest.raises(RuntimeError, match="host unavailable"):
            await get_global(ctx, "failed")

    asyncio.run(scenario())


def test_disabled_v6_config_archives_original_without_on_load(tmp_path: Path) -> None:
    content = (
        '[plugin]\r\nconfig_version="6.0.0"\r\nadmin_qq=["111"]\r\nenabled=false\r\n'
        '[life]\r\nprivate_proactive_enabled=true\r\n'
        '[[life.profiles]]\r\nuser_id="111"\r\nrole="owner"\r\n'
        'proactive_enabled=true\r\ndaily_proactive_max=2\r\ninitial_temperature=72.5\r\n'
        '[[life.important_dates]]\r\nuser_id="111"\r\nname="生日"\r\n'
        'date="2027-01-02"\r\nlead_days=[1,0]\r\n'
        '[messaging]\r\nenabled=true\r\nrecall_enabled=false\r\n'
        'rest_gate_enabled=true\r\nprivate_debounce=true\r\ntext_wait_seconds=8\r\n'
        '[routine]\r\npost_cooldown_minutes=240\r\nscheduled_posts_enabled=true\r\n'
        '[diary]\r\nenabled=true\r\n'
        '[creation]\r\nenabled=true\r\n'
        '[information]\r\nenabled=true\r\nprivate_key="legacy-secret"\r\n'
        '[social]\r\nenabled=true\r\n'
    ).encode("utf-8")
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(content)
    plugin = LocalPlugin(tmp_path)
    plugin._set_context(PluginContext(
        "Rabbit-Jia-Er.MaiTrace", paths=PluginPaths(data_dir=tmp_path / "sdk-data", runtime_dir=tmp_path / "runtime"),
    ))
    defaults = plugin.get_default_config()
    pending = plugin._legacy_config_archive
    source = tomllib.loads(content.decode("utf-8"))
    rebuilt = _host_rebuild_config(defaults, source)
    normalized, changed = plugin.normalize_plugin_config(rebuilt)
    assert changed and normalized["plugin"]["config_version"] == plugin.build_default_config()["plugin"]["config_version"]
    assert {"diary", "creation", "information", "social"}.isdisjoint(normalized)
    profile = normalized["life"]["profiles"][0]
    assert profile["user_id"] == "111" and profile["role"] == "owner"
    assert profile["proactive_enabled"] and profile["daily_proactive_max"] == 2
    assert "initial_temperature" not in profile
    assert normalized["life"]["important_dates"][0]["name"] == "生日"
    assert normalized["messaging"]["rest_gate_enabled"] is True
    assert normalized["messaging"]["recall_enabled"] is False
    assert {"private_debounce", "text_wait_seconds"}.isdisjoint(normalized["messaging"])
    assert normalized["routine"]["post_cooldown_minutes"] == 240
    assert not any(key.startswith("scheduled_") for key in normalized["routine"])
    assert "legacy-secret" not in repr(normalized)
    assert plugin.normalize_plugin_config(normalized) == (normalized, False)
    assert config_file.read_bytes() == content
    # Host 再取默认配置时不能丢掉仍未归档的原始快照。
    plugin.get_default_config()
    # 当前宿主先写新配置，再检查 enabled 并丢弃实例；禁用时从不调用 on_load。
    host = _host_config_writer()
    host._save_plugin_config(str(tmp_path), normalized)
    plugin.set_plugin_config(normalized)
    assert not host._is_plugin_enabled(normalized)
    del plugin
    assert pending is not None
    pending.result(timeout=5)
    archive = tmp_path / "sdk-data/legacy" / f"config-{hashlib.sha256(content).hexdigest()[:16]}.toml"
    assert archive.read_bytes() == content
    asyncio.run(PluginPersistence(tmp_path / "sdk-data").archive_legacy_config(content))
    assert list(archive.parent.glob("config-*.toml")) == [archive]
    written = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert written["plugin"]["config_version"] == defaults["plugin"]["config_version"]
    assert not written["plugin"]["enabled"]
    assert {"diary", "creation", "information", "social"}.isdisjoint(written)


def test_archive_submission_returns_before_disk_io_and_survives_instance_disposal(tmp_path: Path) -> None:
    content = b'[plugin]\nconfig_version="6.0.0"\nenabled=false\n[information]\nenabled=true\n'
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(content)
    started, release = threading.Event(), threading.Event()
    original = persistence._archive_legacy_config

    def delayed(data_dir, snapshot):
        started.set()
        assert release.wait(5)
        original(data_dir, snapshot)

    try:
        with patch("MaiTrace.services.persistence._archive_legacy_config", side_effect=delayed):
            plugin = LocalPlugin(tmp_path)
            plugin._set_context(PluginContext("Rabbit-Jia-Er.MaiTrace", paths=PluginPaths(
                data_dir=tmp_path / "sdk-data", runtime_dir=tmp_path / "runtime",
            )))
            defaults = plugin.get_default_config()
            pending = plugin._legacy_config_archive
            assert started.wait(1)
            assert pending is not None and not pending.done()
            normalized, _ = plugin.normalize_plugin_config(_host_rebuild_config(defaults, tomllib.loads(content.decode())))
            _host_config_writer()._save_plugin_config(str(tmp_path), normalized)
            del plugin
            release.set()
            pending.result(timeout=5)
    finally:
        release.set()
    archive = tmp_path / "sdk-data/legacy" / f"config-{hashlib.sha256(content).hexdigest()[:16]}.toml"
    assert archive.read_bytes() == content


def test_cold_config_archive_moves_to_sdk_even_with_existing_migration_marker(tmp_path: Path) -> None:
    content = b'[plugin]\nconfig_version="6.0.0"\n[creation]\nenabled=true\n'
    config_file = tmp_path / "config.toml"
    config_file.write_bytes(content)
    cold = LocalPlugin(tmp_path)
    defaults = cold.get_default_config()
    assert cold._legacy_config_archive is not None
    cold._legacy_config_archive.result(timeout=5)
    normalized, _ = cold.normalize_plugin_config(_host_rebuild_config(defaults, tomllib.loads(content.decode())))
    _host_config_writer()._save_plugin_config(str(tmp_path), normalized)
    filename = f"config-{hashlib.sha256(content).hexdigest()[:16]}.toml"
    staged = tmp_path / "data/legacy" / filename
    assert staged.read_bytes() == content
    sdk_data = tmp_path / "sdk-data"
    sdk_data.mkdir()
    (sdk_data / data_migration.MIGRATION_MARKER).write_text('{"version": 6}', encoding="utf-8")
    active = LocalPlugin(tmp_path)
    active._set_context(PluginContext("Rabbit-Jia-Er.MaiTrace", paths=PluginPaths(
        data_dir=sdk_data, runtime_dir=tmp_path / "runtime",
    )))
    active.get_default_config()
    assert active._legacy_config_archive is None
    asyncio.run(active.persistence.migrate_legacy_data(tmp_path))
    assert (sdk_data / "legacy" / filename).read_bytes() == content
    assert staged.read_bytes() == content
    asyncio.run(active.persistence.migrate_legacy_data(tmp_path))
    assert list((sdk_data / "legacy").glob("config-*.toml")) == [sdk_data / "legacy" / filename]


def test_populated_v6_life_database_preserves_retired_records_and_blocks_sharing(tmp_path: Path) -> None:
    plugin = MaiTracePlugin()
    config = plugin.build_default_config()
    config["schedule"].update(enabled=False, auto_schedule_enabled=False)
    config["environment"]["calendar_enabled"] = False
    config["life"].update(scene_enabled=False, dream_enabled=False, diary_enabled=False)
    config["life"]["profiles"] = [{"user_id": "111", "role": "owner", "proactive_enabled": True}]
    plugin.set_plugin_config(config)
    plugin._set_context(PluginContext("Rabbit-Jia-Er.MaiTrace", paths=PluginPaths(
        data_dir=tmp_path, runtime_dir=tmp_path / "runtime",
    )))
    now = 1_795_000_000.0
    tables = ("documents", "document_revisions", "jobs", "search_history", "opportunities", "claims", "private_pending", "dates", "memories")

    def records(connection: sqlite3.Connection) -> Dict[str, Any]:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}

    async def scenario() -> None:
        store = LifeStore(tmp_path)
        await store.initialize()
        await store.sync_users([LifeUserProfile(user_id="111", role="owner")])
        await store.close()
        connection = sqlite3.connect(str(tmp_path / "life.sqlite3"))
        try:
            with connection:
                connection.execute(
                    "UPDATE users SET temperature=72.5,last_relation_day='2026-10-20',"
                    "stream_id='private-111',display_name='旧用户',proactive_count=2,proactive_day='2026-10-20' WHERE user_id='111'"
                )
                connection.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                    ("old-document", "legacy-story", "旧作品", "public", "archived", '{"body":"完整旧正文"}', now, now))
                connection.execute("INSERT INTO document_revisions(document_id,revision,payload,created_at) VALUES(?,?,?,?)",
                    ("old-document", 1, '{"body":"初稿"}', now))
                connection.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)",
                    ("old-job", "creation", "2026-10-20", "archived", now, '{"outline":"提纲"}'))
                connection.execute("INSERT INTO search_history VALUES(?,?,?,?,?,?,?)",
                    ("old-search", "news", "新闻关键词", "old-provider", 1, '{"url":"https://example.org/old"}', now))
                for index, (source, key) in enumerate([
                    ("creation", "old-writing"), ("manual_creation", "old-manual"),
                    ("external_reading", "old-reading"), ("news", "old-news"),
                    ("exploration", "old-explore"), ("social", "old-group"),
                    ("external", "scheduled:2026-10-20:08:00"),
                ]):
                    payload = json.dumps({"classification": "normal", "instruction": "旧素材"}, ensure_ascii=False)
                    connection.execute(
                        "INSERT INTO opportunities(id,source,source_key,topic,motive,privacy,weight,payload,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (f"old-source-{index}", source, key, "旧话题", "旧动机", "public", 1.0, payload, now, now + 1_000_000_000),
                    )
                connection.execute("UPDATE opportunities SET status='claimed',claim_token='old-token',consumer='private:111',lease_until=? WHERE id='old-source-0'",
                                   (now + 1_000_000_000,))
                connection.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?)",
                    ("old-token", "old-source-0", "private:111", now, now + 1_000_000_000, "active", "", ""))
                connection.execute("INSERT INTO private_pending VALUES(?,?,?,?,?,?)",
                    ("old-token", "111", "private-111", "proactive:Rabbit-Jia-Er.MaiTrace:old", now + 1_000_000_000, "pending"))
                connection.execute("INSERT INTO dates VALUES(?,?,?,?,?,?,?)",
                    ("old-date", "111", "手动纪念日", "2027-01-02", 1, "[1,0]", 0))
                connection.execute("INSERT INTO memories VALUES(?,?,?,?,?,?,?)",
                    ("old-diary", "diary", "2026-10-20", "private", "", '{"title":"旧日记","content":"一天的记录"}', now))
            before = records(connection)
            user_before = connection.execute(
                "SELECT temperature,last_relation_day,stream_id,display_name,proactive_count,proactive_day FROM users WHERE user_id='111'"
            ).fetchone()
        finally:
            connection.close()

        engine = LifeEngine(plugin, data_dir=tmp_path)
        try:
            await engine.start()
            user = (await engine.store.users())[0]
            assert {"temperature", "initial_temperature", "last_relation_day"}.isdisjoint(user)
            assert user["stream_id"] == "private-111" and user["proactive_count"] == 2
            assert engine.private_task("private-111", task_id="proactive:Rabbit-Jia-Er.MaiTrace:old", claim_token="old-token") is None
            assert engine.cancelled_private_task("private-111", "proactive:Rabbit-Jia-Er.MaiTrace:old")
            assert await engine.store.claim_public("qzone", now + 1, 30) is None
            assert await engine.store.claim_private(user, now + 1, 30) is None
            assert (await engine.store.memories("diary"))[0]["payload"]["content"] == "一天的记录"
            assert (await engine.store.dates("111"))[0]["name"] == "手动纪念日"
            await engine.store.cleanup(now + 2, 1)
        finally:
            await engine.stop()
        # 再次初始化不得丢失历史表或改变关系数字。
        await engine.start()
        await engine.stop()
        connection = sqlite3.connect(str(tmp_path / "life.sqlite3"))
        try:
            assert records(connection) == before
            assert connection.execute(
                "SELECT temperature,last_relation_day,stream_id,display_name,proactive_count,proactive_day FROM users WHERE user_id='111'"
            ).fetchone() == user_before
        finally:
            connection.close()

    asyncio.run(scenario())


def test_retired_material_survives_cleanup_but_current_material_expires(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = LifeStore(tmp_path)
        await store.initialize()
        try:
            for source, key in (("creation", "old"), ("external", "scheduled:old"), ("scene", "active")):
                await store.add_opportunity(source=source, source_key=key, topic="小事", motive="", privacy="public",
                    expires_at=2, now=1)
            await store.cleanup(2 + 366 * 86400, 1)
            items = await store.list_opportunities()
            assert {(item["source"], item["source_key"]) for item in items} == {("creation", "old"), ("external", "scheduled:old")}
        finally:
            await store.close()
    asyncio.run(scenario())


def test_v6_schedule_retains_legacy_goals_and_unbound_commitments(tmp_path: Path) -> None:
    plugin = MaiTracePlugin()
    values = plugin.build_default_config()
    values["schedule"].update(auto_schedule_enabled=False, auto_generate=False)
    plugin.set_plugin_config(values)

    async def scenario() -> None:
        engine = ScheduleEngine(plugin, data_dir=tmp_path / "schedule")
        await engine.start()
        try:
            manager = engine.goal_manager
            legacy = await engine.run_db(manager.create_goal, "旧通用目标", "历史内容", "custom", "111", "global")
            await engine.run_db(manager.complete_goal, legacy.goal_id)
            commitment = await engine.run_db(manager.create_goal, "旧约定", "旧记录", "pending_commitment", "111", "global",
                parameters={"commitment_date": "2027-01-02", "time": "12:00"})
            def make_old() -> None:
                with manager.db._transaction():
                    manager.db.update_goal(legacy.goal_id, created_at="2020-01-01T12:00:00+08:00")
            await engine.run_db(make_old)
            before = await engine.run_db(lambda: manager.db._get_connection().execute(
                "SELECT * FROM goals ORDER BY goal_id"
            ).fetchall())
            await engine.run_db(manager.cleanup_old_goals, 1)
            assert (await engine.run_db(manager.get_goal, commitment.goal_id)).status == GoalStatus.ACTIVE
        finally:
            await engine.stop()
        await engine.start()
        try:
            manager = engine.goal_manager
            after = await engine.run_db(lambda: manager.db._get_connection().execute(
                "SELECT * FROM goals ORDER BY goal_id"
            ).fetchall())
            assert [tuple(row) for row in after] == [tuple(row) for row in before]
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_migrated_chat_diaries_still_read_through_commands(tmp_path: Path) -> None:
    source = tmp_path / "plugin/data/diaries/2026-10-20_120000.json"
    source.parent.mkdir(parents=True)
    content = json.dumps({
        "date": "2026-10-20", "generation_time": 123, "diary_content": "保留的旧聊天日记", "word_count": 9,
    }, ensure_ascii=False).encode("utf-8")
    source.write_bytes(content)
    destination = tmp_path / "sdk-data"
    data_migration.migrate_plugin_data(tmp_path / "plugin", destination)
    plugin = LocalPlugin(tmp_path / "plugin")
    values = plugin.build_default_config()
    values["plugin"]["admin_qq"] = ["111"]
    plugin.set_plugin_config(values)
    sent = []
    async def rpc(method: str, plugin_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        assert payload["capability"] == "send.text"
        sent.append(payload["args"]["text"])
        return {"success": True}
    plugin._set_context(PluginContext(
        "Rabbit-Jia-Er.MaiTrace", rpc_call=rpc, paths=PluginPaths(data_dir=destination, runtime_dir=tmp_path / "runtime"),
    ))
    async def scenario() -> None:
        assert (await DiaryStorage(destination).get_diary("2026-10-20"))["diary_content"] == "保留的旧聊天日记"
        for command in ("ls", "v 2026-10-20"):
            result = await dispatch_zn(plugin, matched_groups={"sub": command}, stream_id="private-111", user_id="111")
            assert result[0]
        assert any("保留的旧聊天日记" in text for text in sent)
    asyncio.run(scenario())
    assert source.read_bytes() == content
