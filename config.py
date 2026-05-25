"""MaiTrace 配置模型（PluginConfigBase × N）。

每段一个 PluginConfigBase 子类。注意 [diary.model] 段名不能叫 `model_*`
（Pydantic v2 保留），所以重命名为 `diary_model`。

v3.1.x 起每个字段都通过 ``json_schema_extra`` 提供 UI 字段（label / hint / order
等），WebUI 实际渲染这些短文本；``description`` 保留长说明用于 schema 文档场景。
"""

from __future__ import annotations

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import AliasChoices


# ===== 默认 prompt 字面量（保留旧版完全一致） =====

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


# ===== Sections =====


class PluginSection(PluginConfigBase):
    """插件基础（Napcat 连接与 Cookie 获取）。"""

    __ui_label__: ClassVar[str] = "插件"
    __ui_icon__: ClassVar[str] = "info"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件。关闭后所有功能停止（发说说/刷空间/日记/Routine）。",
        json_schema_extra={"label": "启用插件", "hint": "关闭后所有功能停止", "order": 1},
    )
    config_version: str = Field(
        default="3.1.0",
        description="配置文件版本号，由 SDK 自动维护。请勿手动修改。",
        json_schema_extra={"label": "配置版本", "disabled": True, "order": 2},
    )
    http_host: str = Field(
        default="127.0.0.1",
        description="Napcat HTTP 服务器地址。本机部署填 127.0.0.1，Docker 部署常填 napcat。",
        json_schema_extra={
            "label": "Napcat 地址",
            "hint": "仅 cookie_methods 含 napcat 时用；Docker 填 napcat",
            "placeholder": "127.0.0.1",
            "order": 10,
        },
    )
    http_port: str = Field(
        default="9999",
        description="Napcat HTTP 服务器端口，需在 Napcat WebUI 中新建相同端口的 http 服务器。",
        json_schema_extra={
            "label": "Napcat 端口",
            "hint": "需与 Napcat WebUI 中 http 服务器配置一致",
            "placeholder": "9999",
            "order": 11,
        },
    )
    napcat_token: str = Field(
        default="",
        description="Napcat HTTP 服务的认证 Token。若 Napcat 设置了 Token 必须填，否则留空。",
        json_schema_extra={
            "label": "Napcat Token",
            "hint": "Napcat 未设置 Token 则留空；建议用纯 ASCII",
            "placeholder": "（留空 = 不认证）",
            "order": 12,
        },
    )
    cookie_methods: List[str] = Field(
        default_factory=lambda: ["adapter", "napcat", "clientkey", "qrcode", "local"],
        description=(
            "Cookie 获取顺序（按列表顺序逐个尝试）。v3.1 起按近期成功率自动重排"
            "（qrcode/local 永远在尾部）。\n"
            "可选项: adapter / napcat / clientkey / qrcode / local"
        ),
        json_schema_extra={
            "label": "Cookie 获取顺序",
            "hint": "adapter=napcat-adapter插件 / napcat=HTTP直连 / clientkey=本机QQ / qrcode=扫码 / local=本地缓存",
            "item_type": "string",
            "order": 20,
        },
    )
    admin_qq: List[str] = Field(
        default_factory=list,
        description=(
            "管理员 QQ 列表。**所有** /zn 命令（help / 主题 / custom / gen / ls / v / debug / "
            "<日期>）都要求调用者的 QQ 在此列表中。空列表 = 禁用所有命令。"
            "注意：这是命令权限，与 [send].permission / [read].permission（控制 @Tool 触发）独立。"
        ),
        json_schema_extra={
            "label": "管理员 QQ",
            "hint": '纯数字 QQ 号，例 ["123456"]。所有 /zn 命令都要求在此列表',
            "item_type": "string",
            "placeholder": '["123456"]',
            "order": 30,
        },
    )


