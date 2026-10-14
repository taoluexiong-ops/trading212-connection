import Foundation

struct AppLanguage: Codable, Identifiable {
    let id: String
    let name: String
    let file: String
}

private struct LanguageManifest: Codable {
    let defaultLanguage: String
    let languages: [AppLanguage]
}

/// Catalogs use stable source phrases as keys. Substitutions are positional and never reprocessed.
final class AppLocalization {
    static let shared = AppLocalization(resourceURL: Bundle.main.resourceURL!.appendingPathComponent("Localization"))
    static let preferenceKey = "appLanguage"
    let languages: [AppLanguage]
    let defaultLanguage: String
    private let catalogs: [String: [String: String]]
    private let defaults: UserDefaults
    private let patterns: [(String, NSRegularExpression)]
    private(set) var language: String

    init(resourceURL: URL, defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let manifest = (try? Data(contentsOf: resourceURL.appendingPathComponent("languages.json")))
            .flatMap { try? JSONDecoder().decode(LanguageManifest.self, from: $0) }
        var loaded: [String: [String: String]] = [:]
        var available: [AppLanguage] = []
        for entry in manifest?.languages ?? [] {
            guard !entry.id.isEmpty, loaded[entry.id] == nil,
                  entry.file == URL(fileURLWithPath: entry.file).lastPathComponent,
                  let data = try? Data(contentsOf: resourceURL.appendingPathComponent(entry.file)),
                  let catalog = try? JSONDecoder().decode([String: String].self, from: data) else { continue }
            loaded[entry.id] = catalog
            available.append(entry)
        }
        let fallback = manifest?.defaultLanguage ?? "zh-Hans"
        defaultLanguage = loaded[fallback] != nil ? fallback : (available.first?.id ?? "zh-Hans")
        languages = available
        catalogs = loaded
        let saved = defaults.string(forKey: Self.preferenceKey) ?? defaultLanguage
        language = loaded[saved] != nil ? saved : defaultLanguage
        patterns = (loaded[defaultLanguage] ?? [:]).keys.filter { $0.range(of: #"\{\d+\}"#, options: .regularExpression) != nil }
            .sorted { $0.count > $1.count }.compactMap { source in
                let tokens = source.components(separatedBy: try! NSRegularExpression(pattern: #"\{\d+\}"#))
                let pattern = "^" + tokens.map(NSRegularExpression.escapedPattern(for:)).joined(separator: "([\\s\\S]*?)") + "$"
                return (try? NSRegularExpression(pattern: pattern)).map { (source, $0) }
            }
    }

    @discardableResult func select(_ id: String) -> Bool {
        guard catalogs[id] != nil else { return false }
        language = id
        defaults.set(id, forKey: Self.preferenceKey)
        return true
    }

    func string(_ key: String, _ arguments: [String] = []) -> String {
        let fallback = catalogs[defaultLanguage]?[key] ?? key
        let candidate = catalogs[language]?[key] ?? fallback
        let format = Self.placeholders(candidate) == Self.placeholders(fallback) ? candidate : fallback
        return Self.render(format, arguments)
    }

    func environmentLabel(_ id: String) -> String {
        switch id {
        case "live": return string("实盘")
        case "demo": return string("模拟盘")
        default: return id
        }
    }

    /// Translate known service messages without altering stored logs or user-entered names.
    func display(_ source: String, depth: Int = 0) -> String {
        guard depth < 3 else { return source }
        if catalogs[defaultLanguage]?[source] != nil { return string(source) }
        let suffix = " 提交结果未知，请在212核对；本工具不会自动重试。"
        if source.hasSuffix(suffix) {
            return display(String(source.dropLast(suffix.count)), depth: depth + 1) + string(suffix)
        }
        let range = NSRange(source.startIndex..., in: source)
        for (key, regex) in patterns {
            guard let match = regex.firstMatch(in: source, range: range), match.range == range else { continue }
            var args = (1..<match.numberOfRanges).map { i -> String in
                guard let range = Range(match.range(at: i), in: source) else { return "" }
                return String(source[range])
            }
            // Only known nested service fields are translated; arbitrary captured data is preserved.
            if key == "Trading 212 HTTP {0}：{1}", args.count == 2 { args[1] = display(args[1], depth: depth + 1) }
            if key == "请由用户在Trading212 Connection窗口手动开启{0}权限。", args.count == 1 { args[0] = string(args[0]) }
            if key == "{0}无效。", args == ["连接名称"] { args[0] = string("连接名称") }
            return string(key, args)
        }
        return source
    }

    static func placeholders(_ source: String) -> [String] {
        let regex = try! NSRegularExpression(pattern: #"\{\d+\}"#)
        return regex.matches(in: source, range: NSRange(source.startIndex..., in: source)).map {
            String(source[Range($0.range, in: source)!])
        }.sorted()
    }

    static func render(_ source: String, _ arguments: [String]) -> String {
        let regex = try! NSRegularExpression(pattern: #"\{(\d+)\}"#)
        var output = source
        for match in regex.matches(in: source, range: NSRange(source.startIndex..., in: source)).reversed() {
            guard let indexRange = Range(match.range(at: 1), in: source),
                  let index = Int(source[indexRange]), index < arguments.count,
                  let range = Range(match.range, in: output) else { continue }
            output.replaceSubrange(range, with: arguments[index])
        }
        return output
    }
}

private extension String {
    func components(separatedBy regex: NSRegularExpression) -> [String] {
        var parts: [String] = [], start = startIndex
        for match in regex.matches(in: self, range: NSRange(startIndex..., in: self)) {
            let range = Range(match.range, in: self)!
            parts.append(String(self[start..<range.lowerBound])); start = range.upperBound
        }
        parts.append(String(self[start...])); return parts
    }
}

func L(_ key: String, _ arguments: Any...) -> String {
    AppLocalization.shared.string(key, arguments.map { String(describing: $0) })
}
func D(_ source: String) -> String { AppLocalization.shared.display(source) }
