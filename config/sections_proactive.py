"""日程驱动行为配置；只在有生活素材时考虑分享。"""

from __future__ import annotations

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase

ActivityName = Literal[
    "sleeping", "waking_up", "eating", "working", "studying", "exercising",
    "relaxing", "socializing", "commuting", "hobby", "self_care", "cooking", "shopping", "gaming", "bathing", "other",
]


class RoutineSection(PluginConfigBase):
    """从所选日程提供方读取当前活动，让模型判断是否发帖或浏览；自动发帖还要求生活引擎提供可公开素材。"""

    __ui_label__: ClassVar[str] = '空间自动行为'
    __ui_icon__: ClassVar[str] = "clock"
    __ui_order__: ClassVar[int] = 130

    post_enabled: bool = Field(
        default=True,
        description='允许自动空间发帖；需要 schedule.enabled、life.enabled、当前活动和可公开素材，再通过冷却、硬规则、模型及概率判断。手动主题和 API 不受此开关影响。',
        json_schema_extra={'label': '自动发说说', 'hint': '允许自动空间发帖；需要 schedule.enabled、life.enabled、当前活动和可公开素材，再通过冷却、硬规则、模型及概率判断。手动主题和 API 不受此开关影响。', 'group': '开关与间隔', 'order': 1},
    )
    browse_enabled: bool = Field(
        default=True,
        description='允许日程驱动的自动浏览；需要 schedule.enabled 和当前活动，再通过冷却、硬规则、模型及概率判断；不要求 life.enabled。',
        json_schema_extra={'label': '自动刷空间', 'hint': '允许日程驱动的自动浏览；需要 schedule.enabled 和当前活动，再通过冷却、硬规则、模型及概率判断；不要求 life.enabled。', 'group': '开关与间隔', 'order': 2},
    )
    check_interval_minutes: int = Field(
        default=20,
        ge=1,
        description='Routine 两轮检查间隔，单位分钟，至少 1，默认 20；不控制生活数值推进、素材巡检或约定提醒。',
        json_schema_extra={'label': '检查间隔（分钟）', 'hint': 'Routine 两轮检查间隔，单位分钟，至少 1，默认 20；不控制生活数值推进、素材巡检或约定提醒。', 'order': 3, 'group': '开关与间隔'},
    )
    post_cooldown_minutes: int = Field(
        default=120,
        ge=1,
        description='两次 Routine 自动发帖的最短间隔，单位分钟，至少 1；冷却期跳过模型判断，不限制手动命令和 API。',
        json_schema_extra={'label': '自动发帖间隔（分钟）', 'hint': '两次 Routine 自动发帖的最短间隔，单位分钟，至少 1；冷却期跳过模型判断，不限制手动命令和 API。', 'order': 4, 'group': '开关与间隔'},
    )
    browse_cooldown_minutes: int = Field(
        default=40,
        ge=1,
        description='两次 Routine 自动浏览的最短间隔，单位分钟，至少 1，默认 40；不限制手动 read_feed。',
        json_schema_extra={'label': '自动浏览间隔（分钟）', 'hint': '两次 Routine 自动浏览的最短间隔，单位分钟，至少 1，默认 40；不限制手动 read_feed。', 'order': 5, 'group': '开关与间隔'},
    )
    respect_silent_hours: bool = Field(
        default=True,
        description='复用 monitor.silent_hours：true 在静默期跳过自动发帖/浏览整轮决策；false 不做该硬限制，但浏览内部仍按静默点赞/评论许可处理。模型也可能选择沉默。',
        json_schema_extra={'label': '静默期跳过整轮决策', 'hint': '复用 monitor.silent_hours：true 在静默期跳过自动发帖/浏览整轮决策；false 不做该硬限制，但浏览内部仍按静默点赞/评论许可处理。模型也可能选择沉默。', 'order': 6, 'group': '决策前硬限制'},
    )
    post_blocked_activities: List[ActivityName] = Field(
        default_factory=lambda: ["sleeping", "working", "studying", "eating", "exercising"],
        description='禁止自动发帖的活动类型，命中后不调模型；空列表关闭这条硬限制。使用活动分类英文值，默认 sleeping/working/studying/eating/exercising。',
        json_schema_extra={'label': '发说说禁止活动', 'hint': '禁止自动发帖的活动类型，命中后不调模型；空列表关闭这条硬限制。使用活动分类英文值，默认 sleeping/working/studying/eating/exercising。', 'item_type': 'string', 'order': 7, 'group': '决策前硬限制'},
    )
    browse_blocked_activities: List[ActivityName] = Field(
        default_factory=lambda: ["sleeping", "working", "studying", "eating", "exercising"],
        description='禁止自动浏览的活动类型，格式和取值同 post_blocked_activities；空列表关闭这条硬限制，模型仍独立判断是否合适。',
        json_schema_extra={'label': '刷空间禁止活动', 'hint': '禁止自动浏览的活动类型，格式和取值同 post_blocked_activities；空列表关闭这条硬限制，模型仍独立判断是否合适。', 'item_type': 'string', 'order': 8, 'group': '决策前硬限制'},
    )
    max_post_chance: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description='模型允许自动发帖后，再通过的概率，范围 0–1；0 从不发，1 不额外限制。冷却、素材和硬规则仍生效。',
        json_schema_extra={'label': '发说说概率上限', 'hint': '模型允许自动发帖后，再通过的概率，范围 0–1；0 从不发，1 不额外限制。冷却、素材和硬规则仍生效。', 'order': 9, 'group': '模型通过后'},
    )
    max_browse_chance: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description='模型允许自动浏览后，再通过的概率，范围 0–1；0 从不浏览，1 不额外限制。',
        json_schema_extra={'label': '刷空间概率上限', 'hint': '模型允许自动浏览后，再通过的概率，范围 0–1；0 从不浏览，1 不额外限制。', 'order': 10, 'group': '模型通过后'},
    )
    require_reason: bool = Field(
        default=True,
        description='要求 Routine 模型使用“是|理由”或“否|理由”，用于 /zn debug routine；false 只要求是/否，不改变最终概率规则。',
        json_schema_extra={'label': '要求 LLM 给理由', 'hint': '要求 Routine 模型使用“是|理由”或“否|理由”，用于 /zn debug routine；false 只要求是/否，不改变最终概率规则。', 'order': 11, 'group': '模型通过后'},
    )
