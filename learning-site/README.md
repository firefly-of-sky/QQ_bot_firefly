# 服务器小课 v1.0.0

面向本项目零基础 Mac 用户的静态学习网站：7 节课程、浏览器模拟终端、真实操作清单、测验、命令速查、术语搜索和故障指引。

## 本地打开

在 Finder 中打开本目录，双击 `打开学习网站.command`。它会启动只监听本机的静态服务并打开浏览器。学习时保持终端窗口开启，Control + C 只关闭学习网站，不影响云端 QQbot。默认使用 8765；如已被占用，会尝试到 8784，进度按浏览器地址分别保存。

可直接用浏览器打开 `dist/index.html`（无第三方依赖、无需构建）。推荐使用固定地址保存进度：

```bash
cd /Users/firefly/Documents/Projects/QQbot/learning-site
python3 -m http.server 8765 --bind 127.0.0.1 --directory dist
```

然后打开 `http://127.0.0.1:8765`。进度保存在浏览器 localStorage；文件地址、本地 HTTP 地址、发布地址的记录互相独立，不跨设备同步。导出按钮用于留存 JSON 记录，不提供导入或账号同步。

## 验证

```bash
node --test tests/simulator.test.cjs
node --check dist/app.js
node --check dist/data.js
node --check dist/simulator.js
```

模拟器仅对教学命令做确定性解析，不调用网络、Shell 或 `eval`。模拟扫码、备份、启动、停止均不会影响真实服务器。真实实操确认由学习者本人勾选，与模拟和测验完成分开保存。

## 内容依据

- `../docs/cloud.md`
- `../docs/v4.0.0.md`（2026-10-17 验收记录）
- `../scripts/cloud/remote.sh`
- Ubuntu 官方终端与 SSH 文档、NapCat 官方接入文档（链接见网站内容说明页）

部署只上传本目录的静态文件，不上传 QQbot 配置、聊天数据、SSH 设置、私钥或环境变量。静态发布身份见 `.openai/hosting.json`。