class SendSection(PluginConfigBase):
    """发说说核心（/zn 命令、SendFeed Action、Routine 共用）。"""

    __ui_label__: ClassVar[str] = "发说说"
    __ui_icon__: ClassVar[str] = "send"
    __ui_order__: ClassVar[int] = 1

    permission: List[str] = Field(
        default_factory=lambda: ["114514", "1919810", "1523640161"],
        description="权限 QQ 号列表，控制谁能让麦麦发说说（/zn 系列命令 + SendFeed Action）。",
        json_schema_extra={
            "label": "授权 QQ",
            "hint": '纯数字 QQ 号，例 ["3082618311"]。不要带中文逗号或空格',
            "item_type": "string",
            "placeholder": '["123456"]',
            "order": 1,
        },
    )
    permission_type: Literal["whitelist", "blacklist"] = Field(
        default="whitelist",
        description="权限模式。whitelist=仅列表中有权限；blacklist=仅列表中无权限。",
        json_schema_extra={
            "label": "权限模式",
            "hint": "whitelist=只允许列表内 / blacklist=只禁止列表内",
            "order": 2,
        },
    )
    prompt: str = Field(
        default=_DEFAULT_SEND_PROMPT,
        description=(
            "生成说说的 prompt 模板。占位符：{current_time}, {bot_personality}, "
            "{bot_expression}, {topic}, {current_activity}"
        ),
        json_schema_extra={
            "label": "说说 prompt",
            "hint": "占位符: {current_time} {bot_personality} {bot_expression} {topic} {current_activity}",
            "rows": 8,
            "order": 10,
        },
    )
    history_number: int = Field(
        default=5, ge=0,
        description="生成说说时参考的历史说说数量。越多越能避免重复，但增加 token 消耗。",
        json_schema_extra={
            "label": "历史参考数",
            "hint": "0 = 不参考；越大越避免重复但越费 token",
            "order": 11,
        },
    )
    custom_qqaccount: str = Field(
        default="",
        description="/zn custom 模式从该 QQ 的私聊取最新内容作为说说。留空 = 禁用 custom 模式。",
        json_schema_extra={
            "label": "Custom QQ",
            "hint": "/zn custom 模式取该 QQ 的私聊内容，留空禁用",
            "placeholder": "（留空 = 禁用）",
            "order": 20,
        },
    )
    custom_only_mai: bool = Field(
        default=True,
        description="custom 模式取消息时只取麦麦自己说的（true）还是只取你说的（false）。",
        json_schema_extra={
            "label": "只取麦麦发言",
            "hint": "true=取麦麦在私聊里说的话 / false=取你说的话",
            "order": 21,
        },
    )


class ImageSection(PluginConfigBase):
    """配图配置（发说说时的图片来源、生成、清理）。"""

    __ui_label__: ClassVar[str] = "配图"
    __ui_icon__: ClassVar[str] = "image"
    __ui_order__: ClassVar[int] = 2

    enable_image: bool = Field(
        default=False,
        description="是否给说说附带图片。需 pic_plugin_model（AI 生图）或表情包至少一个可用。",
        json_schema_extra={
            "label": "启用配图",
            "hint": "需配 pic_plugin_model 或表情包，否则发纯文本兜底",
            "order": 1,
        },
    )
    image_mode: Literal["only_ai", "only_emoji", "random"] = Field(
        default="random",
        description="图片来源策略：only_ai=仅 AI 生图，only_emoji=仅表情包，random=按概率混合。",
        json_schema_extra={
            "label": "图片来源",
            "hint": "only_ai=仅AI / only_emoji=仅表情包 / random=按概率混合",
            "depends_on": "image.enable_image",
            "depends_value": True,
            "order": 2,
        },
    )
    ai_probability: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="仅 image_mode=random 时生效。每次出 AI 图的概率（0-1）。",
        json_schema_extra={
            "label": "AI 图概率",
            "hint": "仅 random 模式生效；0.7 = 70% 概率出 AI 图",
            "depends_on": "image.image_mode",
            "depends_value": "random",
            "order": 3,
        },
    )
    image_number: int = Field(
        default=1, ge=1, le=4,
        description="每条说说附带的图片数量（1-4）。多数 AI 生图一次出 1 张，>1 需确认模型支持。",
        json_schema_extra={
            "label": "图片数量",
            "hint": "1-4；多图模型仅部分支持（如 Kolors）",
            "depends_on": "image.enable_image",
            "depends_value": True,
            "order": 4,
        },
    )
    pic_plugin_model: str = Field(
        default="",
        description="麦麦绘卷（mais_art_journal）的生图模型 key，对应该插件 config.toml 的 models.<key>。",
        json_schema_extra={
            "label": "绘卷模型 key",
            "hint": '麦麦绘卷的 models.<key>，留空禁用 AI 生图',
            "placeholder": "model3",
            "depends_on": "image.enable_image",
            "depends_value": True,
            "order": 5,
        },
    )
    clear_image: bool = Field(
        default=True,
        description="AI 生图上传后是否删除本地 images/ 目录文件。true 节省磁盘，false 保留历史。",
        json_schema_extra={
            "label": "上传后清理",
            "hint": "true=节省磁盘 / false=保留所有生成历史",
            "depends_on": "image.enable_image",
            "depends_value": True,
            "order": 7,
        },
    )


