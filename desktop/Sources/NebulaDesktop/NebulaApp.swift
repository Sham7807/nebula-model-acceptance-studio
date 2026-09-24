import SwiftUI
import AppKit

@main
struct NebulaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = AppModel()
    var body: some Scene {
        Window("小小宇宙无敌", id:"main") {
            MainWindow(model:model)
                .preferredColorScheme(model.colorScheme)
                .onAppear { delegate.model = model; model.start() }
        }
        .defaultSize(width:1280,height:850)
        .windowStyle(.titleBar)
        .commands {
            CommandGroup(replacing:.newItem) {}
            CommandGroup(after:.appInfo) {
                Button("渠道连接…") { model.showChannel = true }.keyboardShortcut("k")
                Divider()
            }
            CommandMenu("工作台") {
                ForEach(Destination.allCases) { item in Button(item.title) { model.select(item) } }
                Divider()
                Button("重新打开工作区") { model.workspace.refresh() }.keyboardShortcut("r").disabled(model.workspace.busy)
                Button("停止当前测试") { model.workspace.stopTests() }.disabled(!model.workspace.busy)
            }
        }
        Settings { SettingsView(model:model).preferredColorScheme(model.colorScheme) }
    }
}

struct MainWindow: View {
    @ObservedObject var model: AppModel
    var body: some View {
        NavigationSplitView {
            VStack(spacing:0) {
                HStack(spacing:11) {
                    ZStack { RoundedRectangle(cornerRadius:11).fill(.blue.gradient).frame(width:38,height:38); Image(systemName:"sparkles").foregroundStyle(.white).font(.system(size:21,weight:.medium)) }
                    VStack(alignment:.leading,spacing:3) { Text("小小宇宙无敌").font(.system(size:14,weight:.semibold)); Text("NEBULA STUDIO").font(.system(size:9,weight:.medium,design:.rounded)).tracking(1.8).foregroundStyle(.secondary) }
                    Spacer(minLength:0)
                }.padding(.horizontal,18).padding(.top,20).padding(.bottom,22)
                List(selection:$model.destination) {
                    section(nil,[.overview,.history])
                    section("多模态工作区",[.text,.image,.video,.audio])
                    section("专业验收",[.general,.ccmax,.claude,.kimi,.gpt])
                }.listStyle(.sidebar)
                Spacer(minLength:0)
                VStack(alignment:.leading,spacing:10) {
                    HStack(spacing:7) {
                        Circle().fill(model.engine.isReady ? .green : .orange).frame(width:6,height:6)
                        Text(engineLabel).font(.system(size:11))
                        Spacer()
                        if model.workspace.busy { ProgressView().controlSize(.mini) }
                    }.foregroundStyle(.secondary)
                    Text("历史记录保存在这台 Mac").font(.system(size:10)).foregroundStyle(.tertiary)
                }.padding(18)
            }.navigationSplitViewColumnWidth(min:215,ideal:225,max:260)
        } detail: {
            VStack(spacing:0) {
                if case .failed(let reason) = model.engine.state {
                    HStack { Image(systemName:"exclamationmark.triangle").foregroundStyle(.orange); Text(reason).font(.callout); Spacer(); Button("重新启动") { model.restart() } }.padding(12).background(.orange.opacity(0.08))
                }
                if let message = model.notice ?? model.workspace.error {
                    HStack { Image(systemName:"info.circle"); Text(message).font(.callout); Spacer(); Button { model.notice=nil; model.workspace.error=nil } label:{Image(systemName:"xmark")}.buttonStyle(.plain) }
                        .padding(12).background(.blue.opacity(0.08))
                }
                ZStack {
                    WorkspaceView(workspace:model.workspace)
                        .opacity(model.selected == .overview || !model.engine.isReady ? 0 : 1)
                        .allowsHitTesting(model.selected != .overview && model.engine.isReady)
                        .accessibilityHidden(model.selected == .overview || !model.engine.isReady)
                    if model.selected == .overview { OverviewView(model:model) }
                    else if !model.engine.isReady || !model.workspace.loaded { EnginePlaceholder(model:model) }
                }
            }.background(Color(nsColor:.windowBackgroundColor))
            .navigationTitle(model.selected.title)
            .navigationSubtitle(model.selected.subtitle)
            .toolbar {
                ToolbarItemGroup(placement:.primaryAction) {
                    if model.workspace.busy { Label("测试进行中",systemImage:"waveform.path").font(.callout).foregroundStyle(.secondary) }
                    Button { model.showChannel=true } label:{Label(model.channel.base.isEmpty ? "连接渠道" : model.channel.name,systemImage:"link")}
                        .disabled(model.workspace.busy).help("配置默认渠道地址和密钥（⌘K）")
                    Button { model.select(.history) } label:{Image(systemName:"clock.arrow.circlepath")}.help("测试档案")
                }
            }
        }.frame(minWidth:1020,minHeight:680)
        .onChange(of:model.destination) { _,_ in model.navigate() }
        .sheet(isPresented:$model.showChannel) { ChannelSheet(model:model) }
    }
    private var engineLabel:String {
        if case .failed = model.engine.state { return "本地引擎需要重启" }
        return model.engine.isReady ? "本地引擎就绪" : "本地引擎连接中"
    }
    @ViewBuilder func section(_ title:String?,_ items:[Destination]) -> some View {
        Section {
            ForEach(items) { item in Label { Text(item.title).font(.system(size:13)) } icon:{ Image(systemName:item.icon).foregroundColor(model.selected == item ? nil : item.tint).frame(width:20) }.padding(.vertical,4).tag(item) }
        } header: { if let title { Text(title).font(.system(size:10,weight:.medium)).padding(.top,12) } }
    }
}

struct EnginePlaceholder: View {
    @ObservedObject var model:AppModel
    var body:some View {
        VStack(spacing:16) {
            if case .failed(let reason) = model.engine.state {
                Image(systemName:"exclamationmark.arrow.triangle.2.circlepath").font(.system(size:36)).foregroundStyle(.orange)
                Text("本地引擎需要重新启动").font(.title3.bold())
                Text(reason).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth:400)
                Button("重新启动") { model.restart() }.buttonStyle(.borderedProminent)
            } else if let error = model.workspace.error {
                Image(systemName:"rectangle.slash").font(.largeTitle).foregroundStyle(.secondary)
                Text(error).frame(maxWidth:400)
                Button("重新打开工作区") { model.workspace.refresh() }
            } else { ProgressView(); Text("正在准备检测工作区…").foregroundStyle(.secondary) }
        }.frame(maxWidth:.infinity,maxHeight:.infinity).background(Color(nsColor:.windowBackgroundColor))
    }
}
