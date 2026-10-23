"""作息生活、联系许可与生活消息钩子的配置。"""

from __future__ import annotations

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator, model_validator

from ...utils.config_validation import clock_time, qq_id


class LifeUserProfile(PluginConfigBase):
    """life.profiles 数组的单个条目；QQ 是身份，显示名来自宿主，最多启用一位主人。"""

    user_id: str = Field(
        default="",
        description='该用户真实半角数字 QQ；完整档案不可留空。显示名称通过宿主已有真实聊天流读取。',
        json_schema_extra={'label': 'QQ 号', 'hint': '该用户真实半角数字 QQ；完整档案不可留空。显示名称通过宿主已有真实聊天流读取。', 'group': '身份与访问', 'order': 1},
    )
    enabled: bool = Field(
        default=True,
        description='启用该用户的生活私聊观察和访问许可；关闭保留数据库历史，但不再主动联系。',
        json_schema_extra={'label': '启用档案', 'hint': '启用该用户的生活私聊观察和访问许可；关闭保留数据库历史，但不再主动联系。', 'group': '身份与访问', 'order': 2},
    )
    role: Literal["owner", "friend"] = Field(
        default="friend",
        description='owner 可在本人私聊访问私人日记/梦境等主人内容，friend 只访问自己的日期及公开生活；最多启用一位 owner。此角色不授权 /zn，命令仍需 plugin.admin_qq。',
        json_schema_extra={'label': '私人生活访问角色', 'hint': 'owner 可在本人私聊访问私人日记/梦境等主人内容，friend 只访问自己的日期及公开生活；最多启用一位 owner。此角色不授权 /zn，命令仍需 plugin.admin_qq。', 'group': '身份与访问', 'order': 3},
    )
    proactive_enabled: bool = Field(
        default=False,
        description='允许生活服务主动联系该用户；还需 life.enabled、life.private_proactive_enabled、messaging.enabled 和已解析的真实私聊会话。',
        json_schema_extra={'label': '允许主动私聊', 'hint': '允许生活服务主动联系该用户；还需 life.enabled、life.private_proactive_enabled、messaging.enabled 和已解析的真实私聊会话。', 'group': '主动联系', 'order': 4},
    )
    daily_proactive_max: int = Field(
        default=1,
        ge=0,
        le=20,
        description='该用户每天生活主动私聊成功发送的最多次数，范围 0–20，默认 1；0 禁用。按关联任务真实发送成功结算，独立于日程提交额度。',
        json_schema_extra={'label': '每日成功发送上限', 'hint': '该用户每天生活主动私聊成功发送的最多次数，范围 0–20，默认 1；0 禁用。按关联任务真实发送成功结算，独立于日程提交额度。', 'group': '主动联系', 'order': 5},
    )
    quiet_start: str = Field(
        default="23:00",
        description='该用户生活主动私聊免打扰开始，HH:MM，按 schedule.timezone；不拦截普通被动聊天。开始和结束相同表示不设静默。',
        json_schema_extra={'label': '免打扰开始', 'hint': '该用户生活主动私聊免打扰开始，HH:MM，按 schedule.timezone；不拦截普通被动聊天。开始和结束相同表示不设静默。', 'group': '主动联系', 'order': 6},
    )
    quiet_end: str = Field(
        default="08:00",
        description='该用户生活主动私聊免打扰结束，HH:MM，支持跨夜；独立于空间及普通日程主动静默。',
        json_schema_extra={'label': '免打扰结束', 'hint': '该用户生活主动私聊免打扰结束，HH:MM，支持跨夜；独立于空间及普通日程主动静默。', 'group': '主动联系', 'order': 7},
    )

    @field_validator("user_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return qq_id(value, allow_empty=True)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return clock_time(value)


class ImportantDate(PluginConfigBase):
    """用户专属的重要日期；创建提醒素材后仍需主动私聊许可才能发送。"""

    user_id: str = Field(
        default="",
        description='日期所属的真实 QQ，必须匹配 life.profiles 的某个用户；该用户启用时才创建提醒素材。',
        json_schema_extra={'label': '所属 QQ 号', 'hint': '日期所属的真实 QQ，必须匹配 life.profiles 的某个用户；该用户启用时才创建提醒素材。', 'group': '日期', 'order': 1},
    )
    name: str = Field(
        default="",
        max_length=100,
        description='日期名称，如生日或考试，最多 100 字；配置条目不可留空。',
        json_schema_extra={'label': '日期名称', 'hint': '日期名称，如生日或考试，最多 100 字；配置条目不可留空。', 'group': '日期', 'order': 2},
    )
    date: str = Field(
        default="",
        description='日期，格式 YYYY-MM-DD，必须为有效公历日期；配置条目不可留空。',
        json_schema_extra={'label': '日期', 'hint': '日期，格式 YYYY-MM-DD，必须为有效公历日期；配置条目不可留空。', 'group': '日期', 'order': 3},
    )
    yearly: bool = Field(
        default=True,
        description='是否每年按月日重复；false 为一次性日期。2 月 29 日年度事件只在有效闰年出现。',
        json_schema_extra={'label': '每年重复', 'hint': '是否每年按月日重复；false 为一次性日期。2 月 29 日年度事件只在有效闰年出现。', 'group': '日期', 'order': 4},
    )
    lead_days: List[int] = Field(
        default_factory=lambda: [7, 1, 0],
        description='提前生成提醒素材的天数列表，每项 0–366；0 为当天，空列表不生成提醒，重复数字合并。默认 [7, 1, 0]，不是保证在当天某时刻发消息。',
        json_schema_extra={'label': '提前提醒天数', 'hint': '提前生成提醒素材的天数列表，每项 0–366；0 为当天，空列表不生成提醒，重复数字合并。默认 [7, 1, 0]，不是保证在当天某时刻发消息。', 'group': '日期', 'order': 5},
    )

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        from datetime import date

        if value:
            date.fromisoformat(value)
        return value

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        return qq_id(value, allow_empty=True)

    @field_validator("lead_days")
    @classmethod
    def validate_leads(cls, values: List[int]) -> List[int]:
        if any(value < 0 or value > 366 for value in values):
            raise ValueError("提前提醒天数必须在 0 至 366 之间")
        return sorted(set(values), reverse=True)


class LifeSection(PluginConfigBase):
    """消费已有日程推进生活，生成场景、梦境、私人日记与素材；主动私聊使用独立用户许可和发送额度。"""

    __ui_label__: ClassVar[str] = '生活状态与私聊'
    __ui_icon__: ClassVar[str] = "heart-pulse"
    __ui_order__: ClassVar[int] = 60

    enabled: bool = Field(
        default=True,
        description='生活服务总开关，控制状态、场景、梦境、私人日记、重要日期及生活主动私聊；自动空间发帖所需的公开素材也由此提供。日程引擎可独立运行。',
        json_schema_extra={'label': '启用生活状态', 'hint': '生活服务总开关，控制状态、场景、梦境、私人日记、重要日期及生活主动私聊；自动空间发帖所需的公开素材也由此提供。日程引擎可独立运行。', 'group': '生活状态', 'order': 1},
    )
    tick_seconds: int = Field(
        default=60,
        ge=10,
        le=3600,
        description='生活素材生成、日期检查和清理的巡检间隔，单位秒，范围 10–3600；主动私聊巡检间隔至少 30 秒，生活数值由统一协调循环约每 30 秒推进。',
        json_schema_extra={'label': '生活素材巡检间隔（秒）', 'hint': '生活素材生成、日期检查和清理的巡检间隔，单位秒，范围 10–3600；主动私聊巡检间隔至少 30 秒，生活数值由统一协调循环约每 30 秒推进。', 'group': '生活状态', 'order': 2},
    )
    offline_hours: int = Field(
        default=72,
        ge=1,
        le=72,
        description='生活数值最多回放已有日程的离线时长，单位小时，范围 1–72；未保存的活动不会补造。',
        json_schema_extra={'label': '离线补算上限（小时）', 'hint': '生活数值最多回放已有日程的离线时长，单位小时，范围 1–72；未保存的活动不会补造。', 'group': '生活状态', 'order': 3},
    )
    initial_energy: float = Field(
        default=70,
        ge=0,
        le=100,
        description='首次初始化生活数据库的精力，范围 0–100，默认 70；修改不会覆盖已有状态。',
        json_schema_extra={'label': '初始精力', 'hint': '首次初始化生活数据库的精力，范围 0–100，默认 70；修改不会覆盖已有状态。', 'group': '生活状态', 'order': 4},
    )
    initial_hunger: float = Field(
        default=20,
        ge=0,
        le=100,
        description='首次初始化生活数据库的饥饿，范围 0–100，默认 20，越大越饿；修改不会覆盖已有状态。',
        json_schema_extra={'label': '初始饥饿', 'hint': '首次初始化生活数据库的饥饿，范围 0–100，默认 20，越大越饿；修改不会覆盖已有状态。', 'group': '生活状态', 'order': 5},
    )
    scene_enabled: bool = Field(
        default=True,
        description='对当前已存在日程活动调用生活模型细化场景；每活动素材保持连续，不生成另一条日程。关闭后停止新增场景，历史记录保留。',
        json_schema_extra={'label': '细化当前场景', 'hint': '对当前已存在日程活动调用生活模型细化场景；每活动素材保持连续，不生成另一条日程。关闭后停止新增场景，历史记录保留。', 'group': '生活素材', 'order': 6},
    )
    dream_enabled: bool = Field(
        default=True,
        description='在日程睡眠结束的醒来记录后生成私人梦境；关闭后停止新增梦境，不改变睡眠数值或删除历史。',
        json_schema_extra={'label': '醒后梦境', 'hint': '在日程睡眠结束的醒来记录后生成私人梦境；关闭后停止新增梦境，不改变睡眠数值或删除历史。', 'group': '生活素材', 'order': 7},
    )
    diary_enabled: bool = Field(
        default=True,
        description='整理昨天的自身场景、梦境和匿名互动数量为私人生活日记；关闭后停止新增，已存日记及旧聊天日记保留。',
        json_schema_extra={'label': '抽象生活日记', 'hint': '整理昨天的自身场景、梦境和匿名互动数量为私人生活日记；关闭后停止新增，已存日记及旧聊天日记保留。', 'group': '生活素材', 'order': 8},
    )
    diary_hour: int = Field(
        default=8,
        ge=0,
        le=23,
        description='每天从此整点开始允许整理昨天日记，范围 0–23，按 schedule.timezone；在随后素材巡检中执行，有素材时每天一篇。',
        json_schema_extra={'label': '昨日生活日记整理整点', 'hint': '每天从此整点开始允许整理昨天日记，范围 0–23，按 schedule.timezone；在随后素材巡检中执行，有素材时每天一篇。', 'group': '生活素材', 'order': 9, 'depends_on': 'life.diary_enabled', 'depends_value': True},
    )
    profiles: List[LifeUserProfile] = Field(
        default_factory=list,
        description='私聊生活访问和联系许可数组，详见 life.profiles 子字段；QQ 不得重复，最多一位启用主人。空数组不观察个人生活、不主动私聊，也没有私人日记读者。',
        json_schema_extra={'label': '私聊联系许可', 'hint': '私聊生活访问和联系许可数组，详见 life.profiles 子字段；QQ 不得重复，最多一位启用主人。空数组不观察个人生活、不主动私聊，也没有私人日记读者。', 'group': '私聊用户与日期', 'order': 12},
    )
    important_dates: List[ImportantDate] = Field(
        default_factory=list,
        description='重要日期数组，详见 life.important_dates 子字段；完整日期必须关联已配置用户，创建提醒素材后还需主动私聊许可才可联系。聊天识别的日期需另行确认。',
        json_schema_extra={'label': '重要日期', 'hint': '重要日期数组，详见 life.important_dates 子字段；完整日期必须关联已配置用户，创建提醒素材后还需主动私聊许可才可联系。聊天识别的日期需另行确认。', 'group': '私聊用户与日期', 'order': 13},
    )
    private_proactive_enabled: bool = Field(
        default=False,
        description='允许生活素材驱动主动私聊；还需 messaging.enabled、用户 enabled/proactive_enabled、真实私聊会话以及静默/精力/冷却/额度全部通过。独立于 schedule.proactive_streams。',
        json_schema_extra={'label': '主动私聊', 'hint': '允许生活素材驱动主动私聊；还需 messaging.enabled、用户 enabled/proactive_enabled、真实私聊会话以及静默/精力/冷却/额度全部通过。独立于 schedule.proactive_streams。', 'group': '生活主动私聊', 'order': 14},
    )
    private_min_interval_minutes: int = Field(
        default=180,
        ge=10,
        le=10080,
        description='同一用户两次生活主动私聊成功发送的最短间隔，单位分钟，范围 10–10080，默认 180；不控制约定提醒。',
        json_schema_extra={'label': '私聊最短间隔（分钟）', 'hint': '同一用户两次生活主动私聊成功发送的最短间隔，单位分钟，范围 10–10080，默认 180；不控制约定提醒。', 'group': '生活主动私聊', 'order': 15, 'depends_on': 'life.private_proactive_enabled', 'depends_value': True},
    )
    private_silence_minutes: int = Field(
        default=15,
        ge=0,
        le=1440,
        description='主动联系前要求用户距最近真实消息的静默时长，单位分钟，范围 0–1440，默认 15；0 不要求此等待。',
        json_schema_extra={'label': '用户静默间隔（分钟）', 'hint': '主动联系前要求用户距最近真实消息的静默时长，单位分钟，范围 0–1440，默认 15；0 不要求此等待。', 'group': '生活主动私聊', 'order': 16, 'depends_on': 'life.private_proactive_enabled', 'depends_value': True},
    )
    private_min_energy: float = Field(
        default=30,
        ge=0,
        le=100,
        description='生活主动私聊最低精力，范围 0–100，默认 30；处于入睡/浅睡/深睡仍跳过，不因低阈值强行唤醒。',
        json_schema_extra={'label': '主动私聊最低精力', 'hint': '生活主动私聊最低精力，范围 0–100，默认 30；处于入睡/浅睡/深睡仍跳过，不因低阈值强行唤醒。', 'group': '生活主动私聊', 'order': 17, 'depends_on': 'life.private_proactive_enabled', 'depends_value': True},
    )
    claim_lease_seconds: int = Field(
        default=1800,
        ge=30,
        le=7200,
        description='生活素材认领的默认租约，单位秒，范围 30–7200，默认 1800；自动空间发布会按实际发布预算延长，不是等待消息合并的时长。',
        json_schema_extra={'label': '素材认领租约（秒）', 'hint': '生活素材认领的默认租约，单位秒，范围 30–7200，默认 1800；自动空间发布会按实际发布预算延长，不是等待消息合并的时长。', 'group': '生活素材', 'order': 11},
    )
    private_pending_seconds: int = Field(
        default=300,
        ge=30,
        le=1800,
        description='交给 Planner 的生活主动任务等待有效期，单位秒，范围 30–1800，默认 300；过期未发送可释放素材，不是收到消息后的延迟或防抖。',
        json_schema_extra={'label': '主动任务有效期（秒）', 'hint': '交给 Planner 的生活主动任务等待有效期，单位秒，范围 30–1800，默认 300；过期未发送可释放素材，不是收到消息后的延迟或防抖。', 'group': '生活主动私聊', 'order': 18, 'depends_on': 'life.private_proactive_enabled', 'depends_value': True},
    )
    opportunity_retention_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description='已到期且未占用生活素材的额外保留天数，范围 1–365，默认 30；不清理私人日记、梦境或结果待核对素材。',
        json_schema_extra={'label': '素材保留天数', 'hint': '已到期且未占用生活素材的额外保留天数，范围 1–365，默认 30；不清理私人日记、梦境或结果待核对素材。', 'group': '生活素材', 'order': 10},
    )

    @model_validator(mode="after")
    def validate_users(self) -> "LifeSection":
        if any(not profile.user_id for profile in self.profiles):
            raise ValueError("私聊许可条目必须填写 user_id；不用时请删除条目")
        ids = [profile.user_id for profile in self.profiles if profile.user_id]
        if len(set(ids)) != len(ids):
            raise ValueError("私聊用户 QQ 号不能重复")
        if sum(profile.enabled and profile.role == "owner" for profile in self.profiles) > 1:
            raise ValueError("最多只能启用一个主人档案")
        for event in self.important_dates:
            if not event.user_id or not event.name.strip() or not event.date:
                raise ValueError("重要日期必须填写 user_id、name 和 date；不用时请删除条目")
            if event.user_id not in ids:
                raise ValueError("重要日期的 user_id 必须匹配 life.profiles 中的用户")
        return self


