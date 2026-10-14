import Foundation

@main struct LocalizationChecks {
    static func main() throws {
        let resources = URL(fileURLWithPath: CommandLine.arguments[1])
        let temporary = URL(fileURLWithPath: CommandLine.arguments[2])
        let suite = "local.trading212.localization-tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        var count = 0
        func check(_ condition: Bool, _ message: String) {
            precondition(condition, message); count += 1
        }
        let original = AppLocalization(resourceURL: resources, defaults: defaults)
        check(original.language == "zh-Hans", "Chinese is the existing default")
        check(original.languages.map(\.id) == ["zh-Hans", "en"], "Both languages available")
        check(original.environmentLabel("live") == "实盘" && original.environmentLabel("demo") == "模拟盘", "Chinese environment labels contain only Chinese")
        check(original.display("Trading 212 HTTP 401：请检查API Key、Secret及Live/Demo环境。") == "Trading 212 HTTP 401：请检查API Key、Secret及实盘／模拟盘环境。", "Chinese nested environment error")
        check(original.select("en"), "Select English")
        check(original.environmentLabel("live") == "Live" && original.environmentLabel("demo") == "Paper", "English environment labels")
        check(original.environmentLabel("unknown") == "unknown", "Unknown environment is not mislabeled")
        check(original.string("设置") == "Settings", "English lookup")
        check(original.string("成功 {0} / 失败 {1}", ["3","2"]) == "Succeeded 3 / Failed 2", "Parameterized UI strings")
        check(original.string("撤销订单 #{0}", ["{1}"]) == "Cancel order #{1}", "Inserted data is not reprocessed")
        check(original.display("212额度尚未重置，请约 12 秒后重试。") == "212 rate limit has not reset. Retry in approximately 12 seconds.", "Parameterized saved errors")
        check(original.display("Trading 212 HTTP 401：请检查API Key、Secret及Live/Demo环境。") == "Trading 212 HTTP 401: Check the API key, secret and Live/Paper environment.", "Nested HTTP errors")
        check(original.display("已配置Claude Desktop。重启或重新加载该AI客户端后可用；初始权限为只读。").hasPrefix("Configured Claude Desktop."), "Client success message")
        check(original.display("无法连接212，请检查网络和IP白名单。 提交结果未知，请在212核对；本工具不会自动重试。").contains("will not retry automatically"), "Uncertain trade warning retained")
        check(original.display("连接名称无效。") == "Invalid Connection name.", "Known service field label")
        check(original.display("请由用户在Trading212 Connection窗口手动开启下单权限。") == "Enable order placement permission manually in the Trading212 Connection window.", "Known permission enum")
        check(original.display("用户账户名称不应翻译") == "用户账户名称不应翻译", "Unknown external data retained")
        check(original.string("missing-key") == "missing-key", "Unknown key fallback")
        check(!original.select("unsupported") && original.language == "en", "Invalid selection rejected")
        check(AppLocalization(resourceURL: resources, defaults: defaults).language == "en", "Preference survives a new instance")
        check(original.select("zh-Hans") && original.string("设置") == "设置", "Can switch back")
        defaults.set("deleted-locale", forKey: AppLocalization.preferenceKey)
        check(AppLocalization(resourceURL: resources, defaults: defaults).language == "zh-Hans", "Removed language fallback")
        try FileManager.default.copyItem(at: resources, to: temporary.appendingPathComponent("Localization"))
        let extensible = temporary.appendingPathComponent("Localization")
        var manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: extensible.appendingPathComponent("languages.json"))) as! [String: Any]
        var entries = manifest["languages"] as! [[String: String]]
        entries.append(["id":"fr", "name":"Français", "file":"fr.json"])
        manifest["languages"] = entries
        try JSONSerialization.data(withJSONObject: manifest).write(to: extensible.appendingPathComponent("languages.json"))
        try JSONSerialization.data(withJSONObject: ["设置":"Réglages", "成功 {0} / 失败 {1}":"Wrong placeholder {0}"]).write(to: extensible.appendingPathComponent("fr.json"))
        let french = AppLocalization(resourceURL: extensible, defaults: defaults)
        check(french.languages.count == 3 && french.select("fr"), "Future language discovers automatically")
        check(french.string("设置") == "Réglages", "New language translation")
        check(french.string("添加连接") == "添加连接", "Missing translation defaults to Chinese")
        check(french.string("成功 {0} / 失败 {1}", ["3","2"]) == "成功 3 / 失败 2", "Mismatched placeholders use safe fallback")
        print("\(count) localization runtime checks passed")
    }
}
