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
    let promptTokens: Int
    let completionTokens: Int
    let totalTokens: Int
    let usageKnown: Bool
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
    // The connection fields are intentionally first-class: a load test should
    // be reproducible from a base URL, API key and selected model alone.
    @Published var baseURL = ""
    @Published var apiKey = ""
    @Published var availableModels: [String] = []
    @Published var selectedModel = ""
    @Published private(set) var isFetchingModels = false
    @Published private(set) var modelsStatus: String?
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
    @Published var maxOutputTokens = 256
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
    @Published private(set) var promptTokens = 0
    @Published private(set) var completionTokens = 0
    @Published private(set) var totalTokens = 0
    @Published private(set) var usageSamples = 0
    @Published private(set) var usageMissing = 0
    @Published private(set) var startedAt: Date?
    @Published private(set) var finishedAt: Date?
    @Published private(set) var running = false

    private var runTask: Task<Void, Never>?

    deinit { runTask?.cancel() }

    var progress: Double {
        if durationMode, let startedAt {
            let elapsed = (finishedAt ?? Date()).timeIntervalSince(startedAt)
            return min(1, max(0, elapsed / Double(max(1, durationSeconds))))
        }
        return totalRequests > 0 ? Double(completed) / Double(totalRequests) : 0
    }
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
    var requestsPerMinute: Double { requestsPerSecond * 60 }
    var tokensPerMinute: Double {
        guard usageSamples > 0, let startedAt else { return 0 }
        let end = finishedAt ?? Date()
        return Double(totalTokens) / max(0.001, end.timeIntervalSince(startedAt)) * 60
    }
    var tokensPerMinuteText: String { usageSamples > 0 ? String(format: "%.0f", tokensPerMinute) : "—" }

    func autofill(channel: ChannelProfile, key: String) {
        if baseURL.isEmpty { baseURL = channel.base.trimmingCharacters(in: .whitespacesAndNewlines) }
        if apiKey.isEmpty { apiKey = key }
        guard endpoint.isEmpty else { return }
        let base = baseURL
        if !base.isEmpty {
            endpoint = chatEndpoint(from: base)
        }
        if !key.isEmpty && !headersText.localizedCaseInsensitiveContains("authorization") {
            headersText = "Content-Type: application/json\nAuthorization: Bearer \(key)"
        }
        if !channel.model.isEmpty {
            selectedModel = channel.model
            bodyText = bodyText.replacingOccurrences(of: "your-model", with: channel.model)
        }
    }

    func chooseModel(_ value: String) {
        selectedModel = value
        guard !value.isEmpty else { return }
        applyModelToBody(value)
    }

    private func applyModelToBody(_ value: String) {
        guard !value.isEmpty else { return }
        if let data = bodyText.data(using: .utf8),
           var object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            object["model"] = value
            if let encoded = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]),
               let text = String(data: encoded, encoding: .utf8) {
                bodyText = text
                return
            }
        }
        if bodyText.contains("\"model\"") {
            bodyText = bodyText.replacingOccurrences(of: #"("model"\s*:\s*")[^"]*(")"#, with: "$1\(value)$2", options: .regularExpression)
        }
    }

    func fetchModels() {
        guard !isFetchingModels else { return }
        guard let url = modelsURL() else { modelsStatus = "请输入有效的 Base URL"; return }
        if endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            endpoint = chatEndpoint(from: baseURL)
        }
        isFetchingModels = true; modelsStatus = "正在获取模型…"
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let value = apiKey.hasPrefix("Bearer ") ? apiKey : "Bearer \(apiKey)"
            request.setValue(value, forHTTPHeaderField: "Authorization")
        }
        Task { [weak self] in
            do {
                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse else { throw NSError(domain: "Models", code: 0, userInfo: [NSLocalizedDescriptionKey: "无法读取模型接口响应"]) }
                guard (200..<300).contains(http.statusCode) else { throw NSError(domain: "Models", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "模型接口返回 HTTP \(http.statusCode)"]) }
                let decoded = try Self.parseModelIDs(data)
                await MainActor.run {
                    guard let self else { return }
                    self.availableModels = decoded.filter { !$0.isEmpty }.sorted()
                    if self.selectedModel.isEmpty, let first = self.availableModels.first { self.chooseModel(first) }
                    self.modelsStatus = self.availableModels.isEmpty ? "接口已连接，但没有返回可用模型" : "已获取 \(self.availableModels.count) 个模型"
                    self.isFetchingModels = false
                }
            } catch {
                await MainActor.run { [weak self] in
                    self?.isFetchingModels = false
                    self?.modelsStatus = "获取失败：\(error.localizedDescription)"
                }
            }
        }
    }

    private static func parseModelIDs(_ data: Data) throws -> [String] {
        let object: Any
        do { object = try JSONSerialization.jsonObject(with: data) }
        catch { throw NSError(domain: "Models", code: 0, userInfo: [NSLocalizedDescriptionKey: "模型接口返回的不是 JSON"]) }
        let values: [Any]
        if let root = object as? [String: Any] {
            values = (root["data"] as? [Any]) ?? (root["models"] as? [Any]) ?? []
        } else if let array = object as? [Any] {
            values = array
        } else { values = [] }
        return values.compactMap { value in
            if let id = value as? String { return id }
            if let row = value as? [String: Any] {
                return (row["id"] as? String) ?? (row["name"] as? String) ?? (row["model"] as? String)
            }
            return nil
        }
    }

    private func modelsURL() -> URL? {
        var raw = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        if raw.hasSuffix("/chat/completions") { raw = String(raw.dropLast("/chat/completions".count)) }
        if raw.hasSuffix("/completions") { raw = String(raw.dropLast("/completions".count)) }
        if raw.hasSuffix("/models") { raw = String(raw.dropLast("/models".count)) }
        raw = raw.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !raw.lowercased().hasSuffix("/v1") { raw += "/v1" }
        return URL(string: raw + "/models")
    }

    private func chatEndpoint(from value: String) -> String {
        var raw = value.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.hasSuffix("/chat/completions") || raw.hasSuffix("/completions") { return raw }
        raw = raw.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return raw.lowercased().hasSuffix("/v1") ? raw + "/chat/completions" : raw + "/v1/chat/completions"
    }

    func syncEndpointFromBase() {
        guard !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        endpoint = chatEndpoint(from: baseURL)
    }

    func start() {
        guard !running else { return }
        guard authorized else { errorText = "请确认你已获得目标接口所有者的压测授权。"; return }
        if endpoint.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !baseURL.isEmpty {
            endpoint = chatEndpoint(from: baseURL)
        }
        if !selectedModel.isEmpty { applyModelToBody(selectedModel) }
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
        let stamp = DateFormatter.apiReportStamp.string(from: Date())
        let model = selectedModel.isEmpty ? "未选择模型" : selectedModel
        let safeModel = model.replacingOccurrences(of: "[^A-Za-z0-9._-]+", with: "-", options: .regularExpression).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        panel.nameFieldStringValue = "测试报告-\(safeModel.isEmpty ? "模型" : safeModel)-\(stamp).\(format.extension)"
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
        latencies = []; totalBytes = 0; promptTokens = 0; completionTokens = 0; totalTokens = 0; usageSamples = 0; usageMissing = 0
        startedAt = nil; finishedAt = nil
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
        if !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            headers["Authorization"] = apiKey.hasPrefix("Bearer ") ? apiKey : "Bearer \(apiKey)"
        }
        var body = bodyText.trimmingCharacters(in: .whitespacesAndNewlines)
        if selectedModel.isEmpty && body.range(of: #""model"\s*:\s*"your-model""#, options: .regularExpression) != nil {
            errorText = "请先点击“获取模型”并选择模型，或在高级请求体中填写实际模型 ID。"; return nil
        }
        if ["GET", "HEAD"].contains(method) && !body.isEmpty {
            errorText = "GET / HEAD 请求不应携带请求体；请清空请求体或改用 POST。"; return nil
        }
        if !body.isEmpty, let jsonData = body.data(using: .utf8), var object = (try? JSONSerialization.jsonObject(with: jsonData)) as? [String: Any], object["max_tokens"] == nil {
            object["max_tokens"] = maxOutputTokens
            if let encoded = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]), let formatted = String(data: encoded, encoding: .utf8) { body = formatted }
        }
        let data = body.isEmpty ? nil : Data(body.utf8)
        // Keep the native runner within the same bounded envelope as the
        // reviewed local load-test engine; this is a capacity probe, not an
        // unbounded traffic generator.
        let total = min(max(totalRequests, 1), 20_000)
        let workers = min(max(concurrency, 1), total)
        let timeout = min(max(timeoutSeconds, 1), 600)
        let duration = durationMode ? min(max(durationSeconds, 1), 600) : 0
        let rate = min(max(ratePerSecond, 0), 1_000)
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
        promptTokens += result.promptTokens
        completionTokens += result.completionTokens
        totalTokens += result.totalTokens
        if result.usageKnown { usageSamples += 1 } else { usageMissing += 1 }
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
                return APIStressResult(statusCode: status, latency: Date().timeIntervalSince(begin), bytes: data.count, error: "检测到重定向，已停止以避免凭据跨域发送", succeeded: false, promptTokens: 0, completionTokens: 0, totalTokens: 0, usageKnown: false)
            }
            let success = config.expectedStatus.map { status == $0 } ?? (status.map { (200..<400).contains($0) } ?? false)
            let error = success ? nil : (config.expectedStatus.map { "HTTP \(status ?? 0)（预期 \($0)）" } ?? "HTTP \(status ?? 0)")
            let usage = Self.usage(from: data)
            let known = usage.prompt != nil || usage.completion != nil || usage.total != nil
            let prompt = usage.prompt ?? 0
            let completion = usage.completion ?? 0
            let total = usage.total ?? (known ? prompt + completion : 0)
            return APIStressResult(statusCode: status, latency: Date().timeIntervalSince(begin), bytes: data.count, error: error, succeeded: success, promptTokens: prompt, completionTokens: completion, totalTokens: total, usageKnown: known)
        } catch is CancellationError {
            return APIStressResult(statusCode: nil, latency: Date().timeIntervalSince(begin), bytes: 0, error: "已取消", succeeded: false, promptTokens: 0, completionTokens: 0, totalTokens: 0, usageKnown: false)
        } catch {
            let message = (error as NSError).localizedDescription
            return APIStressResult(statusCode: nil, latency: Date().timeIntervalSince(begin), bytes: 0, error: message, succeeded: false, promptTokens: 0, completionTokens: 0, totalTokens: 0, usageKnown: false)
        }
    }

    private struct Usage { let prompt: Int?; let completion: Int?; let total: Int? }
    nonisolated private static func usage(from data: Data) -> Usage {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let usage = object["usage"] as? [String: Any] else { return Usage(prompt: nil, completion: nil, total: nil) }
        func number(_ keys: [String]) -> Int? {
            for key in keys {
                if let n = usage[key] as? Int { return n }
                if let n = usage[key] as? NSNumber { return n.intValue }
            }
            return nil
        }
        return Usage(prompt: number(["prompt_tokens", "input_tokens"]), completion: number(["completion_tokens", "output_tokens"]), total: number(["total_tokens"]))
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
        let baseForReport = redactedURL(baseURL)
        let summary = ["baseUrl": baseForReport, "model": selectedModel, "endpoint": endpointForReport, "method": method, "completed": completed, "succeeded": succeeded, "failed": failed, "successRate": successRate, "throughputRps": requestsPerSecond, "requestsPerMinute": requestsPerMinute, "tokensPerMinute": usageSamples > 0 ? tokensPerMinute : NSNull(), "promptTokens": usageSamples > 0 ? promptTokens : NSNull(), "completionTokens": usageSamples > 0 ? completionTokens : NSNull(), "totalTokens": usageSamples > 0 ? totalTokens : NSNull(), "usageSamples": usageSamples, "usageMissing": usageMissing, "p50Ms": p50, "p95Ms": p95, "p99Ms": p99, "bytes": totalBytes, "errors": errorCounts, "headers": header] as [String : Any]
        if format == .json { return (try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys])) ?? Data() }
        let escaped = (try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys])).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        let html = """
<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>API 压测报告</title><style>body{font:15px -apple-system,BlinkMacSystemFont,sans-serif;background:#f5f7fb;color:#18212f;margin:0;padding:40px}main{max-width:980px;margin:auto;background:#fff;border-radius:24px;padding:36px;box-shadow:0 8px 30px #15233d12}h1{margin-top:0;color:#0b70e8}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{padding:16px;background:#f5f8fd;border-radius:14px}.value{font-size:24px;font-weight:650;margin-top:7px}.label{color:#64748b;font-size:12px}pre{white-space:pre-wrap;background:#111827;color:#d1e4ff;padding:20px;border-radius:14px;overflow:auto}</style><main><h1>API 压测报告</h1><p>生成时间：\(Date()) · 模型：\(selectedModel.isEmpty ? "未指定" : selectedModel)</p><div class="grid"><div class="metric"><div class="label">完成</div><div class="value">\(completed)</div></div><div class="metric"><div class="label">成功率</div><div class="value">\(String(format: "%.1f%%", successRate*100))</div></div><div class="metric"><div class="label">RPM</div><div class="value">\(String(format: "%.1f", requestsPerMinute))</div></div><div class="metric"><div class="label">TPM</div><div class="value">\(tokensPerMinuteText)</div></div><div class="metric"><div class="label">P95 延迟</div><div class="value">\(String(format: "%.0f ms", p95))</div></div><div class="metric"><div class="label">输入 Token</div><div class="value">\(usageSamples > 0 ? "\(promptTokens)" : "—")</div></div><div class="metric"><div class="label">输出 Token</div><div class="value">\(usageSamples > 0 ? "\(completionTokens)" : "—")</div></div><div class="metric"><div class="label">总 Token</div><div class="value">\(usageSamples > 0 ? "\(totalTokens)" : "—")</div></div></div><h2>请求摘要</h2><p>服务端提供 usage 的请求：\(usageSamples)；未提供 usage：\(usageMissing)。</p><pre>\(escaped.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;"))</pre></main></html>
"""
        return Data(html.utf8)
    }

    private func redactedURL(_ raw: String) -> String {
        guard var components = URLComponents(string: raw) else { return raw.isEmpty ? "" : "[已隐藏]" }
        if components.user != nil { components.user = "[已隐藏]" }
        if components.password != nil { components.password = "[已隐藏]" }
        if let items = components.queryItems {
            components.queryItems = items.map { URLQueryItem(name: $0.name, value: "[已隐藏]") }
        }
        return components.string ?? raw
    }

    enum ReportFormat { case json, html; var `extension`: String { self == .json ? "json" : "html" } }
}

