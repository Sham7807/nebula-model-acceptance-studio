import Foundation
import SwiftUI
import Security

enum AppVersion {
    static var current: String { Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "开发版" }
}

enum Destination: String, CaseIterable, Identifiable {
    case overview, text, image, video, audio, general, ccmax, claude, kimi, gpt, history
    var id: String { rawValue }
    var title: String {
        switch self {
        case .overview: return "概览"
        case .text: return "文本对话"
        case .image: return "图像创作"
        case .video: return "视频生成"
        case .audio: return "音频与语音"
        case .general: return "通用检测"
        case .ccmax: return "CCMax 验收"
        case .claude: return "Claude 专项"
        case .kimi: return "Kimi · KVV"
        case .gpt: return "GPT 生成专项"
        case .history: return "测试档案"
        }
    }
    var icon: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .text: return "text.bubble"
        case .image: return "photo.on.rectangle.angled"
        case .video: return "play.rectangle"
        case .audio: return "waveform"
        case .general: return "slider.horizontal.3"
        case .ccmax: return "checkmark.shield"
        case .claude: return "sparkle"
        case .kimi: return "moon.stars"
        case .gpt: return "curlybraces"
        case .history: return "clock.arrow.circlepath"
        }
    }
    var subtitle: String {
        switch self {
        case .overview: return "每一次验证，都有据可循。"
        case .text: return "对话、流式输出与多模态理解"
        case .image: return "图像生成、图生图与多图参照"
        case .video: return "生成视频，追踪进度，直接预览"
        case .audio: return "语音合成、音频转写与播放"
        case .general: return "四种协议，多场景参数与能力验证"
        case .ccmax: return "流式、工具、注入与渠道一致性"
        case .claude: return "缓存、签名、透传与受控压测"
        case .kimi: return "官方预检、全套验证与参数矩阵"
        case .gpt: return "HTML / SVG 生成质量与 Token 账本"
        case .history: return "留存结果、请求证据与完整报告"
        }
    }
    var tint: Color {
        switch self {
        case .image: return .pink
        case .video: return .purple
        case .audio: return .orange
        case .claude: return .orange
        case .kimi: return .indigo
        default: return .blue
        }
    }
}

struct ChannelProfile: Codable, Equatable {
    var name = "我的渠道"
    var base = ""
    var model = ""
    var rememberKey = false
    static func validate(base: String) -> Bool {
        guard let url = URLComponents(string: base.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
              let host = url.host, !host.isEmpty, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil else { return false }
        return !base.contains(where: { $0.isNewline || $0 == "\\" || $0 == " " })
    }
}

struct EngineSession: Decodable {
    let type: String
    let url: URL
    let cookieName: String
    let cookie: String
    let csrf: String
    let pid: Int
    var cookieHeader: String { "\(cookieName)=\(cookie)" }
    func contains(_ url: URL) -> Bool {
        url.scheme == self.url.scheme && url.host == self.url.host && url.port == self.url.port
    }
}

struct HistoryStats: Decodable {
    var total = 0
    var passed = 0
    var failed = 0
    var other = 0
}
struct HistoryItem: Decodable, Identifiable {
    let id: String
    let kind: String
    let title: String
    let model: String
    let status: String
    let created_at: Double
    var displayTitle: String { model.isEmpty ? title : model }
    var destination: Destination { Destination(rawValue: kind == "kimi" ? "kimi" : kind) ?? .general }
    var statusText: String {
        ["passed":"通过", "failed":"未通过", "inconclusive":"待复核", "cancelled":"已取消", "error":"请求异常", "pending":"进行中"][status] ?? status
    }
}
struct HistoryPage: Decodable { let items: [HistoryItem]; let stats: HistoryStats }

enum Keychain {
    private static let service = "com.nebula.workbench.channel"
    private static let account = "primary-api-key"
    private static var query: [String: Any] {
        [kSecClass as String:kSecClassGenericPassword, kSecAttrService as String:service, kSecAttrAccount as String:account]
    }
    static func read() throws -> String {
        var q = query; q[kSecReturnData as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let code = SecItemCopyMatching(q as CFDictionary, &item)
        if code == errSecItemNotFound { return "" }
        guard code == errSecSuccess, let data = item as? Data else { throw keychainError(code) }
        return String(data: data, encoding: .utf8) ?? ""
    }
    static func save(_ value: String) throws {
        if value.isEmpty { try remove(); return }
        let data = Data(value.utf8)
        let update = SecItemUpdate(query as CFDictionary, [kSecValueData as String:data] as CFDictionary)
        if update == errSecItemNotFound {
            var q = query; q[kSecValueData as String] = data
            q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            q[kSecAttrLabel as String] = "小小宇宙 · 渠道 API Key"
            let code = SecItemAdd(q as CFDictionary, nil)
            guard code == errSecSuccess else { throw keychainError(code) }
        } else if update != errSecSuccess { throw keychainError(update) }
    }
    static func remove() throws {
        let code = SecItemDelete(query as CFDictionary)
        guard code == errSecSuccess || code == errSecItemNotFound else { throw keychainError(code) }
    }
    private static func keychainError(_ code: OSStatus) -> NSError {
        NSError(domain: "NebulaKeychain", code: Int(code), userInfo: [NSLocalizedDescriptionKey:"无法访问 macOS 钥匙串。请解锁钥匙串，或取消记住密钥后再试。"])
    }
}
