import Foundation

/// Tracking parameters to take out of page addresses: on every address
/// `urlPattern` matches (every address when it is left out) and no
/// `exceptions` pattern matches, each query parameter whose name `params`
/// matches whole is removed. Names are matched as written in the address,
/// ignoring case.
struct PageStripRule {
    let url: NSRegularExpression?
    let params: NSRegularExpression
    let exceptions: NSRegularExpression?

    init(_ d: [String: Any]) throws {
        url = try (d["urlPattern"] as? String).map { try NSRegularExpression(pattern: $0, options: .caseInsensitive) }
        let names = try PageNavigationRules.strings(d["params"], "strip params")
        guard !names.isEmpty else { throw PageError.fail("A strip rule needs params") }
        params = try Self.anyOf(names, whole: true)
        let except = try PageNavigationRules.strings(d["exceptions"] ?? [], "strip exceptions")
        exceptions = except.isEmpty ? nil : try Self.anyOf(except, whole: false)
    }

    func applies(to text: String) -> Bool {
        let all = NSRange(text.startIndex..., in: text)
        if let url, url.firstMatch(in: text, range: all) == nil { return false }
        return exceptions?.firstMatch(in: text, range: all) == nil
    }

    func strips(_ name: String) -> Bool {
        params.firstMatch(in: name, range: NSRange(name.startIndex..., in: name)) != nil
    }

    private static func anyOf(_ patterns: [String], whole: Bool) throws -> NSRegularExpression {
        let body = patterns.map { "(?:\($0))" }.joined(separator: "|")
        return try NSRegularExpression(pattern: whole ? "^(?:\(body))$" : body, options: .caseInsensitive)
    }

    /// `url` without the parameters `rules` remove; nil when none apply.
    static func stripped(_ url: URL, by rules: [PageStripRule]) -> URL? {
        guard !rules.isEmpty, var parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let items = parts.percentEncodedQueryItems, !items.isEmpty
        else { return nil }
        let text = url.absoluteString
        let active = rules.filter { $0.applies(to: text) }
        guard !active.isEmpty else { return nil }
        let kept = items.filter { item in !active.contains { $0.strips(item.name) } }
        guard kept.count < items.count else { return nil }
        parts.percentEncodedQueryItems = kept.isEmpty ? nil : kept
        return parts.url
    }
}
