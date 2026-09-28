import Foundation
import SwiftUI
import AppKit
import Darwin

/// A single request configuration is copied into each worker. Keeping it value
/// typed means the load runner never reads UI state while requests are in flight.
private struct APIStressConfig: Sendable {
    let endpoint: URL
    let method: String
    let headers: [String: String]
    let body: Data?
    let total: Int
    let concurrency: Int
    let timeout: TimeInterval
    let rampUpMilliseconds: UInt64
    let durationSeconds: Int
    let ratePerSecond: Int
    let expectedStatus: Int?
}

private struct APIStressResult: Sendable {
    let statusCode: Int?
    let latency: TimeInterval
    let bytes: Int
    let error: String?
    let succeeded: Bool
}

/// A load test must never follow a redirect and accidentally send an API key
/// or request body to a different origin. The report records the redirect as
/// a failed transport sample instead.
private final class LoadTestRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    var redirected = false
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        redirected = true
        completionHandler(nil)
    }
}

@MainActor
final class APIStressModel: ObservableObject {
    @Published var endpoint = ""
    @Published var method = "POST"
    @Published var headersText = "Content-Type: application/json"
    @Published var bodyText = "{\n  \"model\": \"your-model\",\n  \"messages\": [{\"role\": \"user\", \"content\": \"ping\"}]\n}"
    @Published var totalRequests = 50
    @Published var concurrency = 5
    @Published var timeoutSeconds = 30
    @Published var rampUpMilliseconds = 0
    @Published var durationMode = false
    @Published var durationSeconds = 30
    @Published var ratePerSecond = 0
    @Published var expectedStatusText = "2xx / 3xx"
    @Published var authorized = false
    @Published private(set) var resolutionText: String?
    @Published private(set) var completed = 0
    @Published private(set) var succeeded = 0
    @Published private(set) var failed = 0
    @Published private(set) var statusText = "准备就绪"
    @Published private(set) var errorText: String?
    @Published private(set) var errorCounts: [String: Int] = [:]
    @Published private(set) var latencies: [Double] = []
    @Published private(set) var totalBytes = 0
    @Published private(set) var startedAt: Date?
    @Published private(set) var finishedAt: Date?
    @Published private(set) var running = false

    private var runTask: Task<Void, Never>?

    deinit { runTask?.cancel() }

    var progress: Double { totalRequests > 0 ? Double(completed) / Double(totalRequests) : 0 }
    var successRate: Double { completed > 0 ? Double(succeeded) / Double(completed) : 0 }
    var requestsPerSecond: Double {
        guard let startedAt else { return 0 }
        let end = finishedAt ?? Date()
        let seconds = max(0.001, end.timeIntervalSince(startedAt))
        return Double(completed) / seconds
    }
    var p50: Double { percentile(0.50) }
    var p95: Double { percentile(0.95) }
    var p99: Double { percentile(0.99) }

    func autofill(channel: ChannelProfile, key: String) {
        guard endpoint.isEmpty else { return }
        let base = channel.base.trimmingCharacters(in: .whitespacesAndNewlines)
        if !base.isEmpty {
            endpoint = base.hasSuffix("/chat/completions") ? base : base.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/chat/completions"
        }
        if !key.isEmpty && !headersText.localizedCaseInsensitiveContains("authorization") {
            headersText = "Content-Type: application/json\nAuthorization: Bearer \(key)"
        }
        if !channel.model.isEmpty { bodyText = bodyText.replacingOccurrences(of: "your-model", with: channel.model) }
    }

    func start() {
        guard !running else { return }
        guard authorized else { errorText = "请确认你已获得目标接口所有者的压测授权。"; return }
        guard let config = makeConfig() else { return }
        resetMetrics()
        running = true
        statusText = "正在建立 \(config.concurrency) 个并发工作线程…"
        startedAt = Date()
        runTask = Task { [weak self] in
            guard let self else { return }
            await self.run(config)
        }
    }

