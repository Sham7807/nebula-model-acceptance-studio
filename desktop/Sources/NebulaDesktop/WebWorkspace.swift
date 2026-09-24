import AppKit
import WebKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class WebWorkspace: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    @Published var loaded = false
    @Published var busy = false
    @Published var error: String?
    @Published var currentRoute = "text"
    @Published var taskState = "draft"
    @Published var modelName = ""
    @Published var channelHost = ""
    @Published var progressText = "准备好后即可开始测试"
    @Published var fraction: Double?
    @Published var startedAt: Date?
    @Published var finishedAt: Date?
    private(set) var runID: String?
    let identity = UUID()
    let webView: WKWebView
    private var session: EngineSession?
    private var resources: URL
    private var requestedRoute = "text"
    private var profile: ChannelProfile?
    private var apiKey = ""
    private var downloads: [ObjectIdentifier: DesktopDownload] = [:]
    private var previews: [NSWindow] = []
    var onHistoryChanged: (() -> Void)?
    var onDownload: ((String) -> Void)?

    init(resources: URL = Bundle.main.resourceURL!) {
        self.resources = resources
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: config)
        webView.appearance = NSAppearance(named: .aqua)
        webView.underPageBackgroundColor = .white
        super.init()
        webView.navigationDelegate = self; webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        webView.allowsBackForwardNavigationGestures = false
        webView.configuration.userContentController.add(self, name: "nebula")
    }

    func connect(_ session: EngineSession) async {
        guard self.session?.url != session.url else { return }
        self.session = session; loaded = false; error = nil; busy = false
        let controller = webView.configuration.userContentController
        controller.removeAllUserScripts()
        do {
            let bridge = try String(contentsOf: resources.appendingPathComponent("desktop-bridge.js"), encoding: .utf8)
            let css = try String(contentsOf: resources.appendingPathComponent("desktop.css"), encoding: .utf8)
            let encoded = String(data: try JSONSerialization.data(withJSONObject: ["css":css,"origin":session.url.absoluteString]), encoding: .utf8)!
            controller.addUserScript(WKUserScript(source: "window.__nebulaAssets="+encoded+";\n"+bridge, injectionTime: .atDocumentEnd, forMainFrameOnly: false))
            let properties: [HTTPCookiePropertyKey: Any] = [.name:session.cookieName, .value:session.cookie,
                .domain:"127.0.0.1", .path:"/", HTTPCookiePropertyKey("HttpOnly"):"TRUE"]
            guard let cookie = HTTPCookie(properties: properties) else { throw URLError(.userAuthenticationRequired) }
            await webView.configuration.websiteDataStore.httpCookieStore.setCookie(cookie)
            webView.load(URLRequest(url: workspaceURL(session)))
        } catch { self.error = "无法打开检测工作区："+error.localizedDescription }
    }

    func navigate(_ route: String) {
        requestedRoute = route
        guard loaded else { return }
        call("navigate", arguments: ["route":route])
    }
    func configure(_ profile: ChannelProfile, key: String) {
        self.profile = profile; apiKey = key
        if loaded { call("configure", arguments: ["base":profile.base,"model":profile.model,"key":key]) }
    }
    func openHistory(_ id: String? = nil) {
        navigate("history")
        if let id { call("openHistory", arguments: ["id":id]) }
    }
    func refresh() {
        guard !busy || !loaded, let session else { return }
        loaded = false; error = nil; webView.load(URLRequest(url: workspaceURL(session)))
    }
    private func workspaceURL(_ session: EngineSession) -> URL {
        var url = URLComponents(url:session.url,resolvingAgainstBaseURL:false)!
        url.queryItems = [URLQueryItem(name:"desktop_workspace",value:identity.uuidString)]
        if let runID { url.queryItems?.append(URLQueryItem(name:"desktop_run",value:runID)) }
        return url.url!
    }
    func dispose() {
        guard !busy else { return }
        previews.forEach { $0.close() }; previews.removeAll()
        webView.stopLoading()
        webView.configuration.userContentController.removeScriptMessageHandler(forName:"nebula")
        webView.loadHTMLString("",baseURL:nil)
    }
    func engineStopped() {
        if busy { taskState = "interrupted"; finishedAt = Date(); progressText = "本地引擎已停止，重新启动后请检查已保存结果。" }
        busy = false; loaded = false
        // Discard late page messages from the dead session. A new engine
        // connection installs a fresh origin and retains only this run's ID.
        session = nil
    }
    func stopTests() { call("stop", arguments: [:]) }
    private func call(_ method: String, arguments: [String:Any]) {
        webView.callAsyncJavaScript("return window.NebulaDesktop?.[method](payload);", arguments:["method":method,"payload":arguments], in:nil, in:.page) { [weak self] result in
            if case .failure(let problem) = result {
                Task { @MainActor in self?.error = "工作区暂时未响应："+problem.localizedDescription }
            }
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.webView === webView, message.frameInfo.isMainFrame, let session,
              message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == session.url.port,
              let body = message.body as? [String:Any], let type = body["type"] as? String else { return }
        switch type {
        case "ready":
            loaded = true; error = nil
            if let profile { configure(profile, key:apiKey) }
            navigate(requestedRoute)
        case "state":
            let next = body["busy"] as? Bool ?? false
            if next && !busy { startedAt = Date(); finishedAt = nil }
            if !next && busy { finishedAt = Date() }
            busy = next
            taskState = body["status"] as? String ?? (next ? "running" : taskState)
            if let value = body["model"] as? String, !value.isEmpty { modelName = value }
            channelHost = body["channel"] as? String ?? channelHost
            progressText = body["progress"] as? String ?? progressText
            fraction = body["fraction"] as? Double
            runID = body["runID"] as? String
            currentRoute = body["route"] as? String ?? currentRoute
        case "history": onHistoryChanged?()
        case "notice": error = body["message"] as? String
        default: break
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.shouldPerformDownload { decisionHandler(.download); return }
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if navigationAction.targetFrame?.isMainFrame == true, let session, !session.contains(url), url.scheme != "about", url.scheme != "blob" {
            if navigationAction.navigationType == .linkActivated && ["https","http"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let attachment = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField:"Content-Disposition")?.lowercased().contains("attachment") == true
        decisionHandler(attachment || !navigationResponse.canShowMIMEType ? .download : .allow)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { attach(download) }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { attach(download) }
    private func attach(_ download: WKDownload) {
        let identity = ObjectIdentifier(download)
        let delegate = DesktopDownload(window: webView.window) { [weak self] outcome in
            guard let self else { return }
            self.downloads.removeValue(forKey:identity)
            if case .success(let filename) = outcome { self.onDownload?(filename) }
            if case .failure(let error) = outcome { self.error = error.localizedDescription }
        }
        downloads[identity] = delegate; download.delegate = delegate
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { self.error = "工作区连接中断，请重新打开工作区。" }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        loaded = false
        if runID == nil { busy = false; taskState = "interrupted"; finishedAt = Date() }
        error = "工作区进程已停止。可重新打开恢复本任务；后台验收不会重复提交。浏览器内执行的请求需要重新确认。"
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.message = "选择用于本次模型测试的参考文件"
        if let window = webView.window { panel.beginSheetModal(for:window) { completionHandler($0 == .OK ? panel.urls : nil) } }
        else { panel.begin { completionHandler($0 == .OK ? panel.urls : nil) } }
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["http","https"].contains(url.scheme ?? ""), session?.contains(url) != true {
            NSWorkspace.shared.open(url); return nil
        }
        let preview = WKWebView(frame:.zero, configuration:configuration)
        preview.appearance = NSAppearance(named:.aqua)
        preview.underPageBackgroundColor = .white
        preview.navigationDelegate = self; preview.uiDelegate = self
        let window = NSWindow(contentRect:NSRect(x:0,y:0,width:1000,height:760), styleMask:[.titled,.closable,.miniaturizable,.resizable], backing:.buffered, defer:false)
        window.title = "内容预览"; window.contentView = preview; window.isReleasedWhenClosed = false
        window.center(); window.makeKeyAndOrderFront(nil); previews.removeAll { !$0.isVisible }; previews.append(window)
        return preview
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = "工作台提示"; alert.informativeText = message
        if let window = webView.window { alert.beginSheetModal(for:window) { _ in completionHandler() } }
        else { alert.runModal(); completionHandler() }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = "确认操作"; alert.informativeText = message; alert.addButton(withTitle:"继续"); alert.addButton(withTitle:"取消")
        if let window = webView.window { alert.beginSheetModal(for:window) { completionHandler($0 == .alertFirstButtonReturn) } }
        else { completionHandler(alert.runModal() == .alertFirstButtonReturn) }
    }
}

@MainActor
final class DesktopDownload: NSObject, WKDownloadDelegate {
    weak var window: NSWindow?
    let complete: (Result<String,Error>) -> Void
    private var destination: URL?
    private var temporary: URL?
    init(window: NSWindow?, complete: @escaping (Result<String,Error>) -> Void) { self.window=window; self.complete=complete }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel(); panel.title = "保存测试结果"; panel.canCreateDirectories = true
        panel.nameFieldStringValue = URL(fileURLWithPath:suggestedFilename).lastPathComponent
        panel.directoryURL = FileManager.default.urls(for:.downloadsDirectory,in:.userDomainMask).first
        let finish: (NSApplication.ModalResponse) -> Void = { [weak self] result in
            guard let self, result == .OK, let url = panel.url else { completionHandler(nil); return }
            self.destination = url
            let temporary = url.deletingLastPathComponent().appendingPathComponent(".nebula-download-" + UUID().uuidString)
            self.temporary = temporary
            completionHandler(temporary)
        }
        if let window { panel.beginSheetModal(for:window, completionHandler:finish) } else { panel.begin(completionHandler:finish) }
    }
    func downloadDidFinish(_ download: WKDownload) {
        do {
            guard let temporary, let destination else { throw URLError(.cannotWriteToFile) }
            if FileManager.default.fileExists(atPath:destination.path) {
                _ = try FileManager.default.replaceItemAt(destination, withItemAt:temporary)
            } else { try FileManager.default.moveItem(at:temporary,to:destination) }
            complete(.success(destination.lastPathComponent))
        } catch { if let temporary { try? FileManager.default.removeItem(at:temporary) }; complete(.failure(error)) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let temporary { try? FileManager.default.removeItem(at:temporary) }
        if (error as NSError).code == NSURLErrorCancelled { complete(.success("已取消保存")) }
        else { complete(.failure(error)) }
    }
}

struct WorkspaceView: NSViewRepresentable {
    let workspace: WebWorkspace
    func makeNSView(context: Context) -> WKWebView { workspace.webView }
    func updateNSView(_ view: WKWebView, context: Context) {}
}
