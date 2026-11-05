import Foundation
import Security
import UIKit
import WebKit

/// Copies made on this iPhone, saved to Workshop as the "copied" event
/// (POST <api>/app-events/copied), as the Mac app does for copies made in its
/// other apps. iOS gives no copy events and no background reads, so the shell
/// looks once each time it comes to the front, while the watch is
/// "everything", and the Save Clipboard to Workshop action looks on request.
/// Copies made inside Workshop are reported by the page; the shell marks the
/// pasteboard seen whenever it leaves the front, so those are not sent twice.
public enum ClipboardCapture {
    static let modes = ["workshop", "everything"]
    private static let modeKey = "clipboardWatch"
    private static let seenKey = "clipboardSeenChange"
    private static let apiKey = "workshopApiBase"
    private static let maxText = 100_000
    private static let sessionCookie = "workshop_session"
    private static let clientHeader = "x-workshop-client"
    /// Where expo-secure-store keeps the device token the page hands down
    /// (native/auth.ts): its default service, with and without its suffix.
    private static let tokenServices = ["app:no-auth", "app"]
    private static let tokenAccount = "workshop.ws-device-token"

    static var mode: String {
        let stored = UserDefaults.standard.string(forKey: modeKey) ?? ""
        return modes.contains(stored) ? stored : "workshop"
    }

    static func setMode(_ mode: String) throws -> String {
        guard modes.contains(mode) else { throw CaptureError.badMode }
        UserDefaults.standard.set(mode, forKey: modeKey)
        markSeen()
        return mode
    }

    /// The origin's API base, e.g. "https://host/api", kept for the action,
    /// which can run before the web UI has.
    static func configure(apiBase: String) {
        UserDefaults.standard.set(apiBase, forKey: apiKey)
    }

    /// Whatever is on the pasteboard now counts as seen. Reading the change
    /// count asks nothing of the user.
    public static func markSeen() {
        UserDefaults.standard.set(UIPasteboard.general.changeCount, forKey: seenKey)
    }

    /// Saves the copy made since the last look, if the watch is on and it is
    /// text. Only reading the text shows the system's paste prompt, so an
    /// unchanged pasteboard or one without text never prompts.
    @MainActor
    static func captureNew() async throws -> Bool {
        guard mode == "everything" else { return false }
        let board = UIPasteboard.general
        guard board.changeCount != UserDefaults.standard.integer(forKey: seenKey) else { return false }
        markSeen()
        guard board.hasStrings, let text = board.string, !isBlank(text) else { return false }
        try await save(text)
        return true
    }

    /// Reports `text` as copied. Signed in with the web UI's session cookie,
    /// or the device token when the session has none or is refused.
    @MainActor
    public static func save(_ text: String) async throws {
        guard !isBlank(text) else { throw CaptureError.empty }
        guard let base = UserDefaults.standard.string(forKey: apiKey),
              let url = URL(string: base + "/app-events/copied")
        else { throw CaptureError.notConfigured }
        let body: [String: Any] = [
            "text": String(String.UnicodeScalarView(text.unicodeScalars.prefix(maxText))),
            "source": ["path": ""],
        ]
        let data = try JSONSerialization.data(withJSONObject: body)
        var credentials: [[String: String]] = []
        let jar = await cookies(for: url)
        if jar.contains(where: { $0.name == sessionCookie }) {
            var headers = HTTPCookie.requestHeaderFields(with: jar)
            headers[clientHeader] = "1"
            credentials.append(headers)
        }
        if let token = deviceToken() {
            credentials.append(["Authorization": "Bearer \(token)"])
        }
        guard !credentials.isEmpty else { throw CaptureError.signedOut }
        var status = 0
        for headers in credentials {
            var req = URLRequest(url: url, timeoutInterval: 15)
            req.httpMethod = "POST"
            req.httpShouldHandleCookies = false
            req.httpBody = data
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            for (name, value) in headers { req.setValue(value, forHTTPHeaderField: name) }
            let (_, response) = try await URLSession.shared.data(for: req)
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if (200..<300).contains(status) { return }
            if status != 401 { break }
        }
        throw status == 401 ? CaptureError.signedOut : CaptureError.refused(status)
    }

    /// Leaves the API base and the sign-in (device token, session cookie) in
    /// the app group for the keyboard. Signed out, they are removed.
    @MainActor
    static func share() async {
        guard let store = WorkshopGroup.defaults else { return }
        let base = UserDefaults.standard.string(forKey: apiKey)
        var cookie: String?
        if let base, let url = URL(string: base) {
            let session = await cookies(for: url).filter { $0.name == sessionCookie }
            cookie = session.isEmpty ? nil : HTTPCookie.requestHeaderFields(with: session)["Cookie"]
        }
        store.set(base, forKey: WorkshopGroup.apiBaseKey)
        store.set(deviceToken(), forKey: WorkshopGroup.tokenKey)
        store.set(cookie, forKey: WorkshopGroup.cookieKey)
    }

    private static func isBlank(_ text: String) -> Bool {
        text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The web view's cookies a browser would send to `url`.
    @MainActor
    private static func cookies(for url: URL) async -> [HTTPCookie] {
        guard let host = url.host?.lowercased() else { return [] }
        let all = await WKWebsiteDataStore.default().httpCookieStore.allCookies()
        return all.filter { c in
            let domain = c.domain.lowercased()
            let bare = domain.hasPrefix(".") ? String(domain.dropFirst()) : domain
            let live = c.expiresDate.map { $0 > Date() } ?? true
            let secureOK = !c.isSecure || url.scheme == "https"
            return live && secureOK && (host == bare || host.hasSuffix("." + bare))
        }
    }

    private static func deviceToken() -> String? {
        let account = Data(tokenAccount.utf8)
        for service in tokenServices {
            let query: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: service,
                kSecAttrGeneric as String: account,
                kSecAttrAccount as String: account,
                kSecMatchLimit as String: kSecMatchLimitOne,
                kSecReturnData as String: true,
            ]
            var item: CFTypeRef?
            if SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
               let data = item as? Data, let token = String(data: data, encoding: .utf8), !token.isEmpty {
                return token
            }
        }
        return nil
    }
}

public enum CaptureError: LocalizedError {
    case badMode, empty, notConfigured, signedOut, refused(Int)

    public var errorDescription: String? {
        switch self {
        case .badMode: return "clipboard.watch.set takes \"workshop\" or \"everything\""
        case .empty: return "There is no text to save."
        case .notConfigured: return "Open Workshop once first, so it knows where to save."
        case .signedOut: return "Sign in to Workshop in the app first."
        case .refused(let status): return "Workshop did not save it (status \(status))."
        }
    }
}