    func resolveEndpoint() {
        guard let url = URL(string: endpoint.trimmingCharacters(in: .whitespacesAndNewlines)), let host = url.host else {
            resolutionText = "请输入有效地址后再解析。"; return
        }
        let port = url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80)
        resolutionText = "正在解析 \(host)…"
        Task.detached {
            let result = Self.resolve(host: host)
            await MainActor.run { [weak self] in self?.resolutionText = result.isEmpty ? "未解析到地址（端口 \(port)）" : "\(host):\(port) → \(result.joined(separator: ", "))" }
        }
    }

    func saveReport(format: ReportFormat) {
        guard completed > 0 else { errorText = "完成至少一次请求后才能导出报告。"; return }
        let panel = NSSavePanel()
        panel.title = "导出 API 压测报告"
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = "API压测报告-\(Int(Date().timeIntervalSince1970)).\(format.extension)"
        panel.begin { [weak self] response in
            guard response == .OK, let self, let url = panel.url else { return }
            do { try self.reportData(format: format).write(to: url, options: .atomic); self.statusText = "报告已保存：\(url.lastPathComponent)" }
            catch { self.errorText = "报告保存失败：\(error.localizedDescription)" }
        }
    }

    func stop() {
        guard running else { return }
        runTask?.cancel()
        runTask = nil
        running = false
        finishedAt = Date()
        statusText = "已停止（已完成 \(completed) / \(totalRequests)）"
    }

    func clear() {
        guard !running else { return }
        resetMetrics()
        statusText = "准备就绪"
        errorText = nil
    }

    private func resetMetrics() {
        completed = 0; succeeded = 0; failed = 0
        statusText = "准备就绪"; errorText = nil; errorCounts = [:]
        latencies = []; totalBytes = 0; startedAt = nil; finishedAt = nil
    }

    private func makeConfig() -> APIStressConfig? {
        let rawEndpoint = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let endpointURL = URL(string: rawEndpoint),
              let components = URLComponents(url: endpointURL, resolvingAgainstBaseURL: false),
              ["http", "https"].contains(endpointURL.scheme?.lowercased() ?? ""), endpointURL.host != nil,
              components.user == nil, components.password == nil, components.fragment == nil,
              !rawEndpoint.contains(where: { $0.isWhitespace || $0.isNewline || $0 == "\\" }) else {
            errorText = "请输入有效的 HTTP/HTTPS 请求地址。"; return nil
        }
        let sensitiveQuery = components.queryItems?.contains { item in
            ["key", "api_key", "api-key", "token", "access_token", "secret", "password"].contains(item.name.lowercased())
        } == true
        guard !sensitiveQuery else {
            errorText = "请把密钥放到 Authorization / API Key 请求头中，不要写在 URL 查询参数里。"; return nil
        }
        var headers: [String: String] = [:]
        for raw in headersText.split(whereSeparator: { $0.isNewline }) {
            let line = String(raw).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !line.isEmpty else { continue }
            guard let separator = line.firstIndex(of: ":") else {
                errorText = "请求头格式错误：\(line)（应为 Header: Value）"; return nil
            }
            let name = String(line[..<separator]).trimmingCharacters(in: .whitespaces)
            let value = String(line[line.index(after: separator)...]).trimmingCharacters(in: .whitespaces)
            guard !name.isEmpty else { errorText = "请求头名称不能为空。"; return nil }
            headers[name] = value
        }
        let body = bodyText.trimmingCharacters(in: .whitespacesAndNewlines)
        if ["GET", "HEAD"].contains(method) && !body.isEmpty {
            errorText = "GET / HEAD 请求不应携带请求体；请清空请求体或改用 POST。"; return nil
        }
        let data = body.isEmpty ? nil : Data(body.utf8)
        // Keep the native runner within the same bounded envelope as the
        // reviewed local load-test engine; this is a capacity probe, not an
        // unbounded traffic generator.
        let total = min(max(totalRequests, 1), 20_000)
        let workers = min(max(concurrency, 1), total)
        let timeout = min(max(timeoutSeconds, 1), 600)
        let duration = durationMode ? min(max(durationSeconds, 1), 600) : 0
        let rate = durationMode ? min(max(ratePerSecond, 1), 1_000) : min(max(ratePerSecond, 0), 1_000)
        let expected = expectedStatusText == "2xx / 3xx" ? nil : Int(expectedStatusText)
        return APIStressConfig(endpoint: endpointURL, method: method, headers: headers, body: data,
                               total: total, concurrency: workers, timeout: TimeInterval(timeout),
                               rampUpMilliseconds: UInt64(min(max(rampUpMilliseconds, 0), 60_000)),
                               durationSeconds: duration, ratePerSecond: rate, expectedStatus: expected)
    }

    private func percentile(_ value: Double) -> Double {
        guard !latencies.isEmpty else { return 0 }
        let sorted = latencies.sorted()
        let index = min(sorted.count - 1, max(0, Int(ceil(value * Double(sorted.count)) - 1)))
        return sorted[index]
    }

    private func record(_ result: APIStressResult) {
        completed += 1
        if result.succeeded { succeeded += 1 } else { failed += 1 }
        latencies.append(result.latency * 1_000)
        totalBytes += result.bytes
        if let error = result.error {
            errorCounts[error, default: 0] += 1
        } else if let statusCode = result.statusCode, !(200..<400).contains(statusCode) {
            let key = "HTTP \(statusCode)"
            errorCounts[key, default: 0] += 1
        }
        statusText = "已完成 \(completed) / \(totalRequests) · 成功率 \(Int(successRate * 100))%"
    }

    private func run(_ config: APIStressConfig) async {
        await withTaskGroup(of: Void.self) { group in
            for worker in 0..<config.concurrency {
                group.addTask { [weak self] in
                    guard let self else { return }
                    var index = worker
                    let deadline = config.durationSeconds > 0 ? Date().addingTimeInterval(TimeInterval(config.durationSeconds)) : nil
                    let interval = config.ratePerSecond > 0 ? UInt64(Double(config.concurrency) / Double(config.ratePerSecond) * 1_000_000_000) : 0
                    var first = true
                    while !Task.isCancelled && (deadline == nil ? index < config.total : Date() < deadline!) {
                        if first && config.rampUpMilliseconds > 0 {
                            let delay = config.rampUpMilliseconds * UInt64(worker)
                            if delay > 0 { try? await Task.sleep(nanoseconds: delay * 1_000_000) }
                        } else if !first && interval > 0 {
                            try? await Task.sleep(nanoseconds: interval)
                        }
                        first = false
                        await self.record(await Self.execute(config))
                        index += config.concurrency
                    }
                }
            }
            for await _ in group { }
        }
        if !running { return }
        running = false; runTask = nil; finishedAt = Date()
        statusText = "压测完成 · \(completed) 次请求 · \(String(format: "%.1f", requestsPerSecond)) req/s"
    }

    nonisolated private static func execute(_ config: APIStressConfig) async -> APIStressResult {
        var request = URLRequest(url: config.endpoint, timeoutInterval: config.timeout)
        request.httpMethod = config.method
        request.httpBody = config.body
        for (name, value) in config.headers { request.setValue(value, forHTTPHeaderField: name) }
        let begin = Date()
        do {
            let delegate = LoadTestRedirectDelegate()
            let session = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
            defer { session.invalidateAndCancel() }
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode
            if delegate.redirected {
                return APIStressResult(statusCode: status, latency: Date().timeIntervalSince(begin), bytes: data.count, error: "检测到重定向，已停止以避免凭据跨域发送", succeeded: false)
            }
            let success = config.expectedStatus.map { status == $0 } ?? (status.map { (200..<400).contains($0) } ?? false)
            let error = success ? nil : (config.expectedStatus.map { "HTTP \(status ?? 0)（预期 \($0)）" } ?? "HTTP \(status ?? 0)")
            return APIStressResult(statusCode: status, latency: Date().timeIntervalSince(begin), bytes: data.count, error: error, succeeded: success)
        } catch is CancellationError {
            return APIStressResult(statusCode: nil, latency: Date().timeIntervalSince(begin), bytes: 0, error: "已取消", succeeded: false)
        } catch {
            let message = (error as NSError).localizedDescription
            return APIStressResult(statusCode: nil, latency: Date().timeIntervalSince(begin), bytes: 0, error: message, succeeded: false)
        }
    }

    nonisolated private static func resolve(host: String) -> [String] {
        var hints = addrinfo(ai_flags: 0, ai_family: AF_UNSPEC, ai_socktype: SOCK_STREAM, ai_protocol: 0, ai_addrlen: 0, ai_canonname: nil, ai_addr: nil, ai_next: nil)
        var head: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, nil, &hints, &head) == 0, let head else { return [] }
        defer { freeaddrinfo(head) }
        var values: [String] = []
        var current: UnsafeMutablePointer<addrinfo>? = head
        while let item = current {
            var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if let address = item.pointee.ai_addr,
               getnameinfo(address, item.pointee.ai_addrlen, &buffer, socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 {
                let value = String(cString: buffer)
                if !values.contains(value) { values.append(value) }
            }
            current = item.pointee.ai_next
        }
        return values
    }

    private func reportData(format: ReportFormat) -> Data {
        let header = headersText.split(whereSeparator: { $0.isNewline }).compactMap { line -> String? in
            guard let index = line.firstIndex(of: ":") else { return nil }
            let name = String(line[..<index])
            let lower = name.lowercased()
            let sensitive = lower.contains("authorization") || lower.contains("api-key") || lower.contains("api_key") || lower == "token" || lower.contains("secret")
            return sensitive ? "\(name): <redacted>" : String(line)
        }.joined(separator: "\n")
        let endpointForReport: String = {
            guard var components = URLComponents(string: endpoint) else { return endpoint }
            if let items = components.queryItems {
                components.queryItems = items.map { item in
                    let low = item.name.lowercased()
                    let sensitive = ["key", "api_key", "api-key", "token", "access_token", "secret", "password"].contains(low)
                    return sensitive ? URLQueryItem(name: item.name, value: "[已隐藏]") : item
                }
            }
            return components.string ?? endpoint
        }()
        let summary = ["endpoint": endpointForReport, "method": method, "completed": completed, "succeeded": succeeded, "failed": failed, "successRate": successRate, "throughput": requestsPerSecond, "p50Ms": p50, "p95Ms": p95, "p99Ms": p99, "bytes": totalBytes, "errors": errorCounts, "headers": header] as [String : Any]
        if format == .json { return (try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys])) ?? Data() }
        let escaped = (try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys])).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        let html = """
<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>API 压测报告</title><style>body{font:15px -apple-system,BlinkMacSystemFont,sans-serif;background:#f5f7fb;color:#18212f;margin:0;padding:40px}main{max-width:920px;margin:auto;background:#fff;border-radius:20px;padding:32px;box-shadow:0 8px 30px #15233d12}h1{margin-top:0;color:#0b70e8}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{padding:16px;background:#f5f8fd;border-radius:14px}.value{font-size:24px;font-weight:650;margin-top:7px}pre{white-space:pre-wrap;background:#111827;color:#d1e4ff;padding:20px;border-radius:14px}</style><main><h1>API 压测报告</h1><p>生成时间：\(Date())</p><div class="grid"><div class="metric">完成<div class="value">\(completed)</div></div><div class="metric">成功率<div class="value">\(String(format: "%.1f%%", successRate*100))</div></div><div class="metric">吞吐<div class="value">\(String(format: "%.1f req/s", requestsPerSecond))</div></div><div class="metric">P95<div class="value">\(String(format: "%.0f ms", p95))</div></div></div><h2>请求摘要</h2><pre>\(escaped.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;"))</pre></main></html>
"""
        return Data(html.utf8)
    }

    enum ReportFormat { case json, html; var `extension`: String { self == .json ? "json" : "html" } }
}

