# QQbot 4.0 云端运行

4.0 沿用真实 Tulpa MCP 会话核心，增加云端资源限制、进程健康检查、失败会话恢复、定时备份和版本切换。代码通过测试不代表服务器部署已完成；实际验收需另外确认 QQ 登录、模型调用和好友端收发。

目标是 Ubuntu 22.04、2 核 4 GiB。Compose 给 NapCat 限制 1800 MiB，机器人限制 1100 MiB，并配置日志轮转；安装脚本在没有现有交换空间时增加 2 GiB swap。模型在远程 API 运行。不要在此规格上增加本地大模型。

## 目录与安装

```text
/opt/qqbot/
  .env                 模型密钥、QQ Token，权限 0600
  config.toml          模型配置
  tulpa.toml           MCP 扩展与会话设置
  personas/            人格文件
  data/bot/            原历史数据库和 Tulpa 数据
  data/qq/             QQ 设备与登录状态
  data/napcat/         NapCat 配置
  data/plugins/
  releases/<版本>/     源码、部署脚本、release.env
  current -> releases/<版本>
  run/                 维护标记、监控计数与互斥锁
  backups/             最近 7 份备份
```

本机 `python3 scripts/cloud/package.py` 生成源码白名单压缩包和 SHA256；私钥、`.env`、聊天数据不进入源码包。私密配置和迁移数据单独通过已经核对主机指纹的 SSH 上传。

在服务器上用 root 执行源码中的 `scripts/cloud/install-host.sh`。镜像必须与服务器架构匹配：Mac 的 ARM 镜像不能直接当作 x86 镜像部署。可在本机使用 `docker buildx build --platform linux/amd64 --load -t qqbot-bot:4.0.0 .`，随后 `docker save`、上传并 `docker load`；也可在服务器构建。

若服务器无法访问 Docker 下载站，可按阿里云官方镜像文档设置 `DOCKER_APT_REPO=https://mirrors.aliyun.com/docker-ce/linux/ubuntu` 后执行安装脚本。软件源仍验证签名。激活脚本给只读配置设置 `root:10001` 和 `0640`，让容器中的非 root 用户可以读取配置；`.env` 保持宿主 root 私有。

每个发布目录放置 `release.env`，只写两个已加载的镜像引用，例如：

```dotenv
QQBOT_IMAGE=qqbot-bot:4.0.0
NAPCAT_IMAGE=mlikiowa/napcat-docker@sha256:<已验证的摘要>
```

先停止本机 bot，生成完整迁移快照，再复制数据。`data/bot` 由容器内 UID 10001 使用。避免同时运行两个回复实例。将 `.env`、配置、人格文件放到根目录；确认 NapCat 正向 WS 在容器内监听 `0.0.0.0:3001`，与 `ONEBOT_TOKEN` 一致。最后执行：

```bash
bash /opt/qqbot/releases/<版本>/scripts/cloud/activate.sh /opt/qqbot/releases/<版本>
python3 /opt/qqbot/current/scripts/cloud/ops.py status
```

激活脚本检查配置和本地镜像，再切换 `current` 并启动。容器启动命令失败时恢复原发布目录和镜像引用；没有旧版本的首次部署保留维护标记，供人工处理。它不会把“等待扫码”当作启动失败。后续回退可再次激活旧目录；若未来版本改变数据库格式，应先验证兼容性或恢复升级前备份。

## 扫码与日常查看

云端管理端口只绑定服务器的 `127.0.0.1:6099`。通过 SSH 隧道访问：

```bash
ssh -N -L 6099:127.0.0.1:6099 <服务器SSH配置>
```

然后在本机打开 `http://127.0.0.1:6099/webui/`。不要把 6099、3001 或 Tulpa 管理端口开放到公网。若本机 NapCat 占用 6099，可改用 `-L 16099:127.0.0.1:6099`，访问本机 16099。

