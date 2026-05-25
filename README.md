# MaiTrace（麦时迹）

属于 MaiBot 自己的 QQ 空间生活插件。让麦麦自主发说说、刷动态、点赞回复、写日记，留下属于麦麦自己的时间足迹。

---

## 功能一览

| 模块 | 入口 | 简述 |
|---|---|---|
| **发说说** | `/zn <主题>` / `@Tool send_feed` / `@API publish_topic_api` | LLM 按主题生成正文 + 自动配图 + 历史去重 → 发到 QQ 空间 |
| **读说说** | `@Tool read_feed` | 拉指定好友最近说说，按概率点赞 + LLM 生成评论 |
| **刷空间** | Routine 自动驱动 | 评论好友未读说说、回复自己说说下的新评论、识别并回复"对 bot 评论的回复"（多层链式） |
| **日记** | `/zn gen [日期]` / `diary.schedule_time` 定时 | 抓当天聊天 → timeline → LLM 生成日记 → 发到 QQ 空间 |
| **Routine 严格决策** | 自动 | 通过规划插件 API 取当前活动；**四层防线**：冷却 → 静默时段 → 活动黑名单 → LLM 严格判定 → 二次掷骰 |
| **跨插件 API** | `ctx.api.call("Rabbit-Jia-Er.MaiTrace.<api>")` | 3 个 `public=True` API：`publish_topic_api` / `send_feed_api` / `get_feeds_list_api` |
| **配图** | 自动 | AI 走绘卷 selfie 流程（外观不被优化器改写）+ 自动 img2img（用绘卷参考图）；表情包匹配 |
| **人格系统** | `[persona]` 段 | `self_description` 注入文本 prompt；自动按 `multiple_reply_style` 抽样切换风格；绘卷 `[selfie]` 兜底 |
| **命令权限** | `[plugin].admin_qq` | 所有 `/zn` 命令统一要求 admin（与 Tool 权限独立） |

---

## 安装

```shell
cd MaiBot/plugins
git clone https://github.com/Rabbit-Jia-Er/MaiTrace.git
```

依赖（manifest 已声明，主程序自动装；手动装见下）：

```shell
# uv（推荐）
uv pip install -r plugins/MaiTrace/requirements.txt

# pip
pip install httpx Pillow bs4 json5 openai
```

启动 MaiBot 后插件目录会自动生成 `config.toml`，按注释填好后重启。

### 必配项

```toml
[plugin]
admin_qq = ["你的QQ"]    # 必填，否则所有 /zn 命令被拒
cookie_methods = ["adapter", "napcat", "clientkey", "qrcode", "local"]
```

### 推荐周边插件（依赖）

| 插件 | 作用 | 必需？ |
|---|---|---|
| [`maibot-team_napcat-adapter`](../maibot-team_napcat-adapter) | 通过 napcat-adapter API 取 cookie（最稳） | 否（可走 napcat HTTP / clientkey / qrcode） |
| [`mais_art_journal`](../mais_art_journal)（麦麦绘卷） | AI 配图，含 `[selfie].prompt_prefix` 形象 / `reference_image_path` 参考图 | 否（可只用表情包配图） |
| [`xuqian13_autonomous-planning-plugin-v4`](../xuqian13_autonomous-planning-plugin-v4) | Routine 日程数据来源 | 否（不装时 Routine 永远"无活动"，不发不刷） |


**依赖**:
- `autonomous_planning` 插件（ https://github.com/xuqian13/autonomous_planning_plugin ）  — 提供日程数据（当前活动）
- `MaiTrace` 插件（ https://github.com/Rabbit-Jia-Er/MaiTrace ） — 发布到 QQ 空间

---

## 命令

所有 `/zn` 子命令都需要调用者在 `[plugin].admin_qq` 列表里（v3.1.2+）：

| 命令 | 说明 |
|---|---|
| `/zn help` | 帮助 |
| `/zn <主题>` | 发一条指定主题的说说 |
| `/zn custom` | 用 `send.custom_qqaccount` 的最新私聊内容当说说 |
| `/zn gen [日期]` | 生成日记（默认今天，含发布到空间） |
| `/zn ls` | 日记列表 + 统计 |
| `/zn v [日期] [编号]` | 查看日记 |
| `/zn <日期>` | 等价 `/zn v <日期>` |
| `/zn debug routine` | Routine 决策历史（含拒绝原因） |
| `/zn debug cookie` | Cookie 状态 + 各方式成功率 |
| `/zn debug msgs [日期]` | 当日消息读取统计 |

