import SwiftUI

struct TaskStrip: View {
    @ObservedObject var model: AppModel
    let task: TestSession
    var body: some View {
        HStack(spacing:16) {
            Menu {
                ForEach(model.tasks.filter { $0.destination == task.destination }) { item in
                    Button("\(item.title) · \(item.channelName) · \(item.statusText)") { model.openTask(item) }
                }
            } label: {
                Label(task.title,systemImage:"square.stack").font(.system(size:12,weight:.medium))
            }.menuStyle(.borderlessButton).fixedSize()
            Text(task.statusText).font(.system(size:10,weight:.medium)).foregroundStyle(task.workspace.busy ? DesktopTheme.accent : .secondary)
                .padding(.horizontal,9).padding(.vertical,5).background(DesktopTheme.surface,in:Capsule())
            if task.workspace.busy {
                Text(task.workspace.progressText).font(.system(size:11)).foregroundStyle(.secondary).lineLimit(1)
            } else {
                Text(task.channelName).font(.system(size:11)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength:0)
            Button { model.createTask(for:task.destination) } label: { Label("新建任务",systemImage:"plus") }.controlSize(.small).help("新开干净页面，当前任务保留（⌘N）")
            Button { model.select(.tasks) } label: { Image(systemName:"square.grid.2x2") }.buttonStyle(.plain).help("全部任务")
        }.padding(.horizontal,28).padding(.vertical,13).background(.white).overlay(alignment:.bottom) { Rectangle().fill(DesktopTheme.line).frame(height:1) }
    }
}

struct TaskCenterView: View {
    @ObservedObject var model: AppModel
    @State private var filter = "all"
    @State private var closing: TestSession?
    private var visible: [TestSession] {
        model.tasks.filter { filter == "all" || (filter == "running" ? $0.workspace.busy : !$0.workspace.busy) }
            .sorted { lhs,rhs in lhs.workspace.busy != rhs.workspace.busy ? lhs.workspace.busy : lhs.number > rhs.number }
    }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:24) {
                HStack(alignment:.top) {
                    VStack(alignment:.leading,spacing:10) {
                        Text("任务中心").font(.system(size:28,weight:.semibold))
                        Text("切换自如，进度始终在这里。").font(.system(size:13)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Menu {
                        ForEach(Destination.allCases.filter(\.isTest)) { item in Button(item.title) { model.createTask(for:item) } }
                    } label: { Label("新建任务",systemImage:"plus") }.menuStyle(.borderedButton).controlSize(.large)
                }
                HStack(spacing:24) {
                    metric("运行中",model.runningTasks.count,"waveform.path")
                    Divider().frame(height:35)
                    metric("当前打开",model.tasks.count,"square.stack")
                    Spacer()
                    VStack(alignment:.trailing,spacing:5) {
                        Text("最多 4 个验收任务并行").font(.system(size:12,weight:.medium))
                        Text("任务在本次应用运行期间保留 · 报告存入测试档案").font(.system(size:10)).foregroundStyle(.secondary)
                    }
                }.padding(22).background(DesktopTheme.surface,in:RoundedRectangle(cornerRadius:20))
                Picker("显示任务",selection:$filter) {
                    Text("全部任务").tag("all"); Text("运行中").tag("running"); Text("待开始 / 已结束").tag("idle")
                }.pickerStyle(.segmented).frame(width:350)
                if visible.isEmpty {
                    VStack(spacing:15) {
                        Image(systemName:"square.stack.3d.up").font(.system(size:36,weight:.light)).foregroundStyle(DesktopTheme.accent)
                        Text(model.tasks.isEmpty ? "从一个新任务开始" : "这里暂时没有任务").font(.system(size:17,weight:.medium))
                        Text("新任务使用独立页面。不同渠道可以同时测试，\n上次的已完成报告可在测试档案中查看。").font(.system(size:12)).foregroundStyle(.secondary).multilineTextAlignment(.center).lineSpacing(5)
                        Button("连接新渠道") { model.showChannel=true }.buttonStyle(.borderedProminent).padding(.top,5)
                    }.frame(maxWidth:.infinity).padding(.vertical,62)
                } else {
                    LazyVStack(spacing:14) {
                        ForEach(visible) { task in card(task) }
                    }
                }
                Text("运行中的任务可以单独停止。退出应用会停止本机测试；视频等上游生成任务仍以渠道状态为准。").font(.system(size:11)).foregroundStyle(.tertiary).padding(.top,4)
            }.padding(32).frame(maxWidth:1150).frame(maxWidth:.infinity)
        }.background(DesktopTheme.canvas)
            .confirmationDialog("关闭这个任务？",isPresented:Binding(get:{closing != nil},set:{if !$0 { closing=nil }}),titleVisibility:.visible) {
                Button("关闭任务") { if let task=closing { model.closeTask(task) }; closing=nil }
            } message: { Text("已保存的测试档案会保留。此页面的未保存配置将被关闭。") }
    }
    private func metric(_ label:String,_ count:Int,_ icon:String) -> some View {
        HStack(spacing:12) {
            Image(systemName:icon).font(.system(size:21,weight:.light)).foregroundStyle(DesktopTheme.accent)
            VStack(alignment:.leading,spacing:4) { Text("\(count)").font(.system(size:26,weight:.semibold,design:.rounded)); Text(label).font(.system(size:11)).foregroundStyle(.secondary) }
        }
    }
    private func card(_ task:TestSession) -> some View {
        VStack(alignment:.leading,spacing:17) {
            HStack(spacing:14) {
                Image(systemName:task.destination.icon).font(.system(size:19)).foregroundStyle(DesktopTheme.accent).frame(width:42,height:42).background(DesktopTheme.accent.opacity(0.07),in:RoundedRectangle(cornerRadius:13))
                VStack(alignment:.leading,spacing:5) {
                    Text(task.title).font(.system(size:14,weight:.semibold))
                    Text([task.channelName,task.workspace.channelHost].filter{!$0.isEmpty}.joined(separator:" · ")).font(.system(size:11)).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Text(task.statusText).font(.system(size:11,weight:.medium)).foregroundStyle(task.workspace.busy ? DesktopTheme.accent : .secondary)
                    .padding(.horizontal,11).padding(.vertical,6).background(DesktopTheme.surface,in:Capsule())
                Button(task.workspace.busy ? "查看进度" : "打开任务") { model.openTask(task) }.controlSize(.small)
                if task.workspace.busy {
                    Button("停止") { task.workspace.stopTests() }.controlSize(.small)
                } else {
                    Button { closing=task } label: { Image(systemName:"xmark") }.buttonStyle(.plain).foregroundStyle(.tertiary).help("关闭任务，保留已保存档案")
                }
            }
            HStack {
                Text(task.workspace.modelName.isEmpty ? "尚未选择模型" : task.workspace.modelName).font(.system(size:12,weight:.medium)).lineLimit(1)
                Spacer()
                if let start=task.workspace.startedAt {
                    TimelineView(.periodic(from:.now,by:1)) { context in
                        let seconds = max(0,Int((task.workspace.finishedAt ?? context.date).timeIntervalSince(start)))
                        Text("\(seconds / 60) 分 \(seconds % 60) 秒").monospacedDigit().font(.system(size:11)).foregroundStyle(.secondary)
                    }
                }
            }
            if let fraction=task.workspace.fraction { ProgressView(value:fraction).tint(DesktopTheme.accent) }
            HStack(spacing:8) {
                if task.workspace.busy { ProgressView().controlSize(.mini) }
                Text(task.workspace.progressText).font(.system(size:11)).foregroundStyle(.secondary).lineLimit(2)
            }
        }.padding(22).background(.white,in:RoundedRectangle(cornerRadius:20)).overlay(RoundedRectangle(cornerRadius:20).stroke(DesktopTheme.line))
    }
}
