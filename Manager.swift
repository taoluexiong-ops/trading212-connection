import SwiftUI
import AppKit
import Foundation

func text(_ value: Any?) -> String { value as? String ?? "" }
func number(_ value: Any?) -> Int { (value as? NSNumber)?.intValue ?? 0 }
func flag(_ value: Any?) -> Bool { (value as? NSNumber)?.boolValue ?? false }
func environmentName(_ value: Any?) -> String { AppLocalization.shared.environmentLabel(text(value)) }
func localTime(_ value: Any?) -> String {
    let source = text(value)
    guard !source.isEmpty else { return L("尚未验证") }
    let format = ISO8601DateFormatter()
    format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = format.date(from: source) else { return source }
    let output = DateFormatter()
    output.dateFormat = "MM-dd HH:mm:ss"
    return output.string(from: date)
}

struct Connection: Identifiable {
    let raw: [String: Any]
    var id: String { text(raw["id"]) }
    var name: String { text(raw["name"]) }
    var environment: String { text(raw["environment"]) }
    var enabled: Bool { flag(raw["enabled"]) }
    var orders: Bool { flag(raw["allow_orders"]) }
    var cancel: Bool { flag(raw["allow_cancel"]) }
    var active: Bool { flag(raw["active"]) }
    var environmentLabel: String { environmentName(environment) }
}

struct ClientProfile: Identifiable {
    let raw: [String:Any]
    var id: String { text(raw["id"]) }
    var name: String { text(raw["name"]) }
    var enabled: Bool { flag(raw["enabled"]) }
    var configured: Bool { flag(raw["configured"]) }
    var installed: Bool { flag(raw["installed"]) }
    var orders: Bool { flag(raw["allow_orders"]) }
    var cancel: Bool { flag(raw["allow_cancel"]) }
    var account: String { text(raw["connectionId"]) }
    var status: String {
        if !installed { return L("未检测到安装") }
        if flag(raw["conflict"]) { return L("同名配置冲突") }
        if enabled && configured && flag(raw["accountEnabled"]) { return L("已配置") }
        if enabled && !flag(raw["accountEnabled"]) { return L("需要选择可用账户") }
        if enabled { return L("配置待更新") }
        return L("未连接")
    }
}

struct Action: Identifiable {
    let raw: [String: Any]
    var id: String { text(raw["id"]) }
    var payload: [String: Any] { raw["payload"] as? [String: Any] ?? [:] }
    var state: String { text(raw["state"]) }
    var stateLabel: String {
        ["pending":L("等待你确认"), "accepted":L("212 已接受请求"), "rejected":L("已拒绝"), "revoked":L("已撤销"),
         "failed":L("未提交成功"), "unknown":L("结果未知 · 请到212核对"), "submitting":L("提交状态待核对 · 勿重发"), "expired":L("已过期")][state] ?? state
    }
    var detail: String {
        if text(raw["kind"]) == "cancel" { return L("撤销订单 #{0}" , number(payload["orderId"])) }
        let side = text(payload["side"]) == "buy" ? L("买入") : L("卖出")
        let quantity = (payload["quantity"] as? NSNumber)?.stringValue ?? ""
        let types = ["market":L("市价单"), "limit":L("限价单"), "stop":L("止损单"), "stop_limit":L("止损限价单")]
        var detail = L("{0} {1} 股 {2} · {3}" , side, quantity, text(payload["ticker"]), types[text(payload["orderType"])] ?? "")
        if let price = payload["limitPrice"] as? NSNumber { detail += L("\n限价：{0}" , price.stringValue) }
        if let price = payload["stopPrice"] as? NSNumber { detail += L("　触发价：{0}" , price.stringValue) }
        if let validity = payload["timeValidity"] as? String { detail += L("\n有效期：{0}" , validity) }
        if flag(payload["extendedHours"]) { detail += L("\n允许盘前 / 盘后交易") }
        return detail
    }
}

@MainActor final class Model: ObservableObject {
    @Published var connections: [Connection] = []
    @Published var stats: [String: Any] = [:]
    @Published var limits: [[String: Any]] = []
    @Published var logs: [[String: Any]] = []
    @Published var actions: [Action] = []
    @Published var clients: [ClientProfile] = []
    @Published var usageByClient: [[String:Any]] = []
    @Published var clientAccounts: [String:String] = [:]
    @Published var selectedID: String?
    @Published var busy = false
    @Published var message = ""
    @Published var error = ""
    @Published var showAdd = false
    @Published var showSettings = false
    @Published var renaming: Connection?
    @Published private(set) var language = AppLocalization.shared.language
    func setLanguage(_ id: String) {
        if AppLocalization.shared.select(id) { language = id }
    }
    @Published var section = 1
    var selected: Connection? { connections.first { $0.id == selectedID } ?? connections.first { $0.active } ?? connections.first }
    var backend: URL { Bundle.main.resourceURL!.appendingPathComponent("backend.py") }