class ReadSection(PluginConfigBase):
    """读说说配置（ReadFeed Action + 刷空间评论共用 prompt）。"""

    __ui_label__: ClassVar[str] = "读说说"
    __ui_icon__: ClassVar[str] = "book-open"
    __ui_order__: ClassVar[int] = 3

    permission: List[str] = Field(
        default_factory=lambda: ["114514", "1919810"],
        description="ReadFeed Action 的权限 QQ 列表，控制谁能让麦麦读某人的说说。",
        json_schema_extra={
            "label": "授权 QQ",
            "hint": '纯数字 QQ 号，例 ["123456", "789012"]',
            "item_type": "string",
            "placeholder": '["123456"]',
            "order": 1,
        },
    )
    permission_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description="权限模式。whitelist=仅列表中有权限；blacklist=仅列表中无权限。",
        json_schema_extra={
            "label": "权限模式",
            "hint": "whitelist=只允许列表内 / blacklist=只禁止列表内",
            "order": 2,
        },
    )
    read_number: int = Field(
        default=5, ge=1,
        description="一次读取该好友的最新说说数量。建议 3-10。",
        json_schema_extra={
            "label": "读取条数",
            "hint": "一次取多少条最新说说，建议 3-10",
            "order": 3,
        },
    )
    like_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="读到一条说说后点赞的概率（0-1，1.0 = 必点赞）。",
        json_schema_extra={
            "label": "点赞概率",
            "hint": "0-1，1.0 = 必点赞（旧名 like_possibility 仍可读）",
            "order": 4,
        },
        validation_alias=AliasChoices("like_probability", "like_possibility"),
    )
    comment_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="读到一条说说后评论的概率（0-1，1.0 = 必评论）。",
        json_schema_extra={
            "label": "评论概率",
            "hint": "0-1，1.0 = 必评论（旧名 comment_possibility 仍可读）",
            "order": 5,
        },
        validation_alias=AliasChoices("comment_probability", "comment_possibility"),
    )
    prompt: str = Field(
        default=_DEFAULT_READ_PROMPT,
        description=(
            "对【普通说说】评论的 prompt（也用于 Routine 刷空间评论）。"
            "占位符: {current_time}, {bot_personality}, {bot_expression}, "
            "{target_name}, {created_time}, {content}, {impression}"
        ),
        json_schema_extra={
            "label": "评论 prompt",
            "hint": "占位符: {target_name} {created_time} {content} {impression} {bot_personality} {bot_expression}",
            "rows": 8,
            "order": 10,
        },
    )
    rt_prompt: str = Field(
        default=_DEFAULT_RT_PROMPT,
        description="对【转发说说】评论的 prompt。占位符同 prompt，但多 {rt_con}（原始转发内容）。",
        json_schema_extra={
            "label": "转发评论 prompt",
            "hint": "占位符同 prompt + {rt_con}（被转发的原说说内容）",
            "rows": 8,
            "order": 11,
        },
    )


