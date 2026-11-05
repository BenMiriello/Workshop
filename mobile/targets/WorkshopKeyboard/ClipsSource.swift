import Foundation

struct Clip: Decodable {
    let id: Int
    let text: String
    let source_label: String?
}

enum ClipsError: Error {
    case signedOut
    case noApp
    case failed(String)

    var message: String {
        switch self {
        case .signedOut: return "Open Workshop and sign in to see your clips here."
        case .noApp: return "No running app keeps your copies."
        case .failed(let why): return "Could not load clips: \(why)"
        }
    }
}

/// Recent clips from the app that keeps copies: the running overlay app that
/// subscribes to "copied" (GET /apps/overlays), read through the runner's
/// proxy with the sign-in the Workshop app left in the app group.
enum ClipsSource {
    private struct Overlay: Decodable { let url: String; let events: [String] }
    private struct Overlays: Decodable { let overlays: [Overlay] }
    private struct Page: Decodable { let entries: [Clip] }

    private static let session = URLSession(configuration: .ephemeral)

    static func recent(limit: Int = 30) async throws -> [Clip] {
        guard let store = WorkshopGroup.defaults,
              let base = store.string(forKey: WorkshopGroup.apiBaseKey),
              let api = URL(string: base.hasSuffix("/") ? base : base + "/")
        else { throw ClipsError.signedOut }
        var headers: [[String: String]] = []
        if let token = store.string(forKey: WorkshopGroup.tokenKey) {
            headers.append(["Authorization": "Bearer \(token)"])
        }
        if let cookie = store.string(forKey: WorkshopGroup.cookieKey) {
            headers.append(["Cookie": cookie])
        }
        guard !headers.isEmpty else { throw ClipsError.signedOut }

        let list: Overlays = try await get(api.appendingPathComponent("apps/overlays"), headers)
        guard let app = list.overlays.first(where: { $0.events.contains("copied") }),
              let root = serviceRoot(app.url),
              let entries = URL(string: root + "api/entries", relativeTo: api),
              var parts = URLComponents(url: entries.absoluteURL, resolvingAgainstBaseURL: false)
        else { throw ClipsError.noApp }
        parts.queryItems = [URLQueryItem(name: "q", value: ""), URLQueryItem(name: "limit", value: String(limit))]
        guard let url = parts.url else { throw ClipsError.noApp }
        let page: Page = try await get(url, headers)
        return page.entries
    }

    /// "/api/base-apps/run/<slug>/proxy/<service>/<path>" up to the service.
    private static func serviceRoot(_ frame: String) -> String? {
        guard let proxy = frame.range(of: "/proxy/"),
              let end = frame[proxy.upperBound...].firstIndex(of: "/")
        else { return nil }
        return String(frame[...end])
    }

    /// GETs `url` with each sign-in in turn, moving on only when one is refused.
    private static func get<T: Decodable>(_ url: URL, _ headers: [[String: String]]) async throws -> T {
        var status = 0
        for sign in headers {
            var req = URLRequest(url: url, timeoutInterval: 15)
            req.httpShouldHandleCookies = false
            for (name, value) in sign { req.setValue(value, forHTTPHeaderField: name) }
            let data: Data
            let response: URLResponse
            do {
                (data, response) = try await session.data(for: req)
            } catch {
                throw ClipsError.failed(error.localizedDescription)
            }
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if (200..<300).contains(status) {
                do { return try JSONDecoder().decode(T.self, from: data) }
                catch { throw ClipsError.failed("unreadable answer") }
            }
            if status != 401 { break }
        }
        throw status == 401 ? ClipsError.signedOut : ClipsError.failed("status \(status)")
    }
}