登录失效时需要本人使用机器人手机 QQ 扫码。进程自动恢复不能绕过 QQ 风控、模型账户余额、云服务器欠费或网络中断。

首次扫码成功后执行 `python3 /opt/qqbot/current/scripts/cloud/ops.py configure-login`，将当前已验证账号设为 NapCat 快捷登录账号。它使用已有登录资料，不收集 QQ 密码。QQ 如返回“登录需要手Q验证”，仍需手机确认或重新扫码；快捷登录不是保证凭据始终有效的机制。

```bash
python3 /opt/qqbot/current/scripts/cloud/ops.py status
python3 /opt/qqbot/current/scripts/cloud/ops.py backup
python3 /opt/qqbot/current/scripts/cloud/ops.py stop  # 设置维护标记，监控不会擅自拉起
python3 /opt/qqbot/current/scripts/cloud/ops.py start
systemctl list-timers 'qqbot-*'
journalctl -u qqbot-watchdog.service -n 20
```

状态输出不包含密钥、QQ 号码、聊天原文或二维码。`ready=true` 表示 QQ 已在线、框架接收器连通、当前会话工作进程正常；不代表模型服务一定可用，仍需真实消息验证。健康检查 `/health/live` 只判断进程响应；`/health/ready` 判断连接是否就绪。端点只监听容器内回环地址。

在已经配置专用 SSH 的这台 Mac 上，进入项目目录后可用 `bash scripts/cloud/remote.sh status` 查看云端状态，`backup` 手动备份，`configure-login` 设置当前账号快捷续登，`webui` 建立本机 16099 管理隧道。隧道关闭只影响管理页，不影响云端机器人。SSH 配置与专用私钥在本机 `.deployment/`，不进入 Git；更换电脑需要另外授权新的管理公钥。

## 自动恢复与备份范围

Docker `unless-stopped` 处理进程退出和宿主重启；systemd 每分钟检查一次，连续 3 次发现 bot 不健康或容器缺失才恢复，两次恢复至少间隔 5 分钟。QQ 登录失效只报告 `qq_scan_required`。失败会话每 30 秒重试，其他好友的会话保持独立。

常驻容器检查排除 `compose run` 的一次性管理/验收任务。OneBot 断开时，宿主通过回环地址查询私有 NapCat WebUI 的登录阶段；若等待二维码，即使 OneBot 接口已经消失，也明确报告 `qq_scan_required`。该检查不会自动扫码或在登录失效时循环重启。

每天 04:20（含随机延迟）生成私有备份，保留最近 7 份。SQLite 使用在线备份接口，包含已提交的 WAL 内容；多份数据库分别一致，不是跨数据库事务快照。归档包含模型密钥、人格、机器人数据及 NapCat 配置，文件权限 0600，应按密钥文件保管。QQ 设备登录目录不作在线复制，需要停机导出。备份在同一块云盘，不能应对云盘丢失；后续如需异地备份应另选存储位置。

恢复时先 `ops.py stop`，将备份解压到新的私有目录，执行数据库 `PRAGMA integrity_check`；保留当前数据作为回退，再恢复配置和 `data/bot`，设置正确属主后启动。自动测试已覆盖 WAL 数据恢复，实际版本验收另记。

## 验收

部署完成前逐项确认：镜像为服务器架构、模型检查、QQ 登录与好友权限、文字/图片/连续消息、取消与会话恢复、重启接续、容器退出恢复、服务器重启恢复、备份恢复，以及公网没有管理端口暴露。记录验证结果时区分模拟端、真实模型与真实 QQ。

## 定时日志采集

服务器每分钟采集状态和增量日志，压缩轮转并私有保留，不依赖 Mac 或 Codex 持续运行。查看安全摘要可执行 `python3 /opt/qqbot/current/scripts/cloud/collect_qq.py --summary`。采集边界、容量限制及六小时观察结果说明见 [云端日志采集](cloud-log-collection.md)。
