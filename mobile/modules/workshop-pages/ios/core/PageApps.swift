import Foundation

/// The app a page-view call comes from, and the keys that keep each app's
/// pages, data stores, rule lists and scripts apart.
///
/// An app id is the slug of the app the calling frame serves (Workshop frames
/// an app at `/base-apps/run/<slug>/proxy/...`); the shell's bridge client
/// sends it with every request (`WorkshopNative.forApp`). It is the caller's
/// claim, not something WebKit attests: every app frame shares the Workshop
/// origin, so one that scripts another frame can speak as that app. The ids
/// keep apps from reaching each other's data by accident or through ordinary
/// code, not a hostile app from reaching it on purpose.
enum PageApps {
    private static let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-".utf8)

    static func valid(_ app: String) -> Bool {
        let bytes = Array(app.utf8)
        guard let first = bytes.first, bytes.count <= 100, first != UInt8(ascii: "."), first != UInt8(ascii: "-")
        else { return false }
        return bytes.allSatisfy(allowed.contains)
    }

    /// `app` when it is an app id; refuses a request that carries none, which
    /// is what an app built with a kit from before per-app page views sends.
    static func check(_ app: String?) throws -> String {
        guard let app, !app.isEmpty else {
            throw PageError.fail("Page views need the calling app's id: rebuild the app with a current @workshop/ui")
        }
        guard valid(app) else { throw PageError.fail("Not an app id: \(app)") }
        return app
    }

    /// `name` within `app`. An app id has no "/", so the first one splits it.
    static func key(_ app: String, _ name: String) -> String {
        app + "/" + name
    }

    static func split(_ key: String) -> (app: String, name: String)? {
        guard let slash = key.firstIndex(of: "/") else { return nil }
        let app = String(key[..<slash])
        guard valid(app) else { return nil }
        return (app, String(key[key.index(after: slash)...]))
    }
}