class MonitorSection(PluginConfigBase):
    """刷空间配置（Routine 自动刷空间时使用）。"""

    __ui_label__: ClassVar[str] = "刷空间"
    __ui_icon__: ClassVar[str] = "rss"
    __ui_order__: ClassVar[int] = 4

    read_list: List[str] = Field(
        default_factory=list,
        description="刷空间时优先/排除的好友 QQ 列表（配合 read_list_type 使用）。",
        json_schema_extra={
            "label": "目标好友名单",
            "hint": '纯数字 QQ 号，例 ["123", "456"]。空列表 + blacklist = 刷所有好友',
            "item_type": "string",
            "placeholder": "[]",
            "order": 1,
        },
    )
    read_list_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description="名单模式。whitelist=只对 read_list 里的好友刷；blacklist=不刷 read_list 里的、刷其他所有。",
        json_schema_extra={
            "label": "名单模式",
            "hint": "whitelist=只刷列表内 / blacklist=只跳过列表内",
            "order": 2,
        },
    )
    like_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="刷空间遇到一条新说说后点赞的概率（0-1）。",
        json_schema_extra={
            "label": "点赞概率",
            "hint": "0-1，1.0 = 必点赞（旧名 like_possibility 仍可读）",
            "order": 3,
        },
        validation_alias=AliasChoices("like_probability", "like_possibility"),
    )
    comment_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="刷空间遇到一条新说说后评论的概率。评论 prompt 复用 [read].prompt / rt_prompt。",
        json_schema_extra={
            "label": "评论概率",
            "hint": "0-1；prompt 复用 [read] 段的（旧名 comment_possibility 仍可读）",
            "order": 4,
        },
        validation_alias=AliasChoices("comment_probability", "comment_possibility"),
    )
    silent_hours: str = Field(
        default="22:00-07:00",
        description="不刷空间的时间段（24 小时制）。格式 HH:MM-HH:MM，多段用半角逗号分隔。留空 = 全天可刷。",
        json_schema_extra={
            "label": "静默时段",
            "hint": '例 "22:00-07:00" 或 "22:00-07:00,12:00-14:00"，留空=全天可刷',
            "placeholder": "22:00-07:00",
            "order": 10,
        },
    )
    like_during_silent: bool = Field(
        default=False,
        description="静默时段内是否仍允许点赞。",
        json_schema_extra={
            "label": "静默期允许点赞",
            "hint": "true=只点赞不评论（保持活跃但不打扰）",
            "depends_on": "monitor.silent_hours",
            "depends_value": True,
            "order": 11,
        },
    )
    comment_during_silent: bool = Field(
        default=False,
        description="静默时段内是否仍允许评论（一般保持 false，深夜评论显得突兀）。",
        json_schema_extra={
            "label": "静默期允许评论",
            "hint": "深夜评论显得突兀，建议保持 false",
            "depends_on": "monitor.silent_hours",
            "depends_value": True,
            "order": 12,
        },
    )
    enable_auto_reply: bool = Field(
        default=False,
        description="是否自动回复自己说说下的评论（开启后用 reply_prompt 生成回复）。",
        json_schema_extra={
            "label": "自动回复评论",
            "hint": "回复自己说说下的新评论；prompt 见 reply_prompt",
            "order": 20,
        },
    )
    self_read_number: int = Field(
        default=5, ge=1,
        description="检查评论的自己最新说说数量（仅 enable_auto_reply=true 时生效）。",
        json_schema_extra={
            "label": "自检条数",
            "hint": "检查麦麦最近 N 条说说的评论区（旧名 self_readnum 仍可读）",
            "depends_on": "monitor.enable_auto_reply",
            "depends_value": True,
            "order": 21,
        },
        validation_alias=AliasChoices("self_read_number", "self_readnum"),
    )
    reply_prompt: str = Field(
        default=_DEFAULT_REPLY_PROMPT,
        description=(
            "回复【自己说说下的评论】的 prompt。占位符: {current_time}, {bot_personality}, "
            "{bot_expression}, {nickname}, {created_time}, {content}, {comment_content}, {impression}"
        ),
        json_schema_extra={
            "label": "回复 prompt",
            "hint": "占位符: {nickname} {content} {comment_content} {impression} {bot_personality} {bot_expression}",
            "rows": 8,
            "depends_on": "monitor.enable_auto_reply",
            "depends_value": True,
            "order": 22,
        },
    )
    reply_to_reply_prompt: str = Field(
        default=_DEFAULT_REPLY_TO_REPLY_PROMPT,
        description=(
            "回复【他人空间中、对麦麦评论的回复】的 prompt（多层链式回复）。"
            "占位符: {bot_comment}, {reply_content}, 其余同 reply_prompt"
        ),
        json_schema_extra={
            "label": "链式回复 prompt",
            "hint": "占位符: {bot_comment}（麦麦原评论）{reply_content}（对方回复）+ 其他",
            "rows": 8,
            "order": 23,
        },
    )
    processed_feeds_cache_size: int = Field(
        default=100, ge=1,
        description="已处理说说的缓存上限（防内存无限增长）。100 通常够用。",
        json_schema_extra={
            "label": "说说缓存上限",
            "hint": "防内存增长；超出后丢弃最早记录，100 够用",
            "order": 30,
        },
    )
    processed_comments_cache_size: int = Field(
        default=100, ge=1,
        description="已处理评论的缓存上限（同 processed_feeds_cache_size）。",
        json_schema_extra={
            "label": "评论缓存上限",
            "hint": "同说说缓存上限，100 够用",
            "order": 31,
        },
    )


