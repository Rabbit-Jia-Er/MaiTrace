"""活动状态分析器模块

分析当前活动的进度状态，并生成符合人设的情感化描述。
根据活动类型和进度，动态生成自然的状态文本。
"""

from enum import Enum
from typing import Optional, Tuple
import logging
import random


logger = logging.getLogger(__name__)


class ActivityState(Enum):
    """活动状态枚举"""
    JUST_STARTED = "just_started"        # 刚开始（<10%进度）
    IN_PROGRESS = "in_progress"          # 进行中（10%-80%进度）
    ALMOST_DONE = "almost_done"          # 即将结束（>80%进度）
    UNKNOWN = "unknown"                  # 未知状态


class ActivityStateAnalyzer:
    """活动状态分析器

    负责：
    1. 分析活动的当前进度状态
    2. 根据活动类型和状态生成情感化描述
    3. 提供多样化的表达方式

    Attributes:
        emotion_templates: 按活动类型和状态分类的情感描述模板库
    """

    def __init__(self):
        """初始化状态分析器，构建情感描述模板库"""

        # 情感描述模板库
        # 结构：{活动类型: {状态: [描述列表]}}
        self.emotion_templates = {
            # 学习类活动
            "study": {
                ActivityState.JUST_STARTED: [
                    "刚开始学，还挺有精神",
                    "刚打开书，准备认真学",
                    "刚开始，状态还不错",
                ],
                ActivityState.IN_PROGRESS: [
                    "学了一会儿了，还算专注",
                    "学得还挺认真",
                    "有点累但还能坚持",
                    "效率还行",
                    "状态还算不错",
                ],
                ActivityState.ALMOST_DONE: [
                    "快学完了，有点累",
                    "马上就学完了，坚持一下",
                    "学了好久了，脑子有点晕",
                ],
            },

            # 娱乐类活动
            "entertainment": {
                ActivityState.JUST_STARTED: [
                    "刚开始看，还挺有意思",
                    "刚打开，看看怎么样",
                    "刚开始玩，挺新鲜的",
                ],
                ActivityState.IN_PROGRESS: [
                    "看得正起劲呢",
                    "玩得挺开心",
                    "挺有意思的",
                    "放松一下~",
                    "心情不错",
                ],
                ActivityState.ALMOST_DONE: [
                    "快看完了",
                    "马上就结束了",
                    "差不多该停了",
                ],
            },

            # 吃饭类活动
            "meal": {
                ActivityState.JUST_STARTED: [
                    "刚坐下准备吃",
                    "刚开始吃，饿死了",
                    "才开始吃呢",
                ],
                ActivityState.IN_PROGRESS: [
                    "吃得挺香",
                    "味道还不错",
                    "慢慢品味着",
                    "吃得还算满意",
                    "边吃边聊~",
                ],
                ActivityState.ALMOST_DONE: [
                    "快吃完了",
                    "差不多吃饱了",
                    "马上吃完",
                ],
            },

            # 日常作息
            "daily_routine": {
                ActivityState.JUST_STARTED: [
                    "刚开始",
                    "刚开始弄",
                    "才刚开始呢",
                ],
                ActivityState.IN_PROGRESS: [
                    "做着日常的事",
                    "按部就班地做着",
                    "例行操作中",
                    "习惯了，挺自然的",
                ],
                ActivityState.ALMOST_DONE: [
                    "快弄完了",
                    "马上就好",
                ],
            },

            # 运动健身
            "exercise": {
                ActivityState.JUST_STARTED: [
                    "刚开始运动，还有力气",
                    "才热身完，准备运动",
                    "刚开始，状态不错",
                ],
                ActivityState.IN_PROGRESS: [
                    "有点累了",
                    "出汗了，感觉不错",
                    "坚持着，还能撑",
                    "累但挺爽的",
                    "出了点汗",
                ],
                ActivityState.ALMOST_DONE: [
                    "快练完了，好累",
                    "马上结束，累死了",
                    "差不多该休息了",
                ],
            },

            # 社交维护
            "social_maintenance": {
                ActivityState.JUST_STARTED: [
                    "刚开始聊",
                    "才打招呼呢",
                ],
                ActivityState.IN_PROGRESS: [
                    "聊得挺开心",
                    "气氛还不错",
                    "聊得还挺投机",
                    "话题挺有意思",
                ],
                ActivityState.ALMOST_DONE: [
                    "快聊完了",
                    "差不多该说再见了",
                ],
            },

            # 学习兴趣话题
            "learn_topic": {
                ActivityState.JUST_STARTED: [
                    "刚开始研究，挺感兴趣",
                    "才开始了解",
                ],
                ActivityState.IN_PROGRESS: [
                    "还挺有意思",
                    "了解了不少新东西",
                    "学到了挺多",
                    "挺长知识的",
                ],
                ActivityState.ALMOST_DONE: [
                    "快研究完了",
                    "差不多了解清楚了",
                ],
            },

            # 自定义/其他
            "custom": {
                ActivityState.JUST_STARTED: [
                    "刚开始呢",
                    "才起步",
                    "刚着手",
                    "刚启动",
                ],
                ActivityState.IN_PROGRESS: [
                    "进行得还挺顺利",
                    "做了有一会儿了",
                    "节奏还不错",
                    "状态还行",
                    "挺专注的",
                ],
                ActivityState.ALMOST_DONE: [
                    "快完成了",
                    "马上就好",
                    "差不多要结束了",
                    "收尾阶段",
                ],
            },
        }

        logger.debug("活动状态分析器初始化完成")

    def analyze_activity_state(
        self,
        activity_name: str,
        start_minutes: int,
        end_minutes: int,
        current_minutes: int,
        activity_type: str = "custom"
    ) -> Tuple[ActivityState, Optional[str]]:
        """分析活动当前状态并生成描述

        Args:
            activity_name: 活动名称
            start_minutes: 开始时间（一天中的分钟数，0-1439）
            end_minutes: 结束时间（一天中的分钟数，0-1439）
            current_minutes: 当前时间（一天中的分钟数，0-1439）
            activity_type: 活动类型（study/meal/entertainment等）

        Returns:
            (状态, 状态描述文本)

        Examples:
            >>> analyzer = ActivityStateAnalyzer()
            >>> analyzer.analyze_activity_state("学习", 540, 660, 550, "study")
            (ActivityState.JUST_STARTED, "刚开始学，还挺有精神")

            >>> analyzer.analyze_activity_state("学习", 540, 660, 630, "study")
            (ActivityState.ALMOST_DONE, "快学完了，有点累")
        """
        # 计算活动总时长和已进行时长
        total_duration = end_minutes - start_minutes
        if total_duration <= 0:
            logger.warning(f"活动时长异常: start={start_minutes}, end={end_minutes}")
            return ActivityState.UNKNOWN, None

        elapsed = current_minutes - start_minutes
        progress = elapsed / total_duration  # 进度百分比 (0-1)

        # 判断状态
        if progress < 0.1:
            state = ActivityState.JUST_STARTED
        elif progress > 0.8:
            state = ActivityState.ALMOST_DONE
        else:
            state = ActivityState.IN_PROGRESS

        # 生成情感描述
        emotion_text = self.generate_emotion_text(activity_type, state)

        logger.debug(
            f"状态分析: {activity_name} 进度{progress:.0%} "
            f"({elapsed}/{total_duration}分钟) → {state.value}"
        )

        return state, emotion_text

    def generate_emotion_text(
        self,
        activity_type: str,
        state: ActivityState
    ) -> str:
        """根据活动类型和状态生成情感描述

        Args:
            activity_type: 活动类型（study/meal/entertainment等）
            state: 活动状态

        Returns:
            情感描述文本，随机从模板库中选择

        Examples:
            >>> analyzer = ActivityStateAnalyzer()
            >>> analyzer.generate_emotion_text("study", ActivityState.IN_PROGRESS)
            "学了一会儿了，还算专注"  # 随机选择的一种
        """
        # 获取对应活动类型的模板，不存在则使用custom
        templates = self.emotion_templates.get(activity_type)
        if not templates:
            logger.debug(f"未知活动类型 {activity_type}，使用默认模板")
            templates = self.emotion_templates.get("custom", {})

        # 获取对应状态的描述列表
        descriptions = templates.get(state, [])
        if not descriptions:
            logger.debug(f"未找到状态 {state} 的描述模板")
            return ""

        # 随机选择一个描述（增加多样性）
        selected = random.choice(descriptions)
        return selected

    def get_progress_description(
        self,
        start_minutes: int,
        end_minutes: int,
        current_minutes: int
    ) -> str:
        """获取活动进度的文字描述

        Args:
            start_minutes: 开始时间（分钟）
            end_minutes: 结束时间（分钟）
            current_minutes: 当前时间（分钟）

        Returns:
            进度描述文字

        Examples:
            >>> analyzer = ActivityStateAnalyzer()
            >>> analyzer.get_progress_description(540, 660, 600)
            "已进行1小时，还剩1小时"
        """
        elapsed = current_minutes - start_minutes
        remaining = end_minutes - current_minutes

        if elapsed <= 0:
            return "刚开始"
        elif remaining <= 0:
            return "即将结束"

        # 转换为小时和分钟
        elapsed_hours = elapsed // 60
        elapsed_mins = elapsed % 60
        remaining_hours = remaining // 60
        remaining_mins = remaining % 60

        # 构建描述
        parts = []

        if elapsed_hours > 0:
            parts.append(f"已进行{elapsed_hours}小时")
            if elapsed_mins > 0:
                parts[-1] += f"{elapsed_mins}分钟"
        elif elapsed_mins > 0:
            parts.append(f"已进行{elapsed_mins}分钟")

        if remaining_hours > 0:
            parts.append(f"还剩{remaining_hours}小时")
            if remaining_mins > 0:
                parts[-1] += f"{remaining_mins}分钟"
        elif remaining_mins > 0:
            parts.append(f"还剩{remaining_mins}分钟")

        return "，".join(parts)
