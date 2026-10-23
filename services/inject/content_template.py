"""动态内容模板引擎

根据用户意图、活动状态等信息，动态生成注入到LLM prompt的内容。
提供多样化的表达模板，避免千篇一律的注入文本。
"""

from typing import List, Optional, Tuple
import logging
import random

from .intent_classifier import UserIntent
from .state_analyzer import ActivityState, ActivityStateAnalyzer

logger = logging.getLogger(__name__)


class ContentTemplateEngine:
    """动态内容模板引擎

    负责：
    1. 根据用户意图选择合适的模板类别
    2. 填充动态变量（活动、状态、时间等）
    3. 随机选择变体，增加表达多样性
    4. 构建最终的注入文本

    Attributes:
        state_analyzer: 活动状态分析器
        templates: 按意图分类的模板库
    """

    def __init__(self, state_analyzer: ActivityStateAnalyzer):
        """初始化内容模板引擎

        Args:
            state_analyzer: 活动状态分析器实例
        """
        self.state_analyzer = state_analyzer

        # 模板库：按意图分类
        # 变量占位符：
        #   {activity} - 活动名称
        #   {activity_full} - 活动全称（含描述）
        #   {description} - 活动描述
        #   {future_activities} - 后续活动列表
        self.templates = {
            # 询问当前状态 - 详细回答
            UserIntent.QUERY_CURRENT: [
                "【当前状态】{activity_full}。回答问题时自然提到当前活动即可。",
                "【当前状态】{activity_full}。回复时可以顺便说说现在在做什么。",
                "【当前状态】{activity_full}。自然融入到回复中。",
                "【当前状态】{activity_full}。回答时顺带提一下。",
            ],

            # 询问未来计划 - 展示后续活动
            UserIntent.QUERY_FUTURE: [
                "【当前状态】{activity_full}。【接下来安排】{future_activities}。回复时自然提到后续计划。",
                "【后续计划】{future_activities}。可以在回答中提及接下来的安排。",
                "【今日安排】现在：{activity_full}，之后：{future_activities}。顺便说说计划即可。",
            ],

            # 闲聊寒暄 - 轻量注入（50%概率）
            UserIntent.CASUAL_CHAT: [
                "【提示】{activity_full}。可随口提一下。",
                "【当前】{activity_full}。轻松回复。",
                None,  # 50%概率不注入
                None,  # 增加不注入的概率
            ],

            # 技术问答 - 不注入（返回None）
            UserIntent.TECH_QUESTION: [
                None,  # 技术问答不注入日程
            ],

            # 命令执行 - 不注入（返回None）
            UserIntent.COMMAND_EXECUTION: [
                None,  # 命令执行不注入日程
            ],
        }

        logger.debug("内容模板引擎初始化完成")

    def build_inject_content(
        self,
        intent: UserIntent,
        current_activity: Optional[str] = None,
        current_description: Optional[str] = None,
        activity_state: Optional[ActivityState] = None,
        state_desc: Optional[str] = None,
        next_activities: Optional[List[Tuple[str, str]]] = None,
        **kwargs
    ) -> Optional[str]:
        """构建注入内容

        Args:
            intent: 用户意图
            current_activity: 当前活动名称
            current_description: 当前活动描述
            activity_state: 活动状态（可选，用于增强state_desc）
            state_desc: 状态描述文本（优先使用传入的，否则生成）
            next_activities: 后续活动列表 [(时间, 名称), ...]
            **kwargs: 其他可选参数

        Returns:
            注入文本字符串，如果不应注入则返回None

        Examples:
            >>> engine = ContentTemplateEngine(state_analyzer)
            >>> engine.build_inject_content(
            ...     UserIntent.QUERY_CURRENT,
            ...     current_activity="学习",
            ...     state_desc="学了一会儿了，还算专注"
            ... )
            "【当前状态】\\n这会儿正学习，学了一会儿了，还算专注\\n..."
        """
        # 获取对应意图的模板列表
        template_list = self.templates.get(intent, [])
        if not template_list:
            logger.debug(f"未找到意图 {intent} 的模板")
            return None

        # 随机选择一个模板
        template = random.choice(template_list)

        # 如果模板为None（不注入）
        if template is None:
            logger.debug(f"模板为None，跳过注入 (intent={intent.value})")
            return None

        # 如果没有当前活动，且不是询问未来计划，则不注入
        if not current_activity and intent != UserIntent.QUERY_FUTURE:
            logger.debug("没有当前活动，跳过注入")
            return None

        # 准备变量字典
        variables = {
            "activity": current_activity or "休息",
            "description": current_description or "",
        }

        # 🆕 构建活动全称（包含描述）
        # 如果有描述，格式为：活动名（描述）
        # 如果没描述，只显示活动名
        if current_description:
            activity_with_desc = f"{current_activity}（{current_description}）"
        else:
            activity_with_desc = current_activity or "休息"

        variables["activity_full"] = activity_with_desc

        # 构建后续活动文本
        if next_activities:
            future_text = self._format_future_activities(next_activities)
            variables["future_activities"] = future_text
        else:
            variables["future_activities"] = "暂无后续安排"

        # 填充模板变量
        try:
            inject_content = template.format(**variables)
            logger.debug(f"生成注入内容: intent={intent.value}, len={len(inject_content)}")
            return inject_content
        except KeyError as e:
            logger.warning(f"模板变量缺失: {e}")
            return None

    def _format_future_activities(
        self,
        activities: List[Tuple[str, str]],
        max_count: int = None  # 🆕 默认不限制数量
    ) -> str:
        """格式化未来活动列表

        Args:
            activities: 活动列表 [(时间, 名称), ...]
            max_count: 最多显示的活动数量（None=不限制）

        Returns:
            格式化的活动文本

        Examples:
            >>> engine._format_future_activities([
            ...     ("14:00", "学习"),
            ...     ("16:00", "运动"),
            ...     ("18:00", "晚饭")
            ... ])
            "14:00 学习\\n16:00 运动\\n18:00 晚饭"
        """
        if not activities:
            return "暂无安排"

        # 🆕 如果不限制数量，显示全部
        if max_count is None:
            limited_activities = activities
        else:
            limited_activities = activities[:max_count]

        # 构建文本
        lines = []
        for time_str, activity_name in limited_activities:
            lines.append(f"{time_str} {activity_name}")

        return "\n".join(lines)

    def build_simple_inject(
        self,
        current_activity: str,
        next_activity: Optional[str] = None,
        next_time: Optional[str] = None
    ) -> str:
        """构建简单的注入内容（兼容旧版本）

        这是一个简化版本，用于快速构建注入内容，不依赖意图分类。
        主要用于向后兼容和简单场景。

        Args:
            current_activity: 当前活动名称
            next_activity: 下一个活动名称（可选）
            next_time: 下一个活动时间（可选）

        Returns:
            注入文本字符串

        Examples:
            >>> engine.build_simple_inject("学习", "吃饭", "12:00")
            "【当前状态】\\n这会儿正学习\\n等下12:00要吃饭。\\n..."
        """
        content = f"【当前状态】\n这会儿正{current_activity}\n"

        if next_activity and next_time:
            content += f"等下{next_time}要{next_activity}。\n"

        content += "回复时可以自然提到当前在做什么，不要刻意强调。\n"

        return content

    def get_template_count(self, intent: UserIntent) -> int:
        """获取指定意图的模板数量

        Args:
            intent: 用户意图

        Returns:
            模板数量（不包括None模板）
        """
        templates = self.templates.get(intent, [])
        return sum(1 for t in templates if t is not None)

    def add_custom_template(
        self,
        intent: UserIntent,
        template: str
    ):
        """添加自定义模板

        允许动态扩展模板库。

        Args:
            intent: 用户意图
            template: 模板字符串（支持变量占位符）

        Examples:
            >>> engine.add_custom_template(
            ...     UserIntent.QUERY_CURRENT,
            ...     "【状态】正在{activity}，{state_desc}"
            ... )
        """
        if intent not in self.templates:
            self.templates[intent] = []

        self.templates[intent].append(template)
        logger.debug(f"添加自定义模板: intent={intent.value}")
