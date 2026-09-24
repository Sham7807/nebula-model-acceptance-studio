import SwiftUI
import AppKit

struct ChannelSheet:View {
    @ObservedObject var model:AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var draft=ChannelProfile()
    @State private var key=""
    @State private var error:String?
    var body:some View {
        VStack(alignment:.leading,spacing:22) {
            HStack(spacing:13) { Image(systemName:"link.circle.fill").font(.system(size:40)).foregroundStyle(.blue); VStack(alignment:.leading,spacing:5) { Text("连接你的渠道").font(.title2.weight(.semibold)); Text("统一填入各工作区，仍可在测试时单独调整。").font(.callout).foregroundStyle(.secondary) } }
            Form {
                TextField("连接名称",text:$draft.name)
                TextField("Base URL",text:$draft.base,prompt:Text("https://api.example.com/v1"))
                SecureField("API Key",text:$key,prompt:Text("sk-…"))
                TextField("默认模型",text:$draft.model,prompt:Text("可留空，在工作区获取模型列表"))
                Toggle("将密钥保存在 macOS 钥匙串",isOn:$draft.rememberKey)
            }.textFieldStyle(.roundedBorder)
            Text("不记住密钥时，退出应用即清除。历史记录与导出的报告不会保存渠道密钥。配置保存不会发送测试请求。").font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
            if let error { Text(error).foregroundStyle(.red).font(.callout) }
            HStack { Spacer(); Button("取消",role:.cancel) { dismiss() }.keyboardShortcut(.cancelAction); Button("保存连接") { do { try model.saveChannel(draft,key:key); dismiss() } catch { self.error=error.localizedDescription } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(model.workspace.busy) }
        }.padding(28).frame(width:500).onAppear { draft=model.channel;key=model.apiKey }
    }
}
struct SettingsView:View {
    @ObservedObject var model:AppModel
    var body:some View {
        Form {
            Section("外观") { Picker("应用外观",selection:model.$appearance) { Text("跟随系统").tag("system");Text("浅色").tag("light");Text("深色").tag("dark") }.pickerStyle(.segmented) }
            Section("本地数据") {
                LabeledContent("存储位置") { Button("在 Finder 中显示") { NSWorkspace.shared.open(model.engine.dataDirectory) } }
                Text("测试历史与报告保存在这台 Mac。桌面版的数据与服务器网页版分开保存。").font(.caption).foregroundStyle(.secondary)
            }
            Section("检测引擎") {
                LabeledContent("运行状态",value:model.engine.isReady ? "已连接 · 仅本机访问" : "正在连接 / 已停止")
                HStack { Button("重新启动引擎") { model.restart() }.disabled(model.workspace.busy); Text("不会清除已保存记录").foregroundStyle(.secondary).font(.caption) }
            }
            Section { Text("小小宇宙无敌 · Nebula Studio \(AppVersion.current)\nmacOS 原生窗口 · 本地检测引擎").foregroundStyle(.secondary).font(.caption) }
        }.formStyle(.grouped).padding(10).frame(width:520,height:410)
    }
}
