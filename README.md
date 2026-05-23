# MaiTrace（麦时迹）

MaiBot 的 QQ 空间插件。让你的麦麦发说说、刷空间、自动评论点赞、回复评论、写日记！

> **v3.0 起已迁移到新版 MaiBot SDK**（`maibot_sdk` 2.0+）。代码按 services / handlers / utils 三层重构，老版本所有功能保留。从 v2.5 升级请阅读下方"升级指南"。

## 功能

**发说说** — `/zn <主题>` 或自然语言触发（Action），根据人格和历史说说生成内容，可选 AI 配图

**读说说** — 自然语言触发（"读一下我的QQ空间"），获取好友说说并点赞评论

**自动刷空间** — Routine 模式驱动，自动评论、点赞、回复评论：
- 评论未读的好友说说
- 按概率点赞
- 回复自己说说下的新评论（`monitor.enable_auto_reply`）
- 回复他人空间中对 bot 评论的回复（支持多层链式）

**日记** — 从聊天记录生成日记，手动 `/zn gen` 或定时自动生成

**Routine** — 依赖 autonomous_planning_plugin 的日程数据，LLM 决策是否发说说/刷空间

**跨插件 API** — 通过 `ctx.api.call("Rabbit-Jia-Er.MaiTrace.send_feed_api", ...)` 让其他插件复用 MaiTrace 的发说说和取列表能力

## 使用方法

### 安装插件

1. 进入你的麦麦 plugins 目录：

   ```shell
   cd MaiBot/plugins
   ```

2. 克隆本仓库：

   ```shell
   git clone https://github.com/Rabbit-Jia-Er/MaiTrace.git
   ```

3. 安装依赖（任选一种）：

   - **一键包**：在`![[点我启动!!!`后菜单选择交互式安装 pip，按模块依次装 `httpx`、`Pillow`、`bs4`、`json5`、`openai`

   - **docker**：宿主机内
     ```bash
     docker exec -it maim-bot-core uv pip install -r plugins/MaiTrace/requirements.txt --system
     ```
     （docker-compose.yaml 中按需持久化 python 包）

   - **uv**：在 plugins/MaiTrace 下
     ```shell
     uv pip install -r ./requirements.txt -i https://mirrors.aliyun.com/pypi/simple --upgrade
     ```

   - **pip**：在 MaiBot 文件夹下
     ```shell
     .\venv\Scripts\activate
     cd .\plugins\MaiTrace\
     pip install -i https://mirrors.aliyun.com/pypi/simple -r .\requirements.txt --upgrade
     ```

4. 启动 MaiBot，插件目录下会自动生成 `config.toml`，按注释填写后重启即可。

## 从 v2.5 升级到 v3.0（重要）

1. **配置段改名**：原 `[diary.model]` 段必须改成 `[diary_model]`（Pydantic v2 保留字冲突）。
   - 推荐做法：备份旧 `config.toml` → 删除 `config.toml` → 重启 MaiBot 让插件重新生成 → 把旧文件里 `[diary.model]` 段的内容粘到新文件的 `[diary_model]` 段下。
2. **数据文件迁移**：旧版 `processed_list.json` / `qzone/cookies-<uin>.json` / `qzone/qrcode.png` 在新版会自动迁移到 `data/` 下（插件加载时一次性 move）。无需手动操作。
3. **新增 adapter Cookie 方式**：`plugin.cookie_methods` 默认列表新增了 `"adapter"`（通过 napcat-adapter 插件 API 取 cookie，无需配置 Napcat HTTP 端口），推荐放在首位。
4. **命令、配置项名称、Prompt 模板全部保持不变**，旧版用户体验无变化。

## 命令与触发

### 命令

所有命令统一使用 `/zn` 前缀：

| 命令 | 说明 | 权限 |
|------|------|------|
| `/zn help` | 查看帮助 | 所有人 |
| `/zn <主题>` | 发一条指定主题的说说 | send 权限 |
| `/zn custom` | 发送自定义私聊内容的说说 | send 权限 |
| `/zn gen [日期]` | 生成日记（默认今天） | send 权限 |
| `/zn ls` | 查看日记列表和统计 | send 权限 |
| `/zn v [日期] [编号]` | 查看日记 | 所有人 |
| `/zn <日期>` | 等价于 `/zn v <日期>` | 所有人 |

日期格式支持：`YYYY-MM-DD`、`YYYY/MM/DD`、`YYYY.MM.DD`、`今天`、`昨天`、`前天`

### 自然语言触发

聊天中提到"说说"、"空间"、"动态"等关键词时，可触发以下 Action：

| Action | 说明 | 权限 |
|--------|------|------|
| 发说说 | "发条说说"、"发一条xxx的说说" | send 权限 |
| 读说说 | "读一下我的QQ空间"、"评价一下xxx的空间" | read 权限 |

### 自动行为

以下行为由 Routine 模式自动执行：

| 行为 | 说明 | 控制配置 |
|------|------|----------|
| 自动评论/点赞好友说说 | 刷空间时对未读说说评论点赞 | `[monitor]` 概率和名单 |
| 自动回复自己说说下的评论 | 有人评论 bot 的说说时自动回复 | `monitor.enable_auto_reply` |
| 自动回复他人空间中对 bot 评论的回复 | 有人回复了 bot 的评论时自动回复 | 非静默时段自动生效 |
| 自动生成日记 | 到达设定时间自动生成 | `diary.enabled` + `diary.schedule_time` |

