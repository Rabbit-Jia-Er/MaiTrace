"""MaiTrace 内置日程的生成、裁判、注入和清理配置。

配置模型由根配置以 schedule / inject / goal_cleanup 三个独立 section 挂载。
只声明模板字段，不读取、搬迁或重写其他插件的实际配置。
"""

from __future__ import annotations

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator, model_validator

from ...utils.config_validation import chat_references, clock_time, model_task, qq_list, timezone_name


class GoalCleanupSection(PluginConfigBase):
    """日程引擎启用时清理到期作息和模型归档；生活素材保留期另在 life 设置。"""

    __ui_label__: ClassVar[str] = '日程后台清理'
    __ui_icon__: ClassVar[str] = "trash-2"
    __ui_order__: ClassVar[int] = 50

    cleanup_interval: int = Field(
        default=3600,
        ge=60,
        description='日程后台清理间隔，单位秒，至少 60，默认 3600；启动会执行一次，清理到期作息、旧生活作息及日程模型日志。',
        json_schema_extra={'label': '清理间隔', 'hint': '日程后台清理间隔，单位秒，至少 60，默认 3600；启动会执行一次，清理到期作息、旧生活作息及日程模型日志。', 'order': 1, 'group': '清理'},
    )
    cleanup_old_goals_days: int = Field(
        default=30,
        ge=1,
        description='已完成/取消的旧生活作息保留天数，至少 1，默认 30；无时段旧目标和约定历史保留，不是删除全部目标。',
        json_schema_extra={'label': '保留天数', 'hint': '已完成/取消的旧生活作息保留天数，至少 1，默认 30；无时段旧目标和约定历史保留，不是删除全部目标。', 'order': 2, 'group': '清理'},
    )


# ============================================================
# [schedule]：日程与约定，字段显示顺序由各自的 order 控制。
# ============================================================