class RoutineSection(PluginConfigBase):
    """Routine 日程驱动配置（依赖 autonomous_planning_plugin）。

    Routine 是 MaiTrace 的"行为大脑"：定期读日程 → 让 LLM 决定要不要发说说/刷空间。
    """

    __ui_label__: ClassVar[str] = "日程驱动"
    __ui_icon__: ClassVar[str] = "clock"
    __ui_order__: ClassVar[int] = 5

    check_interval_minutes: int = Field(
        default=20, ge=1,
        description="Routine 检查间隔（分钟）。每隔 N 分钟读一次日程，让 LLM 决策是否行动。",
        json_schema_extra={
            "label": "检查间隔",
            "hint": "分钟；建议 20，调试可改 1-3",
            "order": 1,
        },
    )
    post_cooldown_minutes: int = Field(
        default=120, ge=1,
        description="两次发说说的最短间隔（分钟），冷却期内 LLM 决策直接跳过。",
        json_schema_extra={
            "label": "发说说冷却",
            "hint": "分钟；建议 120（两小时一条上限），调试可改 2-5",
            "order": 2,
        },
    )
    browse_cooldown_minutes: int = Field(
        default=40, ge=1,
        description="两次刷空间的最短间隔（分钟），冷却期内 LLM 决策直接跳过。",
        json_schema_extra={
            "label": "刷空间冷却",
            "hint": "分钟；建议 40（约半小时一次），调试可改 2-5",
            "order": 3,
        },
    )
    respect_silent_hours: bool = Field(
        default=True,
        description=(
            "是否让 [monitor].silent_hours 同时卡发说说和刷空间决策。"
            "true=深夜静默时段直接跳过，不调 LLM；false=只卡刷空间评论（旧行为）。"
        ),
        json_schema_extra={
            "label": "复用静默时段",
            "hint": "true=深夜也不发说说/刷空间",
            "order": 10,
        },
    )
    post_blocked_activities: List[str] = Field(
        default_factory=lambda: ["sleeping", "working", "studying", "eating", "exercising"],
        description=(
            "发说说时禁止的活动类型。命中直接跳过，不调 LLM。"
            "可选值（ActivityType 小写）：sleeping / waking_up / eating / working / studying / "
            "exercising / relaxing / socializing / commuting / hobby / self_care / other"
        ),
        json_schema_extra={
            "label": "发说说禁止活动",
            "hint": "命中直接跳过；ActivityType 小写",
            "item_type": "string",
            "order": 11,
        },
    )
    browse_blocked_activities: List[str] = Field(
        default_factory=lambda: ["sleeping", "working", "studying", "eating", "exercising"],
        description="刷空间时禁止的活动类型，格式同 post_blocked_activities。",
        json_schema_extra={
            "label": "刷空间禁止活动",
            "hint": "命中直接跳过；ActivityType 小写",
            "item_type": "string",
            "order": 12,
        },
    )
    max_post_chance: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description=(
            "LLM 通过后的二次概率上限（0-1）。1.0=LLM 说是就发；"
            "0.3=LLM 说是后还要 30% 概率才真发。降低这个值能让 LLM 偶尔误判时也不会狂发。"
        ),
        json_schema_extra={
            "label": "发说说概率上限",
            "hint": "0-1；1.0=不限制，0.3=LLM 通过后再掷骰",
            "order": 20,
        },
    )
    max_browse_chance: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="同 max_post_chance，作用于刷空间。",
        json_schema_extra={
            "label": "刷空间概率上限",
            "hint": "0-1；1.0=不限制",
            "order": 21,
        },
    )
    require_reason: bool = Field(
        default=True,
        description=(
            "是否要求 LLM 输出 '是|理由' / '否|理由' 格式。"
            "true=方便调试，/zn debug routine 能看到 LLM 给的理由；false=只回'是/否'。"
        ),
        json_schema_extra={
            "label": "要求 LLM 给理由",
            "hint": "true=便于 debug 查看决策原因",
            "order": 30,
        },
    )


