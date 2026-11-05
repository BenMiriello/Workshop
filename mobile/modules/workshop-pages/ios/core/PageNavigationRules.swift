import Foundation

/// Where an app's page views may go, as the app declares it
/// (`pages.navigationRules.set`, shared/native-pages.ts). Every rule is off
/// unless the app turns it on.
struct PageNavigationRules {
    /// Pop-ups open only within `tapWindow` of a tap, click or key, or while
    /// WebKit's own flag says the person started them; the rest are reported.
    var popupsNeedTap = false
    /// A jump of the whole page to another site, started by the page's script
    /// or by a frame from another site, is cancelled unless the person tapped
    /// within `tapWindow` before it.
    var blockUntappedJumps = false
    var tapWindow: TimeInterval = 5
    var sites = PageSites()
    var hops: [PageHopRule] = []
    var strips: [PageStripRule] = []
    /// A response that is a file is cancelled and handed to the app
    /// ("pageDownload", PageDownload.swift).
    var downloadsToApp = false

    private static let keys: Set<String> = [
        "popupsNeedTap", "blockUntappedJumps", "tapWindowMs", "publicSuffixes", "hops", "stripParams",
        "downloadsToApp",
    ]

    init() {}

    init(_ d: [String: Any]) throws {
        if let unknown = d.keys.first(where: { !Self.keys.contains($0) }) {
            throw PageError.fail("Not a navigation rule: \(unknown)")
        }
        popupsNeedTap = d["popupsNeedTap"] as? Bool ?? false
        blockUntappedJumps = d["blockUntappedJumps"] as? Bool ?? false
        downloadsToApp = d["downloadsToApp"] as? Bool ?? false
        if let ms = d["tapWindowMs"] as? NSNumber { tapWindow = max(0, ms.doubleValue / 1000) }
        sites = PageSites(try Self.strings(d["publicSuffixes"] ?? [], "publicSuffixes"))
        hops = try Self.objects(d["hops"], "hops").map(PageHopRule.init)
        strips = try Self.objects(d["stripParams"], "stripParams").map(PageStripRule.init)
    }

    static func strings(_ value: Any?, _ what: String) throws -> [String] {
        guard let list = value as? [Any], list.allSatisfy({ $0 is String }) else {
            throw PageError.fail("\(what) must be a list of strings")
        }
        return list.compactMap { $0 as? String }
    }

    private static func objects(_ value: Any?, _ what: String) throws -> [[String: Any]] {
        guard let value else { return [] }
        guard let list = value as? [[String: Any]] else { throw PageError.fail("\(what) must be a list of objects") }
        return list
    }
}

/// One navigation as WebKit reported it, with what the page view knows.
struct PageNavigationFacts {
    enum Kind { case link, form, backForward, reload, other }

    var kind: Kind
    /// It would replace the page's main frame (not a frame, not a new window).
    var targetsMain: Bool
    var method: String
    var url: URL?
    /// The frame that started it: the main frame or a frame within it, and
    /// its origin's host ("" when it has none).
    var sourceIsMain: Bool
    var sourceHost: String
    /// The host of the page now shown; nil before anything was shown.
    var pageHost: String?
    /// A server redirect of a navigation already allowed.
    var redirect: Bool
    /// WebKit runs it as part of a tap, click or key.
    var userInitiated: Bool
    /// The person touched, clicked or typed in the page within the tap window.
    var tappedRecently: Bool
    /// The app loaded it (`open`, `navigate`), or the shell did for a rule.
    var fromApp: Bool
}

enum PageNavigationVerdict: Equatable {
    case allow
    /// Cancelled; `reason` is "script" or "frame".
    case block(reason: String)
    /// Cancelled for another address: a skipped hop or a cleaned one.
    case load(URL)
}

extension PageNavigationRules {
    func judge(_ f: PageNavigationFacts) -> PageNavigationVerdict {
        guard f.targetsMain, let url = f.url, ["http", "https"].contains(url.scheme?.lowercased() ?? "") else {
            return .allow
        }
        if f.kind == .backForward || f.kind == .reload || f.kind == .form { return .allow }
        if blockUntappedJumps, !f.fromApp, !f.redirect, !f.userInitiated, !f.tappedRecently,
           let reason = jumpReason(f, to: url.host ?? "") {
            return .block(reason: reason)
        }
        guard f.method.uppercased() == "GET" else { return .allow }
        let host = url.host ?? ""
        for hop in hops {
            if let next = hop.destination(url), !sites.sameSite(next.host ?? "", host) { return .load(next) }
        }
        if let clean = PageStripRule.stripped(url, by: strips) { return .load(clean) }
        return .allow
    }

    /// Why a jump to `host` counts as one to cancel; nil when it does not.
    private func jumpReason(_ f: PageNavigationFacts, to host: String) -> String? {
        guard let page = f.pageHost, !page.isEmpty else { return nil }
        if !f.sourceIsMain, f.sourceHost.isEmpty || !sites.sameSite(f.sourceHost, page) { return "frame" }
        let from = f.sourceIsMain && !f.sourceHost.isEmpty ? f.sourceHost : page
        return sites.sameSite(host, from) ? nil : "script"
    }
}