class ScheduleSection(PluginConfigBase):
    """每日作息、临时约定、日程注入和主动行为。日程与生活共用 schedule.timezone；权限、主动名单和素材范围彼此独立。"""

    __ui_label__: ClassVar[str] = '日程与约定'
    __ui_icon__: ClassVar[str] = "calendar"
    __ui_order__: ClassVar[int] = 30

    enabled: bool = Field(
        default=True,
        description='日程总开关，控制所选提供方的读取与日程注入；内置模式还控制生成、约定和日程主动服务。关闭后生活层仍可运行，但 Routine 无法基于日程发帖/浏览。',
        json_schema_extra={'label': '启用日程', 'order': 1, 'hint': '控制所选提供方的读取与注入。内置模式同时控制生成、约定和日程主动服务；关闭后 Routine 无法基于日程发帖/浏览。', 'group': '基础与会话范围'},
    )

    provider: Literal['internal', 'maibot-community.mai-life'] = Field(
        default='internal',
        description='日程提供方：internal 使用 MaiTrace 内置日程；maibot-community.mai-life 读取同一 MaiBot 实例中 Mai_life 的公开 API，生成、修改和维护由 Mai_life 负责。切换后重建日程服务，已有内置日程保留。',
        json_schema_extra={'label': '日程提供方', 'hint': '默认内置。选择 Mai_life 时需安装并启用麦生活，MaiTrace 只读取其日程；生成、修改、删除和清理由麦生活负责。', 'order': 2, 'group': '基础与会话范围'},
    )

    # 注入开关与查询。

    inject_schedule: bool = Field(
        default=True,
        description='允许在 Planner 决策前追加当前日程与生活背景；需要启用日程并在 allowed_streams 范围内，具体注入策略由 inject 控制。',
        json_schema_extra={'label': '注入到 planner', 'hint': '允许在 Planner 决策前追加当前日程与生活背景；需要启用日程并在 allowed_streams 范围内，具体注入策略由 inject 控制。', 'order': 5, 'group': '注入与查询'},
    )
    inject_into_replyer: bool = Field(
        default=True,
        description='允许在 Replyer 请求前追加日程与生活背景；与 Planner 开关独立，承接同轮背景并保留重试内容，不重复计入闲聊冷却。',
        json_schema_extra={'label': '注入到 replyer', 'hint': '允许在 Replyer 请求前追加日程与生活背景；与 Planner 开关独立，承接同轮背景并保留重试内容，不重复计入闲聊冷却。', 'order': 6, 'group': '注入与查询'},
    )
    auto_generate: bool = Field(
        default=True,
        description='仅内置模式：日程查询 Tool 查询今天时，若没有日程则自动补生成；独立于 auto_schedule_enabled，不在普通 Hook 中生成。Mai_life 模式由麦生活负责生成。',
        json_schema_extra={'label': '查询时补生成', 'hint': '仅内置模式：查询今天且无日程时补生成。Mai_life 模式由麦生活负责生成。', 'order': 8, 'group': '注入与查询'},
    )
    max_future_activities: int = Field(
        default=3,
        ge=0,
        description='注入背景中最多列出的未来活动条数，至少 0；0 不列接下来活动，不限制日程查询工具返回的完整日程。',
        json_schema_extra={'label': '未来活动条数', 'hint': '注入背景中最多列出的未来活动条数，至少 0；0 不列接下来活动，不限制日程查询工具返回的完整日程。', 'order': 7, 'group': '注入与查询'},
    )

    # 生成内容与模型参数。

    custom_prompt: str = Field(
        default="",
        description='日程生成的额外要求，自由文本，如今天想多运动；留空使用默认要求。不是可格式化的占位符模板。',
        json_schema_extra={'label': '自定义生成 prompt', 'hint': '日程生成的额外要求，自由文本，如今天想多运动；留空使用默认要求。不是可格式化的占位符模板。', 'rows': 6, 'placeholder': '（留空使用默认风格）', 'order': 9, 'group': '生成内容', 'input_type': 'textarea'},
    )
    use_multi_round: bool = Field(
        default=True,
        description='生成质量不足时按反馈再次请求模型；false 只生成一轮，忽略 max_rounds 和停止阈值。修改后重载插件以同步组件预算。',
        json_schema_extra={'label': '多轮生成', 'hint': '生成质量不足时按反馈再次请求模型；false 只生成一轮，忽略 max_rounds 和停止阈值。修改后重载插件以同步组件预算。', 'order': 20, 'group': '日程模型与多轮'},
    )
    max_rounds: int = Field(
        default=2,
        ge=1,
        le=5,
        description='多轮生成最多轮数，范围 1–5，默认 2；包含首轮，不是额外重试数。修改后重载插件以同步组件预算。',
        json_schema_extra={'label': '最大轮数', 'hint': '多轮生成最多轮数，范围 1–5，默认 2；包含首轮，不是额外重试数。修改后重载插件以同步组件预算。', 'order': 21, 'group': '日程模型与多轮', 'depends_on': 'schedule.use_multi_round', 'depends_value': True},
    )
    quality_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description='多轮生成的提前停止分数，范围 0–1，默认 0.85；达到即停止，达不到时最多执行 max_rounds。最终结果仍需通过实际结构和冲突检查。',
        json_schema_extra={'label': '质量阈值', 'hint': '多轮生成的提前停止分数，范围 0–1，默认 0.85；达到即停止，达不到时最多执行 max_rounds。最终结果仍需通过实际结构和冲突检查。', 'order': 22, 'group': '日程模型与多轮', 'depends_on': 'schedule.use_multi_round', 'depends_value': True},
    )
    min_activities: int = Field(
        default=8,
        ge=1,
        description='生成单日活动数量的下限，至少 1，默认 8；用于模型要求、结构与质量评价，必须不大于 max_activities。',
        json_schema_extra={'label': '最少活动数', 'hint': '生成单日活动数量的下限，至少 1，默认 8；用于模型要求、结构与质量评价，必须不大于 max_activities。', 'order': 10, 'group': '生成内容'},
    )
    max_activities: int = Field(
        default=15,
        ge=1,
        description='生成单日活动数量的上限，至少 1，默认 15；不是预留空档数量，必须不小于 min_activities。',
        json_schema_extra={'label': '最多活动数', 'hint': '生成单日活动数量的上限，至少 1，默认 15；不是预留空档数量，必须不小于 min_activities。', 'order': 11, 'group': '生成内容'},
    )
    enable_detailed_description: bool = Field(
        default=True,
        description='生成时要求活动长描述，注入和日程命令展示时显示描述；关闭后按活动名称呈现，已有数据库描述仍保留。',
        json_schema_extra={'label': '详细描述', 'hint': '生成时要求活动长描述，注入和日程命令展示时显示描述；关闭后按活动名称呈现，已有数据库描述仍保留。', 'order': 12, 'group': '生成内容'},
    )
    min_description_length: int = Field(
        default=20,
        ge=5,
        description='启用详细描述时的最少字符数，至少 5，默认 20；用于模型要求、结构和质量评价，必须不大于 max_description_length。',
        json_schema_extra={'label': '描述最少字数', 'hint': '启用详细描述时的最少字符数，至少 5，默认 20；用于模型要求、结构和质量评价，必须不大于 max_description_length。', 'order': 13, 'group': '生成内容', 'depends_on': 'schedule.enable_detailed_description', 'depends_value': True},
    )
    max_description_length: int = Field(
        default=50,
        ge=5,
        description='启用详细描述时的最多字符数，至少 5，默认 50；必须不小于 min_description_length。',
        json_schema_extra={'label': '描述最多字数', 'hint': '启用详细描述时的最多字符数，至少 5，默认 50；必须不小于 min_description_length。', 'order': 14, 'group': '生成内容', 'depends_on': 'schedule.enable_detailed_description', 'depends_value': True},
    )
    max_tokens: int = Field(
        default=8192,
        ge=1000,
        description='单轮日程生成的最大输出 token 数，至少 1000，默认 8192；不是输入预算，也不控制空间/生活输出。',
        json_schema_extra={'label': '单轮输出 token 上限', 'hint': '单轮日程生成的最大输出 token 数，至少 1000，默认 8192；不是输入预算，也不控制空间/生活输出。', 'order': 18, 'group': '日程模型与多轮'},
    )
    generation_timeout: float = Field(
        default=180.0,
        ge=10.0,
        description='日程生成、约定裁判及次日推断的单次模型超时，单位秒，至少 10，默认 180；普通日程主动话题额外限制最多 60 秒。修改后重载插件以同步组件预算。',
        json_schema_extra={'label': '单次日程模型超时（秒）', 'hint': '生成/裁判/推断使用此超时；普通主动话题最多 60 秒；修改后需重载', 'order': 19, 'group': '日程模型与多轮'},
    )
    recent_schedule_days: int = Field(
        default=3,
        ge=1,
        le=14,
        description='生成时回看最近日程的天数，范围 1–14，默认 3；用于场景连续和主题去重，不控制次日策略推断回看天数。',
        json_schema_extra={'label': '回看天数', 'hint': '生成时回看最近日程的天数，范围 1–14，默认 3；用于场景连续和主题去重，不控制次日策略推断回看天数。', 'order': 15, 'group': '生成内容'},
    )

    # 定时生成与次日策略推断，开关独立。

    auto_schedule_enabled: bool = Field(
        default=True,
        description='每天到 auto_schedule_time 后生成当天缺少的日程；已有当日日程则跳过，不发聊天消息。与次日策略推断、查询补生成的开关独立。',
        json_schema_extra={'label': '定时自动生成', 'hint': '每天到 auto_schedule_time 后生成当天缺少的日程；已有当日日程则跳过，不发聊天消息。与次日策略推断、查询补生成的开关独立。', 'order': 25, 'group': '定时生成'},
    )
    auto_schedule_time: str = Field(
        default="00:30",
        description='当天自动生成的时间，HH:MM，按 schedule.timezone；启动时若已过点会检查当天是否已生成。',
        json_schema_extra={'label': '生成时间', 'hint': '当天自动生成的时间，HH:MM，按 schedule.timezone；启动时若已过点会检查当天是否已生成。', 'placeholder': '00:30', 'order': 26, 'group': '定时生成', 'depends_on': 'schedule.auto_schedule_enabled', 'depends_value': True},
    )
    auto_infer_next_day_prompt: bool = Field(
        default=False,
        description='启用前夜根据近期活动推断次日额外要求；独立于定时生成开关，只保存策略，不创建次日日程。策略由次日定时生成使用。',
        json_schema_extra={'label': '次日策略推断', 'hint': '启用前夜根据近期活动推断次日额外要求；独立于定时生成开关，只保存策略，不创建次日日程。策略由次日定时生成使用。', 'order': 27, 'group': '次日策略推断'},
    )
    infer_time: str = Field(
        default="22:30",
        description='次日策略推断的执行时间，HH:MM，按 schedule.timezone；仅 auto_infer_next_day_prompt=true 时生效。',
        json_schema_extra={'label': '推断时间', 'hint': '次日策略推断的执行时间，HH:MM，按 schedule.timezone；仅 auto_infer_next_day_prompt=true 时生效。', 'placeholder': '22:30', 'order': 28, 'group': '次日策略推断', 'depends_on': 'schedule.auto_infer_next_day_prompt', 'depends_value': True},
    )
    infer_lookback_days: int = Field(
        default=3,
        ge=1,
        le=7,
        description='次日策略推断参考历史的天数，范围 1–7，默认 3；独立于 recent_schedule_days。',
        json_schema_extra={'label': '回看天数', 'hint': '次日策略推断参考历史的天数，范围 1–7，默认 3；独立于 recent_schedule_days。', 'order': 29, 'group': '次日策略推断', 'depends_on': 'schedule.auto_infer_next_day_prompt', 'depends_value': True},
    )
    infer_max_prompt_chars: int = Field(
        default=300,
        ge=50,
        description='次日策略推断结果最多字符数，至少 50，默认 300；不控制日程活动描述长度。',
        json_schema_extra={'label': '推断 prompt 字数', 'hint': '次日策略推断结果最多字符数，至少 50，默认 300；不控制日程活动描述长度。', 'order': 30, 'group': '次日策略推断', 'depends_on': 'schedule.auto_infer_next_day_prompt', 'depends_value': True},
    )
    infer_use_completion_signal: bool = Field(
        default=True,
        description='次日策略推断是否参考活动状态和进度；仅启用推断时生效。',
        json_schema_extra={'label': '参考完成度', 'hint': '次日策略推断是否参考活动状态和进度；仅启用推断时生效。', 'order': 31, 'group': '次日策略推断', 'depends_on': 'schedule.auto_infer_next_day_prompt', 'depends_value': True},
    )

    # 生成参考与跨夜活动。

    history_message_limit: int = Field(
        default=30,
        ge=0,
        description='日程生成的当天聊天背景总条数上限，按 allowed_streams 允许的 QQ 会话分摊查询；0 禁用。空名单允许所有已知 QQ 会话，与 send 聊天素材开关独立。',
        json_schema_extra={'label': '历史消息条数', 'hint': '按允许会话分摊查询，最后截取总条数；0 关闭日程聊天参考', 'order': 23, 'group': '生成参考'},
    )
    knowledge_search_limit: int = Field(
        default=5,
        ge=0,
        description='日程生成的可选知识库检索条数，至少 0；0 禁用。检索不可用时继续生成，不影响普通聊天记忆服务。',
        json_schema_extra={'label': '知识库条数', 'hint': '日程生成的可选知识库检索条数，至少 0；0 禁用。检索不可用时继续生成，不影响普通聊天记忆服务。', 'order': 24, 'group': '生成参考'},
    )
    cross_day_activity: bool = Field(
        default=True,
        description='允许跨夜活动写法，如 23:00-01:30，并在凌晨查询昨日延续的活动；关闭后生成和快照不按该跨夜策略处理。',
        json_schema_extra={'label': '跨天活动', 'hint': '允许跨夜活动写法，如 23:00-01:30，并在凌晨查询昨日延续的活动；关闭后生成和快照不按该跨夜策略处理。', 'order': 16, 'group': '生成内容'},
    )

    # 日程模型与调用归档。

    llm_task_name: str = Field(
        default="replyer",
        description='日程生成、约定角色裁判、次日策略推断和普通日程主动话题使用的主程序模型任务名，如 replyer；与 llm.text_model 独立。',
        json_schema_extra={'label': 'LLM 任务名', 'hint': '日程生成、约定角色裁判、次日策略推断和普通日程主动话题使用的主程序模型任务名，如 replyer；与 llm.text_model 独立。', 'placeholder': 'replyer', 'order': 17, 'group': '日程模型与多轮'},
    )
    llm_log_enabled: bool = Field(
        default=True,
        description='归档日程生成及角色裁判的提示词和响应到 SDK 数据目录 schedule/llm_logs/；与 llm.show_prompt 独立。',
        json_schema_extra={'label': 'LLM 调用归档', 'hint': '归档日程生成及角色裁判的提示词和响应到 SDK 数据目录 schedule/llm_logs/；与 llm.show_prompt 独立。', 'order': 43, 'group': '快照与归档'},
    )
    llm_log_retention_days: int = Field(
        default=7,
        ge=1,
        description='日程模型归档保留天数，至少 1，默认 7；后台清理按 goal_cleanup.cleanup_interval 执行，即使关闭新归档也清理旧日志。',
        json_schema_extra={'label': '日志保留天数', 'hint': '日程模型归档保留天数，至少 1，默认 7；后台清理按 goal_cleanup.cleanup_interval 执行，即使关闭新归档也清理旧日志。', 'order': 44, 'group': '快照与归档'},
    )

    # 约定角色裁判。

    role_judge_enabled: bool = Field(
        default=True,
        description='更新约定时让模型按人设判断接受或拒绝；关闭后按明确日期登记，仍校验日期、时长、冲突，当日只插空档，未来日期不限制天数。',
        json_schema_extra={'label': '角色裁判模式', 'hint': '更新约定时让模型按人设判断接受或拒绝；关闭后按明确日期登记，仍校验日期、时长、冲突，当日只插空档，未来日期不限制天数。', 'order': 32, 'group': '约定裁判'},
    )
    role_judge_temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description='角色裁判的模型采样温度，范围 0–2，默认 0.3；低值使输出较稳定，不保证更容易拒绝。仅开启角色裁判时生效。',
        json_schema_extra={'label': '裁判温度', 'hint': '角色裁判的模型采样温度，范围 0–2，默认 0.3；低值使输出较稳定，不保证更容易拒绝。仅开启角色裁判时生效。', 'order': 33, 'group': '约定裁判', 'depends_on': 'schedule.role_judge_enabled', 'depends_value': True},
    )

    # 快照与注入优化历史的有效期。

    cache_ttl: int = Field(
        default=300,
        ge=10,
        description='当前日程快照及注入优化历史的有效期，单位秒，至少 10，默认 300；后台可提前强制刷新，Hook 只读内存，不表示状态冻结 5 分钟。',
        json_schema_extra={'label': '快照/优化历史有效期（秒）', 'hint': '当前日程快照及注入优化历史的有效期，单位秒，至少 10，默认 300；后台可提前强制刷新，Hook 只读内存，不表示状态冻结 5 分钟。', 'order': 42, 'group': '快照与归档'},
    )
    # 日程命令与被动会话范围。

    admin_users: List[str] = Field(
        default_factory=list,
        description='/zn plan 命令的额外管理员限制；为空只检查 plugin.admin_qq，非空时调用者必须同时属于两份名单。不控制日程 Tool、生活命令或空间权限。',
        json_schema_extra={'label': '日程命令额外管理员限制', 'hint': '/zn plan 命令的额外管理员限制；为空只检查 plugin.admin_qq，非空时调用者必须同时属于两份名单。不控制日程 Tool、生活命令或空间权限。', 'item_type': 'string', 'placeholder': '["123456"]', 'order': 4, 'group': '基础与会话范围'},
    )
    allowed_streams: List[str] = Field(
        default_factory=list,
        description='日程注入、命令、会话 Tool 及生成聊天历史的会话白名单；空列表或 all 允许全部。支持 平台:group:目标ID、平台:private:目标ID、session:真实ID 与裸会话ID；QQ 示例为 qq:group:123，不授权主动消息。',
        json_schema_extra={'label': '日程会话白名单', 'hint': '空列表/all 允许全部；qq:group:123、qq:private:456、session:真实ID；保留其他平台与裸会话ID', 'item_type': 'string', 'placeholder': '[] 或 ["qq:group:123456"]', 'order': 3, 'group': '基础与会话范围'},
    )

    # 日程主动行为，名单为空时全部禁用。

    proactive_streams: List[str] = Field(
        default_factory=list,
        description='约定提醒、普通日程主动任务及频率调节的会话名单；空列表完全禁用这三项。推荐 qq:group:群号、qq:private:QQ号、session:真实ID；保留其他平台与旧裸会话ID写法，必须对应已有聊天流，不支持 all。',
        json_schema_extra={'label': '日程主动行为会话', 'hint': '空列表禁用；填写真实会话，不支持 all；推荐 qq:group:123 或 session:真实ID', 'item_type': 'string', 'placeholder': '[] 或 ["qq:group:123456"]', 'order': 34, 'group': '日程主动行为'},
    )
    enable_proactive_trigger: bool = Field(
        default=True,
        description='对已确认且含目标会话的约定，在开始后的前 5 分钟尝试提交提醒；目标必须属于 proactive_streams，同一约定每天每会话最多一次。不受普通主动冷却、额度或静默时段限制。',
        json_schema_extra={'label': '约定到点提醒', 'hint': '对已确认且含目标会话的约定，在开始后的前 5 分钟尝试提交提醒；目标必须属于 proactive_streams，同一约定每天每会话最多一次。不受普通主动冷却、额度或静默时段限制。', 'order': 35, 'group': '日程主动行为'},
    )
    activity_proactive_enabled: bool = Field(
        default=True,
        description='合适的当前活动通过日程模型提出自然话题并提交 MaiBot Planner；需 proactive_streams，工作/学习/睡眠等场景跳过。Planner 可以不发送。',
        json_schema_extra={'label': '日程主动聊天', 'order': 37, 'hint': '合适的当前活动通过日程模型提出自然话题并提交 MaiBot Planner；需 proactive_streams，工作/学习/睡眠等场景跳过。Planner 可以不发送。', 'group': '日程主动行为'},
    )
    activity_proactive_cooldown_minutes: int = Field(
        default=180,
        ge=10,
        le=10080,
        description='每会话两次普通日程主动任务的最短提交间隔，单位分钟，范围 10–10080；不限制约定提醒或 life 主动私聊。',
        json_schema_extra={'label': '主动聊天间隔', 'order': 38, 'hint': '每会话两次普通日程主动任务的最短提交间隔，单位分钟，范围 10–10080；不限制约定提醒或 life 主动私聊。', 'group': '日程主动行为', 'depends_on': 'schedule.activity_proactive_enabled', 'depends_value': True},
    )
    activity_proactive_daily_max: int = Field(
        default=2,
        ge=0,
        le=20,
        description='每个主动名单会话每天最多提交的普通日程任务数，范围 0–20；0 禁用。按任务提交而非消息发送计数，同一活动最多提交一次。',
        json_schema_extra={'label': '每日任务提交上限', 'order': 39, 'hint': '每个主动名单会话每天最多提交的普通日程任务数，范围 0–20；0 禁用。按任务提交而非消息发送计数，同一活动最多提交一次。', 'group': '日程主动行为', 'depends_on': 'schedule.activity_proactive_enabled', 'depends_value': True},
    )
    activity_proactive_quiet_start: str = Field(
        default="23:00",
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description='普通日程主动任务的免打扰开始时间，HH:MM，按 schedule.timezone；与结束相同表示不设静默。独立于 monitor.silent_hours。',
        json_schema_extra={'label': '主动聊天静默开始', 'order': 40, 'hint': '普通日程主动任务的免打扰开始时间，HH:MM，按 schedule.timezone；与结束相同表示不设静默。独立于 monitor.silent_hours。', 'group': '日程主动行为', 'depends_on': 'schedule.activity_proactive_enabled', 'depends_value': True},
    )
    activity_proactive_quiet_end: str = Field(
        default="08:00",
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description='普通日程主动任务的免打扰结束时间，HH:MM，支持跨夜；不限制约定提醒或频率调节。',
        json_schema_extra={'label': '主动聊天静默结束', 'order': 41, 'hint': '普通日程主动任务的免打扰结束时间，HH:MM，支持跨夜；不限制约定提醒或频率调节。', 'group': '日程主动行为', 'depends_on': 'schedule.activity_proactive_enabled', 'depends_value': True},
    )
    enable_frequency_modulation: bool = Field(
        default=True,
        description='向 proactive_streams 会话按活动施加聊天频率因子：睡眠约 0.05，工作/学习约 0.3，休息/娱乐提高；关闭或卸载时恢复本服务的因子。',
        json_schema_extra={'label': '活动频率调控', 'hint': '向 proactive_streams 会话按活动施加聊天频率因子：睡眠约 0.05，工作/学习约 0.3，休息/娱乐提高；关闭或卸载时恢复本服务的因子。', 'order': 36, 'group': '日程主动行为'},
    )

    # 全局共用时区。

    timezone: str = Field(
        default="Asia/Shanghai",
        description='所有生活/日程时间、空间提示词、静默窗口及每日额度使用的 IANA 时区，如 Asia/Shanghai、UTC；保存时校验。',
        json_schema_extra={'label': '时区', 'hint': '所有生活/日程时间、空间提示词、静默窗口及每日额度使用的 IANA 时区，如 Asia/Shanghai、UTC；保存时校验。', 'placeholder': 'Asia/Shanghai', 'order': 2, 'group': '基础与会话范围'},
    )


