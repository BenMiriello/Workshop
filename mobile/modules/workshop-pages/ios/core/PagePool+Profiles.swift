import Foundation
import WebKit

/// Each app's profile data stores, their cookies, and the stores kept on the
/// device. An app reaches only the stores of its own profiles.
extension PagePool {
    @available(iOS 17.0, *)
    func dataStore(_ app: String, _ profile: String) -> WKWebsiteDataStore {
        let key = PageApps.key(app, profile)
        if let store = stores[key] { return store }
        let store = WKWebsiteDataStore(forIdentifier: PageProfiles.identifier(app: app, name: profile))
        stores[key] = store
        return store
    }

    func exportCookies(_ app: String, _ profile: String, done: @escaping ([[String: Any]]) -> Void) throws {
        guard #available(iOS 17.0, *) else { throw PageError.unsupported }
        dataStore(app, profile).httpCookieStore.getAllCookies { cookies in
            done(cookies.map(PageCookies.export))
        }
    }

    func importCookies(
        _ app: String, _ profile: String, _ list: [[String: Any]], done: @escaping (Int) -> Void
    ) throws {
        guard #available(iOS 17.0, *) else { throw PageError.unsupported }
        let cookies = list.compactMap(PageCookies.cookie(from:))
        let cookieStore = dataStore(app, profile).httpCookieStore
        let group = DispatchGroup()
        for cookie in cookies {
            group.enter()
            cookieStore.setCookie(cookie) { group.leave() }
        }
        let count = cookies.count
        group.notify(queue: .main) { done(count) }
    }

    func listProfiles(_ app: String, done: @escaping ([String]) -> Void) throws {
        guard #available(iOS 17.0, *) else { throw PageError.unsupported }
        WKWebsiteDataStore.fetchAllDataStoreIdentifiers { ids in
            done(PageProfiles.list(app: app, existing: ids))
        }
    }

    func removeProfile(_ app: String, _ profile: String, done: @escaping (String?) -> Void) throws {
        guard #available(iOS 17.0, *) else { throw PageError.unsupported }
        if pages.values.contains(where: { $0.app == app && $0.profile == profile }) {
            throw PageError.fail("Profile \(profile) is used by an open page")
        }
        stores.removeValue(forKey: PageApps.key(app, profile))
        WKWebsiteDataStore.fetchAllDataStoreIdentifiers { ids in
            guard let id = PageProfiles.resolve(app: app, profile, existing: ids) else { return done(nil) }
            guard ids.contains(id) else {
                PageProfiles.forget(app: app, id)
                return done(nil)
            }
            WKWebsiteDataStore.remove(forIdentifier: id) { error in
                if error == nil { PageProfiles.forget(app: app, id) }
                done(error?.localizedDescription)
            }
        }
    }
}
