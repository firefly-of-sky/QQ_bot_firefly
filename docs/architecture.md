# QQbot 3.0 架构

默认入口 `python -m qqbot` 启动 `qqbot.tulpa.app.Application`，不再实例化旧 `Bot`。2.x 的模块保留给兼容 CLI 和回归测试；生产 QQ 消息经过 Tulpa。

## 组件职责

| 组件 | 职责 |
| --- | --- |
| NapCat | QQ 协议、原生消息和操作回执 |
| `OneBot` / `tulpa.relay` | 一条上游 WebSocket，动作关联，带认证的本机 HTTP/WS 桥；断线使下游失效 |
| Tulpa `MCPService` | 授权、会话、消息批次、人格、规划、发送队列、回执、语言学习 |
| `tulpa.client` | 实际 Streamable HTTP MCP 连接、初始化和动态工具发现 |
| `tulpa.runner` | 常驻等待；驱动模型选择工具；绑定当前 session；处理取消和模型预算 |
| `tulpa.settings` | 好友范围、并发上限、附加 MCP 配置 |
| `tulpa.admin` | 原 Tulpa 认证终端管理，不另造管理状态 |

## 一轮对话

1. 接入层核对当前登录账号、好友目录及原生 `sub_type=friend`，为该好友创建或恢复独立 grant。群聊和临时会话不启动 Agent。
2. Tulpa 接收真实事件并去重；`wait_chat_messages` 等待静默，返回有序消息、批次、指导和可确认游标。
3. 模型读取人格快照、当前私聊历史和本批消息，按需调用查询、计算、图片或附加 MCP 工具。
4. 模型调用 Tulpa `plan_chat_reply`，框架检查目标、消息新鲜度和节奏，返回 READY 计划。
5. 模型组织语义气泡并调用 `send_chat_reply`。Tulpa 再检查新消息、账号、授权和停止状态，串行发送并保存回执。
6. 成功、沉默或结果未知后确认本批。旧计划过时则重新等待和规划；UNKNOWN 不自动重试。

自然停止、更正、控制命令和撤回可取消当前模型调用。取消不能收回已交给 QQ 的消息；Tulpa 仍在发送边界核对状态。每个好友只获得当前私聊授权，模型不能创建其他会话或选择其他目的地。额外 MCP 服务的权限由该服务自己控制。

## 扩展边界

模型提供者使用 `LLM.turn`；附加工具通过 `tulpa.toml` 的 MCP 配置发现，保留完整输入 schema。人格由 Tulpa 快照保存。OneBot 桥把 Docker 内网接入转换为框架要求的回环地址，不绕过框架发送与账号检查。

上游固定在 `0090b27fad42428c72e5cbaa903a140ca6bd51d4`。本地补丁包括 `TULPA_ROOT` 数据路径覆盖，以及图片下载对 IPv4-translated 虚拟 DNS 的兼容；仍使用认证 DNS 回退和公网地址校验。原框架的桌面 UI、原生历史导入、调查 Agent、群管理和多媒体发送没有作为默认聊天能力开放。

当前框架最多 8 个活跃会话；模型执行再受 `max_concurrent_requests` 限制。空闲好友会话可以回收；不是无限会话服务。旧版架构见 [2.x 架构](architecture-2.x.md)。
