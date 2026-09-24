import SwiftUI

struct OverviewView:View {
    @ObservedObject var model:AppModel
    private let columns=[GridItem(.flexible()),GridItem(.flexible()),GridItem(.flexible()),GridItem(.flexible())]
    var body:some View {
        ScrollView {
            VStack(alignment:.leading,spacing:26) {
                HStack {
                    VStack(alignment:.leading,spacing:12) {
                        Text("YOUR MODELS. YOUR EVIDENCE.").font(.system(size:10,weight:.semibold)).tracking(2).foregroundStyle(.secondary)
                        Text("看见模型的真实能力。").font(.system(size:30,weight:.semibold))
                        Text("从多模态创作到专业验收，\n把每一次请求，变成有据可循的判断。").font(.system(size:13)).foregroundStyle(.secondary).lineSpacing(5)
                        Button { model.channel.base.isEmpty ? (model.showChannel=true) : model.select(.text) } label:{ HStack(spacing:8){Text(model.channel.base.isEmpty ? "连接第一个渠道" : "开始一次测试"); Image(systemName:"arrow.right")} }.buttonStyle(.borderedProminent).controlSize(.large).padding(.top,6)
                    }
                    Spacer()
                    ZStack {
                        Circle().fill(.blue.opacity(0.08)).frame(width:190,height:190)
                        Circle().stroke(.blue.opacity(0.13),lineWidth:1).frame(width:164,height:164)
                        Circle().fill(LinearGradient(colors:[Color(red:0.61,green:0.76,blue:1),.blue,.indigo],startPoint:.topLeading,endPoint:.bottomTrailing)).frame(width:118,height:118).shadow(color:.blue.opacity(0.2),radius:20,y:10)
                        Image(systemName:"sparkles").font(.system(size:45,weight:.ultraLight)).foregroundStyle(.white.opacity(0.94))
                        Circle().fill(.indigo.opacity(0.8)).frame(width:11,height:11).offset(x:76,y:-28)
                        Circle().fill(.blue.opacity(0.45)).frame(width:7,height:7).offset(x:-70,y:46)
                    }.accessibilityHidden(true).padding(.trailing,20)
                }.padding(.top,12).padding(.bottom,2)
                HStack(spacing:0) {
                    stat("全部测试",model.stats.total,"square.stack.3d.up")
                    Divider().frame(height:32)
                    stat("通过 / 成功",model.stats.passed,"checkmark.circle")
                    Divider().frame(height:32)
                    stat("需要关注",model.stats.failed,"exclamationmark.circle")
                }.padding(.vertical,18).background(.background,in:RoundedRectangle(cornerRadius:14)).overlay(RoundedRectangle(cornerRadius:14).stroke(.primary.opacity(0.05)))
                VStack(alignment:.leading,spacing:13) {
                    Text("多模态工作区").font(.system(size:15,weight:.semibold))
                    LazyVGrid(columns:columns,spacing:12) {
                        ForEach([Destination.text,.image,.video,.audio]) { item in
                            Button { model.select(item) } label:{
                                VStack(alignment:.leading,spacing:15) {
                                    HStack { Image(systemName:item.icon).font(.system(size:22)).foregroundStyle(item.tint); Spacer(); Image(systemName:"arrow.up.right").font(.system(size:10)).foregroundStyle(.tertiary) }
                                    VStack(alignment:.leading,spacing:5) { Text(item.title).font(.system(size:13,weight:.semibold)).foregroundStyle(.primary); Text(cardSubtitle(item)).font(.system(size:10)).foregroundStyle(.secondary).lineLimit(1) }
                                }.padding(18).frame(maxWidth:.infinity,alignment:.leading).background(.background,in:RoundedRectangle(cornerRadius:14)).overlay(RoundedRectangle(cornerRadius:14).stroke(.primary.opacity(0.06)))
                            }.buttonStyle(.plain)
                        }
                    }
                }
                VStack(alignment:.leading,spacing:13) {
                    HStack { Text("最近的测试").font(.system(size:15,weight:.semibold)); Spacer(); Button("查看全部") { model.select(.history) }.buttonStyle(.link).font(.system(size:12)) }
                    VStack(spacing:0) {
                        if model.history.isEmpty {
                            HStack(spacing:16) { Image(systemName:"doc.text.magnifyingglass").font(.system(size:27,weight:.light)).foregroundStyle(.secondary); VStack(alignment:.leading,spacing:5) { Text(model.historyError == nil ? "你的第一份测试档案，从这里开始" : "档案暂时无法读取").font(.system(size:13,weight:.medium)); Text(model.historyError ?? "测试完成后，结果与报告会自动保存在本机。").font(.system(size:11)).foregroundStyle(.secondary) }; Spacer() }.padding(24)
                        } else {
                            ForEach(model.history) { item in
                                Button { model.openHistory(item) } label: {
                                    HStack(spacing:13) { Image(systemName:item.destination.icon).foregroundStyle(item.destination.tint).frame(width:28); VStack(alignment:.leading,spacing:4) { Text(item.displayTitle).font(.system(size:12,weight:.medium)); Text(item.title).font(.system(size:10)).foregroundStyle(.secondary).lineLimit(1) }; Spacer(); Text(item.statusText).font(.system(size:10)).padding(.horizontal,8).padding(.vertical,4).background(.secondary.opacity(0.08),in:Capsule()); Text(Date(timeIntervalSince1970:item.created_at),format:.dateTime.month().day().hour().minute()).font(.system(size:10)).foregroundStyle(.secondary).frame(width:102,alignment:.trailing) }.padding(14).contentShape(Rectangle())
                                }.buttonStyle(.plain)
                                if item.id != model.history.last?.id { Divider().padding(.leading,55) }
                            }
                        }
                    }.background(.background,in:RoundedRectangle(cornerRadius:14)).overlay(RoundedRectangle(cornerRadius:14).stroke(.primary.opacity(0.06)))
                }
                HStack { Image(systemName:"internaldrive"); Text("本机独立存储"); Text("·"); Text("API 密钥可选存入 macOS 钥匙串"); Spacer(); Text("NEBULA / \(AppVersion.current)") }.font(.system(size:10)).foregroundStyle(.tertiary).padding(.bottom,8)
            }.padding(.horizontal,32).padding(.vertical,24).frame(maxWidth:1200).frame(maxWidth:.infinity)
        }.background(Color(nsColor:.windowBackgroundColor))
    }
    func cardSubtitle(_ item:Destination)->String { switch item { case .text:return "对话 · 视觉理解"; case .image:return "生成 · 编辑 · 多图参照";case .video:return "异步生成 · 视频预览";default:return "合成 · 转写 · 播放" } }
    func stat(_ name:String,_ value:Int,_ icon:String)->some View { HStack(spacing:12) { Image(systemName:icon).font(.system(size:18,weight:.light)).foregroundStyle(.secondary); VStack(alignment:.leading,spacing:5) { Text(value.formatted()).font(.system(size:23,weight:.semibold,design:.rounded)); Text(name).font(.system(size:10)).foregroundStyle(.secondary) }; Spacer() }.padding(.horizontal,24).frame(maxWidth:.infinity) }
}
