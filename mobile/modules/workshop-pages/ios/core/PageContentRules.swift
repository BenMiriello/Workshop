import Foundation
import WebKit

/// Content rule lists (WebKit's content blocker format), each app's applied to
/// every page view of that app. Compiled lists live in the default
/// WKContentRuleListStore under the prefix and `PageApps.key(app, identifier)`,
/// so they survive relaunches; `loadStored` puts them back in force at start,
/// before the web side sets them again. Lists stored before rules were per app
/// have no app in their name and are deleted there: the one app that made them
/// sets them again when it finds them missing.
///
/// An app can also lift sites and lists from its lists (`PageContentLifts`)
/// instead of compiling them again; those are kept in the app's defaults.
@MainActor
final class PageContentRules {
    private static let prefix = "workshop.pages.rules."
    private static let liftsKey = "workshop.pages.contentRulesLifted"

    /// The controllers of an app's open page views, supplied by the pool.
    var controllers: (String) -> [WKUserContentController] = { _ in [] }
    /// In force, by `PageApps.key(app, identifier)`.
    private var inForce: [String: WKContentRuleList] = [:]
    /// Bumped by each set and remove of a key, so a slower earlier result
    /// never lands after a later one.
    private var generations: [String: Int] = [:]
    /// The generation of each key's latest remove; when that is also
    /// its latest operation, a compile finishing late is taken out again.
    private var removals: [String: Int] = [:]
    private var loaded = false
    private var lifts: [String: PageContentLifts] = [:]

    init() {
        let kept = UserDefaults.standard.dictionary(forKey: Self.liftsKey) as? [String: [String: Any]] ?? [:]
        for (app, object) in kept {
            if let lifted = try? PageContentLifts(object), !lifted.isEmpty { lifts[app] = lifted }
        }
    }

    private var store: WKContentRuleListStore { WKContentRuleListStore.default() }

    func loadStored() {
        guard !loaded else { return }
        loaded = true
        store.getAvailableContentRuleListIdentifiers { ids in
            Self.onMain { self.lookUp(ids ?? []) }
        }
    }

    private func lookUp(_ ids: [String]) {
        for stored in ids where stored.hasPrefix(Self.prefix) {
            let key = String(stored.dropFirst(Self.prefix.count))
            guard PageApps.split(key) != nil else {
                store.removeContentRuleList(forIdentifier: stored) { _ in }
                continue
            }
            store.lookUpContentRuleList(forIdentifier: stored) { list, _ in
                Self.onMain {
                    guard let list, self.generations[key] == nil else { return }
                    self.install(key, list)
                }
            }
        }
    }

    /// The lists in force for `app`'s pages.
    func lists(for app: String) -> [WKContentRuleList] {
        inForce.filter { PageApps.split($0.key)?.app == app }.map(\.value)
    }

    func set(_ app: String, _ identifier: String, _ json: String, done: @escaping (Int?, String?) -> Void) {
        guard !identifier.isEmpty else { return done(nil, "A content rule list needs an identifier") }
        let key = PageApps.key(app, identifier)
        let generation = bump(key)
        store.compileContentRuleList(
            forIdentifier: Self.prefix + key,
            encodedContentRuleList: json
        ) { list, error in
            Self.onMain {
                guard let list else { return done(nil, Self.message(error)) }
                if self.generations[key] == generation {
                    self.install(key, list)
                } else if self.removals[key] == self.generations[key] {
                    self.store.removeContentRuleList(forIdentifier: Self.prefix + key) { _ in }
                }
                done(Self.ruleCount(json), nil)
            }
        }
    }

    /// Replaces what `app` lifts; refuses what does not read, or what this
    /// WebKit cannot lift, keeping the old. Open pages follow at their next load.
    func setLifted(_ app: String, _ object: [String: Any]) throws {
        let next = try PageContentLifts(object)
        guard next.isEmpty || PageContentLifts.supported else {
            throw PageError.fail("This WebKit cannot lift content rules per page")
        }
        lifts[app] = next.isEmpty ? nil : next
        UserDefaults.standard.set(lifts.mapValues(\.object), forKey: Self.liftsKey)
    }

    /// Lifts from the navigation `action` what `app` lifted, for the whole
    /// page it loads. WebKit keeps the answer it had when the load began, so
    /// `covered` holds whether that load lifted the app's sites, and a redirect
    /// to the other side of that answers false: the page has to start again.
    func lift(
        _ app: String, _ action: WKNavigationAction, _ preferences: WKWebpagePreferences,
        covered: inout Bool, redirect: Bool
    ) -> Bool {
        guard action.targetFrame?.isMainFrame != false else { return true }
        let lifted = lifts[app]
        let covers = lifted?.covers(action.request.url?.host) ?? false
        if redirect && covers != covered { return false }
        covered = covers
        if covers {
            PageContentLifts.liftAll(preferences)
        } else if let lifted, !lifted.lists.isEmpty {
            PageContentLifts.lift(Set(lifted.lists.map { Self.prefix + PageApps.key(app, $0) }), preferences)
        }
        return true
    }

    func remove(_ app: String, _ identifier: String, done: @escaping (String?) -> Void) {
        let key = PageApps.key(app, identifier)
        removals[key] = bump(key)
        if let old = inForce.removeValue(forKey: key) {
            controllers(app).forEach { $0.remove(old) }
        }
        let stored = Self.prefix + key
        store.getAvailableContentRuleListIdentifiers { ids in
            Self.onMain {
                guard (ids ?? []).contains(stored) else { return done(nil) }
                self.store.removeContentRuleList(forIdentifier: stored) { error in
                    Self.onMain { done(error.map { Self.message($0) }) }
                }
            }
        }
    }

    /// Identifiers of `app`'s stored lists.
    func list(_ app: String, done: @escaping ([String]) -> Void) {
        let own = Self.prefix + PageApps.key(app, "")
        store.getAvailableContentRuleListIdentifiers { ids in
            Self.onMain {
                let names = (ids ?? []).filter { $0.hasPrefix(own) }.map { String($0.dropFirst(own.count)) }
                done(names.sorted())
            }
        }
    }

    /// Puts `list` in force on every open page of the key's app, replacing an
    /// earlier list of the same key.
    private func install(_ key: String, _ list: WKContentRuleList) {
        guard let app = PageApps.split(key)?.app else { return }
        let old = inForce[key]
        inForce[key] = list
        for controller in controllers(app) {
            if let old { controller.remove(old) }
            controller.add(list)
        }
    }

    @discardableResult
    private func bump(_ key: String) -> Int {
        let next = (generations[key] ?? 0) + 1
        generations[key] = next
        return next
    }

    nonisolated private static func onMain(_ body: @escaping @MainActor () -> Void) {
        DispatchQueue.main.async { MainActor.assumeIsolated { body() } }
    }

    /// WebKit puts the compiler's own message under the help-anchor key and
    /// leaves only a generic description.
    nonisolated private static func message(_ error: Error?) -> String {
        guard let ns = error as NSError? else { return "The rule list could not be compiled" }
        return (ns.userInfo[NSHelpAnchorErrorKey] as? String) ?? ns.localizedDescription
    }

    nonisolated private static func ruleCount(_ json: String) -> Int? {
        guard let data = json.data(using: .utf8),
              let rules = try? JSONSerialization.jsonObject(with: data) as? [Any]
        else { return nil }
        return rules.count
    }
}
