# MaiTrace（麦时迹）

MaiBot 的 QQ 空间插件。让你的麦麦发说说、刷空间、自动评论点赞、回复评论、写日记。

> **v3.1+ 基于 `maibot_sdk` 2.x**（manifest v2、严格类型化配置、`@Tool` / `@API` / `@Command` / `@HookHandler` 装饰器、跨插件 API 调用）。从 v2.5 / v3.0 升级请阅读下方"升级指南"。

## 功能

| 模块 | 入口 | 说明 |
|---|---|---|
| **发说说** | `/zn <主题>` / `@Tool send_feed` / `@API publish_topic_api` / `@API send_feed_api` | LLM 按主题生成正文 + 自动配图 + 历史去重，可直接发到 QQ 空间 |
| **读说说** | `@Tool read_feed` | 拉好友最近说说，按概率点赞 + LLM 生成评论 |
| **刷空间** | Routine 自动驱动 | 评论好友未读说说、回复自己说说下的新评论、识别并回复"对 bot 评论的回复"（多层链式） |
| **日记** | `/zn gen [日期]` 或 `diary.schedule_time` 定时自动 | 抓当天聊天记录 → timeline → LLM 生成 → 自动发到 QQ 空间 |
| **Routine 严格决策** | 自动 | 通过 `xuqian13.autonomous-planning-plugin-v4` API 拿当前日程，**四层**防误发：冷却 / 静默时段 / 活动黑名单 / LLM 严格决策 / 二次掷骰 |
| **跨插件 API** | `ctx.api.call("Rabbit-Jia-Er.MaiTrace.<api>")` | 3 个 public API：`publish_topic_api` / `send_feed_api` / `get_feeds_list_api` |
| **配图** | `image.image_mode` | AI 生图（调绘卷 `mais_art_journal.generate_image` API）/ 表情包 / 混合；自动把"自我形象描述"拼进生图 prompt |
| **人格扩展** | `[persona]` 段 | `self_description` 注入所有 prompt 头部；按 `personality.multiple_probability` 在备用风格池抽样，与主程序回复行为对齐 |

## 安装

1. 进 MaiBot plugins 目录：
   ```shell
   cd MaiBot/plugins
   ```

2. 克隆：
   ```shell
   git clone https://github.com/Rabbit-Jia-Er/MaiTrace.git
   ```

3. 装依赖（manifest 已声明，主程序会自动装；手动装见下）：
   - **uv**（推荐）：`uv pip install -r plugins/MaiTrace/requirements.txt`
   - **pip**：`pip install httpx Pillow bs4 json5 openai`

4. 启动 MaiBot：插件目录下自动生成 `config.toml`，按注释配好后重启。

## 升级指南

### 从 v2.5 → v3.0

1. **配置段改名**：原 `[diary.model]` 段必须改成 `[diary_model]`（pydantic v2 保留前缀冲突）。备份旧 `config.toml` → 删除 → 重启让插件重新生成 → 把旧 `[diary.model]` 的内容粘到新 `[diary_model]` 下。
2. **数据迁移**：旧 `processed_list.json` / `qzone/cookies-*.json` / `qzone/qrcode.png` 加载时自动 move 到 `data/` 下。
3. **`adapter` cookie 方式**：默认 `cookie_methods` 列表新增 `"adapter"`（通过 `napcat-adapter` 插件 API），放第一位最稳。

### 从 v3.0 → v3.1+

完全向后兼容（除一项命令权限**破坏性变更**，见下文），但有以下行为变化：

