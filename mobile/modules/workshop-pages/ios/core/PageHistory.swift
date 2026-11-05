import Foundation
import WebKit

/// Each page's back and forward list, kept on disk by app and page id, so a
/// page the app opens again under the same id (its screen was rebuilt, the
/// shell document reloaded, the app relaunched) gets its history back instead
/// of starting a new one. Kept as WebKit's own `interactionState`, saved when
/// the page's address changes and when it closes; the newest `keep` pages of
/// each app are kept.
@MainActor
enum PageHistory {
    static let keep = 300
    static var root: URL = {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent(Bundle.main.bundleIdentifier ?? "workshop", isDirectory: true)
            .appendingPathComponent("page-history", isDirectory: true)
    }()

    static func file(_ app: String, _ id: String) -> URL {
        let name = id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? id
        return root.appendingPathComponent(app, isDirectory: true).appendingPathComponent(name + ".state")
    }

    static func save(_ page: PageView) {
        guard page.webView.url != nil, let data = page.webView.interactionState as? Data else { return }
        let target = file(page.app, page.id)
        try? FileManager.default.createDirectory(at: target.deletingLastPathComponent(),
                                                 withIntermediateDirectories: true)
        try? data.write(to: target, options: .atomic)
    }

    /// Puts `page`'s saved history back and loads the page it was on; answers
    /// that page's address, or nil when the page has no saved history.
    static func restore(_ page: PageView) -> URL? {
        guard let data = try? Data(contentsOf: file(page.app, page.id)) else { return nil }
        page.webView.interactionState = data
        let shown = page.webView.backForwardList.currentItem?.url
        page.appLoadHost = shown?.host ?? ""
        return shown
    }

    /// Whether two addresses name the same page: the fragment and a trailing
    /// slash aside.
    static func same(_ a: URL, _ b: URL) -> Bool {
        func key(_ u: URL) -> String {
            var c = URLComponents(url: u, resolvingAgainstBaseURL: false)
            c?.fragment = nil
            let s = c?.string ?? u.absoluteString
            return s.hasSuffix("/") ? String(s.dropLast()) : s
        }
        return key(a) == key(b)
    }

    /// Drops all but the newest `keep` saved histories of `app`.
    static func prune(_ app: String) {
        let folder = root.appendingPathComponent(app, isDirectory: true)
        let keys: [URLResourceKey] = [.contentModificationDateKey]
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: keys), files.count > keep else { return }
        let dated = files.map { ($0, (try? $0.resourceValues(forKeys: Set(keys)))?.contentModificationDate ?? .distantPast) }
        for (url, _) in dated.sorted(by: { $0.1 > $1.1 }).dropFirst(keep) {
            try? FileManager.default.removeItem(at: url)
        }
    }
}