struct APIStressView: View {
    @ObservedObject var model: AppModel
    @StateObject private var runner = APIStressModel()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                configuration
                metrics
                errors
                footer
            }
            .padding(.horizontal, 34).padding(.vertical, 28)
            .frame(maxWidth: 1_180).frame(maxWidth: .infinity)
        }
        .background(DesktopTheme.canvas)
        .onAppear { runner.autofill(channel: model.channel, key: model.apiKey) }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Label("API 压测", systemImage: "speedometer").font(.system(size: 28, weight: .semibold))
                Text("用可复现的并发请求验证吞吐、延迟、稳定性与错误边界。")
                    .font(.system(size: 13)).foregroundStyle(.secondary)
            }
            Spacer()
            HStack(spacing: 9) {
                Button("清空") { runner.clear() }.disabled(runner.running)
                Menu {
                    Button("导出 JSON") { runner.saveReport(format: .json) }
                    Button("导出 HTML") { runner.saveReport(format: .html) }
                } label: { Label("导出报告", systemImage: "square.and.arrow.up") }
                    .disabled(runner.completed == 0)
                if runner.running {
                    Button("停止") { runner.stop() }.buttonStyle(.bordered).tint(.orange)
                } else {
                    Button { runner.start() } label: { Label("开始压测", systemImage: "play.fill") }
                        .buttonStyle(.borderedProminent).tint(DesktopTheme.accent)
                }
            }
        }
    }

    private var configuration: some View {
        VStack(alignment: .leading, spacing: 17) {
            Text("请求配置").font(.system(size: 15, weight: .semibold))
            HStack(spacing: 9) {
                TextField("请求地址，例如 https://api.example.com/v1/chat/completions", text: $runner.endpoint)
                    .textFieldStyle(.roundedBorder)
                Button("解析") { runner.resolveEndpoint() }.controlSize(.small)
            }
            if let resolution = runner.resolutionText { Label(resolution, systemImage: "network").font(.system(size: 11)).foregroundStyle(.secondary) }
            HStack(spacing: 12) {
                Picker("方法", selection: $runner.method) {
                    ForEach(["GET", "POST", "PUT", "PATCH", "DELETE"], id: \.self) { Text($0).tag($0) }
                }.frame(width: 150)
                Picker("模式", selection: $runner.durationMode) {
                    Text("按总请求").tag(false)
                    Text("按持续时间").tag(true)
                }.pickerStyle(.segmented).frame(width: 190)
                Stepper("超时 \(runner.timeoutSeconds) 秒", value: $runner.timeoutSeconds, in: 1...600).frame(width: 180)
                Stepper("启动间隔 \(runner.rampUpMilliseconds) ms", value: $runner.rampUpMilliseconds, in: 0...60_000, step: 100).frame(width: 230)
                Spacer()
            }
            HStack(alignment: .top, spacing: 16) {
                editor("请求头（每行 Header: Value）", text: $runner.headersText, height: 90)
                editor("请求体（支持任意 JSON / 文本）", text: $runner.bodyText, height: 150)
            }
            HStack(spacing: 16) {
                Stepper("总请求 \(runner.totalRequests)", value: $runner.totalRequests, in: 1...20_000, step: 10)
                Stepper("并发数 \(runner.concurrency)", value: $runner.concurrency, in: 1...100)
                if runner.durationMode { Stepper("持续 \(runner.durationSeconds) 秒", value: $runner.durationSeconds, in: 1...600) }
                Stepper("速率 \(runner.ratePerSecond == 0 ? "不限" : "\(runner.ratePerSecond)/s")", value: $runner.ratePerSecond, in: 0...1_000, step: 1)
                Spacer()
            }
            HStack(spacing: 18) {
                Picker("预期状态", selection: $runner.expectedStatusText) {
                    ForEach(["2xx / 3xx", "200", "201", "204", "400", "401", "429", "500"], id: \.self) { Text($0).tag($0) }
                }.frame(width: 170)
                Toggle("我已获得目标接口所有者的压测授权", isOn: $runner.authorized).toggleStyle(.checkbox)
                    .font(.system(size: 11))
                Spacer()
            }
            Text("支持实时停止。建议先用低并发预检，再逐步提升；所有请求均使用当前填写的请求头和请求体。")
                .font(.system(size: 11)).foregroundStyle(.tertiary)
            if let error = runner.errorText { Label(error, systemImage: "exclamationmark.triangle").font(.system(size: 11)).foregroundStyle(.orange) }
        }
        .padding(22).background(.white, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(DesktopTheme.line))
    }

    private func editor(_ title: String, text: Binding<String>, height: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
            TextEditor(text: text).font(.system(size: 11, design: .monospaced))
                .frame(maxWidth: .infinity).frame(height: height)
                .padding(7).background(DesktopTheme.surface, in: RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(DesktopTheme.line))
        }.frame(maxWidth: .infinity)
    }

    private var metrics: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { Text("实时指标").font(.system(size: 15, weight: .semibold)); Spacer(); Text(runner.statusText).font(.system(size: 11)).foregroundStyle(.secondary) }
            ProgressView(value: runner.progress).tint(DesktopTheme.accent)
            HStack(spacing: 0) {
                metric("完成", "\(runner.completed)/\(runner.totalRequests)", "checkmark.circle")
                Divider().frame(height: 38)
                metric("成功率", String(format: "%.1f%%", runner.successRate * 100), "chart.line.uptrend.xyaxis")
                Divider().frame(height: 38)
                metric("吞吐", String(format: "%.1f req/s", runner.requestsPerSecond), "arrow.up.right")
                Divider().frame(height: 38)
                metric("P50 / P95", String(format: "%.0f / %.0f ms", runner.p50, runner.p95), "timer")
                Divider().frame(height: 38)
                metric("P99", String(format: "%.0f ms", runner.p99), "gauge.with.dots.needle.67percent")
                Divider().frame(height: 38)
                metric("响应量", ByteCountFormatter.string(fromByteCount: Int64(runner.totalBytes), countStyle: .file), "arrow.down.doc")
            }
        }.padding(22).background(DesktopTheme.surface, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private func metric(_ title: String, _ value: String, _ icon: String) -> some View {
        HStack(spacing: 9) {
            Image(systemName: icon).foregroundStyle(DesktopTheme.accent)
            VStack(alignment: .leading, spacing: 3) { Text(value).font(.system(size: 16, weight: .semibold, design: .rounded)); Text(title).font(.system(size: 10)).foregroundStyle(.secondary) }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 12)
    }

    @ViewBuilder private var errors: some View {
        if !runner.errorCounts.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Text("错误分布").font(.system(size: 15, weight: .semibold))
                ForEach(runner.errorCounts.sorted(by: { $0.value > $1.value }), id: \.key) { item in
                    HStack { Image(systemName: "xmark.octagon").foregroundStyle(.orange); Text(item.key).font(.system(size: 11)).lineLimit(1); Spacer(); Text("×\(item.value)").font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary) }
                }
            }.padding(22).background(.white, in: RoundedRectangle(cornerRadius: 20, style: .continuous)).overlay(RoundedRectangle(cornerRadius: 20).stroke(DesktopTheme.line))
        }
    }

    private var footer: some View {
        HStack {
            Label("P50 / P95 / P99 延迟、吞吐与 HTTP 错误都会随本次运行记录", systemImage: "waveform.path.ecg").font(.system(size: 10)).foregroundStyle(.tertiary)
            Spacer()
            if let started = runner.startedAt { Text("开始于 \(started, format: .dateTime.hour().minute().second())").font(.system(size: 10)).foregroundStyle(.tertiary) }
        }
    }
}
