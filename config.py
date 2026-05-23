"""MaiTrace 配置模型（PluginConfigBase × N）。

每段一个 PluginConfigBase 子类。注意 [diary.model] 段名不能叫 `model_*`
（Pydantic v2 保留），所以重命名为 `diary_model`。
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

_DEFAULT_IMAGE_PROMPT = (
    "请根据以下QQ空间说说内容配图，并构建生成配图的风格和prompt。说说主人信息：'{personality}'。说说内容:'{"
    "message}'。请注意：仅回复用于生成图片的prompt，不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"
)


# ===== Sections =====


class PluginSection(PluginConfigBase):
    """插件基础（Napcat 连接与 Cookie 获取）。"""

    __ui_label__: ClassVar[str] = "插件"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件。关闭后所有功能停止（发说说/刷空间/日记/Routine）。",
    )
    config_version: str = Field(
        default="3.1.0",
        description="配置文件版本号，由 SDK 自动维护。请勿手动修改，否则可能跳过自动迁移。",
    )
    http_host: str = Field(
        default="127.0.0.1",
        description=(
            "Napcat HTTP 服务器地址。仅在 cookie_methods 包含 napcat 时使用。"
            "本机部署填 127.0.0.1，Docker 部署常填 napcat（与容器名一致）。"
        ),
    )
    http_port: str = Field(
        default="9999",
        description=(
            "Napcat HTTP 服务器端口（仅 cookie_methods 包含 napcat 时使用）。"
            "需在 Napcat WebUI 中新建 http 服务器并填写相同端口；与 9999 冲突时改成其他空闲端口。"
        ),
    )
    napcat_token: str = Field(
        default="",
        description=(
            "Napcat HTTP 服务的认证 Token。若 Napcat 设置了 Token 必须填，"
            "否则留空。注意：用纯 ASCII，特殊字符容易在 toml 引号里出问题。"
        ),
    )
    cookie_methods: List[str] = Field(
        default_factory=lambda: ["adapter", "napcat", "clientkey", "qrcode", "local"],
        description=(
            "Cookie 获取顺序（按列表顺序逐个尝试）。可选项：\n"
            "  adapter   - 通过 napcat-adapter 插件 API（推荐，无需配 HTTP）\n"
            "  napcat    - 直接调 Napcat HTTP /get_cookies（需配 http_host/port）\n"
            "  clientkey - 通过本机 QQ 客户端 clientkey 换 cookie（需 QQ 在同机）\n"
            "  qrcode    - 扫码登录（data/qrcode.png，有效期约 1 天）\n"
            "  local     - 读 data/cookies-<uin>.json 缓存\n"
            "v3.1 起按近期成功率自动重排（qrcode/local 永远在尾部）。"
        ),
    )


class SendSection(PluginConfigBase):
    """发说说核心（/zn 命令、SendFeed Action、Routine 共用）。

    图片相关已分到 [image] 段。
    """

    __ui_label__: ClassVar[str] = "发说说"
    __ui_order__: ClassVar[int] = 1

    permission: List[str] = Field(
        default_factory=lambda: ["114514", "1919810", "1523640161"],
        description=(
            "权限 QQ 号列表，控制谁能让麦麦发说说（包括 /zn <主题>、/zn custom、/zn gen、/zn ls、SendFeed Action）。"
            '注意：QQ 号要用半角引号包裹纯数字，例 ["3082618311"]。不要带中文逗号、空格或其他字符。'
        ),
    )
    permission_type: Literal["whitelist", "blacklist"] = Field(
        default="whitelist",
        description=(
            "权限模式。whitelist=只有 permission 列表里的 QQ 能用；"
            "blacklist=permission 列表里的 QQ 不能用、其他都能用。"
        ),
    )
    prompt: str = Field(
        default=_DEFAULT_SEND_PROMPT,
        description=(
            "生成说说的 prompt 模板。占位符（{xxx} 会被替换）：\n"
            "  {current_time}     当前时间（HH:MM）\n"
            "  {bot_personality}  人格描述（来自 MaiBot 全局 personality.personality）\n"
            "  {bot_expression}   表达风格（来自 personality.reply_style）\n"
            "  {topic}            说说主题（由命令参数或 Action 传入）\n"
            "  {current_activity} 当前活动（Routine 模式下来自日程，其他情况为空）"
        ),
    )
    history_number: int = Field(
        default=5, ge=0,
        description=(
            "生成说说时参考的历史说说数量（取麦麦最近的 N 条放进 prompt 上下文）。"
            "越多越能避免重复内容，但增加 token 消耗。设 0 = 不参考历史。"
        ),
    )
    custom_qqaccount: str = Field(
        default="",
        description=(
            "/zn custom 模式从该 QQ 的私聊取最新内容作为说说。留空则禁用 custom 模式。"
            "通常填你自己的 QQ 号，方便用私聊小作文一键转发空间。"
        ),
    )
    custom_only_mai: bool = Field(
        default=True,
        description=(
            "custom 模式取消息时只取麦麦自己说的（true）还是只取你说的（false）。"
            "true：把麦麦在私聊里说的话当说说发；false：把你在私聊说的话当说说发。"
        ),
    )


class ImageSection(PluginConfigBase):
    """配图配置（发说说时的图片来源、生成、清理）。

    v3.1 新段：从原 [send] 与原 [models] 各搬一些图片相关字段过来。
    """

    __ui_label__: ClassVar[str] = "配图"
    __ui_order__: ClassVar[int] = 2

    enable_image: bool = Field(
        default=False,
        description=(
            "是否给说说附带图片。开启前请确认下面 image_mode + pic_plugin_model（AI 生图）"
            "或已注册的表情包（emoji 路径）至少有一个可用，否则会发纯文本兜底。"
        ),
    )
    image_mode: Literal["only_ai", "only_emoji", "random"] = Field(
        default="random",
        description=(
            "图片来源策略：\n"
            "  only_ai    - 仅用麦麦绘卷生成的 AI 图（需配 pic_plugin_model）\n"
            "  only_emoji - 仅从已注册表情包中随机选\n"
            "  random     - 按 ai_probability 概率混合两者"
        ),
    )
    ai_probability: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description=(
            "仅 image_mode=random 时生效。每次生成时使用 AI 图的概率（0-1，例 0.7 = 70% 概率出 AI 图）。"
            "image_mode=only_ai/only_emoji 时此项被忽略。"
        ),
    )
    image_number: int = Field(
        default=1, ge=1, le=4,
        description=(
            "每条说说附带的图片数量（1-4）。注意：多数 AI 生图模型一次只出 1 张，"
            "只有 Kolors 等少数模型支持多图，要 >1 前请确认 pic_plugin_model 支持。"
        ),
    )
    pic_plugin_model: str = Field(
        default="",
        description=(
            "麦麦绘卷（mais_art_journal 插件）的生图模型 key，对应该插件 config.toml 的 models.<key>。"
            '示例："model1" / "model3"。留空 = 禁用 AI 生图（image_mode=only_ai 时会回退发纯文本）。'
        ),
    )
    image_prompt: str = Field(
        default=_DEFAULT_IMAGE_PROMPT,
        description=(
            "AI 生图提示词模板（当麦麦绘卷的 PromptOptimizer 不可用时作为备选）。\n"
            "占位符：\n"
            "  {personality}  说说主人（麦麦）的人格描述\n"
            "  {message}      要配图的说说文本\n"
            "  {current_time} 当前时间"
        ),
    )
    clear_image: bool = Field(
        default=True,
        description=(
            "AI 生图上传成功后是否删除本地 images/ 目录下的图片文件。"
            "true = 节省磁盘空间；false = 保留所有生成结果便于查看历史。"
        ),
    )


class ReadSection(PluginConfigBase):
    """读说说配置（ReadFeed Action 与刷空间评论共用 prompt）。"""

    __ui_label__: ClassVar[str] = "读说说"
    __ui_order__: ClassVar[int] = 3

    permission: List[str] = Field(
        default_factory=lambda: ["114514", "1919810"],
        description=(
            "ReadFeed Action 的权限 QQ 列表（控制谁能让麦麦读某人的说说）。"
            '例 ["123456", "789012"]，仅填纯数字 QQ 号。'
        ),
    )
    permission_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description=(
            "读说说权限模式。whitelist=只有列表里的 QQ 能用；"
            "blacklist=列表里的 QQ 不能用、其他都能用。"
        ),
    )
    read_number: int = Field(
        default=5, ge=1,
        description="一次读取该好友的最新说说数量。越多耗时越长但覆盖越全，建议 3-10。",
    )
    like_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description=(
            "读到一条说说后点赞的概率（0-1，1.0 = 必点赞）。"
            "v3.1 重命名：旧名 like_possibility 仍可读，但建议改成新名。"
        ),
        validation_alias=AliasChoices("like_probability", "like_possibility"),
    )
    comment_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description=(
            "读到一条说说后评论的概率（0-1，1.0 = 必评论）。"
            "v3.1 重命名：旧名 comment_possibility 仍可读，但建议改成新名。"
        ),
        validation_alias=AliasChoices("comment_probability", "comment_possibility"),
    )
    prompt: str = Field(
        default=_DEFAULT_READ_PROMPT,
        description=(
            "对【普通说说】评论的 prompt 模板（也用于 Routine 刷空间评论好友）。\n"
            "占位符：\n"
            "  {current_time}     当前时间\n"
            "  {bot_personality}  人格\n"
            "  {bot_expression}   表达风格\n"
            "  {target_name}      说说主人昵称\n"
            "  {created_time}     说说发布时间\n"
            "  {content}          说说内容\n"
            "  {impression}       麦麦对说说主人的印象（来自 PersonInfo.memory_points）"
        ),
    )
    rt_prompt: str = Field(
        default=_DEFAULT_RT_PROMPT,
        description=(
            "对【转发的说说】评论的 prompt（占位符同 prompt，但多一个）：\n"
            "  {rt_con}  原始转发内容（被转发的原说说）"
        ),
    )


class MonitorSection(PluginConfigBase):
    """刷空间配置（Routine 自动刷空间时使用，由 LLM 决定何时执行）。"""

    __ui_label__: ClassVar[str] = "刷空间"
    __ui_order__: ClassVar[int] = 4

    read_list: List[str] = Field(
        default_factory=list,
        description=(
            "刷空间时优先/排除的好友 QQ 列表（配合 read_list_type 使用）。"
            '例 ["123", "456"]。空列表 + blacklist = 对所有好友刷空间。'
        ),
    )
    read_list_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description=(
            "刷空间名单模式。whitelist=只对 read_list 里的好友刷；"
            "blacklist=不刷 read_list 里的、刷其他所有好友。"
        ),
    )
    like_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description=(
            "刷空间遇到一条新说说后点赞的概率（0-1）。"
            "v3.1 重命名：旧名 like_possibility 仍可读。"
        ),
        validation_alias=AliasChoices("like_probability", "like_possibility"),
    )
    comment_probability: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description=(
            "刷空间遇到一条新说说后评论的概率（0-1）。评论 prompt 复用 [read].prompt / [read].rt_prompt。"
            "v3.1 重命名：旧名 comment_possibility 仍可读。"
        ),
        validation_alias=AliasChoices("comment_probability", "comment_possibility"),
    )
    silent_hours: str = Field(
        default="22:00-07:00",
        description=(
            "不刷空间的时间段（24 小时制）。格式 HH:MM-HH:MM，多段用半角逗号分隔。\n"
            '例："22:00-07:00"（夜里不刷）、"22:00-07:00,12:00-14:00"（夜里和午休都不刷）。\n'
            "留空 = 全天可刷。跨天用单段表示，如 22:00-07:00。"
        ),
    )
    like_during_silent: bool = Field(
        default=False,
        description=(
            "静默时段内是否仍允许点赞。false=完全不动；true=只点赞不评论（适合保持活跃度但不打扰）。"
            "需配合 silent_hours 使用，silent_hours 为空时此项无效。"
        ),
    )
    comment_during_silent: bool = Field(
        default=False,
        description=(
            "静默时段内是否仍允许评论。一般保持 false，深夜评论会显得突兀。"
            "需配合 silent_hours 使用，silent_hours 为空时此项无效。"
        ),
    )
    enable_auto_reply: bool = Field(
        default=False,
        description=(
            "是否自动回复自己说说下的评论。开启后：刷空间时检查麦麦自己的最近 self_read_number 条说说，"
            "若有新评论会用 reply_prompt 生成回复并发出去。"
        ),
    )
    self_read_number: int = Field(
        default=5, ge=1,
        description=(
            "回复自己说说评论时，检查麦麦最近多少条说说的评论区（仅 enable_auto_reply=true 时生效）。"
            "v3.1 重命名：旧名 self_readnum 仍可读，但建议改成新名。"
        ),
        validation_alias=AliasChoices("self_read_number", "self_readnum"),
    )
    reply_prompt: str = Field(
        default=_DEFAULT_REPLY_PROMPT,
        description=(
            "回复【自己说说下的评论】的 prompt 模板。占位符：\n"
            "  {current_time}     当前时间\n"
            "  {bot_personality}  人格\n"
            "  {bot_expression}   表达风格\n"
            "  {nickname}         评论者昵称\n"
            "  {created_time}     评论时间\n"
            "  {content}          原说说内容\n"
            "  {comment_content}  对方的评论内容\n"
            "  {impression}       对评论者的印象"
        ),
    )
    reply_to_reply_prompt: str = Field(
        default=_DEFAULT_REPLY_TO_REPLY_PROMPT,
        description=(
            "回复【他人空间中、对麦麦评论的回复】的 prompt（多层链式回复场景）。占位符：\n"
            "  {bot_comment}     麦麦原先发的那条评论\n"
            "  {reply_content}   对方对麦麦评论的回复\n"
            "  其他占位符同 reply_prompt"
        ),
    )
    processed_feeds_cache_size: int = Field(
        default=100, ge=1,
        description=(
            "已处理说说的缓存上限（防内存无限增长）。超过后丢弃最早的记录，"
            "已丢弃的说说有可能被再次评论一次。100 通常够用。"
        ),
    )
    processed_comments_cache_size: int = Field(
        default=100, ge=1,
        description=(
            "已处理评论的缓存上限（同 processed_feeds_cache_size）。"
            "对应 data/processed_comments.json。"
        ),
    )


class RoutineSection(PluginConfigBase):
    """Routine 日程驱动配置（依赖 autonomous_planning_plugin 提供日程）。

    Routine 是 MaiTrace 的"行为大脑"：定期读日程 → 让 LLM 决定要不要发说说/刷空间。
    """

    __ui_label__: ClassVar[str] = "日程驱动"
    __ui_order__: ClassVar[int] = 5

    check_interval_minutes: int = Field(
        default=20, ge=1,
        description=(
            "Routine 检查间隔（分钟）。每隔 N 分钟读一次日程，让 LLM 决策是否行动。"
            "建议 20（保持活跃但不频繁）；调试时可改 1-3 加快观察。"
        ),
    )
    post_cooldown_minutes: int = Field(
        default=120, ge=1,
        description=(
            "两次发说说的最短间隔（分钟），冷却期内 LLM 决策直接跳过。"
            "120 = 两小时一条上限；调试可改 2-5。"
        ),
    )
    browse_cooldown_minutes: int = Field(
        default=40, ge=1,
        description=(
            "两次刷空间的最短间隔（分钟），冷却期内 LLM 决策直接跳过。"
            "40 = 约半小时一次；调试可改 2-5。"
        ),
    )


class LLMSection(PluginConfigBase):
    """LLM 调用配置（v3.1 重命名：原 [models] → [llm]，图片相关已分到 [image]）。"""

    __ui_label__: ClassVar[str] = "LLM"
    __ui_order__: ClassVar[int] = 6

    text_model: str = Field(
        default="replyer",
        description=(
            "文本生成所用的模型 task 名（必须在 MaiBot 主程序 config/model_config.toml 里有对应的 [model_task_config.xxx] 段）。\n"
            "可选: replyer(默认，首要回复模型) / utils / utils_small / planner / vlm 等。"
        ),
    )
    llm_timeout_seconds: int = Field(
        default=60, ge=10, le=600,
        description=(
            "单次 LLM 调用外层超时（秒）。超过会返回失败、不阻塞主循环。\n"
            "⚠️ 注意：host RPC 桥接层有 30s 硬上限，本字段 > 30 时仍可能被 RPC 先触发 E_TIMEOUT。\n"
            "长 prompt（如日记）建议改走 [diary_model].use_custom_model 直连。"
        ),
    )
    show_prompt: bool = Field(
        default=False,
        description=(
            "是否在日志（INFO 级别）打印每次发给 LLM 的 prompt 全文。"
            "调试 prompt 模板时开，正式部署关，否则日志膨胀。"
        ),
    )


class DiarySection(PluginConfigBase):
    """日记功能配置（权限复用 [send]）。

    日记 = 从聊天记录生成一篇当日总结，可手动 /zn gen 或定时自动生成。
    """

    __ui_label__: ClassVar[str] = "日记"
    __ui_order__: ClassVar[int] = 7

    enabled: bool = Field(
        default=False,
        description=(
            "是否启用日记功能。关闭后 /zn gen / /zn ls / /zn v 仍可用，"
            "只是不会在 schedule_time 自动生成。"
        ),
    )
    schedule_time: str = Field(
        default="23:30",
        description=(
            "每日自动生成日记的时间（24 小时制，HH:MM 格式）。"
            "Routine 在该时间点触发一次生成；当天已生成过则跳过。"
        ),
    )
    style: Literal["diary", "qqzone", "custom"] = Field(
        default="diary",
        description=(
            "日记风格：\n"
            "  diary  - 日记体（第一人称、内心独白）\n"
            "  qqzone - 说说体（短、口语、适合发空间）\n"
            "  custom - 自定义模板（用下面的 custom_prompt）"
        ),
    )
    min_message_count: int = Field(
        default=3, ge=1,
        description=(
            "生成日记所需的最少消息数量（所有聊天合计）。少于此数则跳过当天日记。"
            "防止聊天太少时生成空洞的日记。"
        ),
    )
    min_messages_per_chat: int = Field(
        default=3, ge=0,
        description=(
            "单个聊天的最少消息数。小于此数的聊天会被整个剔除（不参与日记生成）。"
            "用来过滤零碎水群，让 LLM 上下文更干净。"
            "0 = 不过滤；3 = 一个群至少 3 条消息才参与。"
        ),
    )
    min_word_count: int = Field(
        default=250, ge=20, le=8000,
        description="日记最少字数（生成的日记若不足会触发 LLM 重试/扩写）。范围 20-8000。",
    )
    max_word_count: int = Field(
        default=350, ge=20, le=8000,
        description=(
            "日记最多字数（生成超出会被智能截断，保留完整句子）。范围 20-8000，必须 ≥ min_word_count。"
        ),
    )
    filter_mode: Literal["all", "whitelist", "blacklist"] = Field(
        default="all",
        description=(
            "消息过滤模式（配合 target_chats 使用）：\n"
            "  all       - 所有聊天消息都参与日记生成\n"
            "  whitelist - 仅 target_chats 列出的聊天参与\n"
            "  blacklist - 排除 target_chats 列出的、其他都参与"
        ),
    )
    target_chats: str = Field(
        default="",
        description=(
            "目标聊天列表（多行字符串，每行一个）。格式：\n"
            "  group:群号    - 例 group:123456789\n"
            "  private:QQ号  - 例 private:1523640161\n"
            "filter_mode=all 时此字段被忽略。"
        ),
    )
    custom_prompt: str = Field(
        default="",
        description=(
            "自定义日记 prompt 模板（仅 style=custom 时生效）。占位符：\n"
            "  {date}              日期（YYYY-MM-DD）\n"
            "  {timeline}          当日聊天时间线\n"
            "  {date_with_weather} 带天气的日期\n"
            "  {target_length}     目标字数（min/max 间随机）\n"
            "  {personality_desc}  人格描述\n"
            "  {style}             表达风格\n"
            "  {name}              麦麦昵称"
        ),
    )


class DiaryModelSection(PluginConfigBase):
    """日记自定义模型配置（绕过 host LLM、直连第三方 OpenAI 兼容 API）。

    日记 prompt 长（含整天聊天记录），常超过 host RPC 30s 超时。
    用自定义模型直连可避免该限制。section 名故意叫 diary_model 而非 diary.model
    （Pydantic v2 保留 model_*）。
    """

    __ui_label__: ClassVar[str] = "日记自定义模型"
    __ui_order__: ClassVar[int] = 8

    use_custom_model: bool = Field(
        default=False,
        description=(
            "是否启用自定义模型生成日记。\n"
            "false = 走 ctx.llm.generate（用 [llm].text_model 配置的 task）；\n"
            "true = 直连下面配的 OpenAI 兼容 API（推荐用于长 prompt）。"
        ),
    )
    api_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        description=(
            "OpenAI 兼容 API 基础地址（不含 /chat/completions 后缀）。\n"
            "  ✓ 正确：http://example.com/v1\n"
            "  ✗ 错误：http://example.com/v1/chat/completions\n"
            "仅支持 OpenAI 协议，不支持 Gemini/Claude 原生格式。"
        ),
    )
    api_key: str = Field(
        default="",
        description=(
            "API 密钥（建议用环境变量或独立的 secrets 管理，不要明文提交到 git）。"
            "留空会让生成日记直接失败。"
        ),
    )
    model_name: str = Field(
        default="Pro/deepseek-ai/DeepSeek-V3",
        description=(
            "模型名称，跟服务商提供的 model 字段对齐。\n"
            '示例：硅基流动 "Pro/deepseek-ai/DeepSeek-V3"、'
            'OpenAI "gpt-4o-mini"、月之暗面 "moonshot-v1-32k"。'
        ),
    )
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0,
        description=(
            "生成温度（0-2）。0 = 完全确定、1 = 平衡、>1 = 更随机。"
            "日记建议 0.6-0.8。"
        ),
    )
    api_timeout: int = Field(
        default=300, ge=1, le=6000,
        description=(
            "API 调用超时（秒）。日记 prompt 长，聊天记录多时建议设大（300-600）。"
            "若仍超时可在主程序 config/model_config.toml 中调大全局 timeout。"
        ),
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
    diary_model: DiaryModelSection = Field(default_factory=DiaryModelSection)