1. **`@Action` → `@Tool`**：旧 `send_feed` / `read_feed` Action 已迁移为 SDK 2.x 的 `@Tool`。功能等价，由 LLM 在 tool calling 时自主选择。
2. **`[persona]` 新增段**：含 `self_description` / `use_art_selfie_prompt` / `use_multiple_reply_style`，默认值不破坏旧行为。
3. **`routine.respect_silent_hours` 默认 `true`**：现在 `monitor.silent_hours`（默认 22:00-07:00）会同时卡发说说和刷空间。**比 v3.0 更严**，要回旧行为设为 `false`。
4. **活动黑名单默认开启**：`routine.post_blocked_activities` / `browse_blocked_activities` 默认含 `sleeping / working / studying / eating / exercising`，命中直接拒、不调 LLM。要全部交给 LLM 决策请清空数组。
5. **timeline 单条消息截断**：原硬编码 50 字 → `diary.per_message_max_chars`（默认 200，可设 0-2000；0=不截断）。
6. **Routine 不再直读 sqlite**：改用 `ctx.api.call("xuqian13.autonomous-planning-plugin-v4.get_current_activity")`，需安装 v4 规划插件。
7. **⚠️ v3.1.2 命令权限破坏性变更**：所有 `/zn` 命令（含原本任何人能用的 `help` / `v [日期]` / `<日期>`）现在统一要求调用者在 `[plugin].admin_qq` 列表中。空列表 = 所有 `/zn` 都被拒。**升级后必须先在 config.toml 填 `admin_qq`**，否则命令全不能用。注意这与原 `[send].permission`（控制 `@Tool` 触发）是两套独立的权限。

## 命令

所有命令统一 `/zn` 前缀，**全部要求调用者在 `[plugin].admin_qq` 列表中**（v3.1.2+）：

| 命令 | 说明 |
|---|---|
| `/zn help` | 帮助 |
| `/zn <主题>` | 发一条指定主题的说说 |
| `/zn custom` | 用 `send.custom_qqaccount` 的最新私聊内容发说说 |
| `/zn gen [日期]` | 生成日记（默认今天，含发布到空间） |
| `/zn ls` | 日记列表 + 统计 |
| `/zn v [日期] [编号]` | 查看日记 |
| `/zn <日期>` | 等价 `/zn v <日期>` |
| `/zn debug routine` | 查看 Routine 最近 N 次决策（含拒绝原因） |
| `/zn debug cookie` | 查看 Cookie 当前状态 + 各方式成功率 |
| `/zn debug msgs [日期]` | 查看某日消息读取统计 |

> `[plugin].admin_qq` 为空时所有 `/zn` 都会被拒绝并提示"未配置管理员"。
> 注意这是**命令权限**，与控制 `@Tool` 触发的 `[send].permission` / `[read].permission` **互相独立**。

日期格式：`YYYY-MM-DD` / `YYYY/MM/DD` / `YYYY.MM.DD` / `今天` / `昨天` / `前天`

## `@Tool` 触发

LLM 在群聊中根据上下文自主调用以下工具：

| Tool | 用途 | 关键词 |
|---|---|---|
| `send_feed` | 发一条说说 | 用户要求"发说说"、"更新空间"等 |
| `read_feed` | 读取并评论好友空间 | 用户要求"读 XX 的空间"、"看看 XX 动态" |

## 跨插件 API

```python
# 1. 完整流程：LLM 按主题生成正文 + 自动配图 + 发布（推荐）
result = await ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.publish_topic_api",
    topic="今天的晚霞",
    current_activity="散步",   # 可选
)
# → {"result": True, "story": "...", "message": "..."}

# 2. 直发：调用方自备内容和图（图片为 base64 字符串，跨进程安全）
await ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.send_feed_api",
    message="hello world",
    images=["<base64-1>", "<base64-2>"],  # 可选，最多 4 张
)
# → {"result": True, "message": "说说发送成功，tid=..."}

# 3. 读：拉某 QQ 的最近说说
await ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.get_feeds_list_api",
    target_qq="10001",
    num=5,
)
# → {"result": True, "message": "...", "data": [...]}
```

## 配置一览

完整字段见 `config.toml` 注释或 WebUI 配置页（每个字段都有 label / hint / depends_on 元数据）。下表只列关键项。

### `[plugin]`
| 项 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `http_host` / `http_port` | `127.0.0.1` / `9999` | Napcat 地址 |
| `napcat_token` | `""` | Napcat HTTP token |
| `cookie_methods` | `[adapter, napcat, clientkey, qrcode, local]` | Cookie 获取顺序；v3.1 起按近期成功率自动重排 |
| `admin_qq` ⭐v3.1.2 | `[]` | 命令管理员；空列表 = 所有 /zn 被拒；与 send/read.permission 独立 |

Cookie 方式：