# ============================================================
# [inject]：已有日程背景的注入策略。
# ============================================================


    @field_validator("auto_schedule_time", "infer_time", "activity_proactive_quiet_start", "activity_proactive_quiet_end")
    @classmethod
    def validate_times(cls, value: str) -> str:
        return clock_time(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return timezone_name(value)

    @field_validator("admin_users")
    @classmethod
    def validate_admins(cls, values: List[str]) -> List[str]:
        return qq_list(values)

    @field_validator("allowed_streams", "proactive_streams")
    @classmethod
    def validate_streams(cls, values: List[str], info) -> List[str]:
        return chat_references(values, proactive=info.field_name == "proactive_streams")

    @field_validator("llm_task_name")
    @classmethod
    def validate_llm_task(cls, value: str) -> str:
        return model_task(value)

    @model_validator(mode="after")
    def validate_generation_bounds(self) -> "ScheduleSection":
        if self.min_activities > self.max_activities:
            raise ValueError("min_activities 不能大于 max_activities")
        if self.min_description_length > self.max_description_length:
            raise ValueError("min_description_length 不能大于 max_description_length")
        return self


class InjectSection(PluginConfigBase):
    """仅调整已有日程/生活背景的注入策略；总开关在 schedule，持续话题和同轮重试会保留上下文。"""

    __ui_label__: ClassVar[str] = '日程注入策略'
    __ui_icon__: ClassVar[str] = "zap"
    __ui_order__: ClassVar[int] = 40

    enable_intent_classification: bool = Field(
        default=True,
        description='分类当前用户意图以区分日程问询、闲聊、技术问答和命令；技术/命令场景跳过。关闭后按闲聊处理，并仍应用优化器概率。',
        json_schema_extra={'label': '启用意图分类', 'hint': '分类当前用户意图以区分日程问询、闲聊、技术问答和命令；技术/命令场景跳过。关闭后按闲聊处理，并仍应用优化器概率。', 'order': 1, 'group': '策略'},
    )
    enable_state_analysis: bool = Field(
        default=True,
        description='在当前活动进行期间补充真实阶段和已进行分钟数；不控制 LifeEngine 的精力、饥饿、心情数值推进。',
        json_schema_extra={'label': '启用状态分析', 'hint': '在当前活动进行期间补充真实阶段和已进行分钟数；不控制 LifeEngine 的精力、饥饿、心情数值推进。', 'order': 2, 'group': '策略'},
    )
    enable_inject_optimization: bool = Field(
        default=True,
        description='启用重复控制和闲聊概率；关闭后跳过这两条过滤，仍保留意图分类、会话范围、同轮承接和重试保护。',
        json_schema_extra={'label': '启用注入优化', 'hint': '启用重复控制和闲聊概率；关闭后跳过这两条过滤，仍保留意图分类、会话范围、同轮承接和重试保护。', 'order': 3, 'group': '策略'},
    )
    casual_chat_inject_probability: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description='开启优化时，非连续闲聊场景的背景注入概率，范围 0–1；直接日程问询、持续话题和 Replyer 承接已注入 Planner 回合不受此概率限制。不是主动发消息概率。',
        json_schema_extra={'label': '非连续闲聊注入概率', 'hint': '开启优化时，非连续闲聊场景的背景注入概率，范围 0–1；直接日程问询、持续话题和 Replyer 承接已注入 Planner 回合不受此概率限制。不是主动发消息概率。', 'order': 4, 'group': '策略', 'depends_on': 'inject.enable_inject_optimization', 'depends_value': True},
    )
    context_max_turns: int = Field(
        default=3,
        ge=1,
        description='每会话最多保留的注入对话轮数，至少 1，默认 3；用于识别话题延续，不是模型历史聊天截断条数。',
        json_schema_extra={'label': '注入上下文最多轮数', 'hint': '每会话最多保留的注入对话轮数，至少 1，默认 3；用于识别话题延续，不是模型历史聊天截断条数。', 'order': 5, 'group': '对话上下文'},
    )
    context_ttl: int = Field(
        default=600,
        ge=60,
        description='注入对话记录有效期，单位秒，至少 60，默认 600；超期记录不用于延续判断。',
        json_schema_extra={'label': '注入上下文有效期（秒）', 'hint': '注入对话记录有效期，单位秒，至少 60，默认 600；超期记录不用于延续判断。', 'order': 6, 'group': '对话上下文'},
    )
