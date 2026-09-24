import SwiftUI
import AppKit

struct ChannelSheet:View {
    @ObservedObject var model:AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var draft=ChannelProfile()
    @State private var key=""
    @State private var error:String?
    @State private var route:Destination = .text
    var body:some View {
        VStack(alignment:.leading,spacing:22) {
            HStack(spacing:13) { Image(systemName:"link.circle.fill").font(.system(size:40)).foregroundStyle(.blue); VStack(alignment:.leading,spacing:5) { Text("为新渠道创建任务").font(.title2.weight(.semibold)); Text("独立保存连接，已有任务继续运行。").font(.callout).foregroundStyle(.secondary) } }
            Form {
                Picker("测试工作区",selection:$route) { ForEach(Destination.allCases.filter(\.isTest)) { item in Text(item.title).tag(item) } }
                TextField("连接名称",text:$draft.name)
                TextField("Base URL",text:$draft.base,prompt:Text("https://api.example.com/v1"))
                SecureField("API Key",text:$key,prompt:Text("sk-…"))
                TextField("默认模型",text:$draft.model,prompt:Text("可留空，在工作区获取模型列表"))
                Toggle("将密钥保存在 macOS 钥匙串",isOn:$draft.rememberKey)
            }.textFieldStyle(.roundedBorder)
            Text("不记住密钥时，退出应用即清除。历史记录与导出的报告不会保存渠道密钥。配置保存不会发送测试请求。").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
            if let error { Text(error).foregroundStyle(.red).font(.callout) }
            HStack { Spacer(); Button("取消",role:.cancel) { dismiss() }.keyboardShortcut(.cancelAction); Button("创建任务") { do { try model.saveChannel(draft,key:key,route:route); dismiss() } catch { self.error=error.localizedDescription } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction) }
        }.padding(30).frame(width:510).background(.white).tint(DesktopTheme.accent).onAppear { draft=model.channel;key=model.apiKey;route=model.selected.isTest ? model.selected : .text }
    }
}
struct SettingsView:View {
    @ObservedObject var model:AppModel
    var body:some View {
        Form {
            Section("外观") { LabeledContent("统一外观",value:"亮白 · 原生蓝"); Text("所有工作区保持一致的明亮界面。动效遵循 macOS 的减少动态效果设置。").font(.caption).foregroundStyle(.secondary) }
            Section("本地数据") {
                LabeledContent("存储位置") { Button("在 Finder 中显示") { NSWorkspace.shared.open(model.engine.dataDirectory) } }
                Text("测试历史与报告保存在这台 Mac。桌面版的数据与服务器网页版分开保存。").font(.caption).foregroundStyle(.secondary)
            }
            Section("检测引擎") {
                LabeledContent("运行状态",value:model.engine.isReady ? "已连接 · 仅本机访问" : "正在连接 / 已停止")
                HStack { Button("重新启动引擎") { model.restart() }.disabled(model.hasRunningTasks); Text("所有任务结束后可重启").foregroundStyle(.secondary).font(.caption) }
            }
            Section { Text("\(AppVersion.name) · \(AppVersion.current)\nmacOS 原生窗口 · 本地检测引擎").foregroundStyle(.secondary).font(.caption) }
        }.formStyle(.grouped).scrollContentBackground(.hidden).padding(12).frame(width:540,height:450).background(DesktopTheme.canvas).tint(DesktopTheme.accent)
    }
}
