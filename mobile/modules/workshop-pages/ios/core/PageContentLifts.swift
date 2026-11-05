import Foundation
import WebKit

/// What an app lifts from its content rule lists without compiling them again:
/// whole sites (a host and its subdomains), whose pages carry none of the
/// app's lists, and lists that no page carries. WebKit decides this for each
/// navigation of a whole page, so a change costs nothing and shows from the
/// page's next load.
struct PageContentLifts {
    let sites: Set<String>
    let lists: Set<String>

    var isEmpty: Bool { sites.isEmpty && lists.isEmpty }

    init(_ object: [String: Any]) throws {
        let sites = try Self.names(object["sites"], "sites")
        let lists = try Self.names(object["lists"], "lists")
        for site in sites where site.contains("/") || site.contains(":") || site.contains(" ") {
            throw PageError.fail("Not a site: \(site)")
        }
        self.sites = Set(sites.map { $0.lowercased() })
        self.lists = Set(lists)
    }

    private static func names(_ value: Any?, _ key: String) throws -> [String] {
        guard let value else { return [] }
        guard let names = value as? [String], !names.contains(where: \.isEmpty) else {
            throw PageError.fail("\(key) must be a list of names")
        }
        return names
    }

    var object: [String: Any] { ["sites": sites.sorted(), "lists": lists.sorted()] }

    func covers(_ host: String?) -> Bool {
        guard var name = host?.lowercased() else { return false }
        if name.hasSuffix(".") { name.removeLast() }
        return sites.contains { name == $0 || name.hasSuffix("." + $0) }
    }

    private static let allSelector = NSSelectorFromString("_setContentBlockersEnabled:")
    private static let someSelector = NSSelectorFromString("_setContentRuleListsEnabled:exceptions:")

    /// Whether this WebKit can lift rules per navigation; the calls it takes
    /// are WebKit's own and not in its public interface.
    static var supported: Bool {
        let preferences = WKWebpagePreferences()
        return preferences.responds(to: allSelector) && preferences.responds(to: someSelector)
    }

    /// Turns every content rule list off for the navigation `preferences` belong to.
    static func liftAll(_ preferences: WKWebpagePreferences) {
        guard preferences.responds(to: allSelector) else { return }
        preferences.setValue(false, forKey: "contentBlockersEnabled")
    }

    /// Turns the lists stored as `identifiers` off for the navigation `preferences` belong to.
    static func lift(_ identifiers: Set<String>, _ preferences: WKWebpagePreferences) {
        guard preferences.responds(to: someSelector), let method = preferences.method(for: someSelector) else { return }
        typealias Call = @convention(c) (AnyObject, Selector, Bool, NSSet) -> Void
        unsafeBitCast(method, to: Call.self)(preferences, someSelector, true, identifiers as NSSet)
    }
}
