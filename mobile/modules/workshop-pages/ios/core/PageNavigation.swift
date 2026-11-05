import Foundation
import WebKit

/// Each app's navigation rules, read by every page view of that app on each
/// navigation, so a change reaches pages already open. They are kept in the
/// app's defaults by app, so pages opened at the next launch follow them
/// before the web side sets them again.
@MainActor
final class PageNavigation {
    private static let defaultsKey = "workshop.pages.navigationRules"

    /// As the app set them, by app, for keeping.
    private var stored: [String: Data] = [:]
    private var compiled: [String: PageNavigationRules] = [:]

    init() {
        if let data = UserDefaults.standard.dictionary(forKey: Self.defaultsKey) as? [String: Data] {
            stored = data
        }
    }

    /// `app`'s rules; none when it set none, or when what it set last no
    /// longer reads (it is dropped, and the app sets it again).
    func rules(_ app: String) -> PageNavigationRules? {
        if let rules = compiled[app] { return rules }
        guard let data = stored[app] else { return nil }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rules = try? PageNavigationRules(object)
        else {
            stored.removeValue(forKey: app)
            save()
            return nil
        }
        compiled[app] = rules
        return rules
    }

    /// Replaces `app`'s rules; refuses rules that do not read, keeping the old.
    func set(_ app: String, _ object: [String: Any]) throws {
        let rules = try PageNavigationRules(object)
        guard JSONSerialization.isValidJSONObject(object) else { throw PageError.fail("Navigation rules must be JSON") }
        stored[app] = try JSONSerialization.data(withJSONObject: object)
        compiled[app] = rules
        save()
    }

    func remove(_ app: String) {
        guard stored.removeValue(forKey: app) != nil || compiled.removeValue(forKey: app) != nil else { return }
        compiled.removeValue(forKey: app)
        save()
    }

    private func save() {
        UserDefaults.standard.set(stored, forKey: Self.defaultsKey)
    }
}