日期格式：`YYYY-MM-DD` / `YYYY/MM/DD` / `YYYY.MM.DD` / `今天` / `昨天` / `前天`

> 命令权限 (`[plugin].admin_qq`) 和 Tool/API 权限 (`[send].permission` / `[read].permission`) **互相独立** —— 前者控制直接执行，后者控制 LLM tool calling 时是否允许。

---

## 跨插件 API

```python
# 1. 完整流程：LLM 生正文 + 自动配图 + 发布
result = await self.ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.publish_topic_api",
    topic="今天的晚霞",
    current_activity="散步",  # 可选，会拼到 prompt 末尾
)
# → {"result": True, "story": "晚霞像橘子汽水...", "message": "..."}

# 2. 直发：自备内容和图
await self.ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.send_feed_api",
    message="hello world",
    images=["<base64-1>", "<base64-2>"],  # 可选，最多 4 张，跨进程必须传 base64 字符串
)
# → {"result": True, "message": "说说发送成功，tid=..."}

# 3. 读：拉某 QQ 最近说说
await self.ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.get_feeds_list_api",
    target_qq="10001",
    num=5,
)
# → {"result": True, "message": "成功获取 5 条说说", "data": [...]}
```

---

## 配图链路（重点）

```
说说正文 story
    │
    ├─ persona = resolve_persona(plugin)
    │      reference_image_path ← 绘卷 [selfie].reference_image_path 解析为绝对路径
    │
    └─ collect_images_for_feed(story, reference_image_path=...)
         │
         ├─ AI 路径（image.image_mode = only_ai / random 命中）
         │   ├─ 读参考图 → base64
         │   └─ ctx.api.call("绘卷.generate_image",
         │          prompt        = story（场景，会被 SELFIE_SCENE_SYSTEM_PROMPT 优化）,
         │          selfie_mode   = True     ← 让绘卷走 selfie 流程
         │          selfie_style  = image.selfie_style（默认 "photo" 第三人称）,
         │          input_image_base64 = 参考图 base64    ← img2img
         │       )
         │
         │   绘卷内部：
         │     - 形象 ← [selfie].prompt_prefix（不被优化器改写）
         │     - 自动加 (1girl:1.4)(perfect hands:1.2) + 手部动作描述
         │     - 场景 ← 优化为英文 SD 标签
         │     - 模型不支持 img2img → silent_img2img_fallback 自动降级 txt2img
         │
         └─ emoji 路径（image_mode = only_emoji / random 未中）
             ctx.emoji.get_by_description(description=story)
```

**关键设计**：
- **形象不被优化器改写** —— 走 `selfie_mode=True` 让绘卷用 `SELFIE_SCENE_SYSTEM_PROMPT`（明确禁止改外观），不是默认的 `OPTIMIZER_SYSTEM_PROMPT`（重写整段）
- **MaiTrace 主动传参考图** —— 绘卷 `generate_image` API 不会自动读 `[selfie].reference_image_path`（这是 `/dr` 命令路径独有），必须 MaiTrace 主动 `input_image_base64`
- **视角可配** —— `[image].selfie_style` 默认 `photo`（第三人称，适合叙事说说配图），可改 `standard`（前置自拍）/ `mirror`（对镜自拍）

---

## Routine 严格决策

```
活动来源：ctx.api.call("xuqian13.autonomous-planning-plugin-v4.get_current_activity")

每 check_interval_minutes 分钟跑一次：

  1. 冷却 (post_cooldown_minutes / browse_cooldown_minutes)
       └─ 时间未到 → 拒（reason="冷却中"）

  2. 硬规则
       ├─ respect_silent_hours=true 且当前在 monitor.silent_hours 内 → 拒
       └─ activity_type 在 post/browse_blocked_activities 列表中 → 拒
       默认黑名单：sleeping / working / studying / eating / exercising

  3. LLM 严格判定
       prompt 含清单式"绝对不会"+"才可以"
       require_reason=true 时强制 "是|理由" / "否|理由" 格式
       严格解析：首字符必须是 "是" 或 "否"

  4. 二次掷骰
       max_post_chance / max_browse_chance < 1.0 时
       LLM 通过后再 random() > chance → 拒
```

