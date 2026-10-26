"""QQ 空间、配图和人格配置及 WebUI 元信息。"""

from __future__ import annotations

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator, model_validator

from ..utils.config_validation import chat_references, clock_time, model_task, prompt_template, qq_id, qq_list


_DEFAULT_SEND_PROMPT = (
    "你是'{bot_personality}'，现在是'{current_time}'你想写一条主题是'{topic}'的说说发表在qq空间上，"
    "{bot_expression}，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，可以适当使用颜文字，"
    "只输出一条说说正文的内容，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"
)

_DEFAULT_READ_PROMPT = (
    "你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
    "在qq空间上在'{created_time}'发了一条内容是'{content}'的说说，你想要发表你的一条评论，现在是'{current_time}'"
    "你对'{target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
    "{bot_expression}，回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，不要输出多余内容"
    "(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容"
)

_DEFAULT_RT_PROMPT = (
    "你是'{bot_personality}'，你正在浏览你好友'{target_name}'的QQ空间，你看到了你的好友'{target_name}'"
    "在qq空间上在'{created_time}'转发了一条内容为'{rt_con}'的说说，你的好友的评论为'{content}'，你对'{"
    "target_name}'的印象是'{impression}'，若与你的印象点相关，可以适当评论相关内容，无关则忽略此印象，"
    "现在是'{current_time}'，你想要发表你的一条评论，{bot_expression}，"
    "回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
    "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容"
)

_DEFAULT_REPLY_PROMPT = (
    "你是'{bot_personality}'，你的好友'{nickname}'在'{created_time}'评论了你QQ空间上的一条内容为"
    "'{content}'的说说，你的好友对该说说的评论为:'{comment_content}'，"
    "现在是'{current_time}'，你想要对此评论进行回复，你对该好友的印象是:"
    "'{impression}'，若与你的印象点相关，可以适当回复相关内容，无关则忽略此印象，"
    "{bot_expression}，回复的平淡一些，简短一些，说中文，不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
    "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容"
)

_DEFAULT_REPLY_TO_REPLY_PROMPT = (
    "你是'{bot_personality}'，你之前在好友的QQ空间评论了一条内容为'{content}'的说说，"
    "你的评论为'{bot_comment}'，现在'{nickname}'在'{created_time}'回复了你的评论，"
    "回复内容为'{reply_content}'，现在是'{current_time}'，你想要对此回复进行回复，"
    "你对'{nickname}'的印象是'{impression}'，若与你的印象点相关，可以适当回复相关内容，"
    "无关则忽略此印象，{bot_expression}，回复的平淡一些，简短一些，说中文，"
    "不要刻意突出自身学科背景，不要浮夸，不要夸张修辞，"
    "不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )。只输出回复内容"
)


