# 麦时迹 (MaiTrace) - 生活与 QQ 空间插件

MaiBot 的生活与 QQ 空间插件：让麦麦拥有每日作息、精力心情、睡眠梦境，并在合适的时候分享近况。

## ✨ 主要特性

### 🗓️ 日程与约定

- **每日作息**：生成每日活动安排，支持跨夜活动和次日策略
- **临时约定**：按人设接受或拒绝约定，检查日期、时长和活动冲突
- **聊天背景**：向 Planner 和 Replyer 提供当前日程与生活背景
- **主动聊天与提醒**：在允许的会话中分享话题、提醒约定，并按活动调节聊天频率

### 🌙 生活与陪伴

- **生活状态**：持续推进精力、饥饿、心情与睡眠状态
- **场景与梦境**：细化当前活动场景，醒来后生成私人梦境
- **私人日记**：整理自身生活，支持主人在本人私聊中阅读
- **重要日期与主动私聊**：记录生日等日期，根据用户许可选择联系时机
- **本地历法**：提供节假日、农历和节气；旧聊天日记仍可阅读

### 🌐 QQ 空间与配图

- **主题发帖**：按主题生成说说，也可直接发布指定私聊的最新正文
- **空间互动**：浏览、点赞、评论、回复及回忆曾参与的空间活动
- **自动分享**：根据当前日程、生活素材、静默时段和冷却决定是否发帖或浏览
- **可选配图**：调用[麦麦绘卷](https://github.com/1021143806/mais_art_journal)生成自拍或普通图片，也可使用表情包
- **跨插件接口**：提供空间发布、日程、生活状态、场景和历法等公开 API

### 🎛️ /zn 命令系统

所有命令均需 `plugin.admin_qq` 授权，完整用法见 `/zn help`。

#### 空间发布与帮助

| 命令 | 说明 |
| --- | --- |
| `/zn help`、`/zn plan help`、`/zn debug help` | 查看对应帮助 |
| `/zn <主题>` | 生成并发布说说 |
| `/zn custom` | 发布 `send.custom_qqaccount` 指定私聊的最新正文 |

#### 日程与约定

| 命令 | 说明 |
| --- | --- |
| `/zn plan status`、`/zn plan list` | 查看作息文本或图片 |
| `/zn plan generate`、`/zn plan regenerate [要求]` | 补生成或重新生成今日作息 |
| `/zn plan update <请求>` | 添加约定，如“明天18:00一起吃饭” |
| `/zn plan delete <ID或序号>`、`/zn plan clear [天数]` | 删除活动或清理旧作息 |

#### 生活与重要日期

| 命令 | 说明 |
| --- | --- |
| `/zn status`、`/zn life` | 查看生活状态与场景 |
| `/zn life diary` | 阅读私人生活日记 |
| `/zn dates list`、`/zn dates add YYYY-MM-DD <名称>` | 查看或添加本人重要日期 |
| `/zn dates confirm <ID>`、`/zn dates delete <ID>` | 确认候选或删除手动日期 |
| `/zn ls`、`/zn v [日期] [编号]` | 阅读旧聊天日记 |

#### 运行状态与排查

| 命令 | 说明 |
| --- | --- |
| `/zn config` | 查看生活、消息、主动私聊和历法的开关状态 |
| `/zn debug cookie`、`/zn debug routine` | 检查登录与自动行为 |

当日约定只插入空档，冲突会给出可选时间；未来约定不限制天数。其他会话只看到占用时段，具体约定仅向提出约定的会话展示。

旧聊天日记日期支持 `YYYY-MM-DD`、`YYYY/MM/DD`、`YYYY.MM.DD`、今天、昨天、前天，`/zn <日期>` 等价于 `/zn v <日期>`。`/zn life diary` 需在主人私聊使用；`/zn dates` 需在本人私聊使用，并启用对应用户档案。`/zn plan clear [天数]` 的参数表示保留天数，默认 0，保留今天及仍在持续的跨夜活动。

### 🤖 自然聊天工具

模型可在自然聊天中调用以下工具；例如“发一条关于晚霞的说说”“看看某某最近的空间”“明天18点一起吃饭”。是否调用由主程序模型决定。

| Tool | 用途 | 权限与范围 |
| --- | --- | --- |
| `send_feed` | 根据主题生成正文、配图并发布 | `send.permission`，默认空白名单禁用 |
| `read_feed` | 读取指定好友动态并按配置点赞、评论 | `read.permission`，默认空黑名单允许真实 QQ 调用者 |
| `recall_qzone_activity` | 按关键词回忆自己发布、评论和回复过的内容 | 共用 `read.permission`，返回条数默认 10，范围 1–50 |
| `get_planning_status_v6` | 查询今天的作息，缺少时可补生成 | 日程开关及 `schedule.allowed_streams` |
| `generate_schedule_v6` | 生成今日作息，已有日程时直接返回 | 日程开关及 `schedule.allowed_streams` |
| `update_schedule_v6` | 按人设接受或拒绝约定，检查日期、时长及冲突 | 日程开关及 `schedule.allowed_streams` |

---

## 支持的 QQ 适配器

| 适配器 | 发布方式 |
| --- | --- |
| SnowLuma | 直接调用空间 API，支持文字和多图 |
| NapCat | 获取 Cookie 后发布 |
| QQ 官方机器人 | 需额外连接 SnowLuma/NapCat，或提供真实 QQ 号并扫码登录空间 |

Cookie 默认依次尝试适配器、OneBot HTTP、本机 QQ、扫码和缓存。OneBot HTTP 默认 `127.0.0.1:9999`，认证填写 `plugin.http_token`。登录问题可用 `/zn debug cookie` 排查。

---

## 🚀 快速开始

1. 使用终端或 Git Bash 进入 MaiBot 的插件目录：

   ```shell
   cd MaiBot/plugins
   ```

2. 克隆本仓库，指定目录名为 `MaiTrace`：

   ```shell
   git clone https://github.com/Rabbit-Jia-Er/MaiTrace.git MaiTrace
   ```

3. 主程序负责安装依赖；需要手动安装时，在 MaiBot 使用的 Python 环境中执行：

   ```shell
   uv pip install -r MaiTrace/requirements.txt
   ```

4. 启动或重启 MaiBot，在 WebUI 或插件 `config.toml` 中填写配置。
5. 按下方说明填写管理员 QQ、空间登录和需要启用的功能，保存后重载插件，用 `/zn help` 查看命令。

模型使用主程序的任务名，如 `replyer`、`utils`。

## 配置说明

下面按 `config.toml` 的配置段说明字段、默认值和使用条件，也可在 WebUI 中修改。表中的字段名均属于所在配置段，例如 `[schedule]` 的 `enabled` 对应 `schedule.enabled`。

首次使用先填写管理员 QQ；需要在聊天中让模型调用发帖 Tool 时，再填写发帖授权名单。以下 `123456789` 均为占位账号，请替换为自己的真实 QQ：

```toml
[plugin]
admin_qq = ["123456789"] # 所有 /zn 命令，包括 help，都需要管理员权限

[send]
permission_type = "whitelist"
permission = ["123456789"] # 允许该用户通过聊天调用 send_feed Tool
```

默认启用日程、生活和自动空间发帖/浏览，配图、聊天素材、主动私聊默认关闭；普通日程主动聊天和约定提醒因主动名单为空而不运行。只想先试手动发帖，可在 `[routine]` 中设置 `post_enabled=false`、`browse_enabled=false`。

### 基础与空间登录（`[plugin]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 插件总开关，关闭后停止日程、生活、空间和消息服务 |
| `admin_qq` | `[]` | `/zn` 管理员 QQ 字符串列表；留空禁用全部命令，与 Tool 权限及主人角色独立 |
| `adapter_priority` | `[]` | 适配器探测优先级，填写完整插件 ID 或后缀，如 `snowluma-adapter`；留空自动探测 |
| `cookie_methods` | `["adapter", "napcat", "clientkey", "qrcode", "local"]` | Cookie 获取方式；前几项按近期成功率重排，扫码和缓存在末尾。空列表使用默认全部方式，刷新失败仍尝试缓存 |
| `http_host` / `http_port` | `"127.0.0.1"` / `"9999"` | `napcat` Cookie 方式使用的 OneBot HTTP 地址；主机不带协议、端口或路径，端口是 1–65535 的数字字符串 |
| `http_token` | `""` | OneBot HTTP 认证 Token，未设置认证时留空；适配器 API 获取与 SnowLuma 直发不使用这些 HTTP 设置 |
| `config_version` | `"6.3.0"` | 插件维护的配置模板版本，请勿手动修改 |

### 人设与文本模型（`[persona]` / `[llm]`）

| 配置段 | 字段 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `persona` | `self_description` | `""` | 补充身份和外观，作为空间、生活和日程模型的背景；留空时读取已启用绘卷的 `selfie.prompt_prefix` |
| `persona` | `use_multiple_reply_style` | `true` | 按主程序 `personality.multiple_probability` 从 `multiple_reply_style` 抽样；关闭或风格池为空时使用 `reply_style` |
| `llm` | `text_model` | `"replyer"` | 空间正文、评论、Routine 决策和生活生成使用的主程序模型任务名 |
| `llm` | `llm_timeout_seconds` | `90` | 空间、生活及素材摘要的单次模型超时，单位秒，范围 10–600；修改后重载插件 |
| `llm` | `show_prompt` | `false` | 在 INFO 日志打印空间正文、评论和回复的完整提示词；日程归档使用单独的开关 |

模型字段填写主程序配置中的任务名，如 `replyer`、`utils`；AI 配图的 `image.pic_plugin_model` 则填写绘卷模型 key。文本外观描述和自拍形象分别配置，自拍的角色设定、参考图仍由绘卷管理。

### 每日作息与约定（`[schedule]`）

#### 开关、会话范围与生成时机

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 日程总开关，控制所选提供方的读取与注入；内置模式还控制生成、约定和日程主动服务 |
| `provider` | `"internal"` | 日程提供方；可选 `"maibot-community.mai-life"`，读取麦生活现有公开 API，日程生成、修改与维护由麦生活负责 |
| `allowed_streams` | `[]` | 日程注入、命令、会话 Tool 及生成聊天历史的会话白名单；空列表或 `"all"` 允许全部，不授权主动消息 |
| `admin_users` | `[]` | `/zn plan` 的额外管理员限制；非空时调用者必须同时属于此名单和 `plugin.admin_qq` |
| `timezone` | `"Asia/Shanghai"` | 日程、生活、空间提示词、静默窗口和每日额度共用的 IANA 时区 |
| `auto_schedule_enabled` / `auto_schedule_time` | `true` / `"00:30"` | 每天到点生成当天缺少的日程；已有日程则跳过，启动时已过点也会检查，不主动发聊天消息 |
| `auto_generate` | `true` | 仅内置模式：日程查询 Tool 查询今天且无日程时补生成；与定时生成开关独立 |
| `cross_day_activity` | `true` | 允许 `23:00-01:30` 等跨夜活动，凌晨可承接昨日活动 |
| `custom_prompt` | `""` | 生成日程时的额外要求，例如“多安排运动，晚上留出空档”；普通文本，不使用模板占位符 |

选择麦生活提供日程时，在 WebUI 的“日程与约定 → 日程提供方”选择 `maibot-community.mai-life`，或在 `[schedule]` 设置 `provider = "maibot-community.mai-life"`；麦生活需安装并启用在同一 MaiBot 实例中。默认 `provider = "internal"`，配置重载后重建所选来源。

麦生活模式不启动 MaiTrace 内置日程数据库、定时生成、约定提醒和日程频率调节；原有内置记录保留，切回内置后继续使用。日程查询 Tool、`/zn plan status`、`/zn plan list`、公开日程 API、Planner/Replyer 注入、生活推进和空间活动背景统一读取麦生活日程。`/zn plan` 的生成、修改、删除、清理及相应 Tool 提示使用麦生活对应功能，不操作内置记录。

外部日程在后台刷新，聊天 Hook 只读内存。提供方未就绪或查询失败时明确报告失败并自动重试，不切回内置、不补造日程。空日程和缺失历史保持为空。外部时间戳归一到 MaiTrace 的 `schedule.timezone`，生成时机仍由麦生活自己的配置控制。其余内置生成、裁判和日程主动设置只在内置模式生效；MaiTrace 生活层和空间功能仍使用自己的配置。

#### 生成质量、背景与日志

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_task_name` | `"replyer"` | 日程生成、约定裁判、次日策略推断和普通日程主动话题使用的主程序任务名 |
| `generation_timeout` | `180.0` | 单次日程模型超时，单位秒，至少 10；普通日程主动话题最多等待 60 秒。修改后重载插件 |
| `max_tokens` | `8192` | 单轮日程生成的最大输出 token 数，至少 1000 |
| `use_multi_round` / `max_rounds` | `true` / `2` | 质量不足时再次生成，最多 1–5 轮，包含首轮；关闭多轮后只生成一次。修改后重载插件 |
| `quality_threshold` | `0.85` | 多轮生成提前停止分数，范围 0–1；最终结果仍需通过结构及冲突检查 |
| `min_activities` / `max_activities` | `8` / `15` | 单日活动数量上下限，均至少 1，最小值不能大于最大值 |
| `enable_detailed_description` | `true` | 生成并展示活动详细描述；关闭后以活动名称呈现，已有描述保留 |
| `min_description_length` / `max_description_length` | `20` / `50` | 详细描述的字符数上下限，均至少 5，最小值不能大于最大值 |
| `recent_schedule_days` | `3` | 生成时参考最近日程的天数，范围 1–14，用于场景连续和主题去重 |
| `history_message_limit` | `30` | 当天聊天背景总条数上限，按 `allowed_streams` 允许的 QQ 会话分摊；0 禁用，与 `send.enable_chat_memory` 独立 |
| `knowledge_search_limit` | `5` | 可选知识库检索条数；0 禁用，知识检索不可用时仍继续生成 |
| `cache_ttl` | `300` | 日程快照及注入优化历史有效期，单位秒，至少 10；后台会刷新，生活状态仍持续推进 |
| `llm_log_enabled` / `llm_log_retention_days` | `true` / `7` | 日程生成和裁判日志保存到 SDK 数据目录 `schedule/llm_logs/`，默认保留 7 天；关闭归档后仍清理旧日志 |

#### 次日策略与约定判断

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `auto_infer_next_day_prompt` / `infer_time` | `false` / `"22:30"` | 前夜推断次日额外要求，只保存策略，供次日定时生成使用；与定时生成开关独立 |
| `infer_lookback_days` | `3` | 次日推断参考历史天数，范围 1–7，与 `recent_schedule_days` 独立 |
| `infer_max_prompt_chars` | `300` | 次日策略最多字符数，至少 50 |
| `infer_use_completion_signal` | `true` | 推断时参考活动状态和进度，仅开启次日推断时生效 |
| `role_judge_enabled` / `role_judge_temperature` | `true` / `0.3` | 更新约定时按人设判断接受或拒绝，温度范围 0–2；关闭裁判后仍检查日期、时长和冲突 |

#### 日程主动聊天、约定提醒与聊天频率

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `proactive_streams` | `[]` | 主动任务、约定提醒和频率调节的会话名单；空列表完全禁用这三项，必须显式填写已有会话，不支持 `"all"` |
| `enable_proactive_trigger` | `true` | 对已确认、有目标会话的约定，在开始后前 5 分钟尝试提交提醒；同一约定每天每会话最多一次 |
| `activity_proactive_enabled` | `true` | 合适的活动生成自然话题并交给 MaiBot Planner；工作、学习、睡眠等场景跳过，Planner 可以不发送 |
| `activity_proactive_cooldown_minutes` | `180` | 每会话普通主动任务的最短提交间隔，单位分钟，范围 10–10080 |
| `activity_proactive_daily_max` | `2` | 每会话每天最多提交的普通任务数，范围 0–20；0 禁用，按提交计数，同一活动最多一次 |
| `activity_proactive_quiet_start` / `activity_proactive_quiet_end` | `"23:00"` / `"08:00"` | 普通日程主动任务静默时段，支持跨夜；起止相同表示不设静默 |
| `enable_frequency_modulation` | `true` | 按活动调整名单会话的聊天频率，睡眠约 0.05、工作/学习约 0.3，休息/娱乐提高；关闭或卸载后恢复 |

约定提醒需要目标会话在 `proactive_streams` 中，但不受普通日程主动聊天的冷却、每日额度和静默限制。生活主动私聊使用独立许可；由生活私聊负责的会话会避免重复提交普通日程任务。

会话填写示例，QQ 号与群号请替换为实际值；对应聊天流需已在主程序中存在：

```toml
[schedule]
allowed_streams = ["qq:group:987654321", "qq:private:123456789"]
proactive_streams = ["qq:private:123456789"] # 只允许在该私聊主动聊天、提醒和调节频率
auto_schedule_time = "00:30"
custom_prompt = "下午安排运动，晚饭后留一些机动空档。"
```

也支持 `session:真实会话ID` 和旧裸会话 ID；其他平台可将 `qq` 替换为对应平台标识。日程是麦麦的一条全局时间线；其他会话只能看到约定占用的时段，具体内容仅向提出约定的会话展示。

### 日程与生活背景注入（`[inject]`）

先通过 `[schedule]` 的 `inject_schedule=true` 允许 Planner 背景注入、`inject_into_replyer=true` 允许 Replyer 背景注入；两个开关独立，均默认为 `true`。`max_future_activities=3` 控制注入时展示的未来活动条数，0 不展示，不限制查询工具返回的完整日程。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_intent_classification` | `true` | 区分日程问询、闲聊、技术问答和命令，技术/命令场景跳过；关闭后按闲聊处理 |
| `enable_state_analysis` | `true` | 补充活动阶段和已进行时间，生活精力、饥饿和心情仍由生活引擎推进 |
| `enable_inject_optimization` | `true` | 控制重复注入与闲聊概率；关闭后仍保留会话范围、意图分类、同轮承接及重试保护 |
| `casual_chat_inject_probability` | `0.5` | 优化开启时非连续闲聊的注入概率，范围 0–1；直接问日程、持续话题和 Replyer 承接不受此概率限制 |
| `context_max_turns` / `context_ttl` | `3` / `600` | 每会话注入记录保留轮数及有效秒数，分别至少 1 和 60；用于话题延续判断 |

### 生活状态与私人日记（`[life]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 生活状态、场景、梦境、私人日记、日期和生活主动私聊的总开关，也为自动空间发帖提供公开素材 |
| `tick_seconds` | `60` | 生活素材、日期与清理的巡检间隔，单位秒，范围 10–3600；生活数值约每 30 秒推进 |
| `offline_hours` | `72` | 离线后最多回放已有日程的小时数，范围 1–72；未保存的活动不会补造 |
| `initial_energy` / `initial_hunger` | `70` / `20` | 首次建库的精力与饥饿，范围 0–100，饥饿越大越饿；修改不会覆盖已有状态 |
| `scene_enabled` / `dream_enabled` | `true` / `true` | 根据已有活动细化场景、醒来后生成私人梦境；关闭后历史保留 |
| `diary_enabled` / `diary_hour` | `true` / `8` | 每天从指定整点开始整理昨天的私人生活日记，整点范围 0–23；有素材时每天一篇 |
| `profiles` / `important_dates` | `[]` / `[]` | 用户许可档案和重要日期数组，子字段及示例见下文；空档案没有私人日记读者，也不主动私聊 |
| `private_proactive_enabled` | `false` | 生活主动私聊总开关，还需 `messaging.enabled`、用户许可和真实私聊会话 |
| `private_min_interval_minutes` | `180` | 同用户两次生活主动私聊成功发送的最短间隔，单位分钟，范围 10–10080 |
| `private_silence_minutes` | `15` | 联系前距用户最近真实消息的最短时间，单位分钟，范围 0–1440；0 不要求等待 |
| `private_min_energy` | `30` | 主动私聊所需最低精力，范围 0–100；入睡、浅睡、深睡时仍跳过 |
| `private_pending_seconds` | `300` | 交给 Planner 的主动任务有效期，单位秒，范围 30–1800；过期未发送可释放素材 |
| `claim_lease_seconds` | `1800` | 素材认领租约，单位秒，范围 30–7200；自动空间发布按实际发布预算延长 |
| `opportunity_retention_days` | `30` | 已到期且未占用素材的额外保留天数，范围 1–365；私人日记、梦境及结果待核对素材保留 |

生活日记整理自身场景、梦境和匿名互动数量，保存在本地；旧版抓取聊天并发布日记的功能已停止，旧文件仍可通过 `/zn ls` 和 `/zn v` 阅读。

### 用户许可与主人（`[[life.profiles]]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `user_id` | `""` | 用户真实 QQ 字符串，添加档案时必须填写，不可重复 |
| `enabled` | `true` | 启用该用户的生活私聊观察和访问许可，关闭后保留历史 |
| `role` | `"friend"` | `owner` 可在本人私聊读私人日记等主人内容；`friend` 可访问自己的日期和公开生活，最多启用一位主人 |
| `proactive_enabled` | `false` | 允许生活服务主动联系此用户，还需生活和消息总开关 |
| `daily_proactive_max` | `1` | 每天生活主动私聊成功发送次数上限，范围 0–20；0 禁用，按实际发送成功计数 |
| `quiet_start` / `quiet_end` | `"23:00"` / `"08:00"` | 此用户生活主动私聊静默时段，支持跨夜；起止相同表示不设静默，不限制普通被动聊天 |

启用主人及主动私聊的示例：

```toml
[life]
private_proactive_enabled = true

[[life.profiles]]
user_id = "123456789"
enabled = true
role = "owner"
proactive_enabled = true
daily_proactive_max = 1
quiet_start = "23:00"
quiet_end = "08:00"

[messaging]
enabled = true # 修改后重载插件，注册生活消息处理
```

主人角色不授权命令，使用 `/zn life diary` 仍需加入 `plugin.admin_qq`，并在主人本人私聊中调用。朋友可增加独立的 `[[life.profiles]]`，设置 `role="friend"`。主动任务交给 MaiBot 判断是否发送，满足开关和额度并不保证立即联系。

### 重要日期（`[[life.important_dates]]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `user_id` | `""` | 所属用户 QQ，必须匹配 `life.profiles` 中的档案 |
| `name` / `date` | `""` / `""` | 名称与有效公历日期 `YYYY-MM-DD`，添加条目时均必填，名称最多 100 字 |
| `yearly` | `true` | 是否每年按月日重复，`false` 为一次性日期；2 月 29 日只在有效闰年出现 |
| `lead_days` | `[7, 1, 0]` | 提前生成提醒素材的天数，每项 0–366，0 为当天，空列表禁用提醒素材 |

```toml
[[life.important_dates]]
user_id = "123456789" # 需先配置这个用户的 life.profiles
name = "生日"
date = "2000-11-10"
yearly = true
lead_days = [7, 1, 0]
```

日期只生成提醒素材，发送仍需用户启用主动私聊并通过静默、冷却和额度检查。聊天识别的日期是候选，需要本人用 `/zn dates confirm <ID>` 确认后才进入提醒；配置中的日期请在 WebUI 或配置文件修改。

### 生活消息处理（`[messaging]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 注册生活消息接收、发送前后处理；日期观察、生活主动回执依赖此项，修改后必须重载插件 |
| `turn_expire_seconds` | `300` | 源消息关联、撤回定位和回复轮次标识的保留秒数，范围 30–1800 |
| `recall_enabled` | `true` | 源消息撤回时取消对应尚未发送的回复，已发送消息保留；作用于群聊追踪和已配置的生活私聊 |
| `rest_gate_enabled` | `false` | 根据睡眠/休息状态判断已启用档案的私聊是否回复，需启用生活；群聊由主程序决策，命令直接放行 |
| `wake_probability` | `0.1` | 休息闸门开启时，普通私聊允许唤醒回复的概率，范围 0–1；明确唤醒可通过，明确勿扰优先拒绝 |
| `awake_grace_minutes` | `20` | 休息中实际回复成功后的继续聊天宽限，单位分钟，范围 1–120；明确勿扰仍优先 |

消息逐条处理。`turn_expire_seconds` 与生活主动任务有效期仅用于追踪和过期管理，不会延迟、等待或合并消息。

### 发说说与聊天素材（`[send]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `permission` / `permission_type` | `[]` / `"whitelist"` | `send_feed` Tool 的调用者 QQ 权限；白名单只允许名单内用户，黑名单只拒绝名单内用户 |
| `prompt` | 内置说说模板 | 生成正文的提示词，支持 `{current_time}`、`{bot_personality}`、`{bot_expression}`、`{topic}`、`{current_activity}` |
| `history_number` | `5` | 参考近期说说数以避免重复，0 完全关闭历史查询 |
| `custom_qqaccount` | `""` | `/zn custom` 读取的私聊对象 QQ，留空禁用；直接发布最新一条筛选后的非命令正文，仍可配图 |
| `custom_only_mai` | `true` | Custom 模式只取麦麦发言；`false` 只取对方发言，Custom 跳过正文模型、历史参考和素材整理 |

#### 可选聊天素材

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_chat_memory` | `false` | 在主题正文生成时使用所选聊天作为匿名背景 |
| `filter_mode` / `target_chats` | `"whitelist"` / `[]` | `all` 全部、`whitelist` 仅名单、`blacklist` 排除名单；空白名单不读取任何聊天，空黑名单与 `all` 相同 |
| `include_private_chat_memory` | `false` | 允许把所选私聊用作匿名背景；关闭时所有过滤模式均排除私聊 |
| `time_range` / `last_hours` | `"today"` / `6` | `today` 读取配置时区的当天消息；`last_n_hours` 读取最近 1–72 小时，由 `last_hours` 指定 |
| `min_messages` / `max_messages` | `5` / `50` | 有效消息门槛与最近消息上限，分别为 1–100、1–200，最小值不得大于最大值；上限作用于每会话查询及合并结果 |
| `per_message_max_chars` | `200` | 单条素材字符上限，范围 20–2000；隐藏号码、邮箱和发送者标识 |
| `chat_memory_mode` | `"hybrid"` | `direct` 使用匿名片段，`summary` 全部摘要，`hybrid` 摘要早期消息并保留近期片段 |
| `summary_model` | `"utils"` | 摘要使用的主程序任务名，超时沿用 `llm.llm_timeout_seconds`；`direct` 模式忽略 |
| `summary_prompt` | 内置匿名摘要模板 | 仅支持 `{chat_logs}` 占位符，用于 `summary` / `hybrid` |
| `hybrid_recent_count` | `10` | Hybrid 保留的最近片段数，范围 1–100；全部消息均属近期片段时不调用摘要模型 |
| `fallback_prompt` | `"今天的聊天比较平淡，只根据当前生活和主题自然表达。"` | 无选中会话或消息不足时追加的背景；网络、查询或模型错误仍报告失败 |

素材会话格式为 `group:群号`、`private:QQ号` 或 `session:真实会话ID`，与日程的 `qq:group:群号` 写法不同。例如只选一个群的近期聊天：

```toml
[send]
enable_chat_memory = true
filter_mode = "whitelist"
target_chats = ["group:987654321"]
include_private_chat_memory = false
time_range = "last_n_hours"
last_hours = 6
chat_memory_mode = "hybrid"
summary_model = "utils"
```

素材只用于匿名背景，公开正文不得包含聊天原话、身份和私人安排。修改模板时，字面花括号写作 `{{` 和 `}}`；空间正文、摘要、评论与回复模板只接受各自声明的占位符。

### AI / 表情包配图（`[image]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enable_image` | `false` | 主题和自动发帖的配图总开关，关闭时发布纯文字 |
| `image_mode` | `"random"` | `only_ai` 仅 AI、`only_emoji` 仅表情包、`random` 按概率选一种来源；本条多张图片共用来源 |
| `ai_probability` | `0.5` | Random 模式下本次发布选择 AI 的概率，范围 0–1 |
| `image_number` | `1` | 每条期望配图数，范围 1–4，AI 逐张串行生成；修改后重载插件 |
| `pic_plugin_model` | `""` | 绘卷 `models.<key>` 中的 key，如 `model3`；AI 配图需安装并配置绘卷 |
| `require_image` | `true` | AI 图片必须全部成功才发布；设为 `false` 可缺图继续。表情包未匹配时允许无图 |
| `image_retry` | `2` | 每张 AI 图失败后的额外重试数，范围 0–2，每次间隔 5 秒；修改后重载插件 |
| `ai_content_mode` | `"random"` | 每张 AI 图各以 50% 概率选自拍/普通画图；`selfie` 固定自拍，`illustration` 固定普通画图，重试保持原选择 |
| `selfie_style` | `"photo"` | 自拍视角：`standard` 前置、`mirror` 对镜、`photo` 第三人称、`auto` 按活动选择；普通画图忽略 |
| `clear_image` | `true` | `true` 不新增 AI 图片本地副本；`false` 归档到 SDK 数据目录 `images/`，已有历史图片保留 |

固定使用绘卷自拍的示例，`model3` 请替换为绘卷中实际配置的模型 key：

```toml
[image]
enable_image = true
image_mode = "only_ai"
pic_plugin_model = "model3"
image_number = 1
ai_content_mode = "selfie"
selfie_style = "auto"
require_image = true
image_retry = 2
```

自拍使用绘卷的角色设定和参考图，并同步活动、衣着、地点、时段及细化场景；普通画图只使用本次内容。每次绘卷调用预算为 185 秒，默认一张图最多尝试 3 次；空间发布本身不会自动重发。

### 读取动态与手动互动（`[read]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `permission` / `permission_type` | `[]` / `"blacklist"` | `read_feed` 与 `recall_qzone_activity` Tool 的调用者 QQ 权限；默认允许所有有真实 QQ 身份的调用者 |
| `read_number` | `5` | `read_feed` 单次读取好友动态数，至少 1，建议 3–10；修改后重载插件，不控制自动浏览条数 |
| `like_probability` / `comment_probability` | `1.0` / `1.0` | 手动读取对未处理动态尝试点赞/评论的概率，范围 0–1；自动浏览使用 `monitor` 的概率 |
| `allow_skip_comment` | `true` | 手动读取和自动浏览时允许模型输出“不回复”跳过新说说评论；仍按概率点赞并记入去重，不控制评论区和链式回复 |
| `prompt` / `rt_prompt` | 内置评论模板 | 普通动态/转发动态的评论模板，手动与自动共用 |

普通评论模板支持 `{current_time}`、`{bot_personality}`、`{bot_expression}`、`{target_name}`、`{created_time}`、`{content}`、`{impression}`；转发评论另支持 `{rt_con}`。概率为 1 表示每条尝试，模型仍可跳过评论，接口也可能失败。

`read_feed` 工具支持 `enable_comment=false`（或“不回复”）明确关闭本次评论，不生成评论正文；点赞与动态去重仍按配置执行。

### 自动浏览、点赞与回复（`[monitor]`）

自动浏览由 `[routine]` 驱动，作者范围、互动概率和去重由本段控制。

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `read_list` / `read_list_type` | `[]` / `"blacklist"` | 说说作者 QQ 名单；空黑名单处理全部，空白名单不处理任何人，自己的评论区也受此限制 |
| `like_probability` / `comment_probability` | `1.0` / `1.0` | 对未处理好友动态尝试点赞/评论的概率，范围 0–1；评论共用 `read` 模板及跳过设置 |
| `silent_hours` | `"22:00-07:00"` | 自动互动静默时段，支持跨夜和半角逗号分隔多段；端点包含在内，留空全天可互动 |
| `like_during_silent` / `comment_during_silent` | `false` / `false` | 静默期是否允许点赞、评论及回复；Routine 默认先拦截整轮，见下文静默说明 |
| `enable_auto_reply` / `self_read_number` | `false` / `5` | 是否检查自己最近动态并回复新评论；条数至少 1，关闭后不查询自己的评论区 |
| `reply_prompt` | 内置评论区回复模板 | 支持 `{current_time}`、`{bot_personality}`、`{bot_expression}`、`{nickname}`、`{created_time}`、`{content}`、`{comment_content}`、`{impression}` |
| `reply_to_reply_prompt` | 内置链式回复模板 | 回复好友空间中对麦麦评论的回复，独立于 `enable_auto_reply`；占位符将上行的 `{comment_content}` 替换为 `{bot_comment}`、`{reply_content}` |
| `processed_feeds_cache_size` | `100` | 手动读取与自动浏览共用的已处理动态 ID 上限，至少 1，持久化去重；淘汰的旧动态可能再次互动 |
| `processed_comments_cache_size` | `100` | 自动回复去重记录上限，至少 1，按说说 ID 分组计数，同一说说可保存多条已回复评论 ID |

使用作者白名单并开启自己评论区回复时，`read_list` 中也要允许麦麦自己的 QQ。好友动态评论概率不控制自己评论区及链式回复；这两类回复仍受作者名单和静默评论许可约束。

### 日程驱动的自动空间行为（`[routine]`）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `post_enabled` / `browse_enabled` | `true` / `true` | 自动发帖/浏览开关，均需要日程和当前活动；自动发帖还需要生活及可公开素材 |
| `check_interval_minutes` | `20` | 两轮自动决策的检查间隔，单位分钟，至少 1 |
| `post_cooldown_minutes` / `browse_cooldown_minutes` | `120` / `40` | 自动发帖/浏览冷却，单位分钟，至少 1；手动命令和 Tool/API 不受此冷却限制 |
| `respect_silent_hours` | `true` | 在 `monitor.silent_hours` 内跳过发帖/浏览整轮决策；关闭后浏览内部仍检查静默互动许可 |
| `post_blocked_activities` / `browse_blocked_activities` | `["sleeping", "working", "studying", "eating", "exercising"]` | 发帖/浏览禁止活动类型，命中后不调模型；空列表关闭对应硬限制 |
| `max_post_chance` / `max_browse_chance` | `1.0` / `1.0` | 模型允许之后再次通过的概率，范围 0–1；0 禁用对应行为，1 保留模型判断和其他条件 |
| `require_reason` | `true` | 模型给出是/否及理由，可在 `/zn debug routine` 查看；关闭后只要求是/否 |

Routine 先检查冷却、静默和活动类型，再由模型判断是否适合发帖或浏览，最后按概率决定。日程由 MaiTrace 内部生成，无需安装额外规划插件；即使概率设置为 1，也不会每轮必定发帖。

### 清理与本地历法（`[goal_cleanup]` / `[environment]`）

| 配置段 | 字段 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `goal_cleanup` | `cleanup_interval` | `3600` | 后台清理间隔，单位秒，至少 60；启动时也执行一次，处理到期作息、旧作息和日程模型日志 |
| `goal_cleanup` | `cleanup_old_goals_days` | `30` | 已完成/取消的旧生活作息保留天数，至少 1；无时段旧目标及约定历史保留 |
| `environment` | `calendar_enabled` | `true` | 启用中国节假日、农历与节气，需要生活服务和历法依赖；关闭后仍提供配置时区的日期时间 |

### 静默与自动行为

全部时间使用 `schedule.timezone`，默认 `Asia/Shanghai`。

| 行为 | 静默设置 | 默认时段 |
| --- | --- | --- |
| 自动空间发帖/浏览 | `monitor.silent_hours`，Routine 默认遵守 | 22:00–07:00 |
| 普通日程主动聊天 | `schedule.activity_proactive_quiet_start/end` | 23:00–08:00 |
| 生活主动私聊 | `life.profiles[].quiet_start/end` | 23:00–08:00 |

`routine.respect_silent_hours=false` 后，自动浏览仍受 `monitor` 的静默点赞/评论许可限制。手动命令、Tool 和 API 不受上述自动静默限制；约定提醒不受普通日程主动静默限制。

命令使用 `plugin.admin_qq`，空间 Tool 使用 `send/read.permission`，自动行为使用 Routine 条件及作者名单，跨插件 API 的触发权限由调用方负责。各类权限独立，填写主人档案不会自动获得管理员或空间发帖权限。

## 与麦麦绘卷配合

[麦麦绘卷](https://github.com/1021143806/mais_art_journal)为 MaiTrace 生成自拍或普通配图；MaiTrace 则提供当前日程、生活状态和空间直发接口，供绘卷的主动图片功能使用。

需要空间配图时，在绘卷中配置生图模型，再按上方 `[image]` 说明启用配图并填写模型 key。自拍的角色设定与参考图由绘卷管理，活动、衣着、地点和场景由 MaiTrace 提供。

## 跨插件 API

命名空间为 `Rabbit-Jia-Er.MaiTrace`，以下均为公开接口，通过 SDK `ctx.call_capability("api.call", ...)` 调用。调用方负责触发权限，接口不使用空间 Tool 的 QQ 名单。

| API | 用途 |
| --- | --- |
| `publish_topic_api(topic, ...)` | 生成文图并发布 |
| `send_feed_api(message, images=[])` | 发布自备文图，图片为无 data URL 前缀的 base64 |
| `get_feeds_list_api(target_qq, num=5, filter=False)` | 读取指定 QQ 的动态，`filter=True` 过滤已评论动态；只返回列表，不执行点赞或评论 |
| `get_current_activity()` | 当前活动 |
| `get_schedule(day="")` | 指定公历日期 `YYYY-MM-DD` 的日程，留空为今天 |
| `get_life_state()` | 生活状态 |
| `get_current_scene()` | 当前场景 |
| `get_environment_snapshot()` | 本地历法 |
| `create_proactive_opportunity(topic, ...)` | 创建分享契机，可传 `motive`、`privacy`、`expires_minutes`、`source_key`；默认私密，有效期 120 分钟，由调度器决定是否使用 |

### 生成并发布主题说说

生成正文和配图后发布；`current_activity`、`activity_type`、`outfit` 为可选活动背景，`topic="custom"` 使用 Custom 直发模式：

```python
result = await self.ctx.call_capability(
    "api.call", timeout_ms=1_230_000,
    api_name="Rabbit-Jia-Er.MaiTrace.publish_topic_api", version="1",
    args={"topic": "今天的晚霞", "current_activity": "散步"},
)
# 成功返回 {"result": True, "story": "说说正文", "message": "状态说明"}
```

### 发布自备文图

发布调用方准备好的正文和图片，图片为无 `data:image/...` 前缀的 base64 字符串，纯文字可省略 `images`。此接口直接发布，不调用配图服务，也不受 `image.require_image` 限制：

```python
result = await self.ctx.call_capability(
    "api.call", timeout_ms=185_000,
    api_name="Rabbit-Jia-Er.MaiTrace.send_feed_api", version="1",
    args={"message": "今天也过得很充实。", "images": []},
)
```

### 读取动态列表

```python
result = await self.ctx.call_capability(
    "api.call", timeout_ms=65_000,
    api_name="Rabbit-Jia-Er.MaiTrace.get_feeds_list_api", version="1",
    args={"target_qq": "123456789", "num": 5, "filter": False},
)
# 成功返回 {"result": True, "message": "状态说明", "data": [...]}
```

前三个空间 API 以 `result` 布尔字段表示结果。发布返回 `uncertain=True` 时，表示可能已经发出，应先核对空间；无效 base64 图片会阻止直发。活动、生活和历法接口返回对应快照，`get_schedule` 返回活动列表，公开接口隐藏私人约定详情。

默认配置下，`send_feed_api` 调用预算建议 185 秒，`publish_topic_api` 建议 1230 秒；图片数量、重试、模型超时变化后，重载插件并按实际组件预算调整调用方超时。

## 💡 使用示例

### 主题说说

```text
用户：/zn 今天的晚霞
麦麦：[根据主题生成并发布说说，按配置添加图片]
```

### 日程查询与约定

```text
用户：麦麦，今天有什么安排？
麦麦：[查询今日作息]

用户：/zn plan update 明天18:00一起吃饭
麦麦：[按人设判断是否接受，并检查活动冲突]
```

### 私人日记与重要日期

在已启用用户档案的本人私聊中使用；私人日记还需要主人角色，命令均需管理员权限。

```text
用户：/zn life diary
麦麦：[展示私人生活日记]

用户：/zn dates add 2000-11-10 生日
麦麦：[记录本人重要日期]
```

## 🔧 依赖说明

- Python 3.12+，同时遵循所用 MaiBot 版本的 Python 要求
- MaiBot 1.3.11+、插件 SDK `>=2.15.0,<3`
- Python 依赖由主程序安装，也可按快速开始中的命令手动安装
- AI 配图需要安装并配置[麦麦绘卷](https://github.com/1021143806/mais_art_journal)
- MaiBot 项目地址：https://github.com/MaiM-with-u/MaiBot

## 🔄 升级说明

升级后重载即可，旧配置和数据自动迁移并保留原件。数据默认位于 `data/plugins/Rabbit-Jia-Er.MaiTrace/`；无需清空数据库。

配置修改建议重载，尤其是消息开关、图片数量/重试、模型超时和日程生成轮数。初始精力/饥饿只用于首次建库；生成参数只影响后续日程。

## ⚠️ 注意事项

- 所有 `/zn` 命令均需 `plugin.admin_qq` 授权；主人角色、空间 Tool 权限和管理员权限分别配置。
- 主动任务交给 MaiBot 判断是否发送，满足开关和额度并不保证立即联系。
- 发布结果为 `uncertain=true` 时先核对空间，避免重复发布。

## 常见问题

- **命令提示未配置管理员**：填写 `plugin.admin_qq`；需要日程命令时还要检查 `schedule.admin_users` 和会话范围。主人身份不会自动授权命令。
- **自动发帖或浏览一直没有动作**：用 `/zn plan status` 确认当前作息，再用 `/zn debug routine` 查看冷却、静默、禁止活动、模型拒绝或概率跳过；自动发帖还需生活公开素材。
- **主动私聊或约定提醒未发送**：生活私聊检查生活、消息及用户的主动开关，并确认已有真实私聊会话；约定提醒检查约定目标是否列在 `schedule.proactive_streams`。主动任务提交后仍由 MaiBot 决定是否发送。
- **空间登录或 AI 配图失败**：登录用 `/zn debug cookie` 检查适配器与 Cookie；配图检查绘卷已启用、模型 key 正确及自拍参考图有效。`require_image=true` 时缺图会停止发布。

## 贡献和反馈

- **制作者水平有限，任何漏洞、疑问或建议,欢迎提交 [Issue](https://github.com/Rabbit-Jia-Er/MaiTrace/issues) 或 Pull Request！**
- **作者QQ：3082618311或群1025591815（部分请求可能被过滤导致回复不及时，请见谅）**
- **其余问题请联系作者修复或解决**

---

## 🤝 致谢

- 感谢 [MaiBot](https://github.com/MaiM-with-u/MaiBot)，以及 [xc94188](https://github.com/xc94188)、[myxxr](https://github.com/myxxr)、[UnCLAS-Prommer](https://github.com/UnCLAS-Prommer)、[XXXxx7258](https://github.com/XXXxx7258)、[heitiehu-beep](https://github.com/heitiehu-beep) 提供的功能改进。

## 🔗 版权信息

- 作者：Rabbit-Jia-Er
- 许可证：[AGPL-3.0-only](LICENSE)
- 原有和引用组件的许可保留于 [LICENSES/](LICENSES/)
- 项目主页：https://github.com/Rabbit-Jia-Er/MaiTrace