class EnvironmentSection(PluginConfigBase):
    """生活环境快照的本地时间与中国历法；时区使用 schedule.timezone。"""

    __ui_label__: ClassVar[str] = '本地历法'
    __ui_order__: ClassVar[int] = 70

    calendar_enabled: bool = Field(
        default=True,
        description='生活环境快照提供中国节假日、农历和节气；需 life.enabled 及 chinese-calendar、lunar-python 依赖。关闭后仍有配置时区的日期时间。',
        json_schema_extra={'label': '中国节假日与农历', 'hint': '生活环境快照提供中国节假日、农历和节气；需 life.enabled 及 chinese-calendar、lunar-python 依赖。关闭后仍有配置时区的日期时间。', 'group': '历法', 'order': 1},
    )


class MessagingSection(PluginConfigBase):
    """生活私聊观察、主动任务回执和源消息撤回；改变 enabled 后必须重载插件。"""

    __ui_label__: ClassVar[str] = '生活消息处理'
    __ui_order__: ClassVar[int] = 80

    enabled: bool = Field(
        default=False,
        description='注册生活消息的接收、发送前、发送后 Hook；默认关闭。变更后必须重载插件，热关闭会先停止处理，热开启需重载才注册；生活主动私聊、日期观察和成功发送结算依赖本项。',
        json_schema_extra={'label': '启用生活消息钩子', 'hint': '注册生活消息的接收、发送前、发送后 Hook；默认关闭。变更后必须重载插件，热关闭会先停止处理，热开启需重载才注册；生活主动私聊、日期观察和成功发送结算依赖本项。', 'group': '开关与追踪', 'order': 1},
    )
    turn_expire_seconds: int = Field(
        default=300,
        ge=30,
        le=1800,
        description='保留源消息关联、撤回定位及回复轮次标识的时长，单位秒，范围 30–1800，默认 300；不是延迟发送、等待或合并消息。',
        json_schema_extra={'label': '源消息追踪有效期（秒）', 'hint': '保留源消息关联、撤回定位及回复轮次标识的时长，单位秒，范围 30–1800，默认 300；不是延迟发送、等待或合并消息。', 'group': '开关与追踪', 'order': 2},
    )
    recall_enabled: bool = Field(
        default=True,
        description='按源消息撤回取消尚未发送的对应回复；已发送消息不撤回。群聊撤回追踪和已配置生活私聊生效，关闭不影响生活主动任务回执。',
        json_schema_extra={'label': '撤回取消未发送回复', 'hint': '按源消息撤回取消尚未发送的对应回复；已发送消息不撤回。群聊撤回追踪和已配置生活私聊生效，关闭不影响生活主动任务回执。', 'group': '开关与追踪', 'order': 3},
    )
    rest_gate_enabled: bool = Field(
        default=False,
        description='对已启用 profiles 的私聊，按生活活动的睡眠/休息状态判断是否回复；需 life.enabled。群聊仍由主程序决策，不受此闸门影响，命令直接放行。',
        json_schema_extra={'label': '生活私聊休息回复闸门', 'hint': '对已启用 profiles 的私聊，按生活活动的睡眠/休息状态判断是否回复；需 life.enabled。群聊仍由主程序决策，不受此闸门影响，命令直接放行。', 'group': '生活私聊休息回复', 'order': 4},
    )
    wake_probability: float = Field(
        default=0.1,
        ge=0,
        le=1,
        description='休息回复闸门开启时，普通私聊消息允许唤醒回复的概率，范围 0–1，默认 0.1；明确唤醒可通过，明确勿扰优先拒绝。',
        json_schema_extra={'label': '休息时普通私聊判醒概率', 'hint': '休息回复闸门开启时，普通私聊消息允许唤醒回复的概率，范围 0–1，默认 0.1；明确唤醒可通过，明确勿扰优先拒绝。', 'group': '生活私聊休息回复', 'order': 5, 'depends_on': 'messaging.rest_gate_enabled', 'depends_value': True},
    )
    awake_grace_minutes: int = Field(
        default=20,
        ge=1,
        le=120,
        description='休息中实际回复成功后允许继续聊天的宽限，单位分钟，范围 1–120，默认 20；明确勿扰仍优先，未发送成功不确认醒来。',
        json_schema_extra={'label': '醒后宽限（分钟）', 'hint': '休息中实际回复成功后允许继续聊天的宽限，单位分钟，范围 1–120，默认 20；明确勿扰仍优先，未发送成功不确认醒来。', 'group': '生活私聊休息回复', 'order': 6, 'depends_on': 'messaging.rest_gate_enabled', 'depends_value': True},
    )