    func call(_ request: [String: Any], quiet: Bool = false, done: (() -> Void)? = nil) {
        guard !busy else { return }
        busy = true
        if !quiet { error = ""; message = "正在处理…" }
        let script = backend
        DispatchQueue.global(qos: .userInitiated).async {
            var reply: [String: Any] = [:]
            do {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
                process.arguments = [script.path, "--control"]
                let input = Pipe(), output = Pipe(), errors = Pipe()
                process.standardInput = input
                process.standardOutput = output
                process.standardError = errors
                try process.run()
                input.fileHandleForWriting.write(try JSONSerialization.data(withJSONObject: request))
                try input.fileHandleForWriting.close()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? ["ok":false,"error":"无法读取本机管理服务响应。"]
            } catch {
                reply = ["ok":false,"error":"无法启动本机管理服务，请检查应用文件是否完整。"]
            }
            let response = reply
            DispatchQueue.main.async {
                self.busy = false
                if !flag(response["ok"]) {
                    self.error = text(response["error"])
                    self.message = ""
                    return
                }
                if text(request["operation"]) == "overview", let data = response["data"] as? [String: Any] {
                    self.connections = (data["connections"] as? [[String:Any]] ?? []).map { Connection(raw:$0) }
                    self.stats = data["stats"] as? [String:Any] ?? [:]
                    self.limits = data["rateLimits"] as? [[String:Any]] ?? []
                    self.logs = data["logs"] as? [[String:Any]] ?? []
                    self.actions = (data["actions"] as? [[String:Any]] ?? []).map { Action(raw:$0) }
                    self.clients = (data["clients"] as? [[String:Any]] ?? []).map { ClientProfile(raw:$0) }
                    self.usageByClient = data["usageByClient"] as? [[String:Any]] ?? []
                    if self.selectedID == nil || !self.connections.contains(where:{$0.id == self.selectedID}) {
                        self.selectedID = text(data["activeId"])
                    }
                    for client in self.clients {
                        if self.clientAccounts[client.id] == nil || !self.connections.contains(where: { $0.id == self.clientAccounts[client.id] }) {
                            self.clientAccounts[client.id] = self.connections.contains(where: { $0.id == client.account }) ? client.account : (self.selected?.id ?? "")
                        }
                    }
                } else {
                    self.message = text((response["data"] as? [String:Any])?["message"])
                    if self.message.isEmpty { self.message = "已保存" }
                    done?()
                    self.refresh()
                }
            }
        }
    }
    func refresh() { call(["operation":"overview"],quiet:true) }
    func confirm(title: String, detail: String, action: String, run: () -> Void) {
        guard !busy else { return }
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.alertStyle = .warning
        alert.addButton(withTitle: L("取消"))
        alert.addButton(withTitle: action)
        if alert.runModal() == .alertSecondButtonReturn { run() }
    }
    func permission(_ conn: Connection, orders: Bool, cancel: Bool) {
        let request: [String:Any] = ["operation":"permissions","connectionId":conn.id,"allow_orders":orders,"allow_cancel":cancel]
        if (orders && !conn.orders) || (cancel && !conn.cancel) {
            let scope = orders && !conn.orders ? L("下单") : L("撤单")
            confirm(title:L("开启{0}权限？" , scope),detail:L("账户：{0}\n环境：{1}\n\n开启本工具的账户层{2}权限。AI客户端也需要单独启用该权限，每笔操作仍需你在此窗口确认。212 的 API Key 本身也需要相应权限。" , conn.name, conn.environmentLabel, scope),action:L("手动开启{0}" , scope)) { self.call(request) }
        } else { call(request) }
    }
    func connectClient(_ client: ClientProfile) {
        let accountID = clientAccounts[client.id] ?? ""
        guard let account = connections.first(where: { $0.id == accountID }) else { return }
        confirm(title:L("连接 {0}？" , client.name),detail:L("允许 {0} 通过本机中转站读取：\n{1} · {2}\n\n账户查询结果会进入该AI工具的会话；212密钥留在本机。初始客户端权限为只读，连接后需要重启或重新加载AI客户端。" , client.name, account.name, account.environmentLabel),action:L("连接")) {
            self.call(["operation":"client_connect","clientId":client.id,"connectionId":accountID])
        }
    }
    func clientPermission(_ client: ClientProfile, orders: Bool, cancel: Bool) {
        let request: [String:Any] = ["operation":"client_permissions","clientId":client.id,"allow_orders":orders,"allow_cancel":cancel]
        if (orders && !client.orders) || (cancel && !client.cancel) {
            let scope = orders && !client.orders ? L("下单") : L("撤单")
            confirm(title:L("允许 {0} 创建{1}请求？" , client.name, scope),detail:L("账户：{0}\n环境：{1}\n\n账户层权限也须开启。每笔提交仍需在操作确认页逐笔确认。" , text(client.raw["connectionName"]), environmentName(client.raw["environment"])),action:L("手动开启")) { self.call(request) }
        } else { call(request) }
    }
    func decide(_ action: Action, approve: Bool) {
        let request: [String:Any] = ["operation":"decide","actionId":action.id,"approve":approve]
        if approve {
            confirm(title:L("确认提交到 Trading 212？"),detail:L("来源：{0}\n账户：{1}\n环境：{2}\n\n{3}\n\n提交后无法保证撤销。市价单成交价格可能变动。" , text(action.raw["client_id"]), text(action.raw["connection_name"]), text(action.raw["environment"]) == "live" ? L("实盘 · 将影响真实资金") : environmentName(action.raw["environment"]), action.detail),action:L("确认提交")) { self.call(request) }
        } else { call(request) }
    }
}

