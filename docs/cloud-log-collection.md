# 云端日志采集

QQbot 的状态及日志采集现在由服务器 systemd 每分钟执行，与 Mac、Codex 和 SSH 连接是否开启无关。现有 watchdog 仍负责故障时的现场快照；新增采集器补齐故障前后的周期记录，不替代诊断，也不负责 QQ 重新登录。

## 采集内容与限制

`qqbot-log-collector.timer` 调用 `scripts/cloud/collect_qq.py`，只查询状态和读取 Docker 日志，不执行重启、扫码、消息发送或账号写操作。它使用独立锁，正常聊天无需暂停。

每次保存 Bot/QQ 在线状态和上次成功采集至本次时间点的 NapCat、Bot 日志。首次回看五分钟；每个服务最多 1000 行、保留最后 512 KiB，超出时明确记录截断标记，超时记为不可用。失败不推进日志游标，下一次会重试，可能包含重复行；长时间停采最多回看 72 小时，且只能读取 Docker 尚未轮转丢弃的日志。这是有界快照采集，不保证保存所有消息和无限长日志。

文件保存在 `/opt/qqbot/run/qq-logs/sample-*.json.gz`。压缩归档最多保留 72 小时、合计最多 256 MiB，任一限制先达到便清理最旧文件。目录 0700，归档和状态文件 0600。原始日志可能包含聊天内容和登录凭据，只在服务器私有目录保存，不打印、不提交 Git。

`run/qq-log-collector.json` 仅保存安全摘要：最近采样时间、QQ/业务就绪状态、日志采集结果、保留容量、采样间隔以及本轮观察结果。命令和 systemd journal 只返回该摘要，不输出原始日志。

## 六小时对照

本轮从已记录的首次真实在线时间继续观察，不重新登录、不停止 Bot、不重置计时。服务器自行区分：

- `OBSERVING`：观察中。
- `QQ_OFFLINE`：探针明确显示离线或等待扫码。
- `NATIVE_KICK_OBSERVED`：采集日志中发现观察期内的原生下线事件，即使采样时已恢复在线也保留失败结果。
- `PROBE_UNAVAILABLE`：连续三轮无法确定 QQ 状态，不代表身份失效。
- `ONLINE_WINDOW_COMPLETED`：本轮观察窗口结束时在线；不证明永久修复，也不证明两次采样之间绝无中断。

采样开始时间、最大采样间隔和截断标记均保留，用来说明观察范围。当前六小时起点来自已有跟进记录，起点至采集器部署之间没有本采集器的一分钟快照，不能声称补齐了这段连续证据。

这些结果在服务器自动落盘，即使 Mac 关机仍会继续。Codex 跟进只读取摘要、核对异常并通知用户；这部分通知仍需 Codex 可运行。此轮结束后定时采集继续运行，六小时结论保留，不因再次扫码自动清除。

## 查看与管理

```bash
python3 /opt/qqbot/current/scripts/cloud/collect_qq.py --summary
systemctl status qqbot-log-collector.timer
journalctl -u qqbot-log-collector.service -n 10
```

摘要命令不会获取或打印聊天内容。暂停采集可执行 `systemctl disable --now qqbot-log-collector.timer`，不影响 QQ 或 Bot。手动采集执行同一脚本、不加 `--summary`。

部署脚本会自动启用采集定时器。回退到不包含 `collect_qq.py` 的老发布目录前，应先停用该定时器；现有已登录 QQ 无须为安装采集器而重启。

## 本次验收

本机完整回归 179 项通过。新增测试覆盖大日志输出限额与超时、私有归档权限、增量时间窗口、失败不前进游标、时间/容量轮转、真实离线与探针失败区分、原生下线后状态恢复仍留档。服务器首次采集成功，QQ 与 Bot 容器 ID 均未变化；自动定时执行与实际落盘结果另见验收 JSON。

服务器实际 Python 环境下 6 项采集器测试通过；systemd 单元校验通过。定时器已自行完成第二轮采样，间隔约 66.72 秒，两个归档均为 0600；QQ 在线且 Bot ready。机器可读记录见 [采集验收](acceptance-cloud-log-collection.json)。