`/zn debug routine` 输出示例：
```
最近 5 次 Routine 决策：
  23:45 [post]   sleeping:睡觉   → 硬规则拒绝 (活动黑名单(sleeping))
  23:25 [browse] sleeping:睡觉   → 硬规则拒绝 (静默时段(22:00-07:00))
  20:45 [post]   relaxing:看小说 → ✓ 已执行 (有想分享的轻小说)
  18:05 [post]   eating:晚饭     → 硬规则拒绝 (活动黑名单(eating))
```

---

## 配置一览

完整字段见 `config.toml` 注释或 WebUI 配置页（每段都有 `label / hint / depends_on` 元数据）。

### `[plugin]` 基础
| 项 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `admin_qq` ⭐v3.1.2 | `[]` | 命令管理员；空 = 所有 /zn 被拒；与 send/read.permission 独立 |
| `http_host` / `http_port` | `127.0.0.1` / `9999` | Napcat 地址 |
| `napcat_token` | `""` | Napcat HTTP token |
| `cookie_methods` | `[adapter, napcat, clientkey, qrcode, local]` | Cookie 获取顺序；按近期成功率自动重排，qrcode/local 永远在尾部 |

### `[send]` 发说说
| 项 | 默认 | 说明 |
|---|---|---|
| `permission` / `permission_type` | `[]` / `whitelist` | 谁能让 bot 通过 Tool/API 发说说 |
| `prompt` | 见 config | 占位符 `{bot_personality}` `{bot_expression}` `{topic}` `{current_activity}` |
| `history_number` | `5` | 生成时参考的历史说说数（避免重复） |
| `custom_qqaccount` / `custom_only_mai` | `""` / `true` | `/zn custom` 模式取私聊内容的 QQ |

### `[image]` 配图
| 项 | 默认 | 说明 |
|---|---|---|
| `enable_image` | `false` | 总开关 |
| `image_mode` | `random` | `only_ai` / `only_emoji` / `random` |
| `ai_probability` | `0.5` | random 模式下出 AI 图概率 |
| `image_number` | `1` | 每条说说几张图（1-4） |
| `pic_plugin_model` | `""` | 绘卷 `models.<id>`；留空禁用 AI |
| `clear_image` | `true` | 上传后是否删本地副本（false 时归档到 `data/images/`） |
| `selfie_style` ⭐v3.1.5 | `photo` | 绘卷 selfie 视角：`photo`(第三人称) / `standard`(前置自拍) / `mirror`(对镜) |

### `[read]` 读说说 / 评论
| 项 | 默认 | 说明 |
|---|---|---|
| `permission` / `permission_type` | `[]` / `blacklist` | 谁能让 bot 通过 Tool 读说说 |
| `read_number` | `5` | 一次取的说说数 |
| `like_probability` / `comment_probability` | `1.0` / `1.0` | 点赞/评论概率 |
| `prompt` / `rt_prompt` | 见 config | 普通说说 / 转发说说的评论模板 |

### `[monitor]` 刷空间
| 项 | 默认 | 说明 |
|---|---|---|
| `read_list` / `read_list_type` | `[]` / `blacklist` | 刷空间名单 |
| `like_probability` / `comment_probability` | `1.0` / `1.0` | 概率 |
| `silent_hours` | `22:00-07:00` | 静默时段（可多段，逗号分隔） |
| `like_during_silent` / `comment_during_silent` | `false` / `false` | 静默期是否允许 |
| `enable_auto_reply` | `false` | 自动回复自己说说下的评论 |
| `self_read_number` | `5` | 自查的最近说说数 |
| `reply_prompt` / `reply_to_reply_prompt` | 见 config | 评论回复 / 链式回复模板 |
| `processed_feeds_cache_size` / `processed_comments_cache_size` | `100` / `100` | 去重缓存上限 |

### `[routine]` 日程驱动 + 严格决策 ⭐ v3.1
| 项 | 默认 | 说明 |
|---|---|---|
| `check_interval_minutes` | `20` | 检查间隔 |
| `post_cooldown_minutes` / `browse_cooldown_minutes` | `120` / `40` | 冷却 |
| `respect_silent_hours` | `true` | 复用 `monitor.silent_hours` 卡 post/browse |
| `post_blocked_activities` | `[sleeping, working, studying, eating, exercising]` | 命中直接拒，不调 LLM |
| `browse_blocked_activities` | 同上 | 刷空间黑名单 |
| `max_post_chance` / `max_browse_chance` | `1.0` / `1.0` | LLM 通过后的二次掷骰上限 |
| `require_reason` | `true` | 要求 LLM 输出 `是\|理由` 格式 |

