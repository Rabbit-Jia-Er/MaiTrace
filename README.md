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
| [`mais_art_journal`] 插件（ https://github.com/1021143806/mais_art_journal ）（麦麦绘卷） | AI 配图，含 `[selfie].prompt_prefix` 形象 / `reference_image_path` 参考图 | 否（可只用表情包配图） |
| [`xuqian13_autonomous-planning-plugin-v4`] 插件（ https://github.com/xuqian13/autonomous_planning_plugin ） | Routine 日程数据来源 | 否（不装时 Routine 永远"无活动"，不发不刷） |

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

## 配置一览

完整字段见 `config.toml` 注释或 WebUI 配置页（每段都有 `label / hint / depends_on` 元数据）。

### `[plugin]` 基础
| 项 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `admin_qq` | `[]` | 命令管理员；空 = 所有 /zn 被拒；与 send/read.permission 独立 |
| `http_host` / `http_port` | `127.0.0.1` / `9999` | Napcat 地址（HTTP服务器） |
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
| `selfie_style` | `photo` | 绘卷 selfie 视角：`photo`(第三人称) / `standard`(前置自拍) / `mirror`(对镜) |

> **⚠️ host RPC 30 秒上限**：MaiTrace 调绘卷走 `ctx.api.call`，host 端 30 秒硬超时，会导致**说说发出但无配图**（日志：`feed_image | 调用绘卷 generate_image 异常: [E_TIMEOUT]`）。解决：在**绘卷**配置里把 `[models].<id>.default_size` 改为 `1024x1024`（通常 10s 内），或换更快的绘图模型。

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

### `[routine]` 日程驱动 + 严格决策
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
| `per_message_max_chars` | `200` | timeline 单条消息截断（0=不截断） |
| `min_word_count` / `max_word_count` | `250` / `350` | 字数区间 |
| `filter_mode` / `target_chats` | `all` / `""` | 聊天过滤（all / whitelist / blacklist） |
| `custom_prompt` | `""` | `style=custom` 时的模板 |

### `[diary_model]` 日记直连第三方 API
| 项 | 默认 | 说明 |
|---|---|---|
| `use_custom_model` | `false` | 绕开 host RPC 30s 限制 |
| `api_url` | `https://api.siliconflow.cn/v1` | OpenAI 兼容 base url |
| `api_key` / `model_name` / `temperature` / `api_timeout` | — / `Pro/deepseek-ai/DeepSeek-V3` / `0.7` / `300` | 长 prompt 日记建议 timeout ≥ 300 |

### `[persona]` 人格扩展
| 项 | 默认 | 说明 |
|---|---|---|
| `self_description` | `""` | 中文形象描述。**仅注入文本 LLM prompt**（说说/评论/日记）。空时自动从绘卷 `[selfie].prompt_prefix` 兜底 |
| `use_multiple_reply_style` | `true` | 按主程序 `personality.multiple_probability` 从风格池抽样替换 `reply_style`（与主程序聊天回复行为一致） |

> 文本形象（`self_description` / 绘卷 `prompt_prefix` 兜底）和配图形象（绘卷 selfie 流程）是**两条独立链路**，互不污染。

---

## 贡献和反馈

- **制作者水平有限，任何漏洞、疑问或建议,欢迎提交 Issue 和 Pull Request！**
- **或联系QQ：3082618311**
- **其余问题请联系作者修复或解决（部分好友请求可能被过滤导致回复不及时，请见谅）**

---

## 鸣谢

[MaiBot](https://github.com/MaiM-with-u/MaiBot)

部分代码来自仓库：[qzone-toolkit](https://github.com/gfhdhytghd/qzone-toolkit) · [diary_plugin](https://github.com/bockegai/diary_plugin) · [Maizone](https://github.com/internetsb/Maizone)  

特别感谢[xc94188](https://github.com/xc94188)、[myxxr](https://github.com/myxxr)、[UnCLAS-Prommer](https://github.com/UnCLAS-Prommer)、[XXXxx7258](https://github.com/XXXxx7258)、[heitiehu-beep](https://github.com/heitiehu-beep)提供的功能改进
