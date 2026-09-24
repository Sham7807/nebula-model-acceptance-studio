import SwiftUI
import AppKit

@main
struct NebulaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = AppModel()
    var body: some Scene {
        Window(AppVersion.name, id:"main") {
            MainWindow(model:model)
                .preferredColorScheme(model.colorScheme)
                .onAppear { delegate.model = model; model.start() }
        }
        .defaultSize(width:1280,height:850)
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing:.newItem) {
                Button("新建测试任务") { model.createTask() }.keyboardShortcut("n")
            }
            CommandGroup(after:.appInfo) {
                Button("渠道连接…") { model.showChannel = true }.keyboardShortcut("k")
                Divider()
            }
            CommandMenu("工作台") {
                ForEach(Destination.allCases) { item in Button(item.title) { model.select(item) } }
                Divider()
                Button("重新打开工作区") { model.workspace.refresh() }.keyboardShortcut("r").disabled(model.workspace.busy && model.workspace.loaded)
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
                    ZStack { RoundedRectangle(cornerRadius:12,style:.continuous).fill(DesktopTheme.accent.opacity(0.08)).frame(width:38,height:38); Image(systemName:"point.3.connected.trianglepath.dotted").foregroundStyle(DesktopTheme.accent).font(.system(size:21,weight:.medium)) }
                    VStack(alignment:.leading,spacing:4) { Text(AppVersion.name).font(.system(size:14,weight:.semibold)); Text("CHANNEL TEST SYSTEM").font(.system(size:8,weight:.medium)).tracking(1.1).foregroundStyle(.secondary) }
                    Spacer(minLength:0)
                }.padding(.horizontal,18).padding(.top,20).padding(.bottom,22)
                List(selection:$model.destination) {
                    section(nil,[.overview,.tasks,.history])
                    section("多模态工作区",[.text,.image,.video,.audio])
                    section("专业验收",[.general,.ccmax,.claude,.kimi,.gpt])
                }.listStyle(.sidebar).scrollContentBackground(.hidden)
                Spacer(minLength:0)
                VStack(alignment:.leading,spacing:10) {
                    HStack(spacing:7) {
                        Circle().fill(model.engine.isReady ? .green : .orange).frame(width:6,height:6)
                        Text(engineLabel).font(.system(size:11))
                        Spacer()
                        if model.hasRunningTasks { Text("\(model.runningTasks.count) 个运行中").font(.system(size:10)).foregroundStyle(DesktopTheme.accent) }
                    }.foregroundStyle(.secondary)
                    Text("历史记录保存在这台 Mac").font(.system(size:10)).foregroundStyle(.tertiary)
                }.padding(18)
            }.background(DesktopTheme.sidebar).navigationSplitViewColumnWidth(min:215,ideal:235,max:270)
        } detail: {
            VStack(spacing:0) {
                if case .failed(let reason) = model.engine.state {
                    HStack { Image(systemName:"exclamationmark.triangle").foregroundStyle(.orange); Text(reason).font(.callout); Spacer(); Button("重新启动") { model.restart() } }.padding(12).background(.orange.opacity(0.08))
                }
                if let message = model.notice ?? model.workspace.error {
                    HStack { Image(systemName:"info.circle"); Text(message).font(.callout); Spacer(); Button { model.notice=nil; model.workspace.error=nil } label:{Image(systemName:"xmark")}.buttonStyle(.plain) }
                        .padding(12).background(.blue.opacity(0.08))
                }
                if let task = model.activeTask, model.selected.isTest { TaskStrip(model:model,task:task) }
                ZStack {
                    WorkspaceView(workspace:model.archiveWorkspace)
                        .opacity(model.selected == .history && model.engine.isReady ? 1 : 0)
                        .allowsHitTesting(model.selected == .history && model.engine.isReady)
                        .accessibilityHidden(model.selected != .history || !model.engine.isReady)
                    // Keep every web view mounted: switching the visible task must
                    // not destroy requests, inputs, files, scroll position or previews.
                    ForEach(model.tasks) { task in
                        let visible = model.activeTask?.id == task.id && model.selected.isTest && model.engine.isReady
                        WorkspaceView(workspace:task.workspace)
                            .opacity(visible ? 1 : 0).allowsHitTesting(visible).accessibilityHidden(!visible).zIndex(visible ? 1 : 0)
                    }
                    if model.selected == .overview { OverviewView(model:model).zIndex(2) }
                    else if model.selected == .tasks { TaskCenterView(model:model).zIndex(2) }
                    else if !model.engine.isReady || !model.workspace.loaded { EnginePlaceholder(model:model).zIndex(2) }
                }
            }.background(DesktopTheme.canvas)
            .navigationTitle(model.selected.title)
            .navigationSubtitle(model.selected.subtitle)
            .toolbar {
                ToolbarItemGroup(placement:.primaryAction) {
                    if model.hasRunningTasks { Button { model.select(.tasks) } label:{Label("\(model.runningTasks.count) 个任务运行中",systemImage:"waveform.path")}.help("查看所有后台任务") }
                    Button { model.showChannel=true } label:{Label("新渠道任务",systemImage:"plus.circle")}
                        .help("配置另一渠道并新建独立任务（⌘K）")
                    Button { model.select(.history) } label:{Image(systemName:"clock.arrow.circlepath")}.help("测试档案")
                }
            }
        }.tint(DesktopTheme.accent).frame(minWidth:1020,minHeight:680)
        .onChange(of:model.destination) { _,_ in model.navigate() }
        .sheet(isPresented:$model.showChannel) { ChannelSheet(model:model) }
    }
    private var engineLabel:String {
        if case .failed = model.engine.state { return "本地引擎需要重启" }
        return model.engine.isReady ? "本地引擎就绪" : "本地引擎连接中"
    }
    @ViewBuilder func section(_ title:String?,_ items:[Destination]) -> some View {
        Section {
            ForEach(items) { item in
                HStack {
                    Label { Text(item.title).font(.system(size:13)) } icon:{ Image(systemName:item.icon).foregroundColor(model.selected == item ? nil : item.tint).frame(width:20) }
                    Spacer()
                    let count = item == .tasks ? model.runningTasks.count : model.runningTasks.filter { $0.destination == item }.count
                    if count > 0 { Text("\(count)").font(.system(size:10,weight:.semibold)).padding(.horizontal,6).padding(.vertical,2).background(DesktopTheme.accent.opacity(0.10),in:Capsule()).accessibilityLabel("\(count) 个任务运行中") }
                }.padding(.vertical,4).tag(item)
            }
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
        }.frame(maxWidth:.infinity,maxHeight:.infinity).background(DesktopTheme.canvas)
    }
}
