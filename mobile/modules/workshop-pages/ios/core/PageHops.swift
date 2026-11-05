import Foundation

/// A tracking hop to skip: a page address that only forwards to another,
/// which carries the destination in a query parameter or in its path.
struct PageHopRule {
    let include: [PageURLPattern]
    let exclude: [PageURLPattern]
    /// The query parameter holding the destination.
    let param: String?
    /// The parameter's value is base64 of the destination.
    let base64: Bool
    /// A pattern on the path whose captures make the destination: joined,
    /// or put into `template` at `$1`, `$2`...
    let path: NSRegularExpression?
    let template: String?
    /// Put before a destination that has no scheme ("https").
    let prependScheme: String?

    init(_ d: [String: Any]) throws {
        include = try PageNavigationRules.strings(d["include"], "hop include").map(PageURLPattern.init)
        exclude = try PageNavigationRules.strings(d["exclude"] ?? [], "hop exclude").map(PageURLPattern.init)
        param = d["param"] as? String
        base64 = d["base64"] as? Bool ?? false
        path = try (d["pathPattern"] as? String).map { try NSRegularExpression(pattern: $0) }
        template = d["template"] as? String
        prependScheme = d["prependScheme"] as? String
        guard (param == nil) != (path == nil) else {
            throw PageError.fail("A hop rule takes the destination from param or pathPattern, one of them")
        }
    }

    /// Where `url` forwards to, when this rule covers it.
    func destination(_ url: URL) -> URL? {
        let text = url.absoluteString
        guard include.contains(where: { $0.matches(url) }), !exclude.contains(where: { $0.matches(url) }) else {
            return nil
        }
        var found: String?
        if let param {
            let value = URLComponents(string: text)?.queryItems?.first { $0.name == param }?.value
            found = base64 ? value.flatMap(Self.unbase64) : value
        } else if let path {
            let raw = Self.captured(path, in: PageURLPattern.encodedPath(url), template: template)
            found = raw.map { $0.removingPercentEncoding ?? $0 }
        }
        guard var target = found?.trimmingCharacters(in: .whitespaces), !target.isEmpty else { return nil }
        if let prependScheme, !target.contains("://") { target = prependScheme + "://" + target }
        guard let next = URL(string: target), ["http", "https"].contains(next.scheme?.lowercased() ?? ""),
              next.host?.isEmpty == false, next != url
        else { return nil }
        return next
    }

    private static func captured(_ pattern: NSRegularExpression, in path: String, template: String?) -> String? {
        let range = NSRange(path.startIndex..., in: path)
        guard let match = pattern.firstMatch(in: path, range: range), match.numberOfRanges > 1 else { return nil }
        let groups = (1..<match.numberOfRanges).map { i -> String in
            Range(match.range(at: i), in: path).map { String(path[$0]) } ?? ""
        }
        guard let template else { return groups.joined() }
        var out = template
        for (i, group) in groups.enumerated().reversed() { out = out.replacingOccurrences(of: "$\(i + 1)", with: group) }
        return out
    }

    private static func unbase64(_ value: String) -> String? {
        var text = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while text.count % 4 != 0 { text += "=" }
        return Data(base64Encoded: text).flatMap { String(data: $0, encoding: .utf8) }
    }
}

/// A match pattern as browser extensions write them: `<scheme>://<host><path>`,
/// where the scheme `*` is http or https, the host `*` or `*.example.com`
/// covers subdomains, and `*` in the path is any run of characters. The path
/// is matched against the path and query.
struct PageURLPattern {
    private let scheme: String
    private let host: String
    private let anySubdomain: Bool
    private let path: NSRegularExpression

    init(_ pattern: String) throws {
        guard let split = pattern.range(of: "://") else { throw PageError.fail("Not a match pattern: \(pattern)") }
        scheme = pattern[..<split.lowerBound].lowercased()
        let rest = pattern[split.upperBound...]
        let slash = rest.firstIndex(of: "/") ?? rest.endIndex
        var hostPart = rest[..<slash].lowercased()
        anySubdomain = hostPart.hasPrefix("*.")
        if anySubdomain { hostPart.removeFirst(2) }
        host = hostPart
        let glob = rest[slash...].isEmpty ? "/*" : String(rest[slash...])
        let regex = "^" + glob.split(separator: "*", omittingEmptySubsequences: false)
            .map { NSRegularExpression.escapedPattern(for: String($0)) }.joined(separator: ".*") + "$"
        path = try NSRegularExpression(pattern: regex)
    }

    func matches(_ url: URL) -> Bool {
        let urlScheme = url.scheme?.lowercased() ?? ""
        guard scheme == "*" ? ["http", "https"].contains(urlScheme) : scheme == urlScheme else { return false }
        let urlHost = url.host?.lowercased() ?? ""
        if host != "*" {
            guard urlHost == host || (anySubdomain && urlHost.hasSuffix("." + host)) else { return false }
        }
        var tail = Self.encodedPath(url)
        if let query = url.query { tail += "?" + query }
        return path.firstMatch(in: tail, range: NSRange(tail.startIndex..., in: tail)) != nil
    }

    /// The path as it is written in the address, "/" when it has none.
    static func encodedPath(_ url: URL) -> String {
        let path = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath ?? ""
        return path.isEmpty ? "/" : path
    }
}