let accent = Color(red:0.13,green:0.36,blue:0.93)
let ink = Color(red:0.11,green:0.16,blue:0.23)
let muted = Color(red:0.42,green:0.47,blue:0.55)
let page = Color(red:0.96,green:0.97,blue:0.985)

struct Badge: View {
    let label: String
    var color: Color = accent
    var body: some View {
        Text(label).font(.system(size:11,weight:.semibold)).foregroundStyle(color)
            .padding(.horizontal,9).padding(.vertical,5).background(color.opacity(0.09),in:Capsule())
    }
}

struct Card<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View { VStack(alignment:.leading,spacing:0) { content }.padding(20).frame(maxWidth:.infinity,alignment:.leading).background(.white,in:RoundedRectangle(cornerRadius:14)).overlay(RoundedRectangle(cornerRadius:14).stroke(Color.black.opacity(0.06))) }
}

struct Metric: View {
    let title: String
    let value: String
    let subtitle: String
    var body: some View {
        Card {
            Text(title).font(.system(size:12)).foregroundStyle(muted)
            Text(value).font(.system(size:29,weight:.semibold,design:.rounded)).foregroundStyle(ink).padding(.vertical,3)
            Text(subtitle).font(.system(size:10)).foregroundStyle(muted)
        }
    }
}