| 方式 | 说明 |
|---|---|
| `adapter` | 调 `napcat-adapter` 插件 API（**推荐**） |
| `napcat` | 直连 Napcat HTTP `/get_cookies` |
| `clientkey` | 通过本机 QQ 客户端 clientkey 换取 |
| `qrcode` | 扫码登录（自动生成二维码到 `data/qrcode.png`） |
| `local` | 读 `data/cookies-<uin>.json` 缓存 |

### `[send]`（发说说）
| 项 | 默认 | 说明 |
|---|---|---|
| `permission` / `permission_type` | `[]` / `whitelist` | 谁能让 bot 发说说 |
| `prompt` | 见 config | 占位符 `{bot_personality}` `{bot_expression}` `{topic}` `{current_activity}` |
| `history_number` | `5` | 生成时参考的历史说说数（避免重复） |
| `custom_qqaccount` / `custom_only_mai` | `""` / `true` | `/zn custom` 模式取私聊内容的 QQ |

### `[image]`（配图）
| 项 | 默认 | 说明 |
|---|---|---|
| `enable_image` | `false` | 总开关 |
| `image_mode` | `random` | `only_ai` / `only_emoji` / `random` |
| `ai_probability` | `0.5` | random 模式下出 AI 图概率 |
| `image_number` | `1` | 每条说说几张图（1-4） |
| `pic_plugin_model` | `""` | 绘卷 `models.<id>`，留空禁用 AI 生图 |
| `clear_image` | `true` | 上传后是否删除本地副本；`false` 时归档到 `data/images/` |

> 注：调绘卷 API 时本插件直接传**说说正文 + persona.self_description**作为 prompt，绘卷内部 `prompt_optimizer` 会把中文自动转为英文 SD prompt。

### `[read]`（读说说 / 评论）
| 项 | 默认 | 说明 |
|---|---|---|
| `permission` / `permission_type` | `[]` / `blacklist` | 谁能让 bot 读说说 |
| `read_number` | `5` | 一次取的说说数 |
| `like_probability` / `comment_probability` | `1.0` / `1.0` | 点赞/评论概率 |
| `prompt` / `rt_prompt` | 见 config | 普通说说 / 转发说说的评论模板 |

### `[monitor]`（刷空间）
| 项 | 默认 | 说明 |
|---|---|---|
| `read_list` / `read_list_type` | `[]` / `blacklist` | 刷空间名单 |
| `like_probability` / `comment_probability` | `1.0` / `1.0` | 概率 |
| `silent_hours` | `22:00-07:00` | 静默时段（可多段，逗号分隔） |
| `like_during_silent` / `comment_during_silent` | `false` / `false` | 静默期是否允许点赞/评论 |
| `enable_auto_reply` | `false` | 自动回复自己说说下的评论 |
| `self_read_number` | `5` | 自查的最近说说数 |
| `reply_prompt` / `reply_to_reply_prompt` | 见 config | 评论回复 / 链式回复模板 |
| `processed_feeds_cache_size` / `processed_comments_cache_size` | `100` / `100` | 去重缓存上限 |

### `[routine]`（日程驱动 + 严格决策）⭐ v3.1 增强
| 项 | 默认 | 说明 |
|---|---|---|
| `check_interval_minutes` | `20` | 检查间隔（分） |
| `post_cooldown_minutes` / `browse_cooldown_minutes` | `120` / `40` | 冷却间隔 |
| `respect_silent_hours` | `true` | 复用 `monitor.silent_hours` 卡 post/browse |
| `post_blocked_activities` | `[sleeping, working, studying, eating, exercising]` | 命中直接拒，不调 LLM |
| `browse_blocked_activities` | 同上 | 刷空间黑名单 |
| `max_post_chance` / `max_browse_chance` | `1.0` / `1.0` | LLM 通过后的二次掷骰上限；`0.3` = LLM 说是后 30% 才真发 |
| `require_reason` | `true` | 要求 LLM 输出 `是\|理由` 格式，`/zn debug routine` 能看到 |