class LLMSection(PluginConfigBase):
    """LLM 调用配置（v3.1 重命名：原 [models] → [llm]）。"""

    __ui_label__: ClassVar[str] = "LLM"
    __ui_icon__: ClassVar[str] = "cpu"
    __ui_order__: ClassVar[int] = 6

    text_model: str = Field(
        default="replyer",
        description=(
            "文本生成所用的模型 task 名（需在 MaiBot 主程序 config/model_config.toml 中存在）。"
            "可选: replyer / utils / utils_small / planner / vlm 等。"
        ),
        json_schema_extra={
            "label": "文本模型 task",
            "hint": "对应主程序 model_task_config 下的字段名（replyer / utils / planner ...）",
            "placeholder": "replyer",
            "order": 1,
        },
    )
    llm_timeout_seconds: int = Field(
        default=60, ge=10, le=600,
        description=(
            "单次 LLM 调用外层超时（秒）。⚠️ host RPC 桥接层有 30s 硬上限，"
            "本字段 > 30 时仍可能被 RPC 先触发 E_TIMEOUT。"
            "长 prompt（如日记）建议改走 [diary_model].use_custom_model 直连。"
        ),
        json_schema_extra={
            "label": "LLM 超时",
            "hint": "秒；⚠️ host RPC 桥接层 30s 硬上限，>30 仍会被切断",
            "order": 2,
        },
    )
    show_prompt: bool = Field(
        default=False,
        description="是否在日志（INFO 级别）打印每次发给 LLM 的 prompt 全文。",
        json_schema_extra={
            "label": "日志打印 prompt",
            "hint": "调试 prompt 模板时开，正式部署关",
            "order": 3,
        },
    )