struct Dashboard: View {
    @EnvironmentObject var model: Model
    let timer = Timer.publish(every:5,on:.main,in:.common).autoconnect()
    var body: some View {
        HStack(spacing:0) {
            sidebar
            VStack(alignment:.leading,spacing:20) {
                header
                HStack(spacing:12) {
                    Metric(title:L("API 请求"),value:String(number(model.stats["requests"])),subtitle:L("本工具 · 最近24小时"))
                    Metric(title:L("成功率"),value:rate,subtitle:L("成功 {0} / 失败 {1}" , number(model.stats["successes"]), number(model.stats["failures"])))
                    Metric(title:L("限流次数"),value:String(number(model.stats["rate_limited"])),subtitle:L("HTTP 429 · 最近24小时"))
                    Metric(title:L("待确认"),value:String(number(model.stats["pending"])),subtitle:L("只有你确认后才会提交"))
                }
                Picker(L("页面"),selection:$model.section) {
                    Text(L("连接与权限")).tag(0)
                    Text(L("AI 客户端")).tag(1)
                    Text(L("API 使用情况")).tag(2)
                    Text(L("操作确认")).tag(3)
                }.labelsHidden().pickerStyle(.segmented).frame(maxWidth:.infinity)
                ScrollView {
                    VStack(alignment:.leading,spacing:16) {
                        if model.section == 0 { connectionContent }
                        else if model.section == 1 { clientContent }
                        else if model.section == 2 { usageContent }
                        else { actionContent }
                    }.padding(.bottom,12)
                }
                HStack {
                    Image(systemName:"lock.shield").foregroundStyle(muted)
                    Text(L("密钥保存在 macOS 钥匙串 · 默认只读 · 本机管理")).foregroundStyle(muted)
                    Spacer()
                    if model.busy { ProgressView().controlSize(.small); Text(L("处理中")).foregroundStyle(muted) }
                    else if !model.message.isEmpty { Text(D(model.message)).foregroundStyle(.green) }
                }.font(.system(size:11))
            }.padding(28).background(page)
        }.frame(minWidth:1120,minHeight:760).preferredColorScheme(.light)
        .environment(\.locale,Locale(identifier:model.language))
        .sheet(isPresented:$model.showSettings) { LanguageSettings().environmentObject(model) }
        .sheet(item:$model.renaming) { conn in EditConnectionName(connection:conn).environmentObject(model) }
        .sheet(isPresented:$model.showAdd) { AddConnection().environmentObject(model) }
        .onAppear { model.refresh(); NSApplication.shared.activate(ignoringOtherApps:true) }
        .onReceive(timer) { _ in if !model.showAdd && !model.showSettings && model.renaming == nil { model.refresh() } }
    }
    var rate: String {
        guard let value = model.stats["success_rate"] as? NSNumber else { return "—" }
        return String(format:"%.1f%%",value.doubleValue)
    }
    var sidebar: some View {
        VStack(alignment:.leading,spacing:20) {
            HStack(spacing:10) {
                Image(systemName:"link").font(.system(size:22,weight:.semibold)).foregroundStyle(.white).frame(width:44,height:44).background(accent,in:RoundedRectangle(cornerRadius:12))
                VStack(alignment:.leading,spacing:3) {
                    Text("Trading212").font(.system(size:18,weight:.bold))
                    Text("Connection").font(.system(size:13)).foregroundStyle(muted)
                }
            }.padding(.bottom,12)
            Text(L("已保存的连接")).font(.system(size:11,weight:.semibold)).foregroundStyle(muted)
            ScrollView {
                VStack(spacing:8) {
                    ForEach(model.connections) { conn in
                        Button {
                            model.selectedID = conn.id
                        } label: {
                            VStack(alignment:.leading,spacing:7) {
                                HStack {
                                    Circle().fill(conn.enabled ? .green : Color.gray).frame(width:6,height:6)
                                    Text(conn.name).font(.system(size:12,weight:.semibold)).lineLimit(2)
                                }
                                HStack {
                                    Text(conn.environmentLabel).font(.system(size:10)).foregroundStyle(muted)
                                    Spacer()
                                    if conn.active { Text(L("当前")).font(.system(size:9,weight:.semibold)).foregroundStyle(accent) }
                                }
                            }.padding(12).frame(maxWidth:.infinity,alignment:.leading)
                                .background(model.selected?.id == conn.id ? accent.opacity(0.07) : Color.clear,in:RoundedRectangle(cornerRadius:10))
                                .overlay(RoundedRectangle(cornerRadius:10).stroke(model.selected?.id == conn.id ? accent.opacity(0.15) : Color.clear))
                        }.buttonStyle(.plain)
                    }
                    if model.connections.isEmpty { Text(L("还没有连接\n添加 API Key 后即可使用")).font(.system(size:12)).foregroundStyle(muted).frame(maxWidth:.infinity,alignment:.leading).padding(.vertical,12) }
                }
            }
            Button { model.showAdd = true } label: {
                Label(L("添加连接"),systemImage:"plus").frame(maxWidth:.infinity).padding(.vertical,5)
            }.buttonStyle(.borderedProminent).tint(accent).disabled(model.busy)
            Divider()
            Text(L("先添加212账户，再选择AI客户端。密钥在本机管理，各客户端默认只读。")).font(.system(size:11)).foregroundStyle(muted).lineSpacing(4)
            Button { model.showSettings = true } label: { Label(L("设置"),systemImage:"gearshape") }.buttonStyle(.borderless)
            Text("v1.2.2 · macOS").font(.system(size:10)).foregroundStyle(muted)
        }.padding(22).frame(width:234).background(Color.white)
    }
    var header: some View {
        VStack(alignment:.leading,spacing:8) {
            HStack {
                Text("Trading212 Connection").font(.system(size:25,weight:.bold)).foregroundStyle(ink)
                Spacer()
                Button { model.refresh() } label: { Image(systemName:"arrow.clockwise") }.buttonStyle(.borderless).disabled(model.busy).help(L("刷新本机连接状态，不会查询212"))
                Badge(label:L("本地连接工具"))
            }
            Text(L("Trading 212 账户 → 本机中转站 → AI 客户端")).font(.system(size:13)).foregroundStyle(muted)
            if !model.error.isEmpty {
                Text(D(model.error)).font(.system(size:12)).foregroundStyle(.red).padding(10).frame(maxWidth:.infinity,alignment:.leading).background(Color.red.opacity(0.05),in:RoundedRectangle(cornerRadius:8))
            }
        }
    }
    @ViewBuilder var connectionContent: some View {
        if let conn = model.selected {
            Card {
                HStack {
                    VStack(alignment:.leading,spacing:7) {
                        HStack(spacing:12) {
                            Text(conn.name).font(.system(size:19,weight:.semibold)).foregroundStyle(ink)
                            Button { model.error=""; model.renaming=conn } label: { Label(L("编辑名称"),systemImage:"pencil") }
                                .buttonStyle(.borderless).font(.system(size:12)).disabled(model.busy)
                        }
                        HStack(spacing:8) {
                            Badge(label:conn.environmentLabel,color:conn.environment == "live" ? .orange : accent)
                            Badge(label:conn.enabled ? L("连接已启用") : L("已暂停"),color:conn.enabled ? .green : .gray)
                            if conn.active { Badge(label:L("窗口当前账户")) }
                        }
                    }
                    Spacer()
                    Image(systemName:"checkmark.shield").font(.system(size:30)).foregroundStyle(accent.opacity(0.8))
                }
                Divider().padding(.vertical,14)
                HStack {
                    VStack(alignment:.leading,spacing:6) {
                        Text("API Key").font(.system(size:11)).foregroundStyle(muted)
                        Text(text(conn.raw["masked_key"])).font(.system(size:13,design:.monospaced))
                    }.frame(width:190,alignment:.leading)
                    VStack(alignment:.leading,spacing:6) {
                        Text(L("账户币种")).font(.system(size:11)).foregroundStyle(muted)
                        Text(text(conn.raw["currency"])).font(.system(size:13,weight:.medium))
                    }.frame(width:110,alignment:.leading)
                    VStack(alignment:.leading,spacing:6) {
                        Text(L("最近验证")).font(.system(size:11)).foregroundStyle(muted)
                        Text(localTime(conn.raw["verified_at"])).font(.system(size:13))
                    }
                }
                if !text(conn.raw["last_error"]).isEmpty { Text(D(text(conn.raw["last_error"]))).font(.system(size:12)).foregroundStyle(.red).padding(.top,10) }
                HStack(spacing:10) {
                    Button(L("测试连接")) { model.call(["operation":"verify","connectionId":conn.id]) }.disabled(!conn.enabled)
                    if !conn.active { Button(L("设为窗口当前账户")) { model.call(["operation":"select","connectionId":conn.id]) }.disabled(!conn.enabled) }
                    Button(conn.enabled ? L("暂停连接") : L("恢复连接")) { model.call(["operation":"enabled","connectionId":conn.id,"enabled":!conn.enabled]) }
                    Spacer()
                    Button(L("删除连接"),role:.destructive) {
                        model.confirm(title:L("删除此连接？"),detail:L("将删除本机连接与钥匙串凭证，并撤销尚未提交的请求。\n不会撤销 Trading 212 平台上的 API Key。\n\n账户：{0}" , conn.name),action:L("删除连接")) { model.call(["operation":"delete","connectionId":conn.id]) }
                    }
                }.buttonStyle(.bordered).controlSize(.regular).padding(.top,18).disabled(model.busy)
            }
            Card {
                HStack { Text(L("本工具的账户权限")).font(.system(size:16,weight:.semibold)); Spacer(); Badge(label:conn.orders || conn.cancel ? L("用户已开启额外权限") : L("默认只读"),color:conn.orders || conn.cancel ? .orange : .green) }
                Text(L("即使212的API权限全开，工具也只开放你在这里手动启用的能力。")).font(.system(size:12)).foregroundStyle(muted).padding(.top,4)
                Divider().padding(.vertical,12)
                HStack { Label(L("账户读取"),systemImage:"eye").font(.system(size:13,weight:.medium)); Spacer(); Text(L("标配 · 启用连接即可读取")).font(.system(size:11)).foregroundStyle(.green) }
                Text(L("账户、持仓、挂单、历史订单、分红和资金流水；实际可读范围仍受212密钥权限限制。")).font(.system(size:11)).foregroundStyle(muted).padding(.top,5)
                Divider().padding(.vertical,10)
                Toggle(L("手动开启下单权限"),isOn:Binding(get:{conn.orders},set:{model.permission(conn,orders:$0,cancel:conn.cancel)})).toggleStyle(.switch).font(.system(size:13,weight:.medium)).disabled(model.busy || !conn.enabled)
                Text(L("允许创建市价、限价、止损和止损限价请求；每笔提交需在本窗口确认。")).font(.system(size:11)).foregroundStyle(muted).padding(.top,4)
                Divider().padding(.vertical,10)
                Toggle(L("手动开启撤单权限"),isOn:Binding(get:{conn.cancel},set:{model.permission(conn,orders:conn.orders,cancel:$0)})).toggleStyle(.switch).font(.system(size:13,weight:.medium)).disabled(model.busy || !conn.enabled)
                Text(L("允许创建撤销挂单请求；每笔提交需在本窗口确认。")).font(.system(size:11)).foregroundStyle(muted).padding(.top,4)
            }
        } else {
            Card {
                Image(systemName:"key.horizontal").font(.system(size:32)).foregroundStyle(accent).padding(.bottom,8)
                Text(L("添加你的第一个连接")).font(.system(size:20,weight:.semibold))
                Text(L("在固定窗口中粘贴API Key和API Secret，验证后保存在本机钥匙串。")).font(.system(size:13)).foregroundStyle(muted).padding(.vertical,6)
                Button(L("添加连接")) { model.showAdd = true }.buttonStyle(.borderedProminent).tint(accent)
            }
        }
    }
    var clientContent: some View {
        VStack(alignment:.leading,spacing:16) {
            Card {
                Text(L("选择需要连接的 AI 工具")).font(.system(size:17,weight:.semibold))
                Text(L("一个212账户可供多个AI客户端使用，也可为每个客户端指定不同账户。这里只写入连接配置，不会把API Key或Secret写进AI工具。")).font(.system(size:12)).foregroundStyle(muted).lineSpacing(4).padding(.top,6)
            }
            ForEach(model.clients) { client in
                Card {
                    HStack(alignment:.top) {
                        Image(systemName:client.id == "codex" ? "terminal" : "sparkles").font(.system(size:22)).foregroundStyle(accent).frame(width:32)
                        VStack(alignment:.leading,spacing:5) {
                            Text(client.name).font(.system(size:17,weight:.semibold))
                            Text(client.id == "claude-desktop" ? L("Claude桌面聊天 · 本地MCP") : L("跨项目使用 · 本地MCP")).font(.system(size:11)).foregroundStyle(muted)
                        }
                        Spacer()
                        Badge(label:client.status,color:client.configured && client.enabled ? .green : .gray)
                    }
                    Divider().padding(.vertical,12)
                    HStack(spacing:14) {
                        Text(L("可访问的212账户")).font(.system(size:12,weight:.medium))
                        Picker(L("账户"),selection:Binding(get:{model.clientAccounts[client.id] ?? ""},set:{model.clientAccounts[client.id]=$0})) {
                            Text(L("请选择账户")).tag("")
                            ForEach(model.connections.filter { $0.enabled }) { conn in
                                Text(conn.name+" · "+conn.environmentLabel).tag(conn.id)
                            }
                        }.labelsHidden().frame(maxWidth:380).disabled(model.busy)
                        Spacer()
                        Button(client.configured && client.enabled ? L("保存连接") : L("连接")) { model.connectClient(client) }
                            .buttonStyle(.borderedProminent).tint(accent)
                            .disabled(model.busy || !client.installed || flag(client.raw["conflict"]) || (model.clientAccounts[client.id] ?? "").isEmpty)
                        if client.enabled || client.configured {
                            Button(L("断开")) { model.call(["operation":"client_disconnect","clientId":client.id]) }.buttonStyle(.bordered).disabled(model.busy)
                        }
                    }
                    if !client.installed {
                        HStack {
                            Text(client.id == "claude-code" ? L("尚未检测到Claude Code命令行。安装后点击右上角刷新。") : L("请先安装此AI客户端，再刷新状态。")).font(.system(size:11)).foregroundStyle(muted)
                            Spacer()
                            Button(L("查看官方安装说明")) {
                                let url = client.id == "claude-code" ? "https://code.claude.com/docs/en/overview" : (client.id == "codex" ? "https://learn.chatgpt.com/docs/quickstart" : "https://claude.ai/download")
                                NSWorkspace.shared.open(URL(string:url)!)
                            }.buttonStyle(.link)
                        }.padding(.top,10)
                    }
                    if !text(client.raw["connectionName"]).isEmpty {
                        Text(L("当前绑定：")+text(client.raw["connectionName"])+L("；选择其他账户后请点击保存连接。")).font(.system(size:10)).foregroundStyle(muted).padding(.top,8)
                    }
                    if client.enabled && client.configured {
                        HStack {
                            Toggle(L("此客户端可下单"),isOn:Binding(get:{client.orders},set:{model.clientPermission(client,orders:$0,cancel:client.cancel)})).toggleStyle(.switch)
                            Toggle(L("此客户端可撤单"),isOn:Binding(get:{client.cancel},set:{model.clientPermission(client,orders:client.orders,cancel:$0)})).toggleStyle(.switch)
                            Spacer()
                            Badge(label:client.orders || client.cancel ? L("用户已开启额外权限") : L("只读"),color:client.orders || client.cancel ? .orange : .green)
                        }.font(.system(size:12)).padding(.top,14).disabled(model.busy || !flag(client.raw["accountEnabled"]) || model.clientAccounts[client.id] != client.account)
                        Text(L("交易须同时满足212授权、账户开关和客户端开关；每笔操作仍需确认。")).font(.system(size:10)).foregroundStyle(muted).padding(.top,6)
                    }
                    HStack {
                        Text(L("最近握手：")+localTime(client.raw["lastSeen"])).font(.system(size:10)).foregroundStyle(muted)
                        Spacer()
                        Text(L("配置写入后需重启或重新加载AI客户端")).font(.system(size:10)).foregroundStyle(muted)
                    }.padding(.top,10)
                    if !text(client.raw["error"]).isEmpty { Text(D(text(client.raw["error"]))).font(.system(size:11)).foregroundStyle(.red).padding(.top,7) }
                }
            }
            Card {
                Text(L("网页版与手机端")).font(.system(size:14,weight:.semibold))
                Text(L("当前为本机中转站，支持上面列出的本地AI客户端。ChatGPT或Claude网页聊天需要另行提供可认证的远程服务。")).font(.system(size:11)).foregroundStyle(muted).lineSpacing(3).padding(.top,5)
            }
        }
    }
    var usageContent: some View {
        VStack(alignment:.leading,spacing:16) {
            Card {
                Text(L("按请求来源统计 · 最近24小时")).font(.system(size:16,weight:.semibold))
                ForEach(Array(model.usageByClient.enumerated()),id:\.offset) { _,row in
                    HStack {
                        Text(D(text(row["name"]))).font(.system(size:12,weight:.medium))
                        Spacer()
                        Text(L("请求 {0} · 成功 {1} · 失败 {2}" , number(row["requests"]), number(row["successes"]), number(row["failures"]))).font(.system(size:11)).foregroundStyle(muted)
                    }.padding(.top,10)
                }
                if model.usageByClient.isEmpty { Text(L("暂无请求记录")).font(.system(size:12)).foregroundStyle(muted).padding(.top,10) }
            }
            Card {
                Text(L("212 API 限流额度")).font(.system(size:16,weight:.semibold))
                Text(L("当前账户 · 最近一次响应头观测值。窗口到期后旧值标记为过期，不推测新的剩余额度。")).font(.system(size:11)).foregroundStyle(muted).padding(.vertical,5)
                if model.limits.isEmpty { Text(L("尚无额度数据。测试连接或查询账户后会显示212返回的额度。")).font(.system(size:12)).foregroundStyle(muted).padding(.vertical,12) }
                ForEach(Array(model.limits.enumerated()),id:\.offset) { _,row in
                    Divider().padding(.vertical,7)
                    HStack {
                        VStack(alignment:.leading,spacing:4) {
                            Text(text(row["method"])+" "+text(row["endpoint"])).font(.system(size:11,design:.monospaced))
                            Text(L("最近观测：")+localTime(row["seen_at"])).font(.system(size:10)).foregroundStyle(muted)
                        }
                        Spacer()
                        VStack(alignment:.trailing,spacing:4) {
                            if flag(row["expired"]) { Badge(label:L("观测窗口已过期"),color:.gray) }
                            else { Text(L("剩余 {0} / {1}" , quota(row["remaining"]), quota(row["limit_value"]))).font(.system(size:12,weight:.semibold)).foregroundStyle(accent) }
                            Text(resetLabel(row)).font(.system(size:10)).foregroundStyle(muted)
                        }
                    }
                }
            }
            Card {
                Text(L("最近 API 调用")).font(.system(size:16,weight:.semibold))
                Text(L("仅统计本工具发出的请求；不记录密钥、余额或持仓内容。212的额度限制按账户计算，其他应用也可能占用额度。")).font(.system(size:11)).foregroundStyle(muted).padding(.vertical,5)
                if model.logs.isEmpty { Text(L("暂无请求记录")).font(.system(size:12)).foregroundStyle(muted).padding(.vertical,14) }
                ForEach(Array(model.logs.enumerated()),id:\.offset) { _,row in
                    Divider().padding(.vertical,6)
                    HStack(alignment:.top,spacing:14) {
                        Text(localTime(row["time"])).font(.system(size:10,design:.monospaced)).foregroundStyle(muted).frame(width:110,alignment:.leading)
                        VStack(alignment:.leading,spacing:4) {
                            Text(text(row["method"])+" "+text(row["endpoint"])).font(.system(size:11,design:.monospaced))
                            Text(text(row["connection_name"])+" · "+environmentName(row["environment"])+" · "+text(row["client_id"])).font(.system(size:10)).foregroundStyle(muted)
                            if !text(row["error"]).isEmpty { Text(D(text(row["error"]))).font(.system(size:10)).foregroundStyle(.red) }
                        }
                        Spacer()
                        Badge(label:number(row["status"]) == 0 ? L("网络错误") : String(number(row["status"])),color:flag(row["ok"]) ? .green : .red)
                        Text("\(number(row["duration_ms"]))ms").font(.system(size:10)).foregroundStyle(muted).frame(width:65,alignment:.trailing)
                    }
                }
            }
        }
    }
    func resetLabel(_ row: [String:Any]) -> String {
        guard let reset = row["reset_at"] as? NSNumber else { return L("重置时间未提供") }
        let formatter = DateFormatter(); formatter.dateFormat="HH:mm:ss"
        return L("窗口重置：")+formatter.string(from:Date(timeIntervalSince1970:reset.doubleValue))
    }
    func quota(_ value: Any?) -> String { (value as? NSNumber)?.stringValue ?? L("未提供") }
    var actionContent: some View {
        VStack(alignment:.leading,spacing:16) {
            Card {
                Text(L("逐笔操作确认")).font(.system(size:16,weight:.semibold))
                Text(L("AI客户端只能创建请求。你在这里确认后才会向212提交。请求10分钟后过期；断开客户端、权限改变、暂停或删除连接会撤销待确认请求。")).font(.system(size:12)).foregroundStyle(muted).lineSpacing(4).padding(.top,5)
            }
            if model.actions.isEmpty {
                Card {
                    Image(systemName:"checkmark.seal").font(.system(size:27)).foregroundStyle(.green)
                    Text(L("没有待确认的操作")).font(.system(size:17,weight:.semibold)).padding(.top,5)
                    Text(L("默认只读状态下，不会产生任何交易。")).font(.system(size:12)).foregroundStyle(muted).padding(.top,4)
                }
            }
            ForEach(model.actions) { action in
                Card {
                    HStack {
                        Text(text(action.raw["connection_name"])).font(.system(size:14,weight:.semibold))
                        Badge(label:text(action.raw["client_id"]))
                        Badge(label:environmentName(action.raw["environment"]),color:.orange)
                        Spacer()
                        Badge(label:action.stateLabel,color:action.state == "pending" ? .orange : .gray)
                    }
                    Text(action.detail).font(.system(size:14,weight:.medium)).lineSpacing(5).padding(.vertical,10)
                    Text(L("创建于 ")+localTime(action.raw["created_at"])).font(.system(size:10)).foregroundStyle(muted)
                    if !text(action.raw["error"]).isEmpty { Text(D(text(action.raw["error"]))).font(.system(size:12)).foregroundStyle(.red).padding(.top,5) }
                    if action.state == "accepted" { Text(L("212已接受请求；此状态不代表成交，也不保证撤单成功。请查询订单状态。")).font(.system(size:11)).foregroundStyle(muted).padding(.top,5) }
                    if action.state == "pending" {
                        HStack {
                            Button(L("拒绝")) { model.decide(action,approve:false) }.buttonStyle(.bordered)
                            Spacer()
                            Button(L("查看并确认提交")) { model.decide(action,approve:true) }.buttonStyle(.borderedProminent).tint(accent)
                        }.padding(.top,14).disabled(model.busy)
                    }
                }
            }
        }
    }
}