### `[llm]`（LLM 调用）
| 项 | 默认 | 说明 |
|---|---|---|
| `text_model` | `replyer` | 文本任务名（对应主程序 `model_task_config`） |
| `llm_timeout_seconds` | `60` | 单次 LLM 超时；⚠️ host RPC 30s 硬上限 |
| `show_prompt` | `false` | 日志打印 prompt 全文 |

### `[diary]`（日记）
| 项 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关（关闭后命令仍能用，只是不定时） |
| `schedule_time` | `23:30` | 每日自动生成时间；窗口跨越触发 |
| `style` | `diary` | `diary` / `qqzone` / `custom` |
| `min_message_count` | `3` | 总消息门槛 |
| `min_messages_per_chat` | `3` | 单聊门槛（剔水群） |
| `per_message_max_chars` | `200` | timeline 单条消息截断（v3.1 新增，0=不截） |
| `min_word_count` / `max_word_count` | `250` / `350` | 字数区间 |
| `filter_mode` / `target_chats` | `all` / `""` | 聊天过滤 |
| `custom_prompt` | `""` | `style=custom` 时的模板，含 `{self_description}` 占位符 |

### `[diary_model]`（日记用第三方 LLM，绕开 host 30s 限制）
| 项 | 默认 | 说明 |
|---|---|---|
| `use_custom_model` | `false` | 开关 |
| `api_url` | `https://api.siliconflow.cn/v1` | OpenAI 兼容 base url |
| `api_key` | `""` | 密钥 |
| `model_name` | `Pro/deepseek-ai/DeepSeek-V3` | 模型名 |
| `temperature` | `0.7` | |
| `api_timeout` | `300` | 秒 |

### `[persona]`（人格扩展）⭐ v3.1 新增
| 项 | 默认 | 说明 |
|---|---|---|
| `self_description` | `""` | 中文自我形象描述（"我是银发红瞳的狐妖"），注入所有 prompt 头部 |
| `use_art_selfie_prompt` | `true` | `self_description` 为空时**自动**从绘卷 `selfie.prompt_prefix` 兜底（默认开启，绘卷未装时自动跳过） |
| `use_multiple_reply_style` | `true` | 按主程序 `personality.multiple_probability` 从备用风格池抽样替换 `reply_style`（与主程序聊天回复一致） |

> **形象注入优先级**：`persona.self_description` 非空 → 用 user 填的；为空且 `use_art_selfie_prompt=true` → 自动读绘卷 `[selfie].prompt_prefix`；都没有 → 不注入。

## Routine 决策四层防线

```
1. 冷却：post_cooldown / browse_cooldown 未到 → 拒
2. 硬规则：
   a. 静默时段（respect_silent_hours=true）
   b. 活动黑名单
   → 拒，不调 LLM
3. LLM 严格决策：清单式 prompt + 严格解析（首字符必须是"是/否"）+ require_reason
4. 二次掷骰：max_*_chance < 1.0 时 LLM 通过后再 random
```

`/zn debug routine` 输出示例：
```
最近 5 次 Routine 决策（按时间倒序）：
  10-25 23:45 [post]   sleeping:睡觉   → 硬规则拒绝 (活动黑名单(sleeping))
  10-25 23:25 [browse] sleeping:睡觉   → 硬规则拒绝 (静默时段(22:00-07:00))
  10-25 20:45 [post]   relaxing:看小说 → ✓ 已执行 (有想分享的轻小说)
  10-25 18:05 [post]   eating:晚饭     → 硬规则拒绝 (活动黑名单(eating))
```

## 测试

### 离线 smoke（30 秒，26 个用例）
```shell
.venv/Scripts/python.exe plugins/MaiTrace/tests/smoke_test.py
```
覆盖：组件注册 / 配置完整 / persona 5 场景 / 配图 5 路径 / 日记 timeline + prompt / Routine 8 场景（含严格决策 4 项）/ publish_topic_api 契约。

### 配置迁移验证
```shell
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'plugins')
import MaiTrace.plugin as P
inst = P.create_plugin()
default = inst.build_default_config()
print('段:', list(default.keys()))
print('严格模式字段:', list(default['routine'].keys()))
print('persona:', list(default['persona'].keys()))
"
```

### 线上手动验证

