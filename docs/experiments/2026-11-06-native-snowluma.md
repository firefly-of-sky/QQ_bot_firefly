# 原生 QQ → SnowLuma 分时对照

本轮用户已批准先观察纯原生 QQ 24 小时，再对 SnowLuma 做功能及至少 24 小时在线对照。当前只启动第一阶段，不能把脚本准备、进程存活或扫码成功当作 24 小时结论。

## 第一阶段：纯原生客户端

服务器仍为同一台 Ubuntu 22.04。原 QQ 为 3.2.34-53644，本轮从原镜像构建记录找到腾讯原始安装地址：

`https://qqdl.gtimg.cn/qqfile/QQNT/9.9.36/beta/9ee04bef/linuxqq_3.2.34-53644_amd64.deb`

下载 SHA256 为 `436c65e1dd28422e128a21cbddb5fe4e2e6c079d7f27fec2541e014d38cc336e`。恢复了官方 package.json，官方 main 为 `./application.asar/app_launcher/index.js`，不再使用 loadNapCat.js；`dpkg -V linuxqq` 无输出。移除镜像里的 NapCat 程序和 ZIP，入口仅启动虚拟桌面、原生 QQ 及只读 GUI 观测工具，不执行原 NapCat 入口的环境处理代码。

原版 Bot 5.1.0、NapCat 容器已显式停止，维护租约阻止守护进程拉起。没有并行登录同一账号。原部署、聊天数据库与长期画像不修改；启动前生成私有备份 `qqbot-20261106T102448086742Z.tar.gz`，停止 QQ 后克隆持久目录到 `data/native-control/QQ`。只清理副本中失效的 Chromium 单实例锁。保留原 hostname、MAC、machine-id 与已有 QQ 机器信息，不生成随机 GUID。

当前原生镜像 `qqbot-native-qq:3.2.34-53644` ID 为 `sha256:bbe2852a671b0856c1dd2c2b3f3b596af9a36c7a54186b16c6f70457a25ceca5`。为显示/读取 GUI 补齐了桌面依赖及中文字体，字体在当前容器中补装，后续 Dockerfile 已包含。与旧 NapCat 环境相比，这些是额外运行环境差异，因此第一阶段只能回答“本服务器原生运行条件下是否复现”，不能凭此单独判定究竟哪一个 hook 或依赖是根因。

原生容器名 `qqbot-native-qq-1`，不自动重启；若进程退出或重启，单独记录故障，不把恢复后的会话拼成连续在线。远程桌面端口仅发布到服务器 `127.0.0.1:6081`，只通过授权 SSH 隧道访问。原生 VNC 仅在容器回环监听、不单独发布，桌面入口依赖 SSH 鉴权，不在公网开放。

## 观察口径

手机确认后，已通过远程桌面核对 firefly 主窗口及绿色状态点。服务器用标准 AT-SPI 接口寻找自己的主导航及可见 6–12 像素状态按钮，仅在内存读取该状态区域的 RGB。主导航齐全、绿色状态点且无登录/连接告警才记 online；无绿色、超时、状态不明均为 unknown，不把它们算 QQ 身份失效。明确登录界面在已确认登录之后出现，或可见失效对话框，才记 offline。

**这里的 online 仅代表“QQ GUI 显示在线”，不是 QQ 服务端心跳或端到端消息投递证明。** 未注入框架时不能用 OneBot 探针，也没有伪造一个网络在线接口。发生 unknown、采样间隔超过 150 秒或时钟倒退时，本轮覆盖标记失效；即使到 24 小时最终是在线，也只给 `OBSERVATION_ENDED_UNVERIFIED`，不宣称通过。

`qqbot-native-observation.timer` 每分钟执行 `sample_native_qq.py`。首轮真实采样于北京时间 **2026-11-06 18:39:53** 开始，计划截止 **2026-11-07 18:39:53**。它不回填从手机确认到校准结束期间的未采样时间。已验证自动执行第二轮，首两轮均为 GUI online。起始容器时间在 Ubuntu Python 3.10 对 Docker 纳秒时间戳解析修正后补齐，容器实际启动早于首样本且 RestartCount=0，修正没有重启 QQ 或重置计时。