struct AddConnection: View {
    @EnvironmentObject var model: Model
    @State var name = "Trading 212"
    @State var environment = "live"
    @State var key = ""
    @State var secret = ""
    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            HStack {
                Image(systemName:"key.horizontal.fill").foregroundStyle(accent).font(.system(size:25))
                VStack(alignment:.leading,spacing:4) {
                    Text(L("添加 Trading 212 连接")).font(.system(size:22,weight:.bold))
                    Text(L("复制粘贴凭证即可，密钥不会发送到聊天。")).font(.system(size:12)).foregroundStyle(muted)
                }
            }
            Divider()
            field(L("连接名称")) {
                TextField(L("例如：Trading 212 ISA"),text:$name)
                Text(L("名称由你填写，不随应用语言改变。")).font(.system(size:11)).foregroundStyle(muted)
            }
            field(L("账户环境")) {
                Picker(L("账户环境"),selection:$environment) {
                    Text(environmentName("live")).tag("live")
                    Text(environmentName("demo")).tag("demo")
                }.labelsHidden().pickerStyle(.segmented)
            }
            field("API Key") { SecureField(L("粘贴 API Key"),text:$key).textContentType(.password) }
            field("API Secret") { SecureField(L("粘贴与 Key 配对的 API Secret"),text:$secret).textContentType(.password) }
            HStack(alignment:.top,spacing:8) {
                Image(systemName:"lock.shield").foregroundStyle(.green)
                Text(L("保存前验证账户和持仓读取。凭证存入 macOS 钥匙串；新连接默认只读，下单与撤单权限关闭。")).font(.system(size:12)).foregroundStyle(muted).lineSpacing(4)
            }.padding(12).background(Color.green.opacity(0.05),in:RoundedRectangle(cornerRadius:10))
            if !model.error.isEmpty { Text(D(model.error)).font(.system(size:12)).foregroundStyle(.red) }
            HStack {
                Button(L("取消")) { key="";secret="";model.showAdd=false }.disabled(model.busy)
                Spacer()
                if model.busy { ProgressView().controlSize(.small); Text(L("正在验证…")).font(.system(size:12)).foregroundStyle(muted) }
                Button(L("验证并保存连接")) {
                    model.call(["operation":"add","name":name,"environment":environment,"key":key.trimmingCharacters(in:.whitespacesAndNewlines),"secret":secret.trimmingCharacters(in:.whitespacesAndNewlines)]) {
                        key="";secret="";model.showAdd=false
                    }
                }.buttonStyle(.borderedProminent).tint(accent).disabled(model.busy || key.isEmpty || secret.isEmpty || name.isEmpty)
            }
        }.padding(30).frame(width:600).textFieldStyle(.roundedBorder)
    }
    func field<Content:View>(_ label: String,@ViewBuilder content:() -> Content) -> some View {
        VStack(alignment:.leading,spacing:7) { Text(label).font(.system(size:12,weight:.semibold));content() }
    }
}