class DiarySection(PluginConfigBase):
    """日记功能配置（权限复用 [send]）。

    日记 = 从聊天记录生成一篇当日总结，可手动 /zn gen 或定时自动生成。
    """

    __ui_label__: ClassVar[str] = "日记"
    __ui_icon__: ClassVar[str] = "book"
    __ui_order__: ClassVar[int] = 7

    enabled: bool = Field(
        default=False,
        description="是否启用日记功能（关闭后 /zn gen / ls / v 仍可用，只是不会在 schedule_time 自动生成）。",
        json_schema_extra={
            "label": "启用日记",
            "hint": "关闭后命令仍可用，只是不会定时自动生成",
            "order": 1,
        },
    )
    schedule_time: str = Field(
        default="23:30",
        description="每日自动生成日记的时间（24 小时制，HH:MM 格式）。",
        json_schema_extra={
            "label": "生成时间",
            "hint": "HH:MM 24 小时制",
            "placeholder": "23:30",
            "depends_on": "diary.enabled",
            "depends_value": True,
            "order": 2,
        },
    )
    style: Literal["diary", "qqzone", "custom"] = Field(
        default="diary",
        description="日记风格：diary=日记体 / qqzone=说说体 / custom=自定义模板。",
        json_schema_extra={
            "label": "日记风格",
            "hint": "diary=日记体 / qqzone=说说体 / custom=自定义",
            "order": 3,
        },
    )
    min_message_count: int = Field(
        default=3, ge=1,
        description="生成日记所需的最少消息数量（所有聊天合计）。少于此数则跳过当天日记。",
        json_schema_extra={
            "label": "最少消息数",
            "hint": "所有聊天合计；少于此数当天跳过",
            "order": 4,
        },
    )
    min_messages_per_chat: int = Field(
        default=3, ge=0,
        description="单聊天最少消息数。少于此数的聊天会被剔除（不参与日记生成）。0 = 不过滤。",
        json_schema_extra={
            "label": "单聊最少消息",
            "hint": "过滤零碎水群；0=不过滤",
            "order": 5,
        },
    )
    per_message_max_chars: int = Field(
        default=200, ge=0, le=2000,
        description=(
            "Timeline 中单条消息最大字符数，超出会截断为 \"<前缀>...\"。"
            "0 = 不截断（不推荐，长发言会吃 token）。原 v3.0 硬编码 50 过于激进。"
        ),
        json_schema_extra={
            "label": "单条消息截断",
            "hint": "0-2000；0=不截断，建议 200",
            "order": 6,
        },
    )
    min_word_count: int = Field(
        default=250, ge=20, le=8000,
        description="日记最少字数（不足会触发 LLM 重试/扩写）。",
        json_schema_extra={
            "label": "最少字数",
            "hint": "20-8000；不足会触发重试",
            "order": 10,
        },
    )
    max_word_count: int = Field(
        default=350, ge=20, le=8000,
        description="日记最多字数（超出会被智能截断，保留完整句子）。必须 ≥ min_word_count。",
        json_schema_extra={
            "label": "最多字数",
            "hint": "20-8000；超出会智能截断，必须 ≥ 最少字数",
            "order": 11,
        },
    )
    filter_mode: Literal["all", "whitelist", "blacklist"] = Field(
        default="all",
        description="消息过滤模式（配合 target_chats）。all=全部 / whitelist=只取列表 / blacklist=排除列表。",
        json_schema_extra={
            "label": "聊天过滤",
            "hint": "all=全部 / whitelist=只取 target_chats / blacklist=排除 target_chats",
            "order": 20,
        },
    )
    target_chats: str = Field(
        default="",
        description=(
            "目标聊天列表（多行字符串，每行一个）。格式: group:群号 或 private:QQ号。"
            "filter_mode=all 时被忽略。"
        ),
        json_schema_extra={
            "label": "目标聊天",
            "hint": "每行一个，例 group:123456 或 private:1523640161",
            "placeholder": "group:123456\nprivate:1523640161",
            "rows": 4,
            "depends_on": "diary.filter_mode",
            "depends_value": "whitelist",
            "order": 21,
        },
    )
    custom_prompt: str = Field(
        default="",
        description=(
            "自定义日记 prompt 模板（仅 style=custom 时生效）。"
            "占位符: {date}, {timeline}, {date_with_weather}, {target_length}, "
            "{personality_desc}, {style}, {name}"
        ),
        json_schema_extra={
            "label": "自定义 prompt",
            "hint": "占位符: {date} {timeline} {date_with_weather} {target_length} {personality_desc} {style} {name}",
            "rows": 8,
            "depends_on": "diary.style",
            "depends_value": "custom",
            "order": 30,
        },
    )


class PersonaSection(PluginConfigBase):
    """人格扩展配置（补主程序 [personality] 没覆盖的字段）。

    所有 LLM 写说说 / 评论 / 回复 / 日记的链路共用本段。
    """

    __ui_label__: ClassVar[str] = "人格扩展"
    __ui_icon__: ClassVar[str] = "user-plus"
    __ui_order__: ClassVar[int] = 9

    self_description: str = Field(
        default="",
        description=(
            "自我形象/身份描述（中文，可空）。会注入到所有 LLM prompt 的开头，"
            "让 LLM 在写说说/评论/日记时知道你的外观或额外身份。"
            "示例：\"我是银发红瞳的狐妖。\"\n\n"
            "**留空时自动从麦麦绘卷 (mais_art_journal) 的 [selfie].prompt_prefix 兜底**，"
            "无需任何额外开关；绘卷未安装 / [selfie].enabled=false / prompt_prefix 为空时跳过。\n\n"
            "配图时还会自动用绘卷 [selfie].reference_image_path 走图生图（如配置）。"
        ),
        json_schema_extra={
            "label": "自我形象描述",
            "hint": "中文一句话，留空时自动用绘卷 selfie.prompt_prefix",
            "placeholder": "我是银发红瞳的狐妖。",
            "input_type": "textarea",
            "rows": 3,
            "order": 1,
        },
    )
    use_multiple_reply_style: bool = Field(
        default=True,
        description=(
            "是否启用主程序 [personality].multiple_reply_style 风格池抽样。"
            "true=按 multiple_probability 概率从池中随机选一条替换 reply_style，"
            "与主程序聊天回复行为对齐。"
        ),
        json_schema_extra={
            "label": "启用风格池抽样",
            "hint": "true=与主程序回复一样按概率切风格",
            "order": 10,
        },
    )


