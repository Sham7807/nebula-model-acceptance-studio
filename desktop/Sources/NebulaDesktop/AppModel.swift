import SwiftUI
import Combine
import AppKit

@MainActor
final class AppModel: ObservableObject {
    let engine: EngineService
    let workspace: WebWorkspace
    @Published var destination: Destination? = .overview
    @Published var channel = ChannelProfile()
    @Published var apiKey = ""
    @Published var history: [HistoryItem] = []
    @Published var stats = HistoryStats()
    @Published var showChannel = false
    @Published var notice: String?
    @Published var historyError: String?
    private var subscriptions = Set<AnyCancellable>()
    private var refreshTask: Task<Void, Never>?
    private var started = false
    let isolated: Bool

    init() {
        isolated = ProcessInfo.processInfo.arguments.contains("--isolated-testing")
        let data = isolated ? FileManager.default.temporaryDirectory.appendingPathComponent("Nebula-QA-\(ProcessInfo.processInfo.processIdentifier)") : nil
        engine = EngineService(dataDirectory: data)
        workspace = WebWorkspace()
        if !isolated, let data = UserDefaults.standard.data(forKey:"channelProfile"), let saved = try? JSONDecoder().decode(ChannelProfile.self, from:data) {
            channel = saved
            if saved.rememberKey {
                do { apiKey = try Keychain.read() } catch { notice = error.localizedDescription }
            }
        }
        engine.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in:&subscriptions)
        workspace.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in:&subscriptions)
        engine.$session.compactMap { $0 }.sink { [weak self] session in
            Task { @MainActor in
                guard let self else { return }
                self.workspace.configure(self.channel, key:self.apiKey)
                await self.workspace.connect(session)
                await self.refreshHistory()
            }
        }.store(in:&subscriptions)
        workspace.onHistoryChanged = { [weak self] in Task { await self?.refreshHistory() } }
        workspace.onDownload = { [weak self] filename in self?.notice = "已保存：\(filename)" }
    }
    var selected: Destination { destination ?? .overview }
    var colorScheme: ColorScheme { .light }
    func start() {
        guard !started else { return }; started = true; engine.start()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for:.seconds(15))
                guard !Task.isCancelled else { return }
                await self?.refreshHistory()
            }
        }
    }
    func select(_ value: Destination) {
        if workspace.busy && value != .overview && value != .history && value.rawValue != workspace.currentRoute {
            notice = "当前测试仍在运行，请先停止测试再切换工作区。"; return
        }
        destination = value
    }
    func navigate() {
        if workspace.busy && selected != .overview && selected != .history && selected.rawValue != workspace.currentRoute {
            destination = Destination(rawValue: workspace.currentRoute); notice = "当前测试仍在运行，请先停止测试再切换工作区。"; return
        }
        guard selected != .overview else { Task { await refreshHistory() }; return }
        workspace.navigate(selected.rawValue)
    }
    func saveChannel(_ value: ChannelProfile, key: String) throws {
        guard ChannelProfile.validate(base:value.base) else { throw NSError(domain:"Channel",code:1,userInfo:[NSLocalizedDescriptionKey:"请输入完整的 http:// 或 https:// 渠道地址，不要包含密钥或查询参数。"])}
        if !isolated {
            if value.rememberKey { try Keychain.save(key) } else { try Keychain.remove() }
            UserDefaults.standard.set(try JSONEncoder().encode(value),forKey:"channelProfile")
        }
        channel = value; apiKey = key; workspace.configure(value,key:key)
    }
    func refreshHistory() async {
        guard engine.isReady else { return }
        do {
            let data = try await engine.request("/api/history?limit=5&offset=0")
            let page = try JSONDecoder().decode(HistoryPage.self,from:data)
            history = page.items; stats = page.stats; historyError = nil
        } catch { historyError = "暂时无法读取测试档案，可在档案页重试。" }
    }
    func openHistory(_ item: HistoryItem) {
        destination = .history
        Task { @MainActor in
            // Let the native selection update before opening the selected record.
            await Task.yield(); workspace.openHistory(item.id)
        }
    }
    func restart() {
        guard !workspace.busy else { return }
        Task { await engine.stop(); engine.start() }
    }
    func stop() async { refreshTask?.cancel(); await engine.stop() }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?
    private var terminating = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .aqua)
        NSApp.windows.forEach { $0.backgroundColor = .white }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !terminating else { return .terminateLater }
        if model?.workspace.busy == true {
            let alert = NSAlert(); alert.messageText = "停止测试并退出？"
            alert.informativeText = "当前请求将被取消，已保存的结果仍会保留在测试档案中。"
            alert.addButton(withTitle:"继续测试"); alert.addButton(withTitle:"停止并退出")
            guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
            model?.workspace.stopTests()
        }
        terminating = true
        Task { @MainActor in await model?.stop(); sender.reply(toApplicationShouldTerminate:true) }
        return .terminateLater
    }
}