struct EditConnectionName: View {
    @EnvironmentObject var model: Model
    let connection: Connection
    @State private var name: String
    init(connection: Connection) {
        self.connection = connection
        _name = State(initialValue:connection.name)
    }
    var body: some View {
        VStack(alignment:.leading,spacing:18) {
            Label(L("编辑账户名称"),systemImage:"pencil").font(.system(size:22,weight:.bold))
            Text(L("名称由你填写，不随应用语言改变。")).font(.system(size:12)).foregroundStyle(muted)
            TextField(L("例如：Trading 212 ISA"),text:$name).textFieldStyle(.roundedBorder)
            Text(L("这仅修改本机显示名称，最多60个字符。账户绑定、密钥和交易权限保持不变。")).font(.system(size:12)).foregroundStyle(muted)
            if !model.error.isEmpty { Text(D(model.error)).font(.system(size:12)).foregroundStyle(.red) }
            HStack {
                Button(L("取消")) { model.renaming=nil }.disabled(model.busy)
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Button(L("保存名称")) {
                    model.call(["operation":"rename","connectionId":connection.id,"name":name]) { model.renaming=nil }
                }.buttonStyle(.borderedProminent).tint(accent)
                 .disabled(model.busy || name.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty || name.count > 60)
                 .keyboardShortcut(.defaultAction)
            }
        }.padding(28).frame(width:500)
        .environment(\.locale,Locale(identifier:model.language))
    }
}