私有摘要 `run/native-qq-observation.json`，探针 `run/native-qq-probe.json`；原始有界日志压缩保存 `run/native-qq-logs`，0700/0600，保留 72 小时/最多 64 MiB。仅摘要进入 journal，原始日志、截图、聊天、QQ 号码、凭据与二维码 URL 不进入 Git。运行标记中的 lease 不打印。采集独立于 Mac/Codex。未重新创建 GPT 自动任务。

24 小时完成、明确离线或故障只落盘，**不自动加载 SnowLuma，也不自动恢复旧 Bot**，避免打断有意义的原生对照。当前机器人暂停回复；下一阶段由用户回来查看结果后继续。

只读查看：

```bash
python3 /opt/qqbot/current/scripts/cloud/observe_native_qq.py --summary
systemctl status qqbot-native-observation.timer
```

如用户决定提前结束，`native_qq_experiment.py restore` 会核对独立维护租约，停用本实验定时器、停止原生容器，恢复旧部署的进程。未完成观察会标记人为取消，不冒充 QQ 掉线。恢复进程不等于账号已在线；原 NapCat 数据保持原样，可能仍需扫码，不能自动把副本登录态当成原会话。

## 第二阶段：候选已核对，尚未运行

[NapCat 维护者置顶说明](https://github.com/NapNeko/NapCatQQ/issues/2085)直接链接 SnowLuma，并未承诺根治。[SnowLuma v2.4.0](https://github.com/SnowLuma/SnowLuma/releases/tag/v2.4.0) 支持 Linux 与 OneBot v11。固定官方 amd64 候选为：

`motricseven7/snowluma@sha256:74215e3c785099b809a6d00be107c63ecd6deeabf521ec99faf863e156b8573b`

官方 tag v2.4.0 的多架构摘要为 `sha256:9b035551e8615a20d8367601a6a61c641796d9ed870f5501830f045b68f74667`。本次服务器拉取因 Docker Hub 连接超时失败，未执行 SnowLuma、未设置接受协议变量。来源为 [Docker Hub tag 元数据](https://hub.docker.com/v2/repositories/motricseven7/snowluma/tags/v2.4.0)。

[官方 Docker 源](https://github.com/SnowLuma/SnowLuma.Docker.Framework)当前默认不同的 QQ 版本（3.2.32_260812）、运行系统和启动处理，不能宣称直接换该镜像是“仅替换框架”的单因素测试。下一阶段固定并记录实际 QQ 版本、标识、目录和资源限制，分时运行；若换到不同 QQ 版本，结论只针对组合方案。

预计沿用 OneBot 正向 WS/Bearer：`networks.wsServers`、`role=Universal`、`messageFormat=array`、`reportSelfMessage=true`。必须逐项核对自身消息事件、好友目录、私聊/图片、真实回复、主人操作及 UNKNOWN 回执，不能只因 API 返回 ok 就认定所有权限兼容。官方有部分占位/未实现接口，需要逐项测试。

SnowLuma 官方发行包需接受 [v2.4.0 EULA](https://github.com/SnowLuma/SnowLuma/blob/v2.4.0/EULA.md) 与 [隐私说明](https://github.com/SnowLuma/SnowLuma/blob/v2.4.0/PRIVACY.md)。其中对非商业、原生组件再封装与脚本部署有具体限制；不将其二进制/native 组件加入本公开仓库，也不静默预置接受协议变量。启动第二阶段前向用户说明并处理真实出现的协议确认，不把“源码可见”当作任意再分发授权。若支持，明确关闭可选遥测。后续至少观察 24 小时；仍失败才考虑本地常开环境对照。

## 测试与代码范围

最终完整回归 208 项通过，原生状态/GUI探针专项 25 项通过（服务器实际 Python 3.10 环境同样通过）。已覆盖原生进程与QQ身份区分、未知探针、日志保密、观察缺口、时钟变化、24小时窗口、人工取消、Docker纳秒时间戳、GUI特征及状态像素分类。实际QQ的10个进程映射可读取，未发现NapCat/SnowLuma组件。实验代码、配置和结论记录进入Git；QQ安装包、镜像、账号副本、截图和原始日志不入库。