### `[llm]`
| 项 | 默认 | 说明 |
|---|---|---|
| `text_model` | `replyer` | 文本任务名（对应主程序 `model_task_config`） |
| `llm_timeout_seconds` | `60` | 单次超时；⚠️ host RPC 30s 硬上限 |
| `show_prompt` | `false` | 日志打印 prompt 全文 |

### `[diary]` 日记
| 项 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关（关闭后命令仍能用，只是不定时） |
| `schedule_time` | `23:30` | 每日自动生成时间；窗口跨越触发 |
| `style` | `diary` | `diary` / `qqzone` / `custom` |
| `min_message_count` | `3` | 总消息门槛 |
| `min_messages_per_chat` | `3` | 单聊门槛（剔水群） |
| `per_message_max_chars` ⭐v3.1 | `200` | timeline 单条消息截断（0=不截断） |
| `min_word_count` / `max_word_count` | `250` / `350` | 字数区间 |
| `filter_mode` / `target_chats` | `all` / `""` | 聊天过滤（all / whitelist / blacklist） |
| `custom_prompt` | `""` | `style=custom` 时的模板 |

### `[diary_model]` 日记直连第三方 API
| 项 | 默认 | 说明 |
|---|---|---|
| `use_custom_model` | `false` | 绕开 host RPC 30s 限制 |
| `api_url` | `https://api.siliconflow.cn/v1` | OpenAI 兼容 base url |
| `api_key` / `model_name` / `temperature` / `api_timeout` | — / `Pro/deepseek-ai/DeepSeek-V3` / `0.7` / `300` | 长 prompt 日记建议 timeout ≥ 300 |

### `[persona]` 人格扩展 ⭐ v3.1
| 项 | 默认 | 说明 |
|---|---|---|
| `self_description` | `""` | 中文形象描述。**仅注入文本 LLM prompt**（说说/评论/日记）。空时自动从绘卷 `[selfie].prompt_prefix` 兜底 |
| `use_multiple_reply_style` | `true` | 按主程序 `personality.multiple_probability` 从风格池抽样替换 `reply_style`（与主程序聊天回复行为一致） |

> 文本形象（`self_description` / 绘卷 `prompt_prefix` 兜底）和配图形象（绘卷 selfie 流程）是**两条独立链路**，互不污染。

---

## 测试

### 一键 smoke（31 用例，30 秒）

```shell
.venv/Scripts/python.exe plugins/MaiTrace/tests/smoke_test.py
```

覆盖：
- **[A] 静态**：语法 / 组件注册 / 配置完整 / manifest / 无 DeprecationWarning
- **[B] persona**：baseline / 用户优先 / 绘卷兜底 / 参考图路径 / 缺失文件降级 / 风格抽样 / system_prefix
- **[C] 配图**：AI 路径 (selfie_mode + photo) / img2img with reference / selfie_style 可配 / emoji 路径 / 归档 / disabled
- **[D] 日记**：timeline 截断 / prompt 含 self_description
- **[E] Routine**：规划 API 3 + 决策解析 / 活动黑名单 / 静默 / 掷骰 / 时间窗（共 8）
- **[F] 跨插件 API**：publish_topic_api 契约
- **[G] 命令权限**：is_admin / admin 覆盖所有 /zn 子命令

### 线上手动验证

启动 MaiBot 后按顺序：

1. `/zn debug cookie` — Cookie 状态
2. `/zn debug routine` — 决策历史
3. `/zn 今天阳台的花开了` — 真发说说，看日志是否 `style=photo mode=img2img`
4. `/zn gen 今天` — 生成日记并发布
5. 非 admin QQ 发 `/zn help` — 应被拒

---

## 目录结构