struct LanguageSettings: View {
    @EnvironmentObject var model: Model
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            Label(L("设置"),systemImage:"gearshape").font(.system(size:22,weight:.bold))
            Picker(L("应用语言"),selection:Binding(get:{model.language},set:{model.setLanguage($0)})) {
                ForEach(AppLocalization.shared.languages) { language in
                    Text(language.name).tag(language.id)
                }
            }.pickerStyle(.menu)
            Text(L("立即生效，并保存到本机。")).font(.system(size:13)).foregroundStyle(muted)
            Text(L("更改语言不会改变账户连接或交易权限。")).font(.system(size:12)).foregroundStyle(muted)
            HStack { Spacer(); Button(L("关闭")) { model.showSettings=false }.keyboardShortcut(.defaultAction) }
        }.padding(28).frame(width:460)
        .environment(\.locale,Locale(identifier:model.language))
    }
}

@main struct Trading212ConnectionApp: App {
    @StateObject var model = Model()
    var body: some Scene {
        WindowGroup("Trading212 Connection") { Dashboard().environmentObject(model) }
            .defaultSize(width:1160,height:820)
            .windowResizability(.contentMinSize)
            .commands {
                CommandGroup(replacing:.appSettings) { Button(L("设置")) { model.showSettings=true }.keyboardShortcut(",") }
                CommandGroup(replacing:.newItem) { Button(L("添加连接")) { model.showAdd=true }.keyboardShortcut("n") }
            }
    }
}
