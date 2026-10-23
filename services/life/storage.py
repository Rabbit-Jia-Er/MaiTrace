"""实例拥有的 SQLite 素材枢纽；全部数据库操作在同一工作线程执行。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar
import asyncio
import json
import sqlite3
import time
import uuid

from .models import LifeState, clean_text, material_payload, stable_id


T = TypeVar("T")
SCHEMA_VERSION = 1
# 退役模块只留历史；旧定时任务在 v6 使用 external 来源和 scheduled: 键。
_ACTIVE_MATERIAL_SQL = (
    "source NOT IN ('creation','manual_creation','external_reading','news','exploration','social','scheduled','scheduled_post') "
    "AND source_key NOT LIKE 'scheduled:%'"
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class LifeStore:
    """不读取或迁移其他插件数据库，认领和发送确认均保存在本插件新库。"""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "life.sqlite3"
        self._executor: Optional[ThreadPoolExecutor] = None
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("生活数据库尚未启动")
        return self._connection

    async def _run(self, operation: Callable[[], T]) -> T:
        async with self._lock:
            if self._executor is None:
                raise RuntimeError("生活数据库已经关闭")
            future = asyncio.get_running_loop().run_in_executor(self._executor, operation)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # 取消调用者不能中断 SQLite 事务；完成后才允许 close 或下一个操作进入。
                await asyncio.shield(future)
                raise

    async def initialize(self, state: Optional[LifeState] = None) -> None:
        if self._executor is not None:
            return
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="maitrace-life")
        try:
            await self._run(lambda: self._initialize_sync(state or LifeState()))
        except BaseException:
            await self.close()
            raise

    def _initialize_sync(self, state: LifeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5)
        self._connection = connection
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, SCHEMA_VERSION):
            raise RuntimeError(f"生活数据库版本不兼容：{version}，当前支持 {SCHEMA_VERSION}")
        # 沿用 v6 的表和归档字段，移除功能不删除作品、搜索或关系数值历史。
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS values_cache(key TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS applied_segments(key TEXT PRIMARY KEY, applied_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS scenes(key TEXT PRIMARY KEY, day TEXT NOT NULL, description TEXT NOT NULL,
                scene TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS opportunities(
                id TEXT PRIMARY KEY, source TEXT NOT NULL, source_key TEXT NOT NULL,
                topic TEXT NOT NULL, motive TEXT NOT NULL, privacy TEXT NOT NULL,
                target_user_id TEXT NOT NULL DEFAULT '', weight REAL NOT NULL,
                payload TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'available', claim_token TEXT NOT NULL DEFAULT '',
                consumer TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
                confirmed_at REAL NOT NULL DEFAULT 0, UNIQUE(source, source_key));
            CREATE INDEX IF NOT EXISTS idx_opportunity_ready ON opportunities(status,privacy,expires_at);
            CREATE TABLE IF NOT EXISTS claims(token TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL,
                consumer TEXT NOT NULL, claimed_at REAL NOT NULL, lease_until REAL NOT NULL,
                status TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS users(user_id TEXT PRIMARY KEY, profile TEXT NOT NULL,
                temperature REAL NOT NULL, display_name TEXT NOT NULL DEFAULT '', stream_id TEXT NOT NULL DEFAULT '',
                last_message_at REAL NOT NULL DEFAULT 0, last_proactive_at REAL NOT NULL DEFAULT 0,
                proactive_day TEXT NOT NULL DEFAULT '', proactive_count INTEGER NOT NULL DEFAULT 0,
                last_relation_day TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS interactions(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                message_id TEXT NOT NULL, created_at REAL NOT NULL, kind TEXT NOT NULL,
                UNIQUE(user_id,message_id,kind));
            CREATE TABLE IF NOT EXISTS private_pending(claim_token TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                stream_id TEXT NOT NULL, host_task_id TEXT NOT NULL DEFAULT '', expires_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending');
            CREATE INDEX IF NOT EXISTS idx_private_task ON private_pending(stream_id,host_task_id,status);
            CREATE TABLE IF NOT EXISTS dates(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                event_date TEXT NOT NULL, yearly INTEGER NOT NULL, lead_days TEXT NOT NULL, configured INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, kind TEXT NOT NULL, day TEXT NOT NULL,
                privacy TEXT NOT NULL, user_id TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, privacy TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS document_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(document_id,revision));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, day TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_history(id TEXT PRIMARY KEY, operation TEXT NOT NULL,
                query TEXT NOT NULL, provider TEXT NOT NULL, success INTEGER NOT NULL,
                payload TEXT NOT NULL, created_at REAL NOT NULL);
        """)
        with connection:
            connection.execute("INSERT OR IGNORE INTO state(id,payload) VALUES(1,?)", (_json(state.to_dict()),))
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    async def close(self) -> None:
        async with self._close_lock:
            # 在同一锁内关闭连接并撤下执行器，避免新操作插入 close 的两个阶段之间。
            async with self._lock:
                executor = self._executor
                if executor is None:
                    return
                self._executor = None
                def close_connection() -> None:
                    if self._connection is not None:
                        self._connection.close()
                        self._connection = None
                future = asyncio.get_running_loop().run_in_executor(executor, close_connection)
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    await asyncio.shield(future)
                    raise
                finally:
                    # 线程已无数据库任务，仍在线程池之外等待退出，不阻塞事件循环。
                    await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=False)

    async def get_state(self) -> LifeState:
        return await self._run(lambda: LifeState.from_dict(json.loads(
            self.connection.execute("SELECT payload FROM state WHERE id=1").fetchone()[0])))

    async def save_state(self, state: LifeState) -> None:
        def save() -> None:
            with self.connection:
                self.connection.execute("UPDATE state SET payload=? WHERE id=1", (_json(state.to_dict()),))
        await self._run(save)

    async def commit_state(self, state: LifeState, applied: List[str], *,
                           wakes: Optional[List[Dict[str, Any]]] = None, day: str = "") -> None:
        """状态与场景完成标记在同一事务提交，重启不会重复吃饭或结算精力。"""
        def commit() -> None:
            with self.connection:
                self.connection.execute("UPDATE state SET payload=? WHERE id=1", (_json(state.to_dict()),))
                self.connection.executemany("INSERT OR IGNORE INTO applied_segments(key,applied_at) VALUES(?,?)",
                                            [(key, state.last_updated_at) for key in applied])
                for wake in wakes if wakes is not None else []:
                    self.connection.execute("INSERT OR IGNORE INTO memories VALUES(?,?,?,?,?,?,?)", (
                        stable_id("wake", str(wake["sleep_started_at"])), "wake_event", day, "private", "",
                        _json(wake), state.last_updated_at))
        await self._run(commit)

    async def applied_segments(self, keys: List[str]) -> List[str]:
        if not keys:
            return []
        def read() -> List[str]:
            placeholders = ",".join("?" for _ in keys)
            return [str(row[0]) for row in self.connection.execute(
                f"SELECT key FROM applied_segments WHERE key IN ({placeholders})", keys)]
        return await self._run(read)

    async def get_value(self, key: str) -> Optional[Dict[str, Any]]:
        def read() -> Optional[Dict[str, Any]]:
            row = self.connection.execute("SELECT payload FROM values_cache WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        return await self._run(read)

    async def set_value(self, key: str, value: Dict[str, Any], *, now: Optional[float] = None) -> None:
        def write() -> None:
            with self.connection:
                self.connection.execute("INSERT INTO values_cache VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                                        (key, _json(value), time.time() if now is None else now))
        await self._run(write)

    async def put_scene(self, key: str, day: str, description: str, scene: str, now: float,
                        *, opportunity: Optional[Dict[str, Any]] = None) -> bool:
        prepared = self._prepare_opportunity(**opportunity) if opportunity is not None else None
        def write() -> bool:
            with self.connection:
                inserted = self.connection.execute("INSERT OR IGNORE INTO scenes VALUES(?,?,?,?,?)",
                                                  (key, day, description, scene, now)).rowcount == 1
                if prepared is not None:
                    self._insert_opportunity(prepared)
                return inserted
        return await self._run(write)

    async def get_scene(self, key: str) -> Optional[Dict[str, Any]]:
        def read() -> Optional[Dict[str, Any]]:
            row = self.connection.execute("SELECT * FROM scenes WHERE key=?", (key,)).fetchone()
            return dict(row) if row else None
        return await self._run(read)

    async def scenes_for_day(self, day: str) -> List[Dict[str, Any]]:
        return await self._run(lambda: [dict(row) for row in self.connection.execute(
            "SELECT description,scene FROM scenes WHERE day=? ORDER BY created_at", (day,))])

    @staticmethod
    def _prepare_opportunity(*, source: str, source_key: str, topic: str, motive: str,
                             privacy: str, expires_at: float, now: float, weight: float = 0.5,
                             target_user_id: str = "", payload: Optional[Dict[str, Any]] = None) -> Tuple[Any, ...]:
        if privacy not in {"public", "private", "owner_only", "target_only"}:
            raise ValueError("素材 privacy 必须为 public/private/owner_only/target_only")
        if privacy == "public" and target_user_id:
            raise ValueError("公开素材不能包含私聊定向用户")
        if privacy == "target_only" and not target_user_id:
            raise ValueError("定向素材必须指定用户")
        if not topic.strip() or expires_at <= now or not 0 <= weight <= 1:
            raise ValueError("素材主题、有效期或权重非法")
        opportunity_id = stable_id(source, source_key)
        data = material_payload(source, privacy, payload)
        return (opportunity_id, source, source_key, clean_text(topic, 240), clean_text(motive, 500),
                privacy, target_user_id, weight, _json(data), now, expires_at)

    def _insert_opportunity(self, prepared: Tuple[Any, ...]) -> None:
        self.connection.execute("""INSERT OR IGNORE INTO opportunities
            (id,source,source_key,topic,motive,privacy,target_user_id,weight,payload,created_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", prepared)

    async def add_opportunity(self, *, source: str, source_key: str, topic: str, motive: str,
                              privacy: str, expires_at: float, now: float, weight: float = 0.5,
                              target_user_id: str = "", payload: Optional[Dict[str, Any]] = None) -> str:
        prepared = self._prepare_opportunity(source=source, source_key=source_key, topic=topic, motive=motive,
            privacy=privacy, expires_at=expires_at, now=now, weight=weight, target_user_id=target_user_id, payload=payload)
        def write() -> str:
            with self.connection:
                self._insert_opportunity(prepared)
            return str(prepared[0])
        return await self._run(write)

    @staticmethod
    def _opportunity(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["classification"] = result["payload"]["classification"]
        result["instruction"] = result["payload"]["instruction"]
        return result

    def _claim_sync(self, *, consumer: str, now: float, lease_seconds: int,
                    user: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            # 到期只恢复竞争资格，保留末次 token 以接收迟到的真实回执。
            # 下一次认领会原子换 token，此后旧调用者不能确认、释放或隔离新持有者的素材。
            connection.execute("UPDATE claims SET status='expired' WHERE status='active' AND lease_until<=?", (now,))
            connection.execute("UPDATE opportunities SET status='available',consumer='',lease_until=0 WHERE status='claimed' AND lease_until<=?", (now,))
            # 退役模块的旧素材保留在库中，不能因升级被重新发到空间或私聊。
            source_clause = _ACTIVE_MATERIAL_SQL
            params: List[Any] = [now]
            privacy_clause = "privacy='public' AND target_user_id=''"
            if user is not None:
                user_id = str(user["user_id"])
                owner = user["role"] == "owner"
                privacy_clause = "((privacy='public' AND target_user_id='') OR (target_user_id=? AND privacy='target_only')"
                params.append(user_id)
                if owner:
                    privacy_clause += " OR (privacy IN ('private','owner_only') AND (target_user_id='' OR target_user_id=?))"
                    params.append(user_id)
                privacy_clause += ")"
                pending = connection.execute("SELECT 1 FROM private_pending WHERE user_id=? AND ((status='pending' AND expires_at>?) OR status='uncertain')", (user_id, now)).fetchone()
                if pending:
                    connection.commit()
                    return None
            row = connection.execute(f"SELECT * FROM opportunities WHERE status='available' AND expires_at>? AND {privacy_clause} AND {source_clause} ORDER BY weight DESC,created_at LIMIT 1", params).fetchone()
            if row is None:
                connection.commit()
                return None
            token = uuid.uuid4().hex
            lease_until = now + lease_seconds
            connection.execute("UPDATE opportunities SET status='claimed',claim_token=?,consumer=?,lease_until=? WHERE id=?", (token, consumer, lease_until, row["id"]))
            connection.execute("INSERT INTO claims(token,opportunity_id,consumer,claimed_at,lease_until,status) VALUES(?,?,?,?,?,'active')", (token, row["id"], consumer, now, lease_until))
            if user is not None:
                connection.execute("INSERT INTO private_pending(claim_token,user_id,stream_id,expires_at) VALUES(?,?,?,?)", (token, user["user_id"], user["stream_id"], lease_until))
            connection.commit()
            result = self._opportunity(row)
            result.update(claim_token=token, consumer=consumer, lease_until=lease_until, status="claimed")
            return result
        except BaseException:
            connection.rollback()
            raise

    async def claim_public(self, consumer: str, now: float, lease_seconds: int) -> Optional[Dict[str, Any]]:
        return await self._run(lambda: self._claim_sync(consumer=consumer, now=now, lease_seconds=lease_seconds))

    async def claim_private(self, user: Dict[str, Any], now: float, lease_seconds: int) -> Optional[Dict[str, Any]]:
        return await self._run(lambda: self._claim_sync(consumer=f"private:{user['user_id']}", now=now, lease_seconds=lease_seconds, user=user))

    async def bind_private_task(self, token: str, task_id: str) -> None:
        def write() -> None:
            with self.connection:
                self.connection.execute("UPDATE private_pending SET host_task_id=? WHERE claim_token=? AND status='pending'", (task_id, token))
        await self._run(write)

    async def private_claim_for_task(self, stream_id: str, task_id: str) -> str:
        """按持久化任务和会话查末次认领，供缓存丢失后的迟到回执准确归因。"""
        if not stream_id or not task_id:
            return ""
        def read() -> str:
            # 租期结束不抹掉真实发送事实；只有仍持有同一 token 的任务可完成确认。
            # 已确认的 sent 允许幂等查询，换主、主动释放或隔离的任务均不能重新归因。
            row = self.connection.execute("""SELECT p.claim_token
                FROM private_pending p
                JOIN claims c ON c.token=p.claim_token
                JOIN opportunities o ON o.id=c.opportunity_id AND o.claim_token=p.claim_token
                WHERE p.stream_id=? AND p.host_task_id=?
                  AND c.consumer=('private:' || p.user_id)
                  AND ((p.status IN ('pending','expired') AND c.status IN ('active','expired')
                        AND o.status IN ('claimed','available'))
                       OR (p.status='sent' AND c.status='confirmed' AND o.status='consumed'))
                """, (stream_id, task_id)).fetchone()
            return str(row[0]) if row else ""
        return await self._run(read)

    async def confirm(self, token: str, *, now: float, day: str, receipt: str = "") -> bool:
        """确认真实发送；租期结束但尚未换 token 时仍消费，私聊额度同事务递增一次。"""
        def write() -> bool:
            connection = self.connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                claim = connection.execute("SELECT * FROM claims WHERE token=?", (token,)).fetchone()
                if claim is None:
                    connection.commit()
                    return False
                if claim["status"] == "confirmed":
                    connection.commit()
                    return True
                row = connection.execute("SELECT id FROM opportunities WHERE claim_token=? AND status IN ('claimed','available')", (token,)).fetchone()
                if row is None or claim["status"] not in {"active", "expired"}:
                    connection.commit()
                    return False
                # 回执已经证明平台完成发送。不能因租期结束先释放，再让调用方隔离失效。
                # 认领与确认共用写事务，若其他消费者已经换 token，上方检查会拒绝旧回执。
                connection.execute("UPDATE opportunities SET status='consumed',confirmed_at=? WHERE id=?", (now, row["id"]))
                connection.execute("UPDATE claims SET status='confirmed',receipt=? WHERE token=?", (clean_text(receipt, 500), token))
                private = connection.execute("SELECT user_id FROM private_pending WHERE claim_token=? AND status IN ('pending','expired')", (token,)).fetchone()
                if private:
                    connection.execute("UPDATE users SET proactive_count=CASE WHEN proactive_day=? THEN proactive_count+1 ELSE 1 END,proactive_day=?,last_proactive_at=? WHERE user_id=?", (day, day, now, private[0]))
                    connection.execute("UPDATE private_pending SET status='sent' WHERE claim_token=?", (token,))
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise
        return await self._run(write)

    async def release(self, token: str, *, error: str = "") -> bool:
        def write() -> bool:
            with self.connection:
                changed = self.connection.execute("UPDATE opportunities SET status='available',claim_token='',consumer='',lease_until=0 WHERE claim_token=? AND status IN ('claimed','available')", (token,)).rowcount
                if changed:
                    self.connection.execute("UPDATE claims SET status='released',error=? WHERE token=? AND status IN ('active','expired')", (clean_text(error, 500), token))
                    self.connection.execute("UPDATE private_pending SET status='released' WHERE claim_token=?", (token,))
                return bool(changed)
        return await self._run(write)

    async def quarantine(self, token: str, *, error: str) -> bool:
        """冻结尚未换 token 的未知结果，即使已回收租期也不再自动认领或记作成功。"""
        def write() -> bool:
            with self.connection:
                changed = self.connection.execute("UPDATE opportunities SET status='uncertain' WHERE claim_token=? AND status IN ('claimed','available')", (token,)).rowcount
                if changed:
                    self.connection.execute("UPDATE claims SET status='uncertain',error=? WHERE token=? AND status IN ('active','expired')", (clean_text(error, 500), token))
                    self.connection.execute("UPDATE private_pending SET status='uncertain' WHERE claim_token=?", (token,))
                return bool(changed)
        return await self._run(write)

    async def list_opportunities(self, *, public_only: bool = True, limit: int = 20) -> List[Dict[str, Any]]:
        def read() -> List[Dict[str, Any]]:
            where = "WHERE privacy='public' AND target_user_id=''" if public_only else ""
            return [self._opportunity(row) for row in self.connection.execute(
                f"SELECT * FROM opportunities {where} ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),))]
        return await self._run(read)

    async def sync_users(self, profiles: List[Any]) -> None:
        def write() -> None:
            with self.connection:
                configured = {profile.user_id for profile in profiles if profile.user_id}
                for row in self.connection.execute("SELECT user_id,profile FROM users"):
                    if row["user_id"] not in configured:
                        profile = json.loads(row["profile"])
                        profile["enabled"] = False
                        self.connection.execute("UPDATE users SET profile=? WHERE user_id=?", (_json(profile), row["user_id"]))
                for profile in profiles:
                    if profile.user_id:
                        self.connection.execute("INSERT INTO users(user_id,profile,temperature) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET profile=excluded.profile",
                                                (profile.user_id, _json(profile.model_dump()), 30.0))
        await self._run(write)

    async def users(self) -> List[Dict[str, Any]]:
        def read() -> List[Dict[str, Any]]:
            users = []
            for row in self.connection.execute("SELECT * FROM users"):
                item = dict(row)
                profile = json.loads(item.pop("profile"))
                for field in ("initial_temperature", "temperature", "last_relation_day"):
                    item.pop(field, None)
                    profile.pop(field, None)
                users.append({**profile, **item})
            return users
        return await self._run(read)

    async def set_user_stream(self, user_id: str, stream_id: str, display_name: str) -> None:
        def write() -> None:
            with self.connection:
                self.connection.execute("UPDATE users SET stream_id=?,display_name=? WHERE user_id=?", (stream_id, clean_text(display_name, 120), user_id))
        await self._run(write)

    async def record_interaction(self, user_id: str, message_id: str, now: float, kind: str = "message") -> None:
        def write() -> None:
            with self.connection:
                self.connection.execute("INSERT OR IGNORE INTO interactions(user_id,message_id,created_at,kind) VALUES(?,?,?,?)", (user_id, message_id, now, kind))
                self.connection.execute("UPDATE users SET last_message_at=MAX(last_message_at,?) WHERE user_id=?", (now, user_id))
        await self._run(write)

    async def interaction_counts(self, start: float, end: float) -> Dict[str, int]:
        def read() -> Dict[str, int]:
            row = self.connection.execute("SELECT COUNT(*),COUNT(DISTINCT user_id) FROM interactions WHERE created_at>=? AND created_at<?", (start, end)).fetchone()
            return {"total": int(row[0]), "active_people": int(row[1])}
        return await self._run(read)


    async def sync_dates(self, dates: List[Any]) -> None:
        def write() -> None:
            with self.connection:
                self.connection.execute("DELETE FROM dates WHERE configured=1")
                for item in dates:
                    if not item.user_id or not item.name or not item.date:
                        continue
                    key = stable_id("date", f"{item.user_id}:{item.name}:{item.date}")
                    self.connection.execute("INSERT INTO dates VALUES(?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET name=excluded.name,event_date=excluded.event_date,yearly=excluded.yearly,lead_days=excluded.lead_days",
                                            (key, item.user_id, item.name, item.date, int(item.yearly), _json(item.lead_days)))
        await self._run(write)

    async def dates(self, user_id: str = "") -> List[Dict[str, Any]]:
        def read() -> List[Dict[str, Any]]:
            rows = self.connection.execute("SELECT * FROM dates" + (" WHERE user_id=?" if user_id else ""), (user_id,) if user_id else ())
            return [{**dict(row), "lead_days": json.loads(row["lead_days"])} for row in rows]
        return await self._run(read)

    async def save_memory(self, *, key: str, kind: str, day: str, payload: Dict[str, Any],
                          now: float, privacy: str = "private", user_id: str = "") -> bool:
        def write() -> bool:
            with self.connection:
                return self.connection.execute("INSERT OR IGNORE INTO memories VALUES(?,?,?,?,?,?,?)",
                    (key, kind, day, privacy, user_id, _json(payload), now)).rowcount == 1
        return await self._run(write)

    async def memories(self, kind: str, *, day: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        def read() -> List[Dict[str, Any]]:
            params: List[Any] = [kind]
            where = "kind=?"
            if day:
                where += " AND day=?"
                params.append(day)
            params.append(max(1, min(limit, 100)))
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in self.connection.execute(
                f"SELECT * FROM memories WHERE {where} ORDER BY created_at DESC LIMIT ?", params)]
        return await self._run(read)


    async def add_date(self, *, user_id: str, name: str, event_date: str, yearly: bool = True) -> str:
        key = stable_id("date", f"{user_id}:{name}:{event_date}")
        def write() -> str:
            with self.connection:
                self.connection.execute("INSERT OR IGNORE INTO dates VALUES(?,?,?,?,?,?,0)",
                    (key, user_id, clean_text(name, 100), event_date, int(yearly), _json([7, 1, 0])))
            return key
        return await self._run(write)

    async def remove_date(self, date_id: str, user_id: str) -> bool:
        def write() -> bool:
            with self.connection:
                return self.connection.execute("DELETE FROM dates WHERE id=? AND user_id=? AND configured=0", (date_id, user_id)).rowcount == 1
        return await self._run(write)


    async def private_tasks(self, now: float) -> List[Dict[str, Any]]:
        # 退役来源的未发送任务在内存中恢复为取消标记，原数据库记录继续保留。
        # 已发送任务仍恢复 sent，真实回执可以继续按原 token 幂等结算。
        return await self._run(lambda: [dict(row) for row in self.connection.execute(
            f"SELECT p.claim_token,p.user_id,p.stream_id AS session_id,p.host_task_id AS task_id,p.expires_at,"
            f"CASE WHEN p.status='pending' AND NOT ({_ACTIVE_MATERIAL_SQL}) THEN 'retired' ELSE p.status END AS status,"
            "c.claimed_at AS created_at FROM private_pending p JOIN claims c ON c.token=p.claim_token "
            "JOIN opportunities o ON o.id=c.opportunity_id "
            "WHERE (p.status='pending' AND p.expires_at>?) OR (p.status='sent' AND p.expires_at>?)",
            (now, now - 1800),
        )])

    async def management_counts(self) -> Dict[str, int]:
        def read() -> Dict[str, int]:
            return {name: int(self.connection.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0])
                for name, table, where in (
                    ("users", "users", ""), ("dates", "dates", ""),
                    ("documents", "documents", "WHERE status='archived'"),
                    ("opportunities", "opportunities", "WHERE status='available'"),
                    ("uncertain", "opportunities", "WHERE status='uncertain'"),
                    ("private_pending", "private_pending", "WHERE status='pending'"),
                    ("searches", "search_history", ""),
                )}
        return await self._run(read)


    async def cleanup(self, now: float, retention_days: int) -> None:
        def write() -> None:
            with self.connection:
                self.connection.execute("UPDATE private_pending SET status='expired' WHERE status='pending' AND expires_at<=?", (now,))
                self.connection.execute("DELETE FROM interactions WHERE created_at<?", (now - 366 * 86400,))
                self.connection.execute(
                    f"DELETE FROM opportunities WHERE expires_at<? AND status NOT IN ('claimed','uncertain') AND {_ACTIVE_MATERIAL_SQL}",
                    (now - retention_days * 86400,),
                )
                self.connection.execute(
                    f"DELETE FROM claims WHERE claimed_at<? AND status NOT IN ('active','uncertain') AND opportunity_id NOT IN "
                    f"(SELECT id FROM opportunities WHERE NOT ({_ACTIVE_MATERIAL_SQL}))",
                    (now - retention_days * 86400,),
                )
                self.connection.execute("DELETE FROM applied_segments WHERE applied_at<?", (now - 7 * 86400,))
        await self._run(write)
