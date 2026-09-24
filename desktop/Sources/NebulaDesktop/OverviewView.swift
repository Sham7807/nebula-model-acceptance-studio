import SwiftUI

struct OverviewView: View {
    @ObservedObject var model: AppModel
    private let columns = Array(repeating:GridItem(.flexible(),spacing:14),count:4)
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:28) {
                HStack(alignment:.center,spacing:28) {
                    VStack(alignment:.leading,spacing:12) {
                        Text("你的模型验收工作台").font(.system(size:11,weight:.medium)).foregroundStyle(.secondary)
                        Text("每个渠道，清晰可见。").font(.system(size:30,weight:.semibold)).tracking(-0.8)
                        Text("多模态测试、专业验收与成本核对。\n让每一份结论，都有请求证据可循。").font(.system(size:13)).foregroundStyle(.secondary).lineSpacing(5)
                        Button { model.channel.base.isEmpty ? (model.showChannel=true) : model.select(.text) } label: {
                            Label(model.channel.base.isEmpty ? "连接渠道" : "开始测试",systemImage:"plus")
                                .font(.system(size:12,weight:.medium)).padding(.horizontal,8).padding(.vertical,3)
                        }.buttonStyle(.borderedProminent).controlSize(.large).tint(DesktopTheme.accent).padding(.top,5)
                    }
                    Spacer(minLength:16)
                    ZStack {
                        RoundedRectangle(cornerRadius:36,style:.continuous).fill(DesktopTheme.surface).frame(width:190,height:166)
                        RoundedRectangle(cornerRadius:26,style:.continuous).fill(.white).frame(width:112,height:112)
                            .shadow(color:DesktopTheme.accent.opacity(0.09),radius:18,y:8)
                            .overlay(RoundedRectangle(cornerRadius:26,style:.continuous).stroke(DesktopTheme.line))
                        Image(systemName:"point.3.connected.trianglepath.dotted").font(.system(size:49,weight:.light)).foregroundStyle(DesktopTheme.accent)
                        Image(systemName:"checkmark").font(.system(size:12,weight:.semibold)).foregroundStyle(DesktopTheme.accent)
                            .frame(width:32,height:32).background(.white,in:Circle()).overlay(Circle().stroke(DesktopTheme.line)).offset(x:59,y:49)
                    }.accessibilityHidden(true)
                }.padding(.top,10).padding(.bottom,2)
                HStack(spacing:0) {
                    stat("全部测试",model.stats.total,"square.stack.3d.up")
                    Divider().frame(height:34)
                    stat("通过 / 成功",model.stats.passed,"checkmark.circle")
                    Divider().frame(height:34)
                    stat("需要关注",model.stats.failed,"exclamationmark.circle")
                }.padding(.vertical,22).background(DesktopTheme.surface,in:RoundedRectangle(cornerRadius:20,style:.continuous))
                VStack(alignment:.leading,spacing:14) {
                    sectionTitle("多模态工作区",subtitle:"从输入到输出，直接验证")
                    LazyVGrid(columns:columns,spacing:14) {
                        ForEach([Destination.text,.image,.video,.audio]) { item in
                            Button { model.select(item) } label: {
                                VStack(alignment:.leading,spacing:19) {
                                    HStack {
                                        Image(systemName:item.icon).font(.system(size:20,weight:.regular)).foregroundStyle(DesktopTheme.accent)
                                            .frame(width:38,height:38).background(DesktopTheme.accent.opacity(0.07),in:RoundedRectangle(cornerRadius:12,style:.continuous))
                                        Spacer(); Image(systemName:"arrow.up.right").font(.system(size:10,weight:.medium)).foregroundStyle(.tertiary)
                                    }
                                    VStack(alignment:.leading,spacing:6) {
                                        Text(item.title).font(.system(size:13,weight:.semibold)).foregroundStyle(.primary)
                                        Text(cardSubtitle(item)).font(.system(size:10)).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                }.padding(18).frame(maxWidth:.infinity,alignment:.leading).contentShape(RoundedRectangle(cornerRadius:20))
                            }.buttonStyle(DesktopCardButtonStyle())
                        }
                    }
                }
                VStack(alignment:.leading,spacing:14) {
                    sectionTitle("专业验收",subtitle:"统一标准，保留各模型的测试重点")
                    HStack(spacing:10) {
                        ForEach([Destination.general,.ccmax,.claude,.kimi,.gpt]) { item in
                            Button { model.select(item) } label: {
                                VStack(alignment:.leading,spacing:11) {
                                    Image(systemName:item.icon).font(.system(size:17)).foregroundStyle(DesktopTheme.accent)
                                    Text(item.title).font(.system(size:11,weight:.medium)).foregroundStyle(.primary).lineLimit(1)
                                }.padding(16).frame(maxWidth:.infinity,alignment:.leading).contentShape(RoundedRectangle(cornerRadius:20))
                            }.buttonStyle(DesktopCardButtonStyle())
                        }
                    }
                }
                VStack(alignment:.leading,spacing:14) {
                    HStack { Text("最近的测试").font(.system(size:15,weight:.semibold)); Spacer(); Button("查看全部") { model.select(.history) }.buttonStyle(.link).font(.system(size:12)) }
                    VStack(spacing:0) {
                        if model.history.isEmpty {
                            HStack(spacing:16) {
                                Image(systemName:"doc.text.magnifyingglass").font(.system(size:27,weight:.light)).foregroundStyle(DesktopTheme.accent.opacity(0.6))
                                VStack(alignment:.leading,spacing:6) {
                                    Text(model.historyError == nil ? "从第一份测试档案开始" : "档案暂时无法读取").font(.system(size:13,weight:.medium))
                                    Text(model.historyError ?? "完成测试后，结果与报告会自动保存在这台 Mac。").font(.system(size:11)).foregroundStyle(.secondary)
                                }; Spacer()
                            }.padding(24)
                        } else {
                            ForEach(model.history) { item in
                                Button { model.openHistory(item) } label: {
                                    HStack(spacing:13) {
                                        Image(systemName:item.destination.icon).foregroundStyle(DesktopTheme.accent).frame(width:28)
                                        VStack(alignment:.leading,spacing:4) { Text(item.displayTitle).font(.system(size:12,weight:.medium)); Text(item.title).font(.system(size:10)).foregroundStyle(.secondary).lineLimit(1) }
                                        Spacer()
                                        Text(item.statusText).font(.system(size:10)).padding(.horizontal,9).padding(.vertical,5).background(DesktopTheme.surface,in:Capsule())
                                        Text(Date(timeIntervalSince1970:item.created_at),format:.dateTime.month().day().hour().minute()).font(.system(size:10)).foregroundStyle(.secondary).frame(width:102,alignment:.trailing)
                                    }.padding(16).contentShape(Rectangle())
                                }.buttonStyle(.plain)
                                if item.id != model.history.last?.id { Divider().padding(.leading,55) }
                            }
                        }
                    }.background(.white,in:RoundedRectangle(cornerRadius:20,style:.continuous)).overlay(RoundedRectangle(cornerRadius:20,style:.continuous).stroke(DesktopTheme.line))
                }
                HStack { Label("本机存储 · 钥匙串保护",systemImage:"lock.shield"); Spacer(); Text("渠道测试系统 / \(AppVersion.current)") }.font(.system(size:10)).foregroundStyle(.tertiary).padding(.bottom,8)
            }.padding(.horizontal,34).padding(.vertical,26).frame(maxWidth:1200).frame(maxWidth:.infinity)
        }.background(DesktopTheme.canvas)
    }
    private func sectionTitle(_ title:String,subtitle:String)->some View {
        HStack(alignment:.firstTextBaseline) { Text(title).font(.system(size:15,weight:.semibold)); Spacer(); Text(subtitle).font(.system(size:10)).foregroundStyle(.tertiary) }
    }
    private func cardSubtitle(_ item:Destination)->String { switch item { case .text:return "对话 · 视觉理解"; case .image:return "生成 · 编辑 · 多图参照";case .video:return "异步生成 · 视频预览";default:return "合成 · 转写 · 播放" } }
    private func stat(_ name:String,_ value:Int,_ icon:String)->some View {
        HStack(spacing:13) { Image(systemName:icon).font(.system(size:19,weight:.light)).foregroundStyle(DesktopTheme.accent.opacity(0.8)); VStack(alignment:.leading,spacing:5) { Text(value.formatted()).font(.system(size:25,weight:.semibold,design:.rounded)); Text(name).font(.system(size:11)).foregroundStyle(.secondary) }; Spacer() }.padding(.horizontal,24).frame(maxWidth:.infinity)
    }
}
