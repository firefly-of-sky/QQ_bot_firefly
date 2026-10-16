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

```bash
python3 /opt/qqbot/current/scripts/cloud/ops.py status
python3 /opt/qqbot/current/scripts/cloud/ops.py backup
python3 /opt/qqbot/current/scripts/cloud/ops.py stop  # 设置维护标记，监控不会擅自拉起
python3 /opt/qqbot/current/scripts/cloud/ops.py start
systemctl list-timers 'qqbot-*'
journalctl -u qqbot-watchdog.service -n 20
```

状态输出不包含密钥、QQ 号码、聊天原文或二维码。`ready=true` 表示 QQ 已在线、框架接收器连通、当前会话工作进程正常；不代表模型服务一定可用，仍需真实消息验证。健康检查 `/health/live` 只判断进程响应；`/health/ready` 判断连接是否就绪。端点只监听容器内回环地址。

## 自动恢复与备份范围

Docker `unless-stopped` 处理进程退出和宿主重启；systemd 每分钟检查一次，连续 3 次发现 bot 不健康或容器缺失才恢复，两次恢复至少间隔 5 分钟。QQ 登录失效只报告 `qq_scan_required`。失败会话每 30 秒重试，其他好友的会话保持独立。

每天 04:20（含随机延迟）生成私有备份，保留最近 7 份。SQLite 使用在线备份接口，包含已提交的 WAL 内容；多份数据库分别一致，不是跨数据库事务快照。归档包含模型密钥、人格、机器人数据及 NapCat 配置，文件权限 0600，应按密钥文件保管。QQ 设备登录目录不作在线复制，需要停机导出。备份在同一块云盘，不能应对云盘丢失；后续如需异地备份应另选存储位置。

恢复时先 `ops.py stop`，将备份解压到新的私有目录，执行数据库 `PRAGMA integrity_check`；保留当前数据作为回退，再恢复配置和 `data/bot`，设置正确属主后启动。自动测试已覆盖 WAL 数据恢复，实际版本验收另记。

## 验收

部署完成前逐项确认：镜像为服务器架构、模型检查、QQ 登录与好友权限、文字/图片/连续消息、取消与会话恢复、重启接续、容器退出恢复、服务器重启恢复、备份恢复，以及公网没有管理端口暴露。记录验证结果时区分模拟端、真实模型与真实 QQ。