启动 MaiBot 后在群里按顺序：
1. `/zn help` — 帮助
2. `/zn debug cookie` — Cookie 状态
3. `/zn debug routine` — 决策历史（含严格模式拒绝原因）
4. `/zn 今天阳台的花开了` — 真实发说说
5. `/zn gen 今天` — 生成日记并发布

## 目录结构

```
plugins/MaiTrace/
├── _manifest.json
├── plugin.py                # MaiBotPlugin 入口（生命周期 + @Command/@Tool/@API）
├── config.py                # PluginConfigBase × 10 sections
├── requirements.txt
├── README.md
├── data/                    # 运行时数据（cookies / processed_list / diaries / images 归档）
├── images/                  # 临时生图（clear_image=false 时归档）
├── services/                # 业务层（函数式，无 SDK 装饰器）
│   ├── persona.py           # ⭐ 统一人格加载（multiple_reply_style 抽样 + 绘卷兜底）
│   ├── cookie.py            # 5 种 cookie 获取，自适应重排
│   ├── qzone_api.py         # QzoneAPI HTTP 客户端
│   ├── feed_publish.py      # 发说说（含 custom 模式）
│   ├── feed_read.py         # 读说说 + 点赞评论
│   ├── feed_image.py        # ⭐ 走绘卷 ctx.api.call（无 src.*/plugins.* 越权）
│   ├── monitor.py           # 刷空间 + 多层链式回复
│   ├── routine.py           # ⭐ 严格决策四层防线（cooldown / silent / blacklist / LLM / 掷骰）
│   ├── persistence.py       # processed_* / cookie_stats 持久化
│   ├── permission.py        # 白/黑名单
│   ├── prompts.py           # send/read/reply 模板 + self_description 注入
│   ├── llm_runner.py        # ctx.llm.generate 包装
│   └── diary/
│       ├── pipeline.py      # 抓消息 → timeline → prompt → LLM → 落库 → 发布
│       ├── storage.py       # JSON 文件存储
│       ├── timeline.py      # ⭐ per_message_max_chars 可配
│       ├── prompts.py       # diary / qqzone / custom + self_description
│       └── fetcher.py       # ctx.message + 白/黑名单
├── handlers/                # 表现层（薄壳，分发到 services）
│   ├── commands.py          # /zn 子命令
│   ├── actions.py           # send_feed / read_feed Tool 实现
│   └── apis.py              # 跨插件 @API 实现
├── utils/
│   ├── _envelope.py         # ctx 返回值剥壳
│   ├── _logging.py          # ⭐ plugin.<id>.* 命名空间 logger
│   ├── ctx_config.py        # ⭐ get_global_* 统一 helper
│   ├── date.py              # 日期解析
│   ├── time_window.py       # 静默时段
│   └── tokens.py            # token 估算 / 智能截断
└── tests/
    └── smoke_test.py        # 26 个离线用例
```

## 依赖的其他插件

| 插件 | 用途 | 必需 |
|---|---|---|
| [`maibot-team_napcat-adapter`](../maibot-team_napcat-adapter) | Cookie 取数（`cookie_methods` 含 `adapter` 时） | 否（可用 napcat HTTP 兜底） |
| [`mais_art_journal`](../mais_art_journal) (麦麦绘卷) | AI 生图（`image.image_mode != only_emoji` 时） | 否（可只用表情包） |
| [`xuqian13_autonomous-planning-plugin-v4`](../xuqian13_autonomous-planning-plugin-v4) | 日程驱动 Routine（`get_current_activity` API） | 否（无活动时 Routine 不发不刷） |

## 贡献和反馈

- Issue / Pull Request 欢迎提交
- 联系 QQ：1523640161 / 3082618311

## 鸣谢

[MaiBot](https://github.com/MaiM-with-u/MaiBot)

部分代码参考：[qzone-toolkit](https://github.com/gfhdhytghd/qzone-toolkit)、[diary_plugin](https://github.com/bockegai/diary_plugin)

感谢 [xc94188](https://github.com/xc94188)、[myxxr](https://github.com/myxxr)、[UnCLAS-Prommer](https://github.com/UnCLAS-Prommer)、[XXXxx7258](https://github.com/XXXxx7258)、[heitiehu-beep](https://github.com/heitiehu-beep) 提供的功能改进。
