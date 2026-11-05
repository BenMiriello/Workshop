import Foundation

/// Groups hosts into sites (registrable domains) by a public suffix list the
/// app supplies, in the list's own syntax: "com", "co.uk", "*.ck", "!www.ck".
/// `accounts.example.co.uk` and `example.co.uk` are one site; two
/// `github.io` pages are two. With no list, a site is a host's last two labels.
struct PageSites {
    private let plain: Set<String>
    private let wild: Set<String>
    private let exceptions: Set<String>

    init(_ rules: [String] = []) {
        var plain = Set<String>(), wild = Set<String>(), exceptions = Set<String>()
        for raw in rules {
            let rule = raw.trimmingCharacters(in: .whitespaces).lowercased()
            if rule.isEmpty || rule.hasPrefix("//") { continue }
            if rule.hasPrefix("!") {
                exceptions.insert(String(rule.dropFirst()))
            } else if rule.hasPrefix("*.") {
                wild.insert(String(rule.dropFirst(2)))
            } else {
                plain.insert(rule)
            }
        }
        self.plain = plain
        self.wild = wild
        self.exceptions = exceptions
    }

    /// The site of `host`. An IP address, a single label and a host that is
    /// itself a public suffix are their own site.
    func site(_ host: String) -> String {
        var name = host.lowercased()
        if name.hasSuffix(".") { name.removeLast() }
        if name.contains(":") || name.hasPrefix("[") { return name }
        let labels = name.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
        if labels.count < 2 || labels.allSatisfy({ Int($0) != nil }) { return name }
        for i in labels.indices {
            let candidate = labels[i...].joined(separator: ".")
            if exceptions.contains(candidate) { return candidate }
            let parent = labels[(i + 1)...].joined(separator: ".")
            if plain.contains(candidate) || (i + 1 < labels.count && wild.contains(parent)) {
                return i == 0 ? name : labels[(i - 1)...].joined(separator: ".")
            }
        }
        return labels.suffix(2).joined(separator: ".")
    }

    func sameSite(_ a: String, _ b: String) -> Bool {
        site(a) == site(b)
    }
}
