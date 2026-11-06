import Cocoa

final class Publisher: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    let titleLabel = NSTextField(labelWithString: "正在準備更新…")
    let detail = NSTextField(wrappingLabelWithString: "此流程不使用 AI。查核通過後會自動提交 GitHub 並驗證公開網站。")
    let progress = NSProgressIndicator()
    let elapsed = NSTextField(labelWithString: "已執行 0 秒")
    let started = Date()
    var reportButton: NSButton!
    var siteButton: NSButton!
    var process: Process?
    var report: URL?
    var timer: Timer?
    var finished = false
    var received = ""
    var setupLog: URL!
    var logHandle: FileHandle?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 270), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "台股融資維持率 · 更新並發布"
        window.delegate = self
        window.isReleasedWhenClosed = false
        let view = window.contentView!
        titleLabel.frame = NSRect(x: 24, y: 214, width: 452, height: 26)
        titleLabel.font = .boldSystemFont(ofSize: 17)
        detail.frame = NSRect(x: 24, y: 87, width: 452, height: 116)
        detail.font = .preferredFont(forTextStyle: .body)
        elapsed.frame = NSRect(x: 46, y: 31, width: 220, height: 20)
        elapsed.font = .systemFont(ofSize: 11); elapsed.textColor = .secondaryLabelColor
        progress.frame = NSRect(x: 24, y: 33, width: 16, height: 16)
        progress.style = .spinning; progress.isDisplayedWhenStopped = false; progress.startAnimation(nil)
        reportButton = NSButton(title: "查看報告", target: self, action: #selector(openReport))
        reportButton.frame = NSRect(x: 278, y: 24, width: 90, height: 28); reportButton.bezelStyle = .rounded; reportButton.isEnabled = false
        siteButton = NSButton(title: "開啟網站", target: self, action: #selector(openSite))
        siteButton.frame = NSRect(x: 376, y: 24, width: 100, height: 28); siteButton.bezelStyle = .rounded; siteButton.isEnabled = false
        for child in [titleLabel, detail, elapsed, progress, reportButton!, siteButton!] as [NSView] { view.addSubview(child) }
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        let root = Bundle.main.bundleURL.deletingLastPathComponent()
        let local = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/tw-margin-rate")
        try? FileManager.default.createDirectory(at: local, withIntermediateDirectories: true)
        setupLog = local.appendingPathComponent("launcher-\(UUID().uuidString).log")
        FileManager.default.createFile(atPath: setupLog.path, contents: nil)
        logHandle = try? FileHandle(forWritingTo: setupLog)
        let task = Process(); task.executableURL = URL(fileURLWithPath: "/bin/zsh")
        task.arguments = [root.appendingPathComponent("scripts/launch_publisher.sh").path] + (CommandLine.arguments.contains("--verify-only") ? ["--verify-only"] : [])
        task.currentDirectoryURL = root
        let pipe = Pipe(); task.standardOutput = pipe; task.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async { self?.consume(data) }
        }
        task.terminationHandler = { [weak self] p in
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { self?.complete(p.terminationStatus) }
        }
        process = task
        do { try task.run() }
        catch { detail.stringValue = "無法啟動更新。請將 App 留在專案資料夾內。"; complete(1) }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refresh() }
    }
    func consume(_ data: Data) {
        logHandle?.write(data)
        received += String(decoding: data, as: UTF8.self)
        while let newline = received.firstIndex(of: "\n") {
            let line = String(received[..<newline]); received.removeSubrange(...newline)
            if line.hasPrefix("REPORT:") { report = URL(fileURLWithPath: String(line.dropFirst(7))); reportButton.isEnabled = true }
            if line.hasPrefix("RESULT:") { titleLabel.stringValue = String(line.dropFirst(7)) }
        }
    }
    func refresh() {
        let seconds = Int(Date().timeIntervalSince(started))
        elapsed.stringValue = "已執行 \(seconds / 60) 分 \(seconds % 60) 秒"
        guard let report else { return }
        let json = report.deletingLastPathComponent().appendingPathComponent("result.json")
        if let data = try? Data(contentsOf: json), let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if !finished { titleLabel.stringValue = state["stage"] as? String ?? "正在更新…" }
            detail.stringValue = "資料日期：\(state["target"] as? String ?? "確認中")\n\(state["message"] as? String ?? "正在查核，請勿同時在另一台 Mac 更新或關閉本程式。")"
        }
    }
    func complete(_ code: Int32) {
        finished = true; timer?.invalidate(); progress.stopAnimation(nil); progress.isHidden = true
        refresh()
        if code == 0 {
            if !titleLabel.stringValue.contains("驗證") && !titleLabel.stringValue.contains("成功") { titleLabel.stringValue = "完成，請查看報告" }
            siteButton.isEnabled = true
        } else {
            if !titleLabel.stringValue.contains("停止") && !titleLabel.stringValue.contains("部署") { titleLabel.stringValue = "已停止，未確認發布成功" }
            if report == nil { detail.stringValue = "啟動或環境準備未完成。請查看報告；沒有略過檢查。" }
        }
        reportButton.isEnabled = true
        try? logHandle?.close()
    }
    @objc func openReport() { NSWorkspace.shared.open(report ?? setupLog) }
    @objc func openSite() { NSWorkspace.shared.open(URL(string: "https://willychen0924.github.io/tw-margin-rate/")!) }
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if finished { NSApp.terminate(nil); return true }
        sender.miniaturize(nil)
        return false
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.deminiaturize(nil); window.makeKeyAndOrderFront(nil); return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { finished ? .terminateNow : .terminateCancel }
}
let app = NSApplication.shared
let delegate = Publisher()
app.delegate = delegate
app.run()