```
plugins/MaiTrace/
├── _manifest.json
├── plugin.py                # MaiBotPlugin 入口（@Command/@Tool/@API 装饰器）
├── config.py                # PluginConfigBase × 10 sections
├── README.md
├── data/                    # 运行时数据
│   ├── cookies-<uin>.json   # cookie 持久化
│   ├── cookie_stats.json    # cookie 各方式成功率
│   ├── processed_list.json  # 已处理说说去重
│   ├── processed_comments.json
│   ├── diaries/             # 日记 JSON 文件
│   ├── diary_index.json
│   └── qrcode.png           # 扫码登录用
├── images/                  # AI 图归档（clear_image=false 时）
├── services/                # 业务层
│   ├── persona.py           # ⭐ 统一人格加载
│   ├── cookie.py            # 5 种 cookie 获取，自适应重排
│   ├── qzone_api.py         # QzoneAPI HTTP 客户端
│   ├── feed_publish.py      # 发说说主流程
│   ├── feed_read.py         # 读说说 + 评论/点赞
│   ├── feed_image.py        # ⭐ 配图（selfie_mode + img2img）
│   ├── monitor.py           # 刷空间 + 多层链式回复
│   ├── routine.py           # ⭐ 严格决策四层防线
│   ├── persistence.py       # processed_* / cookie_stats 持久化
│   ├── permission.py        # 权限：check_permission / is_admin
│   ├── prompts.py           # send/read/reply 模板（注入 self_description）
│   ├── llm_runner.py        # ctx.llm.generate 包装
│   └── diary/
│       ├── pipeline.py      # 抓消息 → timeline → prompt → LLM → 落库 → 发布
│       ├── storage.py
│       ├── timeline.py      # per_message_max_chars 可配
│       ├── prompts.py       # diary/qqzone/custom 模板
│       └── fetcher.py       # ctx.message + 白/黑名单
├── handlers/                # 表现层（薄壳分发）
│   ├── commands.py          # /zn 子命令（统一 admin 检查）
│   ├── actions.py           # send_feed / read_feed Tool 实现
│   └── apis.py              # 3 个跨插件 @API 实现
├── utils/
│   ├── _envelope.py         # ctx 返回值剥壳
│   ├── _logging.py          # ⭐ plugin.<id>.* 命名空间 logger
│   ├── ctx_config.py        # ⭐ get_global_* 统一 helper
│   ├── date.py
│   ├── time_window.py
│   └── tokens.py
└── tests/
    └── smoke_test.py        # 31 个离线用例
```

---

## 从老版本升级

### v2.5 → v3.0

1. **配置段改名**：旧 `[diary.model]` → `[diary_model]`（pydantic v2 保留前缀冲突）。备份旧 `config.toml` → 删除 → 重启重新生成 → 把旧 `[diary.model]` 内容粘到新 `[diary_model]`。
2. **数据迁移**：旧 `processed_list.json` / `qzone/cookies-*.json` / `qzone/qrcode.png` 加载时自动 move 到 `data/`。
3. **`adapter` cookie 方式**：默认列表新增 `"adapter"`（通过 napcat-adapter 插件 API），放第一位最稳。

### v3.0 → v3.1+

完全向后兼容，但行为变化（自动配置迁移已处理；下面是行为差异）：

1. **`@Action` → `@Tool`** —— SDK 2.x 标准。功能等价。
2. **`[persona]` 新增段** —— 默认行为不破坏旧体验：未填 `self_description` 时自动从绘卷 `[selfie].prompt_prefix` 兜底。
3. **`routine.respect_silent_hours` 默认 `true`** —— `monitor.silent_hours` 现在也卡发说说/刷空间，**比 v3.0 更严**。要回旧行为设 `false`。
4. **活动黑名单默认开启** —— `routine.post_blocked_activities` / `browse_blocked_activities` 默认含 `sleeping / working / studying / eating / exercising`，命中直接拒、不调 LLM。
5. **timeline 单条消息截断**：原硬编码 50 字 → `diary.per_message_max_chars`（默认 200）。
6. **Routine 不再直读 sqlite** —— 改用 `ctx.api.call("xuqian13.autonomous-planning-plugin-v4.get_current_activity")`，需安装 v4 规划插件。
7. **⚠️ v3.1.2 命令权限破坏性变更** —— 所有 `/zn` 命令（含原本任何人能用的 `help` / `v` / `<日期>`）现在要求 `[plugin].admin_qq` 在列表。升级后必须先填 `admin_qq`，否则命令全不能用。

---

## 贡献和反馈

- Issue / PR 欢迎提交
- 联系 QQ：1523640161 / 3082618311

## 鸣谢

[MaiBot](https://github.com/MaiM-with-u/MaiBot) · [qzone-toolkit](https://github.com/gfhdhytghd/qzone-toolkit) · [diary_plugin](https://github.com/bockegai/diary_plugin) · 老版本作者 [internetsb/Maizone](https://github.com/internetsb/Maizone)

特别感谢 [xc94188](https://github.com/xc94188) / [myxxr](https://github.com/myxxr) / [UnCLAS-Prommer](https://github.com/UnCLAS-Prommer) / [XXXxx7258](https://github.com/XXXxx7258) / [heitiehu-beep](https://github.com/heitiehu-beep) 等贡献。
