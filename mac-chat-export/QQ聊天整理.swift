import AppKit

final class Delegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var stopper: Process?
    let root = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 610, height: 485),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "QQ 聊天整理 · v0.3.1"
        window.center()
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        let title = NSTextField(labelWithString: "把聊天记录整理成文字和图片")
        title.font = .systemFont(ofSize: 23, weight: .semibold)
        stack.addArrangedSubview(title)
        let detail = NSTextField(wrappingLabelWithString:
            "1. 完全退出桌面 QQ，点击下方按钮。\n2. 用你的个人 QQ 扫码登录。\n3. 在导出页面选择会话和时间范围，格式选 JSON，开启图片下载。\n4. 导出完成后，自动生成 chat.txt、images 文件夹和 ZIP。")
        detail.font = .systemFont(ofSize: 15)
        stack.addArrangedSubview(detail)
        let launch = NSButton(title: "启动 QQ 批量读取", target: self, action: #selector(start))
        launch.bezelStyle = .rounded
        stack.addArrangedSubview(launch)
        let stop = NSButton(title: "停止读取", target: self, action: #selector(stopReading(_:)))
        stop.bezelStyle = .rounded
        stack.addArrangedSubview(stop)
        let results = NSButton(title: "打开整理结果", target: self, action: #selector(openResults))
        results.bezelStyle = .rounded
        stack.addArrangedSubview(results)
        let guide = NSButton(title: "查看使用说明", target: self, action: #selector(openGuide))
        guide.bezelStyle = .rounded
        stack.addArrangedSubview(guide)
        let footer = NSTextField(wrappingLabelWithString:
            "停止读取会中断未完成的任务，已生成的归档保留。\n关闭读取终端会停止后台组件；只关闭此主窗口不会停止读取。")
        footer.font = .systemFont(ofSize: 12)
        footer.textColor = .secondaryLabelColor
        stack.addArrangedSubview(footer)
        window.contentView!.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 30),
            stack.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -30),
            stack.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 30)
        ])
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func stopReading(_ sender: NSButton) {
        guard stopper == nil else { return }
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [root.appendingPathComponent("QQ聊天整理.command").path, "--stop"]
        process.standardOutput = output
        process.standardError = output
        sender.isEnabled = false
        process.terminationHandler = { [weak self] child in
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let message = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            DispatchQueue.main.async {
                self?.stopper = nil
                sender.isEnabled = true
                let alert = NSAlert()
                alert.messageText = child.terminationStatus == 0 ? "读取已停止" : "未能停止读取"
                alert.informativeText = message
                alert.runModal()
            }
        }
        stopper = process
        do { try process.run() }
        catch {
            stopper = nil
            sender.isEnabled = true
            let alert = NSAlert()
            alert.messageText = "无法运行停止程序"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }

    @objc func start() {
        let target = root.appendingPathComponent("QQ聊天整理.command")
        guard FileManager.default.fileExists(atPath: target.path) else {
            let alert = NSAlert()
            alert.messageText = "找不到读取程序"
            alert.informativeText = "请将应用保留在 mac-chat-export/build 文件夹内，不要单独移动应用。"
            alert.runModal()
            return
        }
        // The command displays the explicit runtime consent before starting QQ.
        NSWorkspace.shared.open(target)
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard urls.contains(where: { $0.scheme == "qqchatarchive" }) else { return }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func openResults() {
        let path = root.appendingPathComponent("output", isDirectory: true)
        try? FileManager.default.createDirectory(at: path, withIntermediateDirectories: true)
        NSWorkspace.shared.open(path)
    }

    @objc func openGuide() {
        NSWorkspace.shared.open(root.appendingPathComponent("使用说明.md"))
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
