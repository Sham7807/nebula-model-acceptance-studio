import Foundation
import Combine
import AppKit

@MainActor
final class EngineService: ObservableObject {
    enum State: Equatable { case idle, starting, ready, stopping, failed(String) }
    @Published private(set) var state: State = .idle
    @Published private(set) var session: EngineSession?
    let resources: URL
    let dataDirectory: URL
    private var process: Process?
    private var input: Pipe?
    private var output: Pipe?
    private var launchID = UUID()
    private var buffer = Data()
    private var deadline: Task<Void, Never>?
    var isReady: Bool { state == .ready }

    init(resources: URL? = nil, dataDirectory: URL? = nil) {
        self.resources = resources ?? Bundle.main.resourceURL!
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        self.dataDirectory = dataDirectory ?? support.appendingPathComponent("NebulaWorkbench", isDirectory: true)
    }

    func start() {
        guard state != .starting && state != .ready && state != .stopping else { return }
        state = .starting; session = nil; buffer = Data(); launchID = UUID()
        let identity = launchID
        let runner = resources.appendingPathComponent("engine/desktop_service.py")
        let python = resources.appendingPathComponent("Python/bin/python3.12")
        let workspace = resources.appendingPathComponent("Workbench")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.fileExists(atPath: runner.path) else {
            state = .failed("应用中的检测引擎不完整。请重新复制完整的 .app 到“应用程序”文件夹。"); return
        }
        do {
            try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let task = Process(), stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
            task.executableURL = python
            task.arguments = ["-u", "-B", runner.path, "--workspace", workspace.path, "--data", dataDirectory.path]
            task.currentDirectoryURL = workspace
            var environment = ProcessInfo.processInfo.environment
            for key in ["PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "WORKBENCH_AUTH_FILE", "WORKBENCH_DB", "WORKBENCH_REPORTS"] { environment.removeValue(forKey: key) }
            environment["PYTHONNOUSERSITE"] = "1"
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPYCACHEPREFIX"] = dataDirectory.appendingPathComponent("Cache").path
            environment["SSL_CERT_FILE"] = resources.appendingPathComponent("Python/lib/python3.12/site-packages/certifi/cacert.pem").path
            task.environment = environment
            task.standardInput = stdin; task.standardOutput = stdout; task.standardError = stderr
            // Drain stderr without writing request data or credentials to a log.
            stderr.fileHandleForReading.readabilityHandler = { handle in
                if handle.availableData.isEmpty { handle.readabilityHandler = nil }
            }
            stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                if data.isEmpty { handle.readabilityHandler = nil; return }
                Task { @MainActor in self?.receive(data, identity: identity) }
            }
            task.terminationHandler = { [weak self] _ in
                Task { @MainActor in
                    guard let self, self.launchID == identity else { return }
                    self.deadline?.cancel(); self.session = nil
                    if self.state == .stopping || self.state == .idle { self.state = .idle }
                    else if case .failed = self.state { return }
                    else { self.state = .failed("检测引擎已停止。已保存的测试档案仍在本机；可重新启动引擎后继续。")}
                }
            }
            process = task; input = stdin; output = stdout
            try task.run()
            deadline = Task { [weak self] in
                try? await Task.sleep(for: .seconds(25))
                guard !Task.isCancelled, let self, self.launchID == identity, self.state == .starting else { return }
                self.process?.terminate()
                self.state = .failed("引擎启动超时。请检查磁盘空间，并尝试重新启动。")
            }
        } catch { state = .failed("无法启动本地引擎：" + error.localizedDescription) }
    }

    private func receive(_ data: Data, identity: UUID) {
        guard launchID == identity, state == .starting else { return }
        buffer.append(data)
        guard buffer.count < 256_000 else { process?.terminate(); state = .failed("引擎返回了异常的启动信息。"); return }
        while let newline = buffer.firstIndex(of: 10) {
            let line = buffer.prefix(upTo: newline); buffer.removeSubrange(...newline)
            if let packet = try? JSONSerialization.jsonObject(with:line) as? [String:Any], packet["type"] as? String == "error", let message = packet["message"] as? String {
                deadline?.cancel(); state = .failed(message); return
            }
            if let value = try? JSONDecoder().decode(EngineSession.self, from: line),
               value.type == "ready", value.url.host == "127.0.0.1", value.url.scheme == "http", value.url.port != nil {
                deadline?.cancel(); session = value; state = .ready; return
            }
        }
    }

    func stop() async {
        deadline?.cancel()
        guard let process, process.isRunning else { session = nil; state = .idle; return }
        state = .stopping; session = nil
        try? input?.fileHandleForWriting.write(contentsOf: Data("shutdown\n".utf8))
        try? input?.fileHandleForWriting.close()
        for _ in 0..<60 {
            if !process.isRunning { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
        if process.isRunning { process.terminate() }
        for _ in 0..<20 {
            if !process.isRunning { break }
            try? await Task.sleep(for: .milliseconds(100))
        }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        self.process = nil; input = nil
        output?.fileHandleForReading.readabilityHandler = nil; output = nil
        state = .idle
    }

    func request(_ path: String) async throws -> Data {
        guard let session else { throw URLError(.cannotConnectToHost) }
        guard let url = URL(string: path, relativeTo: session.url)?.absoluteURL, session.contains(url) else { throw URLError(.badURL) }
        var request = URLRequest(url: url)
        request.setValue(session.cookieHeader, forHTTPHeaderField: "Cookie")
        request.setValue(session.csrf, forHTTPHeaderField: "X-Workbench-Token")
        request.timeoutInterval = 10
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw URLError(.badServerResponse) }
        return data
    }
}
