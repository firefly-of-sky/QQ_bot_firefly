# QQ 聊天记录整理快捷指令：可行性与设计 v0.1

核查日期：2026-10-10。状态：方案设计，尚未交付可安装、可运行的快捷指令。

## 用户需求与结论

用户要求直接从 QQ 批量读取完整聊天记录，输出 `data/<会话>/chat.txt` 与 `images/`，手机上可运行，也兼容 Mac。

- iPhone 原生快捷指令独立读取 QQ 私有聊天数据库：不能作为可行方案交付。iOS 应用隔离限制直接访问，必须有 QQ 提供的共享、导出或其他明确接口。
- iPhone 快捷指令作为入口，Mac 执行批量导出：有条件可实现，需用户接受 Mac 运行依赖，并验证目标账号的记录可访问性。
- 完整性只能相对于实际可访问的数据核验。已删除、仅在其他设备上存在、漫游不可取回的消息，以及失效原图不能保证恢复。

## 建议的操作流程

1. Mac 首次配置导出程序，用需要导出的个人 QQ 账号登录。
2. iPhone 快捷指令连接 Mac，选择好友或群聊及时间范围。
3. Mac 分页读取消息，按消息时间与原始顺序整理，保存可取回的图片。
4. 每条图片消息在聊天文字中写入对应相对路径；取不到图片时保留缺失标记，不假装已成功导出。
5. Mac 打包 ZIP，手机快捷指令接收并保存到“文件”；Mac 也可直接运行同一导出任务。

连接方式待选定：同一局域网的受鉴权服务，或已配置的私人网络。默认不将聊天或管理接口开放到公网。快捷指令需采用提交任务、查询状态、下载结果的方式，避免大型历史导出超过一次请求的等待时间。

## 目标输出示例

```text
data/chat_sample/
├── chat.txt
└── images/
    ├── 001.jpg
    └── 002.png
```

`chat.txt` 示例（虚构内容）：

```text
2026-10-10 10:30:00 张三
这是今天的图片。
[图片: images/001.jpg]

2026-10-10 10:31:00 李四
[图片: images/002.png]

2026-10-10 10:32:00 张三
[图片缺失: 原始消息标识 xxx；原因：资源不可取回]
```

图片应保留真实格式，编号至少三位。重复时间戳需要保留消息顺序；文本中的姓名、日期不得由模型编造。聊天归档无需使用大模型；内容摘要属于另外的可选功能。

## 数据来源与现有项目的关系

本项目 QQbot v0.4.0 使用独立机器人 QQ 账号。机器人账号只能访问该账号有权访问的会话，不能读取主人的个人账号与其他好友的历史。QQbot 自身保存的模型问答也不等于 QQ 完整聊天记录，且不保存聊天原图。

已检查本机 `/Applications/QQ.app`，版本为 7.0.2。尚未核验登录账号、聊天历史范围、导出程序兼容性或原图获取；当前受限环境不能读取 Docker 状态，因此不将现有服务描述为已验证可复用。

可验证的候选路线：

- QQ Chat Exporter（QCE）提供好友、群聊及媒体导出，并列出 macOS Apple Silicon 预览支持和 Docker 部署方式。macOS 原生模式要求先退出日常桌面 QQ，使用专用 QQ 副本；文档说明该副本重新签名后失去 App Sandbox 保护，并与桌面 QQ 共用聊天数据。因此原生安装前必须明确评估这些变化，不能直接假定它只是普通文件整理工具。
- NapCat 有好友和群历史消息接口，但接口存在不代表任意年份的历史都可读取。新建 Docker 登录环境也不等于拥有 Mac 或手机上的全部历史数据。
- QQNT_Export 的前置条件是已经解密的 QQNT 数据库，不能直接作为 iPhone 原生快捷指令的数据来源。

## 实施前需要确定的条件

首要条件是用户接受“手机发起，Mac 执行”。之后确定目标个人 QQ 账号、私聊或群聊范围、Mac 架构以及是否需要跨网络使用。无需用户在聊天中提供密码、登录令牌或聊天正文。

先验证一段已知包含文字、原图和较早消息的会话，核对数量、最早/最晚时间、发送者、消息顺序、图片对应与缺失情况。验证通过后再实现快捷指令与自动打包。当前没有验证过的批量读取链路，不能标记任务为完成。

## 依据

- [Apple：iOS、iPadOS 和 visionOS 中的运行时进程安全性](https://support.apple.com/zh-cn/guide/security/sec15bfe098e/web)
- [Apple：快捷指令可接收的输入类型](https://support.apple.com/en-euro/guide/shortcuts/apd7644168e1/ios)
- [Apple：在 Mac 和其他 Apple 设备上运行快捷指令](https://support.apple.com/zh-cn/guide/shortcuts-mac/apd163eb9f95/mac)
- [QQ Chat Exporter 项目](https://github.com/shuakami/qq-chat-exporter)
- [QCE macOS 部署与数据共享机制](https://shuakami.github.io/qq-chat-exporter/docs/macos-deploy.html)
- [QCE Docker 部署](https://shuakami.github.io/qq-chat-exporter/docs/docker-napcat-deployment.html)
- [NapCat：获取好友历史消息](https://napcat.apifox.cn/226659174e0)
- [QQNT_Export：解密数据库前提](https://github.com/Tealina28/QQNT_Export)
