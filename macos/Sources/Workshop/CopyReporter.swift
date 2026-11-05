import Foundation
import WebKit

/// Reports a copy made in another app as Workshop's `copied` event, the same
/// POST the web UI makes for its own copies (frontend CopyRelay.tsx), signed in
/// with the web view's session cookie, so it works with no page open. Signed
/// out (no session cookie, or the post answers 401) the copy is dropped:
/// nothing is kept to send later.
@MainActor
final class CopyReporter {
    static let sessionCookie = "workshop_session"
    /// The backend's limits (services/app_events.py `Copied`, `Source`),
    /// counted in code points as it counts them.
    static let maxText = 100_000
    static let maxApp = 200

    private let origin: URL
    private let endpoint: URL
    private let session: URLSession

    init(origin: URL) {
        self.origin = origin
        endpoint = URL(string: "/api/app-events/copied", relativeTo: origin)?.absoluteURL ?? origin
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.httpCookieAcceptPolicy = .never
        config.timeoutIntervalForRequest = 15
        session = URLSession(configuration: config)
    }

    func report(_ text: String, app: String) {
        Task { await send(text, app: app) }
    }

    private func send(_ text: String, app: String) async {
        let cookies = await WKWebsiteDataStore.default().httpCookieStore.allCookies().filter(sentTo)
        guard cookies.contains(where: { $0.name == Self.sessionCookie }) else { return }
        var source: [String: Any] = ["path": ""]
        if !app.isEmpty { source["app"] = Self.prefix(app, Self.maxApp) }
        let body: [String: Any] = ["text": Self.prefix(text, Self.maxText), "source": source]
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        for (name, value) in HTTPCookie.requestHeaderFields(with: cookies) {
            request.setValue(value, forHTTPHeaderField: name)
        }
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // The backend's CSRF guard on cookie-signed mutations (deps_user.py).
        request.setValue("1", forHTTPHeaderField: "X-Workshop-Client")
        _ = try? await session.data(for: request)
    }

    /// Whether the browser would send `cookie` to the Workshop's origin.
    private func sentTo(_ cookie: HTTPCookie) -> Bool {
        guard let host = origin.host?.lowercased() else { return false }
        if cookie.isSecure && origin.scheme != "https" { return false }
        let domain = cookie.domain.lowercased()
        let bare = domain.hasPrefix(".") ? String(domain.dropFirst()) : domain
        return host == bare || host.hasSuffix("." + bare)
    }

    private static func prefix(_ text: String, _ count: Int) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.prefix(count)))
    }
}
