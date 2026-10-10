# QQbot：私人 QQ 模型助手

使用单独的 QQ 号接收你的私聊，通过可配置的 Chat Completions 兼容 API 回答。当前 v0.2.0 支持连续对话、图片识别、多个模型、模型切换、清空会话、重启恢复。群聊、文件、语音和联网搜索暂未启用。

消息路径：你的 QQ → 机器人 QQ / NapCat → Python 聊天服务 → 模型 API → QQ 回复。

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

程序发送 `model`、`messages`、`max_tokens`、`stream=false`。文字消息使用字符串 content，图片消息使用 text 与 image_url 内容数组，图片通过 Base64 Data URL 传入。要求接口返回 `choices[0].message.content` 文本。仅支持原生 Responses、Anthropic Messages 或 Gemini 协议的端点不能直接填入；只接受 `max_completion_tokens` 等特殊参数的模型也需要后续适配。

## 2. 先验证模型

这些命令只启动临时聊天服务容器，不启动 QQ。第二个检查会产生一次真实 API 请求，可能按服务商规则计费。

```bash
docker compose build bot
docker compose run --rm --no-deps bot python -m qqbot --check-config
docker compose run --rm --no-deps bot python -m qqbot --check-model primary
```

分别看到“配置有效”和“模型调用成功”后继续。配置检查不验证密钥真实有效，也不检查 QQ 是否在线；模型检查只输出成功状态，不显示密钥或回答正文。

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

看到 `QQ WebSocket connected` 后，用你的个人 QQ 私聊机器人。`Ctrl+C` 退出日志查看不会停止容器。

建议按顺序发送：

1. `/status`：确认当前模型和主人私聊模式。
2. `我叫小明，请记住。`，再发 `我叫什么？`：检查连续对话。
3. `/reset`：确认当前问答记录清空。
4. `/models`：查看模型别名；添加第二个模型后可用 `/model secondary` 切换。
5. 再聊一轮，执行 `docker compose restart bot`，发 `/status` 并继续提问：检查重启恢复。
6. 用其他账号或在群里发送消息：应不回复。发 JPG/PNG 图片：应识别图片；发语音：应提示暂不支持。

| 命令 | 功能 |
| --- | --- |
| `/help` | 查看帮助 |
| `/models` | 列出模型，箭头标记当前项 |
| `/model 别名` | 切换模型并清空当前上下文，包括重新选择当前模型 |
| `/reset` | 删除当前已保存问答，保留模型选择 |
| `/status` | 查看当前模型和保存的问答轮数 |

纯文字中以 `/` 开头的消息按命令解析；命令不会发送给模型。图文混合消息整体视为提问，不执行命令。群消息、非主人消息、机器人自身消息、临时会话都忽略。须已互加好友，好友私聊才会处理。

## 图片识别（v0.2.0）

可以直接发送一张图片，机器人默认描述内容并读取清晰文字；也可以在**同一条消息**里发送图片和“读出截图中的报错”“比较这几张图”等问题。分开发送的问题会参考上一轮文字分析，不会重新上传上一张原图。

- 默认支持 JPG、PNG、WebP；每条最多 4 张，每张下载体积不超过 5 MiB，单张下载超时 30 秒。不支持 GIF、闪照、语音、视频、文件附件、转发或引用消息中的图片。
- 需要模型及服务商支持 Chat Completions 的 `image_url` 图片输入和 Base64 Data URL。`supports_images = true` 默认开启请求能力，并不保证服务商支持。纯文字模型可在对应 `[models.别名]` 下设置 `supports_images = false`，或通过 `/model` 切换视觉模型。`/models` 显示的是配置状态。
- 程序只下载 QQ 媒体域名（`qpic.cn`、`qq.com`、`qq.com.cn`、`gtimg.cn` 及其子域名）的 HTTP(S) 图片，不读取本地路径，不跟随重定向。下载连接不携带模型密钥、不使用环境代理，避免转发认证信息。
- 图片内容会发送给当前模型服务商；聊天数据库只保存提问、图片数量说明和模型文字回答，不保存图片、Base64 或临时链接。QQ/NapCat 自身的图片缓存不受此规则控制。后续可围绕分析结果追问，要重新检查图片细节请再次发图。
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

这会重建聊天容器，保留数据库和 NapCat 登录状态。更新完成后，给机器人直接发一张普通截图；只在配置支持的服务商模型上才能成功识别。

## 添加模型与调整体验

在 `.env` 增加 `SECONDARY_API_KEY`；在 `config.toml` 取消第二个模型示例的注释，并填写接口前缀和模型 ID。别名任意且不能含空白。然后执行：

```bash
docker compose up -d --force-recreate bot
docker compose run --rm --no-deps bot python -m qqbot --check-model secondary
```

配置变更需重新启动服务；`.env` 改动需要重新创建容器，单独 `restart` 不会更新容器环境变量。所有已配置模型都必须有非空密钥。移除当前使用的模型后，需要在 QQ 里用 `/model` 选择现有模型，服务不会偷偷切换提供商。

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

重复事件 ID 保存 7 天。事件在开始处理时登记，因此进程中途退出时该条不会自动重放；需要重新发送。队列最多 100 条，满时丢弃新事件并记录日志。运行中断时尚未处理的内存队列不保证恢复。QQ 断线后按 1–30 秒退避重连，不保证补收离线期间消息。

SQLite 位于 `bot-data` Docker 卷；QQ 登录状态、NapCat 配置位于项目 `data/` 目录。`docker compose stop` 暂停，`docker compose up -d` 恢复。`docker compose down` 保留数据；不要使用 `down -v`，除非确定要删除聊天数据卷。

## 开发与测试

项目模块：`config.py` 校验配置，`onebot.py` 负责 WS 和发送回执，`bot.py` 处理消息权限和命令，`llm.py` 处理模型调用和上下文，`store.py` 保存状态。

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
