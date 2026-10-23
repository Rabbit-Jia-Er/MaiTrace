"""生活功能的可调用命令入口；鉴权、可见性和执行分离于消息发送。"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, TYPE_CHECKING


if TYPE_CHECKING:
    from .engine import LifeEngine


def _result(text: str, data: Any = None, *, success: bool = True) -> Dict[str, Any]:
    return {"success": success, "text": text, "data": data}


class LifeCommands:
    def __init__(self, engine: "LifeEngine") -> None:
        self.engine = engine

    async def _private_actor(self, stream_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """只认 Host 中已经存在、且与当前会话一致的本人私聊。"""
        if not stream_id or not user_id:
            return None
        user = self.engine._users.get(user_id)
        if user is None or not user["enabled"]:
            return None
        # 同一 QQ 在不同账号或路由下可能有多条私聊，按 QQ 取第一条会误拒当前会话。
        # 核验 Host 返回的当前真实 session，不凭本地映射或重新计算 ID 授权。
        streams = await self.engine.plugin.ctx.chat.get_all_streams(platform="qq")
        if not isinstance(streams, list):
            return None
        stream = next((item for item in streams if isinstance(item, dict) and item.get("session_id") == stream_id), None)
        if stream is None:
            return None
        if (stream.get("platform") != "qq" or stream.get("chat_type") != "private"
                or stream.get("group_id") or stream.get("is_group_session")
                or str(stream.get("user_id", "")) != user_id):
            return None
        return dict(user)

    async def execute(self, content: str, *, stream_id: str = "", user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        engine = self.engine
        if not engine.started:
            return _result("生活服务尚未启动", success=False)
        if str(user_id) not in engine.plugin.config.plugin.admin_qq:
            return _result("生活命令仅限已配置的管理员", success=False)
        text = content.strip()
        if text.startswith("/zn "):
            text = text[4:].strip()
        if not text:
            text = str(kwargs.get("action", "status")) + " " + str(kwargs.get("param", ""))
        parts = text.split(None, 1)
        command = parts[0].lower()
        param = parts[1].strip() if len(parts) > 1 else ""
        if command in {"status", "life"} and param not in {"diary", "日记"}:
            state = engine.peek_state()
            message = (f"当前活动：{state.get('current_activity') or '暂无日程'}\n"
                       f"场景：{state.get('scene') or '尚未细化'}\n"
                       f"精力 {float(state.get('energy', 0)):.1f} / 饥饿 {float(state.get('hunger', 0)):.1f}\n"
                       f"心情 {float(state.get('mood_valence', 0)):+.2f} / 睡眠阶段 {state.get('sleep_phase', '未知')}\n"
                       f"身体状态：{state.get('health_note', '未知')}")
            if state["errors"]:
                message += "\n待处理模块：" + "、".join(state["errors"])
            return _result(message, state)
        if command == "config":
            data = {"生活状态": engine.plugin.config.life.enabled,
                    "主动私聊": engine.plugin.config.life.private_proactive_enabled,
                    "生活消息钩子": engine.plugin.config.messaging.enabled,
                    "睡眠闸门": engine.plugin.config.messaging.rest_gate_enabled,
                    "本地历法": engine.plugin.config.environment.calendar_enabled}
            return _result("生活功能配置\n" + "\n".join(f"{key}：{'开启' if value else '关闭'}" for key, value in data.items()), data)
        actor = await self._private_actor(stream_id, user_id)
        if command == "dates":
            if actor is None:
                return _result("重要日期需在本人私聊使用，并启用该 QQ 的生活联系许可", success=False)
            return await self._dates(param, user_id)
        if command == "life" and param in {"diary", "日记"}:
            if actor is None or actor["role"] != "owner":
                return _result("私人生活日记仅在主人私聊可读", success=False)
            items = await engine.store.memories("diary", limit=1)
            if not items:
                return _result("还没有生活日记", [])
            diary = items[0]["payload"]
            return _result(f"{items[0]['day']} · {diary['title']}\n{diary['content']}", diary)
        return _result("未知生活子命令。可用 status / life diary / dates / config。", success=False)

    async def _dates(self, param: str, user_id: str) -> Dict[str, Any]:
        parts = param.split(None, 1)
        action = parts[0].lower() if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""
        store = self.engine.store
        if action in {"add", "添加"}:
            values = rest.split(None, 1)
            if len(values) != 2:
                return _result("用法：/zn dates add YYYY-MM-DD <名称>；添加后默认每年提醒", success=False)
            event_date = date.fromisoformat(values[0]).isoformat()
            date_id = await store.add_date(user_id=user_id, name=values[1], event_date=event_date)
            await self.engine._refresh_users(self.engine.environment.now())
            return _result(f"已保存本人重要日期：{values[1]} {event_date}\n编号：{date_id}", {"id": date_id})
        if action in {"delete", "del", "remove", "删除"}:
            removed = await store.remove_date(rest.strip(), user_id)
            await self.engine._refresh_users(self.engine.environment.now())
            return _result("已删除本人日期" if removed else "未找到可删除的本人日期；配置模板中的日期需通过配置管理修改", success=removed)
        if action in {"confirm", "确认"}:
            date_id = await self.engine.memory.confirm_date_candidate(rest.strip(), user_id)
            await self.engine._refresh_users(self.engine.environment.now())
            return _result(f"已确认日期，编号：{date_id}", {"id": date_id})
        if action not in {"list", "列表"}:
            return _result("日期操作：list / add YYYY-MM-DD 名称 / delete 编号 / confirm 候选编号", success=False)
        dates = await store.dates(user_id)
        candidates: List[Dict[str, Any]] = []
        for item in await store.memories("date_candidate", limit=100):
            if item["user_id"] == user_id and await store.get_value(f"date.confirmed:{item['id']}") is None:
                candidates.append(item)
        lines = ["本人的重要日期"]
        lines.extend(f"{item['id']} · {item['name']} {item['event_date']}" for item in dates)
        if not dates:
            lines.append("暂无已确认日期")
        if candidates:
            lines.append("待本人确认（未加入提醒）")
            lines.extend(f"{item['id']} · {item['payload']['name']} {item['payload']['date']}" for item in candidates)
        return _result("\n".join(lines), {"dates": dates, "candidates": candidates})