class DiaryModelSection(PluginConfigBase):
    """日记自定义模型配置（绕过 host LLM、直连第三方 OpenAI 兼容 API）。

    日记 prompt 长（含整天聊天记录），常超过 host RPC 30s 超时。
    用自定义模型直连可避免该限制。
    """

    __ui_label__: ClassVar[str] = "日记自定义模型"
    __ui_icon__: ClassVar[str] = "zap"
    __ui_order__: ClassVar[int] = 8

    use_custom_model: bool = Field(
        default=False,
        description="是否启用自定义模型生成日记。false=走 ctx.llm.generate；true=直连 OpenAI 兼容 API。",
        json_schema_extra={
            "label": "启用自定义模型",
            "hint": "true=直连 OpenAI 兼容 API（推荐用于长 prompt）",
            "order": 1,
        },
    )
    api_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        description="OpenAI 兼容 API 基础地址（不含 /chat/completions 后缀）。仅支持 OpenAI 协议。",
        json_schema_extra={
            "label": "API 地址",
            "hint": "基础 URL（不含 /chat/completions）；仅支持 OpenAI 协议",
            "placeholder": "https://api.siliconflow.cn/v1",
            "depends_on": "diary_model.use_custom_model",
            "depends_value": True,
            "order": 2,
        },
    )
    api_key: str = Field(
        default="",
        description="API 密钥（建议用环境变量或 secrets 管理，不要明文提交到 git）。",
        json_schema_extra={
            "label": "API 密钥",
            "hint": "建议用环境变量管理；留空会让日记生成失败",
            "placeholder": "sk-...",
            "depends_on": "diary_model.use_custom_model",
            "depends_value": True,
            "input_type": "password",
            "order": 3,
        },
    )
    model_name: str = Field(
        default="Pro/deepseek-ai/DeepSeek-V3",
        description="模型名称，跟服务商提供的 model 字段对齐。",
        json_schema_extra={
            "label": "模型名称",
            "hint": '例 "Pro/deepseek-ai/DeepSeek-V3" / "gpt-4o-mini" / "moonshot-v1-32k"',
            "placeholder": "Pro/deepseek-ai/DeepSeek-V3",
            "depends_on": "diary_model.use_custom_model",
            "depends_value": True,
            "order": 4,
        },
    )
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0,
        description="生成温度（0-2）。0=完全确定，1=平衡，>1=更随机。日记建议 0.6-0.8。",
        json_schema_extra={
            "label": "温度",
            "hint": "0-2；日记建议 0.6-0.8",
            "depends_on": "diary_model.use_custom_model",
            "depends_value": True,
            "order": 5,
        },
    )
    api_timeout: int = Field(
        default=300, ge=1, le=6000,
        description="API 调用超时（秒）。日记 prompt 长时建议设大（300-600）。",
        json_schema_extra={
            "label": "API 超时",
            "hint": "秒；长聊天建议 300-600",
            "depends_on": "diary_model.use_custom_model",
            "depends_value": True,
            "order": 6,
        },
    )


# ===== 顶层 =====


class MaiTracePluginConfig(PluginConfigBase):
    """MaiTrace 顶层配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    send: SendSection = Field(default_factory=SendSection)
    image: ImageSection = Field(default_factory=ImageSection)
    read: ReadSection = Field(default_factory=ReadSection)
    monitor: MonitorSection = Field(default_factory=MonitorSection)
    routine: RoutineSection = Field(default_factory=RoutineSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    diary: DiarySection = Field(default_factory=DiarySection)
    persona: PersonaSection = Field(default_factory=PersonaSection)
    diary_model: DiaryModelSection = Field(default_factory=DiaryModelSection)