private extension DateFormatter {
    static let apiReportStamp: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter
    }()
}

struct APIStressView: View {
    @ObservedObject var model: AppModel
    @StateObject private var runner = APIStressModel()
    @State private var showAdvanced = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                connectionCard
                metrics
                requestCard
                advancedCard
                errors
                footer
            }
            .padding(.horizontal, 36)
            .padding(.vertical, 30)
            .frame(maxWidth: 1_180)
            .frame(maxWidth: .infinity)
        }
        .background(DesktopTheme.canvas)
        .onAppear { runner.autofill(channel: model.channel, key: model.apiKey) }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 18) {
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 10) {
                    Image(systemName: "chart.xyaxis.line")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundStyle(DesktopTheme.accent)
                    Text("API 压测")
                        .font(.system(size: 30, weight: .bold, design: .rounded))
                }
                Text("用真实模型请求测量 RPM、TPM、延迟与稳定性。先连接渠道，再开始受控压测。")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if runner.running {
                StatusPill(text: "压测进行中", color: .orange, icon: "waveform.path.ecg")
                Button("停止") { runner.stop() }
                    .buttonStyle(.bordered)
                    .tint(.orange)
            } else {
                StatusPill(text: "准备就绪", color: .green, icon: "checkmark.circle.fill")
                Button { runner.start() } label: {
                    Label("开始压测", systemImage: "play.fill")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(DesktopTheme.accent)
            }
            Menu {
                Button("导出 HTML 报告") { runner.saveReport(format: .html) }
                Button("导出 JSON 数据") { runner.saveReport(format: .json) }
                Divider()
                Button("清空本次结果") { runner.clear() }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.system(size: 22))
                    .foregroundStyle(.secondary)
            }
            .menuStyle(.borderlessButton)
            .disabled(runner.running)
        }
    }

    private var connectionCard: some View {
        VStack(alignment: .leading, spacing: 18) {
            cardTitle("连接渠道", subtitle: "输入 OpenAI 兼容接口的 Base URL 与 API Key，自动读取可用模型")
            HStack(alignment: .bottom, spacing: 14) {
                VStack(alignment: .leading, spacing: 7) {
                    Text("BASE URL").font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary)
                    TextField("https://api.example.com/v1", text: $runner.baseURL)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 13, design: .monospaced))
                        .onSubmit { runner.fetchModels() }
                        .onChange(of: runner.baseURL) { _, _ in runner.syncEndpointFromBase() }
                }
                .frame(maxWidth: .infinity)
                VStack(alignment: .leading, spacing: 7) {
                    Text("API KEY").font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary)
                    SecureField("sk-…", text: $runner.apiKey)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 13, design: .monospaced))
                }
                Button {
                    runner.fetchModels()
                } label: {
                    Label(runner.isFetchingModels ? "获取中…" : "获取模型", systemImage: runner.isFetchingModels ? "arrow.triangle.2.circlepath" : "arrow.down.circle.fill")
                        .font(.system(size: 12, weight: .semibold))
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(DesktopTheme.accent)
                .disabled(runner.isFetchingModels)
            }
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 7) {
                    Text("压测模型").font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary)
                    if runner.availableModels.isEmpty {
                        HStack(spacing: 8) {
                            Image(systemName: "square.stack.3d.up").foregroundStyle(.secondary)
                            Text("先点击“获取模型”，或在下方手动填写模型 ID")
                                .font(.system(size: 12)).foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 11).padding(.vertical, 8)
                        .background(DesktopTheme.surface, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    } else {
                        Picker("选择模型", selection: Binding(get: { runner.selectedModel }, set: { runner.chooseModel($0) })) {
                            ForEach(runner.availableModels, id: \.self) { Text($0).tag($0) }
                        }
                        .labelsHidden()
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .frame(maxWidth: .infinity)
                VStack(alignment: .leading, spacing: 7) {
                    Text("模型 ID（可选）").font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary)
                    TextField("例如 gpt-4o-mini", text: Binding(get: { runner.selectedModel }, set: { runner.chooseModel($0) }))
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12, design: .monospaced))
                        .onSubmit { runner.chooseModel(runner.selectedModel) }
                }
                .frame(maxWidth: .infinity)
            }
            if let status = runner.modelsStatus {
                Label(status, systemImage: status.hasPrefix("获取失败") ? "exclamationmark.triangle" : "info.circle")
                    .font(.system(size: 11))
                    .foregroundStyle(status.hasPrefix("获取失败") ? .orange : .secondary)
            }
            if let error = runner.errorText {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.orange)
            }
        }
        .padding(24)
        .background(.white, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(DesktopTheme.line))
        .shadow(color: .black.opacity(0.025), radius: 8, y: 3)
    }

    private var requestCard: some View {
        VStack(alignment: .leading, spacing: 17) {
            cardTitle("运行配置", subtitle: "默认发送 OpenAI Chat Completions 请求，可展开自定义请求参数")
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 7) {
                    Text("请求地址").font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary)
                    TextField("自动由 Base URL 生成，也可以手动覆盖", text: $runner.endpoint)
                        .textFieldStyle(.roundedBorder)
                        .font(.system(size: 12, design: .monospaced))
                }
                .frame(maxWidth: .infinity)
                Picker("方法", selection: $runner.method) {
                    ForEach(["POST", "GET", "PUT", "PATCH"], id: \.self) { Text($0).tag($0) }
                }
                .labelsHidden()
                .frame(width: 105)
            }
            HStack(spacing: 18) {
                Stepper(value: $runner.totalRequests, in: 1...20_000, step: 10) {
                    setting("请求数", "\(runner.totalRequests)")
                }
                Stepper(value: $runner.concurrency, in: 1...100) {
                    setting("并发", "\(runner.concurrency)")
                }
                Stepper(value: $runner.timeoutSeconds, in: 1...600) {
                    setting("超时", "\(runner.timeoutSeconds)s")
                }
                Stepper(value: $runner.ratePerSecond, in: 0...1_000) {
                    setting("限速", runner.ratePerSecond == 0 ? "不限" : "\(runner.ratePerSecond) RPS")
                }
                Spacer()
            }
            HStack(spacing: 18) {
                Stepper(value: $runner.maxOutputTokens, in: 1...32_768, step: 32) {
                    setting("输出上限", "\(runner.maxOutputTokens) tokens")
                }
                Text("仅在请求体未设置 max_tokens 时作为默认值")
                    .font(.system(size: 11)).foregroundStyle(.tertiary)
                Spacer()
            }
            HStack(spacing: 12) {
                Toggle("持续时间模式", isOn: $runner.durationMode)
                    .toggleStyle(.switch)
                if runner.durationMode {
                    Stepper("持续 \(runner.durationSeconds) 秒", value: $runner.durationSeconds, in: 1...600)
                }
                Spacer()
                Toggle("我已获得目标接口所有者的压测授权", isOn: $runner.authorized)
                    .toggleStyle(.checkbox)
                    .font(.system(size: 11))
            }
            if let resolution = runner.resolutionText {
                Label(resolution, systemImage: "network")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            HStack {
                Button("解析 DNS") { runner.resolveEndpoint() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                Text("建议从 1 并发、10 次请求开始，确认渠道正常后逐步提高。")
                    .font(.system(size: 11)).foregroundStyle(.tertiary)
                Spacer()
            }
        }
        .padding(24)
        .background(.white, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(DesktopTheme.line))
    }

    private var advancedCard: some View {
        DisclosureGroup(isExpanded: $showAdvanced) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 16) {
                    editor("请求头（每行 Header: Value）", text: $runner.headersText, height: 100)
                    editor("请求体（OpenAI JSON）", text: $runner.bodyText, height: 145)
                }
                HStack(spacing: 18) {
                    Picker("预期状态", selection: $runner.expectedStatusText) {
                        ForEach(["2xx / 3xx", "200", "201", "204", "400", "401", "429", "500"], id: \.self) { Text($0).tag($0) }
                    }
                    .frame(width: 170)
                    Stepper("启动间隔 \(runner.rampUpMilliseconds) ms", value: $runner.rampUpMilliseconds, in: 0...60_000, step: 100)
                    Spacer()
                }
            }
            .padding(.top, 16)
        } label: {
            HStack(spacing: 9) {
                Image(systemName: "slider.horizontal.3")
                    .foregroundStyle(DesktopTheme.accent)
                Text("高级请求参数")
                    .font(.system(size: 14, weight: .semibold))
                Text("请求头、请求体、状态码与启动策略")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
        }
        .padding(20)
        .background(DesktopTheme.surface, in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous).stroke(DesktopTheme.line))
    }

    private var metrics: some View {
        VStack(alignment: .leading, spacing: 17) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("实时指标").font(.system(size: 17, weight: .semibold))
                    Text("RPM 按完成请求统计；TPM 只使用服务端返回的真实 usage，未返回时显示“—”。")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer()
                Text(runner.statusText).font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
            }
            ProgressView(value: runner.progress)
                .tint(DesktopTheme.accent)
                .scaleEffect(x: 1, y: 1.2, anchor: .center)
            HStack(spacing: 10) {
                metric("RPM", String(format: "%.1f", runner.requestsPerMinute), "arrow.up.right.circle.fill", "请求 / 分钟")
                metric("TPM", runner.tokensPerMinuteText, "textformat.123", runner.usageSamples > 0 ? "Token / 分钟" : "服务未返回 usage")
                metric("成功率", String(format: "%.1f%%", runner.successRate * 100), "checkmark.circle.fill", "\(runner.completed) 次完成")
                metric("P95", String(format: "%.0f ms", runner.p95), "timer", "延迟")
            }
            HStack(spacing: 10) {
                compactMetric("输入 Token", runner.usageSamples > 0 ? "\(runner.promptTokens)" : "—")
                compactMetric("输出 Token", runner.usageSamples > 0 ? "\(runner.completionTokens)" : "—")
                compactMetric("总 Token", runner.usageSamples > 0 ? "\(runner.totalTokens)" : "—")
                compactMetric("响应量", ByteCountFormatter.string(fromByteCount: Int64(runner.totalBytes), countStyle: .file))
            }
        }
        .padding(24)
        .background(DesktopTheme.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    private func metric(_ title: String, _ value: String, _ icon: String, _ caption: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 7) {
                Image(systemName: icon).foregroundStyle(DesktopTheme.accent)
                Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
            }
            Text(value).font(.system(size: 25, weight: .bold, design: .rounded))
            Text(caption).font(.system(size: 10)).foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(15)
        .background(.white, in: RoundedRectangle(cornerRadius: 13, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 13, style: .continuous).stroke(DesktopTheme.line))
    }

    private func compactMetric(_ title: String, _ value: String) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 10)).foregroundStyle(.secondary)
                Text(value).font(.system(size: 14, weight: .semibold, design: .rounded))
            }
            Spacer()
        }
        .padding(.horizontal, 13).padding(.vertical, 11)
        .background(.white.opacity(0.76), in: RoundedRectangle(cornerRadius: 11, style: .continuous))
    }

    @ViewBuilder private var errors: some View {
        if !runner.errorCounts.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Text("错误分布").font(.system(size: 15, weight: .semibold))
                ForEach(runner.errorCounts.sorted(by: { $0.value > $1.value }), id: \.key) { item in
                    HStack(spacing: 8) {
                        Image(systemName: "xmark.octagon.fill").foregroundStyle(.orange)
                        Text(item.key).font(.system(size: 11)).lineLimit(1)
                        Spacer()
                        Text("×\(item.value)").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(22)
            .background(.white, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(DesktopTheme.line))
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            Image(systemName: "lock.shield").foregroundStyle(.secondary)
            Text("受控压测：最多 20,000 次请求、100 并发、600 秒；不会自动重试或跟随重定向。")
                .font(.system(size: 10)).foregroundStyle(.tertiary)
            Spacer()
            if let started = runner.startedAt {
                Text("开始于 \(started, format: .dateTime.hour().minute().second())")
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
            }
        }
    }

    private func cardTitle(_ title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 16, weight: .semibold))
            Text(subtitle).font(.system(size: 11)).foregroundStyle(.secondary)
        }
    }

    private func setting(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 10)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 13, weight: .semibold, design: .rounded))
        }
    }

    private func editor(_ title: String, text: Binding<String>, height: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(title).font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
            TextEditor(text: text)
                .font(.system(size: 11, design: .monospaced))
                .frame(maxWidth: .infinity).frame(height: height)
                .padding(7)
                .background(.white, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(DesktopTheme.line))
        }
        .frame(maxWidth: .infinity)
    }
}

private struct StatusPill: View {
    let text: String
    let color: Color
    let icon: String
    var body: some View {
        Label(text, systemImage: icon)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 11).padding(.vertical, 7)
            .background(color.opacity(0.10), in: Capsule())
    }
}
