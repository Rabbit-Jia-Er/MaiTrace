"""生活日记、梦境与重要日期：私聊资料始终不进入公开素材。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
import json
import re

from .config import LifeSection
from .models import TextGenerator, clean_text, generate_object, required_text, stable_id
from .storage import LifeStore


class MemoryService:
    def __init__(self, store: LifeStore, config: LifeSection, generator: TextGenerator) -> None:
        self.store = store
        self.config = config
        self.generator = generator
        self._last_attempts: Dict[str, float] = {}

    def _due(self, key: str, now: float, interval: int = 600) -> bool:
        if now - self._last_attempts.get(key, 0) < interval:
            return False
        self._last_attempts[key] = now
        return True

    @staticmethod
    def occurrence(event_date: str, yearly: bool, today: date) -> Optional[date]:
        original = date.fromisoformat(event_date)
        if not yearly:
            return original if original >= today else None
        for year in range(today.year, today.year + 9):
            try:
                candidate = date(year, original.month, original.day)
            except ValueError:
                continue
            if candidate >= today:
                return candidate
        return None

    async def record_wakes(self, wakes: List[Dict[str, Any]], now: datetime) -> None:
        for wake in wakes:
            await self.store.save_memory(key=stable_id("wake", str(wake["sleep_started_at"])),
                kind="wake_event", day=now.date().isoformat(), payload=wake, now=now.timestamp())

    async def dreams(self, now: datetime) -> None:
        if not self.config.dream_enabled:
            return
        for event in reversed(await self.store.memories("wake_event", limit=10)):
            key = stable_id("dream", event["id"])
            if await self.store.get_value(f"memory.done:{key}") is not None or not self._due(key, now.timestamp()):
                continue
            # 模型只写梦境叙事，数值状态已经在单一时间线推进时完成。
            prompt = ("根据以下麦麦自己的睡眠事实写醒后梦境。不得出现用户身份、聊天原话或预言。"
                      "返回 JSON 对象，summary 为40至120字，fragments 为最多3个短片段。\n"
                      + json.dumps(event["payload"], ensure_ascii=False))
            result = await generate_object(self.generator, prompt)
            summary = required_text(result.get("summary"), "summary", 500)
            fragments = result.get("fragments", [])
            if not isinstance(fragments, list) or not all(isinstance(item, str) for item in fragments):
                raise ValueError("梦境模型结果缺少 summary 或 fragments 格式不正确")
            payload = {"summary": summary, "fragments": [clean_text(item, 150) for item in fragments[:3]]}
            await self.store.save_memory(key=key, kind="dream", day=event["day"], payload=payload,
                                         now=now.timestamp(), privacy="private")
            await self.store.add_opportunity(source="dream", source_key=key, topic="醒来后还记得一点梦",
                motive=summary, privacy="private", expires_at=now.timestamp() + 12 * 3600,
                now=now.timestamp(), weight=0.45, payload=payload)
            await self.store.set_value(f"memory.done:{key}", {"done": True})

    async def diary(self, now: datetime) -> None:
        if not self.config.diary_enabled or now.hour < self.config.diary_hour:
            return
        target = now.date() - timedelta(days=1)
        day = target.isoformat()
        if await self.store.memories("diary", day=day, limit=1) or not self._due(f"diary:{day}", now.timestamp()):
            return
        start = datetime.combine(target, datetime.min.time(), tzinfo=now.tzinfo)
        scenes = await self.store.scenes_for_day(day)
        dreams = await self.store.memories("dream", day=day)
        counts = await self.store.interaction_counts(start.timestamp(), (start + timedelta(days=1)).timestamp())
        if not scenes and not dreams and not counts["total"]:
            return
        source = {"day": day, "scenes": scenes, "dreams": [item["payload"] for item in dreams], "anonymous_interactions": counts}
        prompt = ("只用以下自身场景、梦境和匿名互动数量写第一人称抽象生活日记。"
                  "不得猜测聊天内容、人物姓名或群号；未知内容不补写。"
                  "返回 JSON：title、content、mood_summary。日记正文不超过1000字。\n"
                  + json.dumps(source, ensure_ascii=False))
        result = await generate_object(self.generator, prompt)
        title, content = required_text(result.get("title"), "title", 120), required_text(result.get("content"), "content", 1400)
        if not title or not content:
            raise ValueError("日记结果缺少标题或正文")
        key = stable_id("diary", day)
        payload = {"title": title, "content": content, "mood_summary": clean_text(result.get("mood_summary", ""), 200)}
        await self.store.save_memory(key=key, kind="diary", day=day, payload=payload, now=now.timestamp())
        await self.store.add_opportunity(source="diary", source_key=key, topic="整理昨天的私人日记",
            motive=payload["mood_summary"], privacy="owner_only", expires_at=now.timestamp() + 18 * 3600,
            now=now.timestamp(), weight=0.4, payload=payload)

    async def important_dates(self, now: datetime) -> None:
        users = {str(item["user_id"]) for item in await self.store.users() if item["enabled"]}
        for item in await self.store.dates():
            if item["user_id"] not in users:
                continue
            occurrence = self.occurrence(item["event_date"], bool(item["yearly"]), now.date())
            if occurrence is None:
                continue
            lead = (occurrence - now.date()).days
            if lead not in item["lead_days"]:
                continue
            key = f"{item['id']}:{occurrence.isoformat()}:{lead}"
            topic = f"今天是{item['name']}" if lead == 0 else f"距离{item['name']}还有{lead}天"
            tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)
            await self.store.add_opportunity(source="important_date", source_key=key, topic=topic,
                motive="在合适时关心这位网友的重要安排", privacy="target_only", target_user_id=item["user_id"],
                expires_at=tomorrow.timestamp(), now=now.timestamp(), weight=0.9 if lead == 0 else 0.65)


    async def observe_date_candidates(self, user_id: str, message_id: str, text: str, now: datetime) -> None:
        name_match = re.search(r"生日|考试|面试|纪念日|旅行|毕业|入职", text)
        if name_match is None:
            return
        for match in re.finditer(r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?!\d)", text):
            try:
                candidate = date(int(match[1]), int(match[2]), int(match[3]))
            except ValueError:
                continue
            await self.store.save_memory(key=stable_id("date-candidate", f"{user_id}:{message_id}:{candidate}"),
                kind="date_candidate", day=now.date().isoformat(), user_id=user_id,
                payload={"name": name_match[0], "date": candidate.isoformat(), "source_message_id": message_id},
                now=now.timestamp())

    async def confirm_date_candidate(self, candidate_id: str, user_id: str) -> str:
        candidates = await self.store.memories("date_candidate", limit=100)
        match = next((item for item in candidates if item["id"] == candidate_id and item["user_id"] == user_id), None)
        if match is None:
            raise ValueError("日期候选不存在或不属于当前用户")
        result = await self.store.add_date(user_id=user_id, name=match["payload"]["name"],
                                           event_date=match["payload"]["date"])
        await self.store.set_value(f"date.confirmed:{candidate_id}", {"date_id": result})
        return result

    async def context_for_user(self, user: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        diaries = await self.store.memories("diary", limit=1) if user["role"] == "owner" else []
        dates = []
        for item in await self.store.dates(str(user["user_id"])):
            occurrence = self.occurrence(item["event_date"], bool(item["yearly"]), now.date())
            if occurrence and 0 <= (occurrence - now.date()).days <= 45:
                dates.append({"name": item["name"], "date": occurrence.isoformat()})
        return {"diary": diaries[0]["payload"] if diaries else {}, "important_dates": dates}