## 跨插件 API

其他插件可以这样调用 MaiTrace：

```python
# 发说说
result = await ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.send_feed_api",
    params={"message": "hello world", "images": []},
)
# → {"result": True, "message": "说说发送成功，tid=..."}

# 取说说列表
result = await ctx.api.call(
    "Rabbit-Jia-Er.MaiTrace.get_feeds_list_api",
    params={"target_qq": "10001", "num": 5},
)
# → {"result": True, "message": "成功获取 5 条说说", "data": [...]}
```

## 配置

### `[plugin]`

| 项 | 默认值 | 说明 |
|----|--------|------|
| `enabled` | `true` | 启用插件 |
| `http_host` | `"127.0.0.1"` | Napcat 地址 |
| `http_port` | `"9999"` | Napcat 端口 |
| `napcat_token` | `""` | Napcat Token |
| `cookie_methods` | `["adapter", "napcat", "clientkey", "qrcode", "local"]` | Cookie 获取方式，按顺序尝试 |

| 方式 | 说明 |
|------|------|
| `adapter` | 通过 napcat-adapter 插件 API 取（**v3 新增、推荐**） |
| `napcat` | 通过 Napcat HTTP 接口取 |
| `clientkey` | 通过本机 QQ 客户端取（需 QQ 在同一机器） |
| `qrcode` | 扫描插件目录下的二维码登录（有效期约 1 天） |
| `local` | 读取 `data/cookies-<uin>.json` 缓存 |

### `[send]` / `[read]` / `[monitor]` / `[routine]` / `[models]`

字段与 v2.5 完全一致，详见生成的 config.toml 里的注释。

### `[diary]` / `[diary_model]`

> **v3 改动**：原 `[diary.model]` 段改名为 `[diary_model]`。

`[diary]` 字段与 v2.5 完全一致。`[diary_model]`：

| 项 | 默认值 | 说明 |
|----|--------|------|
| `use_custom_model` | `false` | 使用自定义模型 |
| `api_url` | `"https://api.siliconflow.cn/v1"` | API 地址 |
| `api_key` | `""` | API 密钥 |
| `model_name` | `"Pro/deepseek-ai/DeepSeek-V3"` | 模型名称 |
| `temperature` | `0.7` | 温度 |
| `api_timeout` | `300` | 超时（秒） |

## 目录结构（v3）

```
plugins/MaiTrace/
├── _manifest.json
├── plugin.py                # MaiBotPlugin 入口（生命周期 + @Command/@Action/@API）
├── config.py                # PluginConfigBase × 8 sections
├── requirements.txt
├── data/                    # 运行时数据（cookies/processed_list/diaries 等）
├── images/                  # 生图缓存
├── services/                # 业务层（无 SDK 装饰器）
│   ├── cookie.py            # 5 种 cookie 获取方式
│   ├── qzone_api.py         # QzoneAPI HTTP 客户端
│   ├── feed_publish.py      # 发说说（含 custom 模式 / AI 配图 / 表情包）
│   ├── feed_read.py         # 读说说 + 点赞/评论包装
│   ├── feed_image.py        # 麦麦绘卷桥接 + emoji
│   ├── monitor.py           # 刷空间 + 回复评论（含多层链式）
│   ├── routine.py           # autonomous_planning 日程驱动 + LLM 决策
│   ├── persistence.py       # processed_list / cookies 持久化
│   ├── permission.py        # 白/黑名单
│   ├── prompts.py           # prompt 构建
│   ├── llm_runner.py        # ctx.llm.generate 包装
│   └── diary/
│       ├── pipeline.py      # 日记编排（取消息→时间线→prompt→LLM→落库→发布）
│       ├── storage.py
│       ├── timeline.py      # 含天气推断/图片检测
│       ├── prompts.py       # diary/qqzone/custom 三种模板
│       └── fetcher.py       # ctx.message + 黑/白名单
├── handlers/                # 表现层
│   ├── commands.py          # /zn 子命令分发
│   ├── actions.py           # SendFeed / ReadFeed Action
│   └── apis.py              # 跨插件 @API 实现
└── utils/
    ├── _envelope.py         # 处理 ctx 返回的 success/error 信封
    ├── date.py              # parse_date / format_date_str
    ├── time_window.py       # 静默时段
    └── tokens.py            # token 估算 / 智能截断
```

## 贡献和反馈

- **任何漏洞、疑问或建议欢迎提交 Issue 和 Pull Request**
- **联系 QQ：1523640161 / 3082618311**

---

## 鸣谢

[MaiBot](https://github.com/MaiM-with-u/MaiBot)

部分代码参考：[qzone-toolkit](https://github.com/gfhdhytghd/qzone-toolkit)、[diary_plugin](https://github.com/bockegai/diary_plugin)、[MaiTrace v3 独立版](https://github.com/Rabbit-Jia-Er/MaiTrace)

感谢 [xc94188](https://github.com/xc94188)、[myxxr](https://github.com/myxxr)、[UnCLAS-Prommer](https://github.com/UnCLAS-Prommer)、[XXXxx7258](https://github.com/XXXxx7258)、[heitiehu-beep](https://github.com/heitiehu-beep) 提供的功能改进。