class PluginSection(PluginConfigBase):
    """插件总开关、命令管理员与 QQ 空间登录。HTTP 设置仅用于 napcat Cookie 获取方式。"""

    __ui_label__: ClassVar[str] = '基础与登录'
    __ui_icon__: ClassVar[str] = "info"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description='插件总开关；关闭后停止日程、生活、空间和消息服务。',
        json_schema_extra={'label': '启用插件', 'hint': '插件总开关；关闭后停止日程、生活、空间和消息服务。', 'order': 1, 'group': '基础'},
    )
    config_version: str = Field(
        default='6.3.0',
        description='配置模板版本，由 SDK 和插件维护；升级保留已有有效设置，请勿手动修改。',
        json_schema_extra={'label': '配置版本', 'disabled': True, 'order': 2, 'hint': '配置模板版本，由 SDK 和插件维护；升级保留已有有效设置，请勿手动修改。', 'group': '基础'},
    )
    http_host: str = Field(
        default="127.0.0.1",
        description='napcat Cookie 方式使用的 OneBot HTTP 主机；填写主机名或 IP，不带协议、端口或路径。adapter 获取和 SnowLuma 直发不使用此项。',
        json_schema_extra={'label': 'Cookie HTTP 主机', 'hint': 'napcat Cookie 方式使用的 OneBot HTTP 主机；填写主机名或 IP，不带协议、端口或路径。adapter 获取和 SnowLuma 直发不使用此项。', 'placeholder': '127.0.0.1', 'order': 6, 'group': '空间登录'},
    )
    http_port: str = Field(
        default="9999",
        description='napcat Cookie 方式使用的 OneBot HTTP 端口，半角数字字符串，范围 1–65535；与适配器 HTTP 服务一致。',
        json_schema_extra={'label': 'Cookie HTTP 端口', 'hint': 'napcat Cookie 方式使用的 OneBot HTTP 端口，半角数字字符串，范围 1–65535；与适配器 HTTP 服务一致。', 'placeholder': '9999', 'order': 7, 'group': '空间登录'},
    )
    http_token: str = Field(
        default="",
        description='napcat Cookie 方式使用的 HTTP 认证 Token；适配器未设置认证时留空。',
        json_schema_extra={'label': 'Cookie HTTP Token', 'hint': 'napcat Cookie 方式使用的 HTTP 认证 Token；适配器未设置认证时留空。', 'placeholder': '（留空 = 不认证）', 'order': 8, 'group': '空间登录'},
    )
    cookie_methods: List[Literal["adapter", "napcat", "clientkey", "qrcode", "local"]] = Field(
        default_factory=lambda: ["adapter", "napcat", "clientkey", "qrcode", "local"],
        description='Cookie 获取方式：adapter=适配器 API，napcat=OneBot HTTP，clientkey=本机 QQ，qrcode=扫码，local=缓存。前几项按近期成功率重排，qrcode/local 在末尾；空列表使用默认全部方式，刷新失败仍尝试本地缓存。',
        json_schema_extra={'label': 'Cookie 获取顺序', 'hint': '空列表使用默认全部方式；即使未列 local，刷新失败仍尝试本地缓存', 'item_type': 'string', 'order': 5, 'group': '空间登录'},
    )
    adapter_priority: List[str] = Field(
        default_factory=list,
        description='QQ 账号识别、SnowLuma 直发和 adapter Cookie 获取的适配器优先级；空列表自动探测。可填完整 plugin_id 或后缀，如 snowluma-adapter。',
        json_schema_extra={'label': '适配器优先级', 'hint': 'QQ 账号识别、SnowLuma 直发和 adapter Cookie 获取的适配器优先级；空列表自动探测。可填完整 plugin_id 或后缀，如 snowluma-adapter。', 'item_type': 'string', 'placeholder': '["snowluma-adapter"]', 'order': 4, 'group': '空间登录'},
    )
    admin_qq: List[str] = Field(
        default_factory=list,
        description='所有 /zn 命令的管理员 QQ 列表，包括 help；空列表禁用命令。与空间 Tool 权限、生活主人角色独立。',
        json_schema_extra={'label': '管理员 QQ', 'hint': '所有 /zn 命令的管理员 QQ 列表，包括 help；空列表禁用命令。与空间 Tool 权限、生活主人角色独立。', 'item_type': 'string', 'placeholder': '["123456"]', 'order': 3, 'group': '基础'},
    )


    @field_validator("admin_qq")
    @classmethod
    def validate_admins(cls, values: List[str]) -> List[str]:
        return qq_list(values)

    @field_validator("http_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        if not value or any(part in value for part in ("/", "?", "#", " ")):
            raise ValueError("HTTP 主机必须为主机名或 IP，不带协议、路径或空格")
        return value

    @field_validator("http_port")
    @classmethod
    def validate_port(cls, value: str) -> str:
        value = value.strip()
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
            raise ValueError("HTTP 端口必须为 1 至 65535 的数字字符串")
        return value


class SendSection(PluginConfigBase):
    """手动主题、send_feed Tool 和自动发帖的正文设置；custom 直接使用指定私聊内容，跳过正文模型。"""

    __ui_label__: ClassVar[str] = '空间发布与素材'
    __ui_icon__: ClassVar[str] = "send"
    __ui_order__: ClassVar[int] = 90

    permission: List[str] = Field(
        default_factory=list,
        description='send_feed Tool 的调用者 QQ 名单；默认空白名单禁用该 Tool。填写半角数字 QQ 字符串。',
        json_schema_extra={'label': '授权 QQ', 'hint': 'send_feed Tool 的调用者 QQ 名单；默认空白名单禁用该 Tool。填写半角数字 QQ 字符串。', 'item_type': 'string', 'placeholder': '["123456"]', 'order': 2, 'group': '工具权限'},
    )
    permission_type: Literal["whitelist", "blacklist"] = Field(
        default="whitelist",
        description='send_feed Tool 的 QQ 权限模式：whitelist 仅允许 permission 中的账号，blacklist 仅拒绝名单内账号。/zn 命令使用 plugin.admin_qq，自动发帖和跨插件 API 不使用该名单。',
        json_schema_extra={'label': '权限模式', 'hint': 'send_feed Tool 的 QQ 权限模式：whitelist 仅允许 permission 中的账号，blacklist 仅拒绝名单内账号。/zn 命令使用 plugin.admin_qq，自动发帖和跨插件 API 不使用该名单。', 'order': 1, 'group': '工具权限'},
    )
    prompt: str = Field(
        default=_DEFAULT_SEND_PROMPT,
        description='生成说说正文的模板。支持 {current_time}、{bot_personality}、{bot_expression}、{topic}、{current_activity}；时间使用 schedule.timezone，字面花括号写成 {{ 和 }}。custom 直发不使用。',
        json_schema_extra={'label': '说说 prompt', 'hint': '生成说说正文的模板。支持 {current_time}、{bot_personality}、{bot_expression}、{topic}、{current_activity}；时间使用 schedule.timezone，字面花括号写成 {{ 和 }}。custom 直发不使用。', 'rows': 6, 'order': 3, 'group': '正文', 'input_type': 'textarea'},
    )
    history_number: int = Field(
        default=5,
        ge=0,
        description='生成正文时参考的近期说说条数；0 完全关闭历史查询。用于避免重复，条数越多背景越长；custom 直发不使用。',
        json_schema_extra={'label': '历史参考数', 'hint': '生成正文时参考的近期说说条数；0 完全关闭历史查询。用于避免重复，条数越多背景越长；custom 直发不使用。', 'order': 4, 'group': '正文'},
    )
    custom_qqaccount: str = Field(
        default="",
        description='/zn custom 读取的私聊对象 QQ；留空禁用 custom。直接发布筛选后的最新一条非命令正文，跳过正文生成和聊天素材整理，仍可配图。',
        json_schema_extra={'label': 'Custom 私聊 QQ', 'hint': '/zn custom 读取的私聊对象 QQ；留空禁用 custom。直接发布筛选后的最新一条非命令正文，跳过正文生成和聊天素材整理，仍可配图。', 'placeholder': '（留空 = 禁用）', 'order': 5, 'group': 'Custom 直发'},
    )
    custom_only_mai: bool = Field(
        default=True,
        description='custom 私聊内容来源：true 只取麦麦自己的发言，false 只取对方发言；仅 custom_qqaccount 非空时有作用。',
        json_schema_extra={'label': '只取麦麦发言', 'hint': 'custom 私聊内容来源：true 只取麦麦自己的发言，false 只取对方发言；仅 custom_qqaccount 非空时有作用。', 'order': 6, 'group': 'Custom 直发'},
    )


    # 聊天素材继承 Maizone 的三种整理方式；新能力默认关闭。
    enable_chat_memory: bool = Field(
        default=False,
        description='在主题正文生成时读取选定会话作为匿名背景；默认关闭。与日程的 history_message_limit 独立，custom 直发不使用。',
        json_schema_extra={'label': '聊天素材', 'order': 7, 'hint': '在主题正文生成时读取选定会话作为匿名背景；默认关闭。与日程的 history_message_limit 独立，custom 直发不使用。', 'group': '聊天素材范围'},
    )
    filter_mode: Literal["all", "whitelist", "blacklist"] = Field(
        default="whitelist",
        description='聊天素材的会话过滤：all 使用所有允许的会话，whitelist 只使用 target_chats，blacklist 排除 target_chats；空白名单不读任何聊天，空黑名单与 all 相同。',
        json_schema_extra={'label': '素材会话过滤', 'hint': '聊天素材的会话过滤：all 使用所有允许的会话，whitelist 只使用 target_chats，blacklist 排除 target_chats；空白名单不读任何聊天，空黑名单与 all 相同。', 'group': '聊天素材范围', 'order': 8, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    target_chats: List[str] = Field(
        default_factory=list,
        description='素材会话名单：group:群号、private:QQ号、session:真实会话ID。用于 whitelist/blacklist；这里不使用 schedule 的 qq:group: 格式。',
        json_schema_extra={'label': '素材会话', 'hint': '素材会话名单：group:群号、private:QQ号、session:真实会话ID。用于 whitelist/blacklist；这里不使用 schedule 的 qq:group: 格式。', 'group': '聊天素材范围', 'order': 9, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    include_private_chat_memory: bool = Field(
        default=False,
        description='是否允许把所选私聊作为匿名背景；false 在所有过滤模式下都排除私聊。公开正文仍不得包含原话、身份或私人安排。',
        json_schema_extra={'label': '允许私聊素材', 'hint': '是否允许把所选私聊作为匿名背景；false 在所有过滤模式下都排除私聊。公开正文仍不得包含原话、身份或私人安排。', 'group': '聊天素材范围', 'order': 10, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    time_range: Literal["today", "last_n_hours"] = Field(
        default="today",
        description='聊天素材时间范围：today 为 schedule.timezone 下当天零点至现在；last_n_hours 为最近 last_hours 小时。',
        json_schema_extra={'label': '素材时间范围', 'hint': '聊天素材时间范围：today 为 schedule.timezone 下当天零点至现在；last_n_hours 为最近 last_hours 小时。', 'group': '聊天素材范围', 'order': 11, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    last_hours: int = Field(
        default=6,
        ge=1,
        le=72,
        description='last_n_hours 模式的回看时长，单位小时，范围 1–72；today 模式忽略。',
        json_schema_extra={'label': '最近小时数', 'hint': 'last_n_hours 模式的回看时长，单位小时，范围 1–72；today 模式忽略。', 'group': '聊天素材范围', 'order': 12, 'depends_on': 'send.time_range', 'depends_value': 'last_n_hours'},
    )
    min_messages: int = Field(
        default=5,
        ge=1,
        le=100,
        description='有效聊天素材最少条数，范围 1–100；少于此数时使用 fallback_prompt，不调用摘要模型。必须不大于 max_messages。',
        json_schema_extra={'label': '最少素材条数', 'hint': '有效聊天素材最少条数，范围 1–100；少于此数时使用 fallback_prompt，不调用摘要模型。必须不大于 max_messages。', 'group': '聊天素材整理', 'order': 13, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    max_messages: int = Field(
        default=50,
        ge=1,
        le=200,
        description='每会话查询以及跨会话合并后保留的最近消息上限，范围 1–200；按时间排序，不包含命令和空正文。',
        json_schema_extra={'label': '最多素材条数', 'hint': '每会话查询以及跨会话合并后保留的最近消息上限，范围 1–200；按时间排序，不包含命令和空正文。', 'group': '聊天素材整理', 'order': 14, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    per_message_max_chars: int = Field(
        default=200,
        ge=20,
        le=2000,
        description='每条素材最多保留的字符数，范围 20–2000；截断后隐藏号码和邮箱，并去掉发送者标识。',
        json_schema_extra={'label': '单条素材字数', 'hint': '每条素材最多保留的字符数，范围 20–2000；截断后隐藏号码和邮箱，并去掉发送者标识。', 'group': '聊天素材整理', 'order': 15, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    chat_memory_mode: Literal["direct", "summary", "hybrid"] = Field(
        default="hybrid",
        description='素材整理方式：direct 使用匿名片段，summary 全部摘要，hybrid 摘要较早消息并保留近期片段；片段只作背景，正文仍需匿名概括。',
        json_schema_extra={'label': '素材整理方式', 'hint': '素材整理方式：direct 使用匿名片段，summary 全部摘要，hybrid 摘要较早消息并保留近期片段；片段只作背景，正文仍需匿名概括。', 'group': '聊天素材整理', 'order': 16, 'depends_on': 'send.enable_chat_memory', 'depends_value': True},
    )
    summary_model: str = Field(
        default="utils",
        description='summary/hybrid 使用的主程序模型任务名；单次超时沿用 llm.llm_timeout_seconds，direct 模式忽略。hybrid 全部消息都属于近期片段时不调摘要。',
        json_schema_extra={'label': '素材摘要模型', 'hint': 'summary/hybrid 使用的主程序模型任务名；单次超时沿用 llm.llm_timeout_seconds，direct 模式忽略。hybrid 全部消息都属于近期片段时不调摘要。', 'group': '聊天素材整理', 'order': 17},
    )
    summary_prompt: str = Field(
        default="请匿名概括以下聊天的日常话题，不超过200字。不保留原话、昵称、号码、私人约定或敏感信息。\n{chat_logs}",
        description='摘要提示词模板，仅支持 {chat_logs}；其内容已包装为不可信背景。用于 summary/hybrid，字面花括号写成 {{ 和 }}。',
        json_schema_extra={'label': '素材摘要提示词', 'input_type': 'textarea', 'hint': '摘要提示词模板，仅支持 {chat_logs}；其内容已包装为不可信背景。用于 summary/hybrid，字面花括号写成 {{ 和 }}。', 'group': '聊天素材整理', 'order': 18, 'rows': 6},
    )
    hybrid_recent_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description='hybrid 保留的最近片段条数，范围 1–100；超出本次消息数时保留全部片段，不生成摘要。',
        json_schema_extra={'label': '近期片段条数', 'hint': 'hybrid 保留的最近片段条数，范围 1–100；超出本次消息数时保留全部片段，不生成摘要。', 'group': '聊天素材整理', 'order': 19, 'depends_on': 'send.chat_memory_mode', 'depends_value': 'hybrid'},
    )
    fallback_prompt: str = Field(
        default="今天的聊天比较平淡，只根据当前生活和主题自然表达。",
        description='无选中会话或有效消息不足时追加的背景提示；不是正文替代文本。网络、查询和模型错误仍报告失败。',
        json_schema_extra={'label': '平淡一天提示', 'hint': '无选中会话或有效消息不足时追加的背景提示；不是正文替代文本。网络、查询和模型错误仍报告失败。', 'group': '聊天素材整理', 'order': 20, 'depends_on': 'send.enable_chat_memory', 'depends_value': True, 'input_type': 'textarea', 'rows': 6},
    )


    @field_validator("permission")
    @classmethod
    def validate_permissions(cls, values: List[str]) -> List[str]:
        return qq_list(values)

    @field_validator("target_chats")
    @classmethod
    def validate_targets(cls, values: List[str]) -> List[str]:
        return chat_references(values, material=True)

    @field_validator("summary_model")
    @classmethod
    def validate_summary_model(cls, value: str) -> str:
        return model_task(value)

    @field_validator("custom_qqaccount")
    @classmethod
    def validate_custom_qq(cls, value: str) -> str:
        return qq_id(value, allow_empty=True)

    @field_validator("prompt", "summary_prompt")
    @classmethod
    def validate_templates(cls, value: str, info) -> str:
        placeholders = {"chat_logs"} if info.field_name == "summary_prompt" else {
            "current_time", "bot_personality", "bot_expression", "topic", "current_activity",
        }
        return prompt_template(value, placeholders)

    @model_validator(mode="after")
    def validate_message_bounds(self) -> "SendSection":
        if self.min_messages > self.max_messages:
            raise ValueError("min_messages 不能大于 max_messages")
        return self


class ImageSection(PluginConfigBase):
    """只控制主题发布与自动发帖的配图生成。send_feed_api 接收调用方自备图片，不经过本段生成开关。"""

    __ui_label__: ClassVar[str] = '空间配图'
    __ui_icon__: ClassVar[str] = "image"
    __ui_order__: ClassVar[int] = 100

    enable_image: bool = Field(
        default=False,
        description='主题和自动发帖是否生成配图；默认关闭，可发布纯文字。AI 来源需要麦麦绘卷与模型 key，表情包来源使用主程序表情包。',
        json_schema_extra={'label': '启用配图', 'hint': '主题和自动发帖是否生成配图；默认关闭，可发布纯文字。AI 来源需要麦麦绘卷与模型 key，表情包来源使用主程序表情包。', 'order': 1, 'group': '图片来源'},
    )
    image_mode: Literal["only_ai", "only_emoji", "random"] = Field(
        default="random",
        description='图片来源：only_ai 仅 AI，only_emoji 仅表情包，random 按 ai_probability 为本次发布选择一种来源；多张图共用来源。',
        json_schema_extra={'label': '图片来源', 'hint': '图片来源：only_ai 仅 AI，only_emoji 仅表情包，random 按 ai_probability 为本次发布选择一种来源；多张图共用来源。', 'order': 2, 'group': '图片来源', 'depends_on': 'image.enable_image', 'depends_value': True},
    )
    ai_probability: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description='image_mode=random 时，本次发布选择 AI 来源的概率，范围 0–1；0 全部使用表情包，1 全部使用 AI。',
        json_schema_extra={'label': 'AI 图概率', 'hint': 'image_mode=random 时，本次发布选择 AI 来源的概率，范围 0–1；0 全部使用表情包，1 全部使用 AI。', 'order': 3, 'group': '图片来源', 'depends_on': 'image.image_mode', 'depends_value': 'random'},
    )
    image_number: int = Field(
        default=1,
        ge=1,
        le=4,
        description='本次期望图片数量，范围 1–4；AI 串行逐张请求，表情包逐次匹配，未匹配的表情包跳过。修改后重载插件以同步组件预算。',
        json_schema_extra={'label': '图片数量', 'hint': '本次期望图片数量，范围 1–4；AI 串行逐张请求，表情包逐次匹配，未匹配的表情包跳过。修改后重载插件以同步组件预算。', 'order': 4, 'group': '图片来源', 'depends_on': 'image.enable_image', 'depends_value': True},
    )
    pic_plugin_model: str = Field(
        default="",
        description='AI 配图使用的麦麦绘卷 models.<key>，如 model3；仅 AI 来源使用。require_image=true 时缺少 key 会使本次发布失败。',
        json_schema_extra={'label': '绘卷模型 key', 'hint': 'AI 配图使用的麦麦绘卷 models.<key>，如 model3；仅 AI 来源使用。require_image=true 时缺少 key 会使本次发布失败。', 'placeholder': 'model3', 'order': 5, 'group': 'AI 内容', 'depends_on': 'image.enable_image', 'depends_value': True},
    )
    clear_image: bool = Field(
        default=True,
        description='是否跳过 AI 图片的本地副本归档：true 不新增副本，false 保存到 SDK 数据目录 images/；不会清理已经存在的历史图片。',
        json_schema_extra={'label': '不保存 AI 图片副本', 'hint': '是否跳过 AI 图片的本地副本归档：true 不新增副本，false 保存到 SDK 数据目录 images/；不会清理已经存在的历史图片。', 'order': 10, 'group': '失败与归档', 'depends_on': 'image.enable_image', 'depends_value': True},
    )
    require_image: bool = Field(
        default=True,
        description='AI 图片是否必须全部成功：true 失败则停止发布；false 允许缺少失败图片继续发布。仅影响 AI，未匹配表情包允许无图。',
        json_schema_extra={'label': 'AI 配图必须成功', 'order': 8, 'hint': 'AI 图片是否必须全部成功：true 失败则停止发布；false 允许缺少失败图片继续发布。仅影响 AI，未匹配表情包允许无图。', 'group': '失败与归档', 'depends_on': 'image.enable_image', 'depends_value': True},
    )
    image_retry: int = Field(
        default=2,
        ge=0,
        le=2,
        description='每张 AI 图片失败后的额外重试次数，范围 0–2；每次间隔 5 秒。空间发布本身不重试；修改后重载插件以同步组件预算。',
        json_schema_extra={'label': '生图重试次数', 'order': 9, 'hint': '每张 AI 图片失败后的额外重试次数，范围 0–2；每次间隔 5 秒。空间发布本身不重试；修改后重载插件以同步组件预算。', 'group': '失败与归档', 'depends_on': 'image.enable_image', 'depends_value': True},
    )
    ai_content_mode: Literal["random", "selfie", "illustration"] = Field(
        default="random",
        description='AI 每张内容：random 各以 50% 概率选择自拍/普通画图，selfie 全部自拍，illustration 全部普通画图；该张重试保持原选择。',
        json_schema_extra={'label': 'AI 配图内容', 'hint': 'AI 每张内容：random 各以 50% 概率选择自拍/普通画图，selfie 全部自拍，illustration 全部普通画图；该张重试保持原选择。', 'order': 6, 'group': 'AI 内容', 'depends_on': 'image.enable_image', 'depends_value': True},
    )
    selfie_style: Literal["auto", "standard", "mirror", "photo"] = Field(
        default="photo",
        description='仅自拍时传给绘卷的视角：standard 前置，mirror 对镜，photo 第三人称，auto 按当前活动选择。普通画图忽略视角、角色参考图和衣着。',
        json_schema_extra={'label': '自拍视角', 'hint': '仅自拍时传给绘卷的视角：standard 前置，mirror 对镜，photo 第三人称，auto 按当前活动选择。普通画图忽略视角、角色参考图和衣着。', 'order': 7, 'group': 'AI 内容', 'depends_on': 'image.enable_image', 'depends_value': True},
    )


class ReadSection(PluginConfigBase):
    """read_feed Tool 的权限、读取和互动参数；普通/转发评论模板与跳过策略也供自动浏览使用。"""

    __ui_label__: ClassVar[str] = '空间读取与评论'
    __ui_icon__: ClassVar[str] = "book-open"
    __ui_order__: ClassVar[int] = 110

    allow_skip_comment: bool = Field(
        default=True,
        description='手动读取及自动浏览允许模型输出“不回复”跳过新说说评论，仍按点赞概率处理并记入去重；不控制自己评论区和链式回复。',
        json_schema_extra={'label': '新说说允许跳过评论', 'order': 6, 'hint': '手动读取及自动浏览允许模型输出“不回复”跳过新说说评论，仍按点赞概率处理并记入去重；不控制自己评论区和链式回复。', 'group': '共用评论模板'},
    )

    permission: List[str] = Field(
        default_factory=list,
        description='读取与空间回忆 Tool 的 QQ 名单；默认空黑名单允许所有具有真实调用者 QQ 的用户。需要限制时改为 whitelist 并填写 QQ。',
        json_schema_extra={'label': '授权 QQ', 'hint': '读取与空间回忆 Tool 的 QQ 名单；默认空黑名单允许所有具有真实调用者 QQ 的用户。需要限制时改为 whitelist 并填写 QQ。', 'item_type': 'string', 'placeholder': '["123456"]', 'order': 2, 'group': '工具权限'},
    )
    permission_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description='read_feed 与 recall_qzone_activity Tool 的调用者 QQ 权限模式：whitelist 仅允许名单内，blacklist 仅拒绝名单内；不控制 /zn、自动浏览或跨插件 API。',
        json_schema_extra={'label': '权限模式', 'hint': 'read_feed 与 recall_qzone_activity Tool 的调用者 QQ 权限模式：whitelist 仅允许名单内，blacklist 仅拒绝名单内；不控制 /zn、自动浏览或跨插件 API。', 'order': 1, 'group': '工具权限'},
    )
    read_number: int = Field(
        default=5,
        ge=1,
        description='read_feed Tool 单次读取好友最近说说条数，至少 1，建议 3–10；不控制自动浏览条数。修改后重载插件以同步组件预算。',
        json_schema_extra={'label': '手动读取条数', 'hint': 'read_feed Tool 单次读取好友最近说说条数，至少 1，建议 3–10；不控制自动浏览条数。修改后重载插件以同步组件预算。', 'order': 3, 'group': '手动读取'},
    )
    like_probability: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description='手动 read_feed 对未处理说说尝试点赞的概率，范围 0–1；1 代表每条都尝试，成功仍取决于空间接口。不控制自动浏览。',
        json_schema_extra={'label': '点赞概率', 'hint': '手动 read_feed 对未处理说说尝试点赞的概率，范围 0–1；1 代表每条都尝试，成功仍取决于空间接口。不控制自动浏览。', 'order': 4, 'group': '手动读取'},
    )
    comment_probability: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description='手动 read_feed 对未处理说说尝试生成评论的概率，范围 0–1；1 仍允许 allow_skip_comment 跳过。不控制自动浏览。',
        json_schema_extra={'label': '评论概率', 'hint': '手动 read_feed 对未处理说说尝试生成评论的概率，范围 0–1；1 仍允许 allow_skip_comment 跳过。不控制自动浏览。', 'order': 5, 'group': '手动读取'},
    )
    prompt: str = Field(
        default=_DEFAULT_READ_PROMPT,
        description='普通说说评论模板，手动/自动共用。支持 {current_time}、{bot_personality}、{bot_expression}、{target_name}、{created_time}、{content}、{impression}。',
        json_schema_extra={'label': '评论 prompt', 'hint': '普通说说评论模板，手动/自动共用。支持 {current_time}、{bot_personality}、{bot_expression}、{target_name}、{created_time}、{content}、{impression}。', 'rows': 6, 'order': 7, 'group': '共用评论模板', 'input_type': 'textarea'},
    )
    rt_prompt: str = Field(
        default=_DEFAULT_RT_PROMPT,
        description='转发说说评论模板，手动/自动共用；支持普通评论的全部占位符及 {rt_con}（转发原文）。',
        json_schema_extra={'label': '转发评论 prompt', 'hint': '转发说说评论模板，手动/自动共用；支持普通评论的全部占位符及 {rt_con}（转发原文）。', 'rows': 6, 'order': 8, 'group': '共用评论模板', 'input_type': 'textarea'},
    )


    @field_validator("permission")
    @classmethod
    def validate_permissions(cls, values: List[str]) -> List[str]:
        return qq_list(values)

    @field_validator("prompt", "rt_prompt")
    @classmethod
    def validate_templates(cls, value: str, info) -> str:
        placeholders = {"current_time", "bot_personality", "bot_expression", "target_name", "created_time", "content", "impression"}
        if info.field_name == "rt_prompt":
            placeholders.add("rt_con")
        return prompt_template(value, placeholders)


class MonitorSection(PluginConfigBase):
    """Routine 自动浏览的好友过滤、静默、点赞、评论和回复设置；本段不启动独立定时任务。"""

    __ui_label__: ClassVar[str] = '空间自动互动'
    __ui_icon__: ClassVar[str] = "rss"
    __ui_order__: ClassVar[int] = 120

    read_list: List[str] = Field(
        default_factory=list,
        description='自动浏览处理/排除的说说作者 QQ；空黑名单处理全部，空白名单不处理任何人。启用自己评论区回复时，名单也需允许麦麦 QQ。',
        json_schema_extra={'label': '目标好友名单', 'hint': '自动浏览处理/排除的说说作者 QQ；空黑名单处理全部，空白名单不处理任何人。启用自己评论区回复时，名单也需允许麦麦 QQ。', 'item_type': 'string', 'placeholder': '[]', 'order': 2, 'group': '好友范围与互动'},
    )
    read_list_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description='自动浏览名单模式：whitelist 只处理 read_list，blacklist 排除 read_list；名单过滤也作用于自己的说说评论区。',
        json_schema_extra={'label': '名单模式', 'hint': '自动浏览名单模式：whitelist 只处理 read_list，blacklist 排除 read_list；名单过滤也作用于自己的说说评论区。', 'order': 1, 'group': '好友范围与互动'},
    )
    like_probability: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description='自动浏览对未处理好友说说尝试点赞的概率，范围 0–1；1 表示每条都尝试，还受静默时段限制。',
        json_schema_extra={'label': '点赞概率', 'hint': '自动浏览对未处理好友说说尝试点赞的概率，范围 0–1；1 表示每条都尝试，还受静默时段限制。', 'order': 3, 'group': '好友范围与互动'},
    )
    comment_probability: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description='自动浏览对未处理好友说说生成评论的概率，范围 0–1；使用 read.prompt/rt_prompt 和 allow_skip_comment。不控制自己评论区或链式回复。',
        json_schema_extra={'label': '评论概率', 'hint': '自动浏览对未处理好友说说生成评论的概率，范围 0–1；使用 read.prompt/rt_prompt 和 allow_skip_comment。不控制自己评论区或链式回复。', 'order': 4, 'group': '好友范围与互动'},
    )
    silent_hours: str = Field(
        default="22:00-07:00",
        description='自动浏览的静默时段，使用 schedule.timezone；格式 HH:MM-HH:MM，多段半角逗号分隔，支持跨夜，端点包含在内；留空全天可互动。默认不点赞、评论或回复。',
        json_schema_extra={'label': '静默时段', 'hint': '自动浏览的静默时段，使用 schedule.timezone；格式 HH:MM-HH:MM，多段半角逗号分隔，支持跨夜，端点包含在内；留空全天可互动。默认不点赞、评论或回复。', 'placeholder': '22:00-07:00', 'order': 5, 'group': '自动互动静默'},
    )
    like_during_silent: bool = Field(
        default=False,
        description='允许自动浏览在静默期仍按点赞概率互动；routine.respect_silent_hours=true 会在决策前跳过整轮，此许可仅在整轮未被拦截时有效。',
        json_schema_extra={'label': '静默期允许点赞', 'hint': '允许自动浏览在静默期仍按点赞概率互动；routine.respect_silent_hours=true 会在决策前跳过整轮，此许可仅在整轮未被拦截时有效。', 'order': 6, 'group': '自动互动静默'},
    )
    comment_during_silent: bool = Field(
        default=False,
        description='允许自动浏览在静默期仍评论、回复自己评论区和链式回复；routine.respect_silent_hours=true 会先跳过整轮，需关闭该硬限制才可能互动。',
        json_schema_extra={'label': '静默期允许评论', 'hint': '允许自动浏览在静默期仍评论、回复自己评论区和链式回复；routine.respect_silent_hours=true 会先跳过整轮，需关闭该硬限制才可能互动。', 'order': 7, 'group': '自动互动静默'},
    )
    enable_auto_reply: bool = Field(
        default=False,
        description='自动浏览时检查自己的说说并回复新评论；还受名单和静默评论许可限制。不控制好友空间中对麦麦评论的链式回复。',
        json_schema_extra={'label': '自动回复评论', 'hint': '自动浏览时检查自己的说说并回复新评论；还受名单和静默评论许可限制。不控制好友空间中对麦麦评论的链式回复。', 'order': 8, 'group': '回复评论'},
    )
    self_read_number: int = Field(
        default=5,
        ge=1,
        description='enable_auto_reply=true 时检查自己的最近说说条数，至少 1；关闭自动回复后不查询自己的评论区。不是好友动态读取上限。',
        json_schema_extra={'label': '自己评论区检查条数', 'hint': 'enable_auto_reply=true 时检查自己的最近说说条数，至少 1；关闭自动回复后不查询自己的评论区。不是好友动态读取上限。', 'order': 9, 'group': '回复评论', 'depends_on': 'monitor.enable_auto_reply', 'depends_value': True},
    )
    reply_prompt: str = Field(
        default=_DEFAULT_REPLY_PROMPT,
        description='回复自己说说下评论的模板。支持 {current_time}、{bot_personality}、{bot_expression}、{nickname}、{created_time}、{content}、{comment_content}、{impression}。',
        json_schema_extra={'label': '回复 prompt', 'hint': '回复自己说说下评论的模板。支持 {current_time}、{bot_personality}、{bot_expression}、{nickname}、{created_time}、{content}、{comment_content}、{impression}。', 'rows': 6, 'order': 10, 'group': '回复评论', 'depends_on': 'monitor.enable_auto_reply', 'depends_value': True, 'input_type': 'textarea'},
    )
    reply_to_reply_prompt: str = Field(
        default=_DEFAULT_REPLY_TO_REPLY_PROMPT,
        description='回复好友空间中对麦麦评论的回复，独立于 enable_auto_reply。支持 {current_time}、{bot_personality}、{bot_expression}、{nickname}、{created_time}、{content}、{bot_comment}、{reply_content}、{impression}；不支持 {comment_content}。',
        json_schema_extra={'label': '链式回复 prompt', 'hint': '回复好友空间中对麦麦评论的回复，独立于 enable_auto_reply。支持 {current_time}、{bot_personality}、{bot_expression}、{nickname}、{created_time}、{content}、{bot_comment}、{reply_content}、{impression}；不支持 {comment_content}。', 'rows': 6, 'order': 11, 'group': '回复评论', 'input_type': 'textarea'},
    )
    processed_feeds_cache_size: int = Field(
        default=100,
        ge=1,
        description='手动读取和自动浏览共用的已处理说说 ID 上限，至少 1；持久化保存，超出删除最早 ID，淘汰后旧动态可能再次互动。',
        json_schema_extra={'label': '说说缓存上限', 'hint': '手动读取和自动浏览共用的已处理说说 ID 上限，至少 1；持久化保存，超出删除最早 ID，淘汰后旧动态可能再次互动。', 'order': 12, 'group': '持久去重'},
    )
    processed_comments_cache_size: int = Field(
        default=100,
        ge=1,
        description='自动回复共用的去重记录上限，按说说 ID 分组计数，至少 1；不是总评论条数，同一说说可保留多个已回复评论 ID。',
        json_schema_extra={'label': '评论去重分组上限', 'hint': '自动回复共用的去重记录上限，按说说 ID 分组计数，至少 1；不是总评论条数，同一说说可保留多个已回复评论 ID。', 'order': 13, 'group': '持久去重'},
    )


    @field_validator("read_list")
    @classmethod
    def validate_read_list(cls, values: List[str]) -> List[str]:
        return qq_list(values)

    @field_validator("silent_hours")
    @classmethod
    def validate_silent_hours(cls, value: str) -> str:
        if not value.strip():
            return ""
        periods = []
        for period in value.split(","):
            parts = period.split("-")
            if len(parts) != 2:
                raise ValueError("静默时段必须为 HH:MM-HH:MM，多段用半角逗号分隔")
            periods.append(f"{clock_time(parts[0])}-{clock_time(parts[1])}")
        return ",".join(periods)

    @field_validator("reply_prompt", "reply_to_reply_prompt")
    @classmethod
    def validate_templates(cls, value: str, info) -> str:
        placeholders = {"current_time", "bot_personality", "bot_expression", "nickname", "created_time", "content", "impression"}
        placeholders.update({"comment_content"} if info.field_name == "reply_prompt" else {"bot_comment", "reply_content"})
        return prompt_template(value, placeholders)


class LLMSection(PluginConfigBase):
    """空间正文、评论、Routine 决策以及生活场景、梦境、日记和主动私聊使用本段。日程模型由 schedule 配置。"""

    __ui_label__: ClassVar[str] = '空间与生活模型'
    __ui_icon__: ClassVar[str] = "cpu"
    __ui_order__: ClassVar[int] = 20

    text_model: str = Field(
        default="replyer",
        description='空间正文、评论、Routine 决策和生活生成使用的主程序模型任务名，如 replyer 或 utils；不是模型供应商名称。日程生成及裁判使用 schedule.llm_task_name。',
        json_schema_extra={'label': '空间/生活模型任务', 'hint': '空间正文、评论、Routine 决策和生活生成使用的主程序模型任务名，如 replyer 或 utils；不是模型供应商名称。日程生成及裁判使用 schedule.llm_task_name。', 'placeholder': 'replyer', 'order': 1, 'group': '模型'},
    )
    llm_timeout_seconds: int = Field(
        default=90,
        ge=10,
        le=600,
        description='空间、生活及聊天素材摘要的单次模型请求超时，单位秒，范围 10–600；修改后重载插件以同步组件预算。',
        json_schema_extra={'label': '单次模型超时（秒）', 'hint': '空间、生活及聊天素材摘要的单次模型请求超时，单位秒，范围 10–600；修改后重载插件以同步组件预算。', 'order': 2, 'group': '模型'},
    )
    show_prompt: bool = Field(
        default=False,
        description='在 INFO 日志打印空间正文、评论和回复的完整提示词；不控制生活模型日志或日程归档，日程归档由 schedule.llm_log_enabled 控制。',
        json_schema_extra={'label': '打印空间提示词', 'hint': '在 INFO 日志打印空间正文、评论和回复的完整提示词；不控制生活模型日志或日程归档，日程归档由 schedule.llm_log_enabled 控制。', 'order': 3, 'group': '模型'},
    )


    @field_validator("text_model")
    @classmethod
    def validate_text_model(cls, value: str) -> str:
        return model_task(value)


class PersonaSection(PluginConfigBase):
    """空间文案、生活素材与日程生成共用的人格扩展；基础人设读取 MaiBot 主程序配置。"""

    __ui_label__: ClassVar[str] = '人格与形象'
    __ui_icon__: ClassVar[str] = "user-plus"
    __ui_order__: ClassVar[int] = 10

    self_description: str = Field(
        default="",
        description='额外身份和外观描述，作用于空间、生活与日程模型背景。留空时读取已启用绘卷的 selfie.prompt_prefix；均未配置则省略。自拍参考图另由绘卷配置。',
        json_schema_extra={'label': '自我形象描述', 'hint': '额外身份和外观描述，作用于空间、生活与日程模型背景。留空时读取已启用绘卷的 selfie.prompt_prefix；均未配置则省略。自拍参考图另由绘卷配置。', 'placeholder': '我是银发红瞳的狐妖。', 'input_type': 'textarea', 'rows': 3, 'order': 1, 'group': '人格'},
    )
    use_multiple_reply_style: bool = Field(
        default=True,
        description='按主程序 personality.multiple_probability 从 multiple_reply_style 风格池抽样；关闭或风格池为空时使用 reply_style。',
        json_schema_extra={'label': '启用风格池抽样', 'hint': '按主程序 personality.multiple_probability 从 multiple_reply_style 风格池抽样；关闭或风格池为空时使用 reply_style。', 'order': 2, 'group': '人格'},
    )
