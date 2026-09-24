import SwiftUI
import Combine
import AppKit

@MainActor
final class TestSession: Identifiable {
    let id = UUID()
    let destination: Destination
    let channelName: String
    let workspace: WebWorkspace
    let number: Int
    init(destination: Destination, channel: ChannelProfile, number: Int, resources: URL) {
        self.destination = destination; channelName = channel.name; self.number = number
        workspace = WebWorkspace(resources:resources)
        workspace.navigate(destination.rawValue)
    }
    var title: String { "\(destination.title) · \(number)" }
    var statusText: String {
        if workspace.busy { return "运行中" }
        switch workspace.taskState {
        case "draft": return "待开始"
        case "cancelled": return "已取消"
        case "error", "interrupted": return "需关注"
        default: return "已结束"
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    let engine: EngineService
    let archiveWorkspace: WebWorkspace
    @Published private(set) var tasks: [TestSession] = []
    @Published private(set) var selectedTasks: [Destination:UUID] = [:]
    @Published var destination: Destination? = .overview
    @Published var channel = ChannelProfile()
    @Published var apiKey = ""
    @Published var history: [HistoryItem] = []
    @Published var stats = HistoryStats()
    @Published var showChannel = false
    @Published var notice: String?
    @Published var historyError: String?
    private var subscriptions = Set<AnyCancellable>()
    private var taskSubscriptions: [UUID:AnyCancellable] = [:]
    private var refreshTask: Task<Void, Never>?
    private var started = false
    private var sequence = 0
    private let resources: URL
    let isolated: Bool
    let taskLimit = 12

    init(resources: URL = Bundle.main.resourceURL!, isolated testing: Bool? = nil) {
        self.resources = resources
        isolated = testing ?? ProcessInfo.processInfo.arguments.contains("--isolated-testing")
        let data = isolated ? FileManager.default.temporaryDirectory.appendingPathComponent("Nebula-QA-"+UUID().uuidString) : nil
        engine = EngineService(resources:resources,dataDirectory:data)
        archiveWorkspace = WebWorkspace(resources:resources)
        archiveWorkspace.navigate("history")
        if !isolated, let data = UserDefaults.standard.data(forKey:"channelProfile"), let saved = try? JSONDecoder().decode(ChannelProfile.self, from:data) {
            channel = saved
            if saved.rememberKey {
                do { apiKey = try Keychain.read() } catch { notice = error.localizedDescription }
            }
        }
        engine.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in:&subscriptions)
        archiveWorkspace.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }.store(in:&subscriptions)
        engine.$session.compactMap { $0 }.sink { [weak self] session in
            Task { @MainActor in
                guard let self else { return }
                await self.archiveWorkspace.connect(session)
                for task in self.tasks { await task.workspace.connect(session) }
                await self.refreshHistory()
            }
        }.store(in:&subscriptions)
        engine.$state.sink { [weak self] state in
            guard case .failed = state, let self else { return }
            self.tasks.forEach { $0.workspace.engineStopped() }
        }.store(in:&subscriptions)
        wire(archiveWorkspace)
    }
    var selected: Destination { destination ?? .overview }
    var activeTask: TestSession? { tasks.first { $0.id == selectedTasks[selected] } }
    var workspace: WebWorkspace { activeTask?.workspace ?? archiveWorkspace }
    var runningTasks: [TestSession] { tasks.filter { $0.workspace.busy } }
    var hasRunningTasks: Bool { !runningTasks.isEmpty }
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
    private func wire(_ workspace: WebWorkspace) {
        workspace.onHistoryChanged = { [weak self] in Task { await self?.refreshHistory() } }
        workspace.onDownload = { [weak self] filename in self?.notice = "已保存：\(filename)" }
    }
    @discardableResult
    func createTask(for route: Destination? = nil) -> TestSession? {
        let target = route ?? (selected.isTest ? selected : .text)
        guard target.isTest else { return nil }
        guard tasks.count < taskLimit else {
            notice = "已打开 \(taskLimit) 个任务。请在任务中心关闭不再需要的已结束任务，已保存的档案不会删除。"
            destination = .tasks
            return nil
        }
        sequence += 1
        let task = TestSession(destination:target,channel:channel,number:sequence,resources:resources)
        task.workspace.configure(channel,key:apiKey)
        wire(task.workspace)
        taskSubscriptions[task.id] = task.workspace.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
        tasks.append(task); selectedTasks[target] = task.id; destination = target
        if let session = engine.session {
            Task { @MainActor [weak self, weak task] in
                guard let self, let task, self.tasks.contains(where:{$0.id == task.id}) else { return }
                await task.workspace.connect(session)
            }
        }
        return task
    }
    func openTask(_ task: TestSession) {
        guard tasks.contains(where:{$0.id == task.id}) else { return }
        selectedTasks[task.destination] = task.id; destination = task.destination
    }
    func closeTask(_ task: TestSession) {
        guard !task.workspace.busy else { notice = "运行中的任务需要先停止，才能关闭。"; return }
        task.workspace.dispose(); taskSubscriptions.removeValue(forKey:task.id)
        tasks.removeAll { $0.id == task.id }
        if selectedTasks[task.destination] == task.id {
            selectedTasks[task.destination] = tasks.last(where:{$0.destination == task.destination})?.id
            if selected == task.destination && selectedTasks[task.destination] == nil { destination = .tasks }
        }
    }
    func select(_ value: Destination) { destination = value; navigate() }
    func navigate() {
        if selected == .overview { Task { await refreshHistory() }; return }
        if selected == .tasks { return }
        if selected == .history { archiveWorkspace.openHistory(); return }
        if activeTask == nil { _ = createTask(for:selected) }
        activeTask?.workspace.navigate(selected.rawValue)
    }
    func saveChannel(_ value: ChannelProfile, key: String, route: Destination? = nil) throws {
        guard ChannelProfile.validate(base:value.base) else { throw NSError(domain:"Channel",code:1,userInfo:[NSLocalizedDescriptionKey:"请输入完整的 http:// 或 https:// 渠道地址，不要包含密钥或查询参数。"])}
        guard tasks.count < taskLimit else { throw NSError(domain:"Channel",code:2,userInfo:[NSLocalizedDescriptionKey:"请先在任务中心关闭一个已结束的任务，再添加新渠道。"])}
        if !isolated {
            if value.rememberKey { try Keychain.save(key) } else { try Keychain.remove() }
            UserDefaults.standard.set(try JSONEncoder().encode(value),forKey:"channelProfile")
        }
        channel = value; apiKey = key
        // Existing tasks keep their own profile and in-memory key, even while
        // another channel becomes the default for future workspaces.
        createTask(for:route)
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
        Task { @MainActor in await Task.yield(); archiveWorkspace.openHistory(item.id) }
    }
    func restart() {
        guard !hasRunningTasks else { return }
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
        if let model, model.hasRunningTasks {
            let alert = NSAlert(); alert.messageText = "停止 \(model.runningTasks.count) 个任务并退出？"
            alert.informativeText = "切换工作区可以继续后台测试；退出应用会停止本机任务。已保存的报告保留在测试档案中，上游已提交的生成任务可能继续执行。"
            alert.addButton(withTitle:"继续测试"); alert.addButton(withTitle:"停止并退出")
            guard alert.runModal() == .alertSecondButtonReturn else { return .terminateCancel }
            model.runningTasks.forEach { $0.workspace.stopTests() }
        }
        terminating = true
        Task { @MainActor in await model?.stop(); sender.reply(toApplicationShouldTerminate:true) }
        return .terminateLater
    }
}
