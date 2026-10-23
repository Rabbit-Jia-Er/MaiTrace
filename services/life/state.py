"""确定性的生活状态推进；只使用 planning 已保存的真实活动区间。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

from .models import LifeState, activity_kind, clean_text, stable_id


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class LifeStateEngine:
    """纯计算引擎，提交由 LifeStore 统一完成，模型不能改写核心数值。"""

    @staticmethod
    def segment_key(segment: Dict[str, Any]) -> str:
        return stable_id("segment", f"{segment['goal_id']}:{segment['start'].isoformat()}:{segment['end'].isoformat()}")

    @staticmethod
    def _span(state: LifeState, start: float, end: float, kind: str,
              wakes: List[Dict[str, Any]]) -> None:
        if end < start:
            raise ValueError("生活状态区间结束早于开始")
        if kind == "sleep" and start < state.awake_grace_until < end:
            LifeStateEngine._span(state, start, state.awake_grace_until, kind, wakes)
            LifeStateEngine._span(state, state.awake_grace_until, end, kind, wakes)
            return
        sleeping = kind == "sleep" and start >= state.awake_grace_until
        previously_sleeping = state.sleep_phase in {"falling_asleep", "light_sleep", "deep_sleep"}
        hours = (end - start) / 3600.0
        if sleeping:
            if not previously_sleeping:
                state.sleep_started_at = start
            sleep_hours = (end - state.sleep_started_at) / 3600.0
            state.sleep_phase = "deep_sleep" if sleep_hours >= 0.5 else "light_sleep"
            state.energy = _clamp(state.energy + 8 * hours, 0, 100)
            state.hunger = _clamp(state.hunger + hours, 0, 100)
            state.mood_arousal = _clamp(state.mood_arousal - 0.2 * hours, 0, 1)
        else:
            if previously_sleeping and state.sleep_started_at:
                duration = max(0.0, (start - state.sleep_started_at) / 3600.0)
                if duration >= 3:
                    wakes.append({"sleep_started_at": state.sleep_started_at, "woke_at": start,
                                  "hours": duration, "activity": state.current_activity})
            state.sleep_phase = "woken" if start < state.awake_grace_until else "awake"
            load = {"work": 2.1, "study": 1.8, "travel": 1.4, "rest": 0.3, "meal": 0.5}.get(kind, 1.0)
            state.energy = _clamp(state.energy - load * hours, 0, 100)
            state.hunger = _clamp(state.hunger + 5 * hours, 0, 100)
            state.mood_arousal = _clamp(state.mood_arousal + 0.05 * hours, 0, 1)
        mood_delta = (-0.03 if state.energy < 30 else 0.01 if state.energy > 70 else 0.0) * hours
        if state.hunger > 75:
            mood_delta -= 0.04 * hours
        state.mood_valence = _clamp(state.mood_valence + mood_delta, -1, 1)
        state.last_updated_at = end

    def advance(self, state: LifeState, now: datetime, segments: List[Dict[str, Any]],
                current: Dict[str, Any], applied: Set[str], *, offline_hours: int = 72
                ) -> Tuple[LifeState, List[str], List[Dict[str, Any]]]:
        now_ts = now.timestamp()
        if state.last_updated_at > now_ts:
            raise ValueError("系统时间早于生活状态最后更新时间")
        cursor = max(state.last_updated_at or now_ts, now_ts - offline_hours * 3600)
        completions: List[str] = []
        wakes: List[Dict[str, Any]] = []
        cutoff = now_ts - offline_hours * 3600
        if state.sleep_started_at and state.sleep_started_at < cutoff:
            state.sleep_started_at = cutoff
        for segment in sorted(segments, key=lambda item: item["start"]):
            start, end = segment["start"].timestamp(), segment["end"].timestamp()
            if end <= cursor or start >= now_ts:
                continue
            # 日程空白只推进基本代谢，不能把后来生成的活动套到整段离线时间上。
            if start > cursor:
                self._span(state, cursor, min(start, now_ts), "other", wakes)
                cursor = start
            kind = activity_kind(str(segment["goal_type"]), str(segment["description"]))
            state.current_activity = clean_text(segment["description"] or segment["name"], 500)
            self._span(state, cursor, min(end, now_ts), kind, wakes)
            cursor = min(end, now_ts)
            key = self.segment_key(segment)
            if end <= now_ts and key not in applied:
                if kind == "meal":
                    state.hunger = _clamp(state.hunger - 35, 0, 100)
                elif kind == "leisure":
                    state.mood_valence = _clamp(state.mood_valence + 0.03, -1, 1)
                completions.append(key)
        if cursor < now_ts:
            self._span(state, cursor, now_ts, "other", wakes)
        description = clean_text(current.get("description", current.get("name", "")), 500)
        kind = activity_kind(str(current.get("goal_type", "")), description)
        self._span(state, now_ts, now_ts, kind, wakes)
        state.current_activity = description
        state.activity_kind = kind
        parameters = current.get("parameters", {})
        location = current.get("location", parameters.get("location", ""))
        if location:
            state.current_location = clean_text(location, 120)
        state.last_updated_at = now_ts
        if state.energy < 20:
            state.health_status, state.health_note = "tired", "精力很低，需要休息"
        elif state.hunger > 88:
            state.health_status, state.health_note = "mild_discomfort", "有些饿，需要进餐"
        else:
            state.health_status, state.health_note = "normal", "状态正常"
        return state, completions, wakes
