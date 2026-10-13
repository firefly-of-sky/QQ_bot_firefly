# QQbot 2.0：可调用工具的 QQ 会话助手

使用单独的 QQ 号接收好友私聊，通过可配置的 Chat Completions 兼容 API 回答。v2.0.0 加入自主工具调用：模型可以搜索、读取网页、计算、查询时间，根据中间结果再次调用工具，最后回答。支持独立会话、连续对话、图片识别、引用、撤回取消、Markdown 转纯文本、模型切换和重启恢复。群聊、文件附件、语音、长期记忆和外部 MCP 尚未启用。

消息路径：你的 QQ → NapCat → 会话管理 → Agent（模型 ⇄ 工具）→ 最终回答 → QQ。

2.0 默认启用 Agent，模型需要原生 `tools` / `tool_calls` 支持。配置 `supports_tools = false` 可明确选择旧版兼容聊天，不会因为接口失败而静默降级。完整说明见 [2.0 更新与迁移](docs/v2.0.0.md) 和 [架构与扩展](docs/architecture.md)。

QQ 聊天记录整理工具已移到同级独立项目 [QQChatArchive](../QQChatArchive/README.md)，由该项目单独维护源码、运行组件和 Git 历史。

## 你需要准备

- 一个用于运行机器人的 QQ 号，以及用来向它发消息的个人 QQ 号；两个账号互加好友。
- 当前 Mac 上安装并启动 [Docker Desktop（Apple silicon）](https://docs.docker.com/desktop/setup/install/mac-install/)。打开应用，等待引擎就绪，终端执行 `docker version` 和 `docker compose version` 确认。
- 模型服务商提供的 **API Base URL、准确模型 ID、API Key**。网页会员不代表一定包含 API 额度；这里需要独立可调用的 API。

NapCat 是非官方接入，使用单独机器人账号；兼容性或账号状态变化可能需要重新登录。Mac 关机、睡眠或 Docker 停止后，机器人不能保持在线。现阶段不需要购买服务器。

## 1. 初始化本地配置

在终端执行：

```bash
cd /Users/firefly/Documents/Projects/QQbot
python3 scripts/init_config.py
```

脚本生成 `.env`、`config.toml`，自动生成两个不同的随机令牌；再次运行不会覆盖已有文件。文件权限为仅当前用户可读写。两个文件、数据库和 QQ 登录数据都已加入 `.gitignore`，并从镜像构建上下文排除。

用编辑器打开 `.env`，填写：

- `OWNER_QQ`：**你用来提问的个人 QQ 号**，不是机器人 QQ 号。
- `PRIMARY_API_KEY`：模型 API 密钥。密钥只保存在本机，不需要贴进聊天。
- `ONEBOT_TOKEN`：脚本已生成，稍后复制到 NapCat 的 WebSocket 配置。
- `WEBUI_TOKEN`：脚本已生成，用于 NapCat 管理页面初始登录。登录后如页面要求换密码，按页面完成。

用编辑器打开 `config.toml`：

```toml
[models.primary]
base_url = "https://你的服务商地址/v1"
model = "服务商提供的准确模型ID"
api_key_env = "PRIMARY_API_KEY"
```

`base_url` 应是接口前缀：程序会在后面加 `/chat/completions`。有的服务商前缀不是 `/v1`，按其文档填写，不要机械增加 `/v1`。`api_key_env` 是环境变量名，不是密钥本身。

程序发送 `model`、`messages`、`max_tokens`、`stream=false`。Agent 模式还发送 `tools` 和 `tool_choice`，接收 `tool_calls`，执行工具后以 `role=tool` 回传结果。支持同一回复多工具调用，最终读取 `choices[0].message.content` 文本。文字消息使用字符串 content，图片消息使用 text 与 image_url 内容数组，图片通过 Base64 Data URL 传入。仅支持原生 Responses、Anthropic Messages 或 Gemini 协议的端点不能直接填入；只接受 `max_completion_tokens` 等特殊参数的模型也需要后续适配。

## 2. 先验证模型

这些命令只启动临时聊天服务容器，不启动 QQ。第二个检查会产生一次真实 API 请求，可能按服务商规则计费。

```bash
docker compose build bot
docker compose run --rm --no-deps bot python -m qqbot --check-config
docker compose run --rm --no-deps bot python -m qqbot --check-model primary
docker compose run --rm --no-deps bot python -m qqbot --check-agent primary
```

分别看到“配置有效”“模型调用成功”和“Agent 验证成功”后继续。Agent 检查用计算工具完成一次闭环，最多 3 次模型请求，不连接 QQ；检查成功会显示调用次数。配置检查不验证密钥真实有效，也不检查 QQ 是否在线。模型检查不显示密钥或回答正文。选择兼容模式时跳过 Agent 检查。

## 3. 启动 NapCat 并扫码登录

```bash
docker compose up -d napcat
```

浏览器打开 [本机 NapCat 管理页面](http://127.0.0.1:6099/webui)，使用 `.env` 中的 `WEBUI_TOKEN` 登录，用机器人账号所在的手机 QQ 扫码。如登录后令牌变更，按页面提示或在本机查看 `docker compose logs --tail=80 napcat`；日志可能含管理令牌，不要直接公开粘贴。

登录成功后，在 NapCat 的“网络配置”里编辑已有的 **WebSocket 服务端**，若没有再新建；避免创建两个使用相同端口的服务：

| 配置 | 值 |
| --- | --- |
| 启用 | 是 |
| Host / 主机 | `0.0.0.0`（容器内部） |
| Port / 端口 | `3001` |
| Token | `.env` 中的 `ONEBOT_TOKEN` |
| 消息格式 / messagePostFormat | `array` |
| 上报自身消息 / reportSelfMessage | 关闭 |

保存并启用。这里是 **WebSocket 服务端 / 正向 WS**，由聊天服务主动连接；不要选 WebSocket 客户端。容器间地址已设置为 `ws://napcat:3001`，不需要填写 Mac 的 IP。

Docker 仅把管理端口 6099 映射到 Mac 的 `127.0.0.1`，3001 不对宿主机或局域网开放。NapCat 的 `MODE`、`WEBUI_TOKEN` 环境变量用于初始化；已有配置要在管理页面修改。后续修改 `.env` 的 `ONEBOT_TOKEN` 时也要同步修改页面里的 Token。

部署配置参考 [NapCat Docker 官方仓库](https://github.com/NapNeko/NapCat-Docker) 和 [NapCat 网络配置文档](https://doc.napneko.icu/config/basic)。镜像默认 `latest`；联调通过后可将 `.env` 的 `NAPCAT_IMAGE` 固定为验证过的版本标签或镜像摘要，避免升级引入变化。

## 4. 启动聊天服务并验收

```bash
docker compose up -d bot
docker compose logs -f bot
```

`QQ WebSocket connected` 仅表示接入服务连通，不代表 QQ 账号在线；程序通过心跳和每 30 秒的状态查询记录 `QQ account online/offline`。确认 NapCat 已登录后，用你的个人 QQ 私聊机器人。`Ctrl+C` 退出日志查看不会停止容器。

建议按顺序发送：

1. `/status`：确认当前模型和好友私聊模式。
2. `我叫小明，请记住。`，再发 `我叫什么？`：检查连续对话。
3. `/reset`：确认当前问答记录清空。
4. `/models`：查看模型别名；添加第二个模型后可用 `/model secondary` 切换。
5. 再聊一轮，执行 `docker compose restart bot`，发 `/status` 并继续提问：检查重启恢复。
6. 用已通过好友申请的其他账号私聊：应正常回复且与主人的上下文隔离；在群里发送消息：应不回复。发 JPG/PNG 图片：应等待 3 秒询问用途，补充问题后识别图片；发语音：应提示暂不支持。

| 命令 | 功能 |
| --- | --- |
| `/help` | 查看帮助 |
| `/models` | 仅主人可用：列出模型，箭头标记当前项 |
| `/model 别名` | 仅主人可用：切换主人自己的模型并清空自己的上下文，不改变好友模型 |
| `/reset` | 取消自己当前及排队问题，清空上下文和临时图片 |
| `/cancel` | 取消自己当前及排队问题，保留已完成问答 |
| `/search 问题` | 使用 Tavily 搜索后直接回答，不展示来源列表或引用编号 |
| `/status` | 查看自己的问答轮数和模式；仅主人看到模型别名 |

纯文字中以 `/` 开头的消息按命令解析；`/search` 会将问题交给搜索服务和模型，其他命令不会发送给模型。图文混合消息整体视为提问，不执行命令。群消息、机器人自身消息、陌生人和临时会话都忽略。接入端上报为好友私聊的消息才会处理，不自动通过好友申请。

## 交互与联网更新（v0.5.0）

本次问题清单及处理边界见 [v0.5.0 更新记录](docs/v0.5.0.md)。默认通过提示词要求回答通常 80—180 字、最多 3 个要点，先给结论，必要时只问一个澄清问题；详细解答仍可主动要求。不会按字数硬截断代码、公式或重要内容，实际篇幅仍受模型影响。

好友申请事件和常见“请求添加你为好友”“我们已成功添加为好友，现在可以开始聊天啦～”自动通知不触发模型。手动通过好友后，等对方主动发来实际消息再回答；不会自动批准好友或发送欢迎消息。匹配自动通知是有限文本规则，其他 QQ 客户端文案可能需要补充。

处理超过 12 秒时，根据当前阶段发送一次自然的等待提示（联网查找、看图、解题、写作、代码、资料整理或普通思考），可用 `/cancel` 中止自己正在生成和排队的问题，避免旧答案继续堆积；`/reset` 同时取消并清空历史。取消无法撤回已经送达的消息，也不保证上游停止计费。错误提示附原问题前 20 字，日志使用请求编号、耗时和错误类型，不记录正文。

### 开通搜索（由你申请 Key）

1. 在 [Tavily 控制台](https://app.tavily.com/) 获取 API Key，写入本机 `.env`：`TAVILY_API_KEY=你的密钥`，不要发到聊天或提交 Git。
2. 执行 `docker compose run --rm --no-deps bot python -m qqbot --check-search`，会产生一次真实搜索请求，成功时只显示结果数量。
3. 执行 `docker compose up -d --force-recreate --no-deps bot` 让常驻容器读取新密钥。
4. 在 QQ 发 `/search 上海今天的天气`，检查回答是否直接呈现结果，不再附带来源列表或引用编号。

Agent 模式由模型判断何时搜索、搜索什么，以及是否需要展开网页或继续核实；模型判断不保证覆盖所有实时问题。显式 `/search 问题` 强制本轮取得搜索资料，没有成功搜索就不会发送模型生成的最终答案。`auto_search = false` 时普通对话不开放联网工具，`/search` 仍可使用。未配置搜索 Key 时仅开放计算和时间工具，并告知模型无法联网。`supports_tools = false` 的兼容模式仍使用旧版关键词搜索流程。

Agent 仅将模型选择的查询词或网页 URL 发送到 Tavily，不自动附带完整历史或图片；查询词仍可能包含完成当前任务所需的对话信息。单次搜索返回最多 5 条普通或 6 条深度结果，每轮对话默认最多 8 次工具调用。网页正文使用 Tavily Extract，读取失败会明确返回给模型。工具结果默认最多 8000 UTF-8 字节，超过时标记截断。来源保留在本轮内部资料中，QQ 回复不额外附来源列表，并清理常见引用编号；最终措辞仍受模型影响。服务商原生 `enable_search` 不参与本流程。协议参考 [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search) 和 [Extract API](https://docs.tavily.com/documentation/api-reference/endpoint/extract)。

可选配置放在 `config.toml` 顶部、任何 `[models.*]` 表之前，已有配置不填即采用默认值：

```toml
image_wait_seconds = 3
media_ttl_seconds = 600
progress_delay_seconds = 12
auto_search = true
search_api_key_env = "TAVILY_API_KEY"
```

QQ 风控下线需在 NapCat / 手机 QQ 上处理并重新登录，程序不能消除风控。登录异常与模型超时分开诊断；离线期间的消息无法保证补收。

## 所有好友私聊（v0.4.0）

其他人添加机器人 QQ，你在机器人账号上手动通过好友申请后，就能直接发送文字或图片聊天。已通过申请的老好友也可以使用；不需要配置 QQ 号白名单，也不用给对方 API Key。你的 Mac 和 Docker 仍需运行。群聊尚未开放。

每个人的会话按“机器人账号 + 发送者 QQ”隔离，回答和错误提示都发给对应发送者。主人原有的模型选择和历史记录继续保留，不需要迁移数据库。好友可以使用 `/help`、`/status`、`/reset`、`/cancel`、`/search` 操作自己的会话；`/models` 与 `/model` 仅 `.env` 中 `OWNER_QQ` 对应的主人可用。

普通好友使用 `config.toml` 的 `default_model`。主人在 QQ 中使用 `/model` 只改变主人的会话模型。若要统一改变好友模型，修改 `default_model` 后重新启动 bot；普通好友下次发送消息时会使用新的默认模型，并清空旧模型的上下文，避免混用。主人的模型选择不受此更改影响。

同一个人的消息按顺序处理，不同人可以并发。以下参数放在 `config.toml` 顶部，未填写时自动使用这些默认值：

```toml
max_concurrent_requests = 3
requests_per_minute = 10
```

每人的 60 秒滑动窗口最多接受 10 次有效格式的提问，失败调用也计入；图片下载和模型调用共享最多 3 个并发名额。重复事件不重复计数，命令不消耗提问额度，`/reset` 不重置限流。限流记录保存在内存中，重启后重新计数。所有好友共用你的模型 API 额度；这不是每日费用上限。

验收建议：让另一位好友说“我叫小红”，再问“我叫什么”；你自己的对话不应出现小红的记录。让对方发送 `/reset`、`/model primary`，应分别只清空其个人记录、提示模型管理仅主人可用。无需重新获取 API 或修改 NapCat 网络配置。

## QQ 文字排版（v0.3.0）

模型回答发送前会通过 Markdown 解析器转换成普通文字，再按 QQ 消息长度分段，无需修改现有配置：

- 标题变成 `【标题】`；去掉加粗、斜体的格式标记，保留文字。删除线变成“（删除：内容）”，避免丢失删除语义。
- 列表保留编号和嵌套层次，无序列表使用 `•`。引用用“引用：”标记。
- 链接保留名称和地址；图片链接仅显示说明及地址，不下载或发送图片。
- 表格转成“第几项 + 列名：值”的逐项说明，方便在手机上阅读。
- 代码块移除围栏，保留代码内容、内部空行、缩进和符号；行内代码仅移除反引号。常见 LaTeX 公式保留源码，不渲染公式图片。

系统提示会自动附加适合 QQ 文字消息的排版建议，该建议计入上下文预算。数据库继续保存模型原始回答，后续对话使用原文；帮助、状态、错误提示等程序消息不经过 Markdown 转换。没有图片渲染功能；联网能力见 v0.5.0 说明。

更新方式仍为 `docker compose up -d --build bot`。可要求机器人“用标题、加粗、表格和代码块介绍 Python 列表”，检查输出是否变成清晰的普通文字。程序遵循标准 Markdown 语法，无法完全修复模型生成的不完整格式；未识别部分保留原文。

## 图片识别（v0.2.0）

可以在同一条消息里发送图片和问题，也可以先发图片再发文字。v0.5.0 起，单独图片不立即调用模型：等待 3 秒，期间收到文字就合并处理；未收到则询问用途一次，并继续保留图片链接最多 10 分钟，等你补充问题。连续单独图片合并，等待从最后一条图片开始计算。超过 10 分钟或服务重启后需引用原图片或重发。

- 默认支持 JPG、PNG、WebP；每条最多 4 张，每张下载体积不超过 5 MiB，单张下载超时 30 秒。不支持 GIF、闪照、语音、视频、文件附件或合并转发；支持当前私聊普通引用中的文字和图片。
- 需要模型及服务商支持 Chat Completions 的 `image_url` 图片输入和 Base64 Data URL。`supports_images = true` 默认开启请求能力，并不保证服务商支持。纯文字模型可在对应 `[models.别名]` 下设置 `supports_images = false`，或由主人通过 `/model` 切换自己的视觉模型（普通好友使用默认模型）。`/models` 显示的是配置状态。
- 程序只下载 QQ 媒体域名（`qpic.cn`、`qq.com`、`qq.com.cn`、`gtimg.cn` 及其子域名）的 HTTP(S) 图片，不读取本地路径，不跟随重定向。下载连接不携带模型密钥、不使用环境代理，避免转发认证信息。
- 图片内容会发送给当前模型服务商；聊天数据库只保存提问、图片数量说明和模型文字回答，不保存图片、Base64 或临时链接。QQ/NapCat 自身的图片缓存不受此规则控制。后续可围绕分析结果追问，要重新检查图片细节请引用原图或再次发图。引用缓存最多保留 10 分钟、500 条，仅存内存；缓存未命中时通过 OneBot 查询，并校验当前私聊发送者。机器人发出的旧消息若已不在缓存中，因无法可靠校验接收者，要求用户重发相关内容。
- 图片大小与张数限制独立于文字上下文字节预算；视觉 token 计费和模型窗口限制取决于服务商。图片失败时不调用模型、不写入该轮上下文；模型拒绝图片时显示检查视觉能力或缩小图片的提示。

旧版 `config.toml` 无需改动即可使用默认图片设置。若要调整，将以下参数放在文件顶部、任何 `[models.*]` 表之前：

```toml
max_images = 4
max_image_bytes = 5242880
image_timeout_seconds = 30
```

更新服务：

```bash
docker compose up -d --build bot
```

这会重建聊天容器，保留数据库和 NapCat 登录状态。更新完成后，给机器人发送普通截图并补充问题；只在配置支持的服务商模型上才能成功识别。

## 添加模型与调整体验

在 `.env` 增加 `SECONDARY_API_KEY`；在 `config.toml` 取消第二个模型示例的注释，并填写接口前缀和模型 ID。别名任意且不能含空白。然后执行：

```bash
docker compose up -d --force-recreate bot
docker compose run --rm --no-deps bot python -m qqbot --check-model secondary
```

配置变更需重新启动服务；`.env` 改动需要重新创建容器，单独 `restart` 不会更新容器环境变量。所有已配置模型都必须有非空密钥。移除主人当前使用的模型后，需要由主人在 QQ 里用 `/model` 选择现有模型。普通好友跟随配置中的 `default_model`，它必须指向现有模型。

`config.toml` 中可调整：

- `system_prompt`：系统提示词；`history_turns`：默认保留并最多发送最近 20 轮完整问答。
- `max_input_chars`：默认 8000 字符；`context_budget_bytes`：默认 48000 UTF-8 字节，加每条消息 32 字节开销估算。
- `max_output_tokens`：默认 2048；`request_timeout_seconds`：默认单次 API 请求 90 秒。
- `reply_chunk_chars`：默认每条 QQ 回复不超过 1500 字符，优先按换行拆分，超长单段强制切分。

字节预算不是精确 token 数，也不保证适配每个模型的窗口大小。系统提示和当前输入先占预算，历史从最近一轮向前整轮加入；不足时停止，不拆散问答。请结合模型上下文窗口，为输出预留空间。默认仅保留最近 20 轮，不提供长期记忆，也不保留已清空的聊天备份。`/reset` 删除应用数据库记录，不负责擦除 QQ、服务商或磁盘备份中的记录。

对话内容会发送到当前所选模型服务商。聊天服务日志不记录消息正文、API 密钥和上游错误正文；NapCat 自身日志独立管理。

## 故障处理和运行边界

| 现象 | 操作 |
| --- | --- |
| 找不到 docker / 无法连接 Docker daemon | 安装并打开 Docker Desktop，重新打开终端后检查 `docker version` |
| 镜像拉取失败 | 检查 Docker Desktop 的网络与代理；镜像下载代理需在 Docker 中配置 |
| 配置无效 | 检查 TOML 语法、模型别名、主人 QQ、令牌及所有模型密钥环境变量 |
| 鉴权失败 | 检查 API 密钥是否有效，以及账号是否有模型权限 |
| 请求受限 | 检查额度或频率限制，稍后重新发送 |
| HTTP 400/404 | 检查 base_url、模型 ID、请求参数及 Chat Completions 兼容性 |
| 模型超时 / 无法连接 | 检查模型地址、网络和服务商状态；必要时调整超时 |
| QQ 连接失败 / 不回复 | 确认已扫码登录、正向 WS 已启用、端口 3001、Token 一致、消息格式 array、OWNER_QQ 正确且双方为好友 |
| 本机能访问但容器不能 | 容器的 localhost 指向容器自身；Mac 上的服务应使用 `host.docker.internal` |
| 修改环境变量后无效 | 执行 `docker compose up -d --force-recreate bot` |

需要 HTTP 代理时可在 `.env` 添加 `HTTPS_PROXY=http://host.docker.internal:你的代理端口`、`HTTP_PROXY=...` 和 `NO_PROXY=napcat,localhost,127.0.0.1`。程序读取代理环境变量。不要把模型 API 地址指向 NapCat 端口。

模型失败不写入上下文，也不自动重试或切换服务商。QQ 发送会等待动作成功回执；失败或断线时停止后续分段，不写入该轮上下文、不自动重发，避免重复回复。可能已经收到部分内容，可重新发一条消息请求再答。

重复事件 ID 保存 7 天。事件在开始处理时登记，因此进程中途退出时该条不会自动重放；需要重新发送。网络接收队列最多 100 条，另有最多 100 个已调度任务（包含等待同一会话或模型名额的任务）；队列满时丢弃新事件并记录日志。运行中断时尚未处理的内存队列不保证恢复。QQ 断线后按 1–30 秒退避重连，不保证补收离线期间消息。

SQLite 位于 `bot-data` Docker 卷；QQ 登录状态、NapCat 配置位于项目 `data/` 目录。`docker compose stop` 暂停，`docker compose up -d` 恢复。`docker compose down` 保留数据；不要使用 `down -v`，除非确定要删除聊天数据卷。

## 开发与测试

项目模块：`config.py` 校验配置，`onebot.py` 负责 WS 和发送回执，`bot.py` 处理消息权限和命令，`llm.py` 处理模型调用和上下文，`store.py` 保存状态，`references.py` 校验引用来源，`search.py` 获取带链接的搜索结果。

使用 Python 3.12 或更高版本，在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

测试包含本机临时 HTTP/WS 服务，不需要真实 QQ 或 API Key，不访问模型服务商。覆盖权限、上下文与预算、持久化、模型切换、清空、分段顺序、重复事件、401/403/429/500、超时、连接失败、QQ 断线和重连后的完整收发。

也可在 Docker 环境运行同一套测试：

```bash
docker compose run --rm --no-deps -v "$PWD/tests:/app/tests:ro" -v "$PWD/scripts:/app/scripts:ro" bot python -m unittest discover -s tests -v
```

v0.1.0 的真实文字聊天已由用户验证。v0.2.0 已通过本机 Python 3.14 和 Docker Python 3.12 的 23 项测试，并通过当前模型 API 的红蓝色块图片识别测试（正确返回左红右蓝）。图片版测试覆盖图片解析、多图、模型请求格式、下载失败/超时/超限、权限过滤、纯文字模型拒绝和历史记录不保留图片。实际 QQ 图片链路需要用手机发送一张图片完成验收。


v0.3.0 已通过本机 Python 3.14 和 Docker Python 3.12 的 31 项测试，包含 Markdown 转换、代码/公式保留、表格与嵌套列表、先转换再拆分、历史原文保留及原有文字/图片功能回归。


v0.4.0 已通过本机 Python 3.14 和 Docker Python 3.12 的 40 项测试，新增好友消息路由、跨用户历史隔离、主人权限、个人重置、旧会话兼容、多人并发与限流测试。实际新增好友聊天需通过好友申请后由对方发起测试。


v0.5.0 已通过本机 Python 3.14 和 Docker Python 3.12 的 56 项测试；新增图片需求等待、引用来源隔离、取消生成/排队/引用查询、搜索来源与失败处理测试。实际 QQ 交互及配置 Key 后的真实联网效果仍需端到端验收。


v0.5.1 调整：联网回复隐藏来源列表和引用编号，保留内部检索资料；等待提示改为“稍等稍等，我还在找呢，可以输入/cancel取消哦”。


v0.5.2 调整：等待提示按实际处理阶段和本次任务选择，每类有多个表达，同一用户连续提示尽量不重复。只有搜索调用尚未完成时才说“还在找”，找到资料后改为整理信息；排队期间不声称正在搜索或解题。任务类型使用本地关键词判断，未命中时使用通用思考提示，不额外调用模型。仍每次请求最多提示一次，并保留 `/cancel`。

可在 `config.toml` 末尾添加自定义话术，不必修改代码。列表中填写提示正文，程序自动加上“，可以输入/cancel取消哦”。支持阶段：`queued`、`search`、`image`、`reasoning`、`writing`、`coding`、`summary`、`thinking`；未配置的阶段沿用默认话术。修改后重启 bot。

```toml
[progress_templates]
search = ["稍等稍等，我还在找呢", "我再查查资料，等我一下哦"]
reasoning = ["稍等一下，我还在想这道题呢", "等我捋一捋解题思路哦"]
```


## v0.5.3：撤回取消与薪资检索

好友撤回消息时，程序优先处理 OneBot `friend_recall` 通知，即使聊天队列已满也能取消相关任务。文字生成、搜索、下载图片、引用查询和排队中的对应请求都会停止，不发送取消确认，不把该轮正文或回答写入有效上下文。待处理图片被撤回时也取消用途询问；如果后续问题正在使用这张图片，相关处理一并取消。其他好友与不依赖该消息的请求不受影响。

引用缓存中的相关会话内容随撤回清理；数据库只保留会话和撤回消息 ID、时间作为防重放标记，不保存撤回正文。新版产生的已完成问答带消息 ID 关联，撤回时可删除对应问答。升级前的旧问答没有此关联，无法按 QQ 消息 ID 精确删除，可用 `/reset` 清空。已发到 QQ 的消息、已提交上游服务商的内容、NapCat 自身缓存和磁盘备份不由本功能撤除。离线时未收到的撤回通知无法自动处理。

“岳阳市教师薪资”“能源与动力工程专业华中科技大学薪资水平”这类短语现在也会触发检索。薪资查询拆成当前问题、招聘/待遇/就业报告、到手收入或相关专业应届岗位等方向，使用 Tavily advanced 检索，默认最多 3 次。结果跨查询交错取样、按 URL 去重、排除明显乱码，优先保留金额附近的岗位和待遇说明。搜索资料受到上下文预算约束；部分查询失败但已有资料时使用现有资料并提示模型降低结论确定性，鉴权/额度错误立即停止。

回答优先给有证据支撑的区间与适用人群，区分月薪/年薪、税前/到手、奖金/公积金、编制/学历等口径；没有精确统计时可给相近岗位参考，不能冒充地区或学校专业的平均工资。历史资料需说明样本年份，月薪换算年薪需说明是否仅按12个月计算。完全没有数字依据时仍应说明不足，不强行编造。QQ 回复继续隐藏检索来源列表和引用编号。检索和模型的准确性仍需结合实际岗位核实。

可在 `config.toml` 顶部修改以下默认值，降低查询次数可减少搜索用量：

```toml
search_max_queries = 3
search_max_results = 9
search_timeout_seconds = 45
```

高级检索比基础检索消耗更多 Tavily credits，具体以 [Tavily 官方计费说明](https://docs.tavily.com/documentation/api-credits) 为准。协议依据：[OneBot 好友撤回事件](https://github.com/botuniverse/onebot-11/blob/master/event/notice.md)、[Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)。

验证记录：v0.5.3 新增撤回在排队、搜索、引用、读图、生成和分段发送期间的回归；两个薪资示例已进行真实搜索与模型调用验证，另使用同批资料验证薪资证据约束。QQ 客户端实际撤回仍需用户现场验收。

本版已通过本机 Python 3.14 与 Docker Python 3.12 的 78 项自动测试。
