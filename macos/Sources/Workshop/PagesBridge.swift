import Foundation
import WebKit

/// The bridge's `pages` capability on the Mac: each method of
/// `PAGES_METHODS` (shared/native-pages.ts) onto the shared page pool, with
/// arguments as the web sent them and results as the phone returns them.
/// Every method but `isSupported` acts for the app the request names.
@MainActor
enum PagesBridge {
    private static let topics = [
        "pageState": "pages:state",
        "pageError": "pages:error",
        "pageNewWindow": "pages:newWindow",
        "pageCommand": "pages:command",
        "pageMessage": "pages:message",
        "pageNavigationBlocked": "pages:navigationBlocked",
        "pageDownload": "pages:download",
    ]

    static func install(_ pool: PagePool, commands: PageCommands, on bridge: Bridge) {
        pool.emit = { [weak bridge] name, payload in
            bridge?.emit(topics[name] ?? name, payload.mapValues { $0 ?? NSNull() })
        }
        bridge.handle("pages", "isSupported") { _, _ in true }
        let on = { (method: String, handler: @escaping @MainActor ([Any], String) async throws -> Any?) in
            bridge.handle("pages", method) { a, app in try await handler(a, try PageApps.check(app)) }
        }

        on("open") { a, app in try pool.open(app: app, id: try a.string(0), try request(a.object(1))); return nil }
        on("close") { a, app in pool.close(app, try a.string(0)); return nil }
        on("closeAll") { _, app in pool.closeAll(app); return nil }
        on("setRect") { a, app in try pool.setRect(app, try a.string(0), try frame(a.object(1))); return nil }
        on("show") { a, app in try pool.setHidden(app, try a.string(0), false); return nil }
        on("hide") { a, app in
            let keepsPlaying = (try? a.object(1))?["keepPlaying"] as? Bool ?? false
            try pool.setHidden(app, try a.string(0), true, keepsPlaying: keepsPlaying)
            return nil
        }
        on("bringToFront") { a, app in try pool.bringToFront(app, try a.string(0)); return nil }
        on("adopt") { a, app in try pool.adopt(app, try a.string(0), as: try a.string(1)); return nil }
        on("navigate") { a, app in try pool.navigate(app, try a.string(0), try a.string(1)); return nil }
        on("back") { a, app in _ = try pool.page(app, try a.string(0)).webView.goBack(); return nil }
        on("forward") { a, app in _ = try pool.page(app, try a.string(0)).webView.goForward(); return nil }
        on("reload") { a, app in _ = try pool.page(app, try a.string(0)).webView.reload(); return nil }
        on("stop") { a, app in try pool.page(app, try a.string(0)).webView.stopLoading(); return nil }
        on("setContentMode") { a, app in
            try pool.setContentMode(app, try a.string(0), try a.string(1), userAgent: a.optionalString(2))
            return nil
        }
        on("setZoom") { a, app in
            try pool.setZoom(app, try a.string(0), try a.double(1))
            return nil
        }
        on("setCutouts") { a, app in
            let modal = (try? a.object(2))?["modal"] as? Bool ?? true
            try pool.setCutouts(app, try a.string(0), try a.objects(1).map(frame), modal: modal)
            return nil
        }
        // The web UI's own layers, cut out of every app's pages (shared/native-bridge.ts `shell`).
        bridge.handle("shell", "overlays") { a, _ in
            pool.setOverlays(try a.objects(0).map(frame), modal: (a.count > 1 ? a[1] as? Bool : nil) ?? false)
            return nil
        }
        on("setMuted") { a, app in
            try pool.setMuted(app, try a.string(0), try a.bool(1))
            return nil
        }
        on("run") { a, app in
            let page = try pool.page(app, try a.string(0))
            let script = try a.string(1)
            let isolated = try PageView.isolated((a.count > 2 ? a[2] as? [String: Any] : nil)?["world"] as? String)
            let json: String = await withCheckedContinuation { done in
                page.run(script, isolated: isolated) { done.resume(returning: $0) }
            }
            return (try? JSONSerialization.jsonObject(with: Data(json.utf8), options: .fragmentsAllowed))
                ?? ["ok": false, "error": "the page returned a result that is not JSON"]
        }
        on("snapshot") { a, app in
            let page = try pool.page(app, try a.string(0))
            return try await withCheckedThrowingContinuation { done in
                page.snapshot { image, message in
                    if let image { done.resume(returning: image) }
                    else { done.resume(throwing: PageError.fail(message ?? "The page could not be drawn")) }
                }
            }
        }
        on("contentRules.set") { a, app in
            let identifier = try a.string(0), json = try a.string(1)
            return try await withCheckedThrowingContinuation { done in
                pool.contentRules.set(app, identifier, json) { count, message in
                    if let message { done.resume(throwing: PageError.fail(message)) }
                    else { done.resume(returning: count.map { ["ruleCount": $0] } ?? [:]) }
                }
            }
        }
        on("contentRules.remove") { a, app in
            let identifier = try a.string(0)
            return try await withCheckedThrowingContinuation { done in
                pool.contentRules.remove(app, identifier) { message in
                    if let message { done.resume(throwing: PageError.fail(message)) }
                    else { done.resume(returning: NSNull()) }
                }
            }
        }
        on("contentRules.list") { _, app in
            await withCheckedContinuation { done in pool.contentRules.list(app) { done.resume(returning: $0) } }
        }
        on("contentRules.setLifted") { a, app in try pool.contentRules.setLifted(app, try a.object(0)); return nil }
        on("userScripts.set") { a, app in
            let d = try a.object(1)
            let script = PageUserScript.with(source: d["source"] as? String ?? "", world: d["world"] as? String,
                                             frames: d["frames"] as? String, at: d["at"] as? String)
            try pool.userScripts.set(app, try a.string(0), script)
            return nil
        }
        on("userScripts.remove") { a, app in pool.userScripts.remove(app, try a.string(0)); return nil }
        on("navigationRules.set") { a, app in try pool.navigation.set(app, try a.object(0)); return nil }
        on("navigationRules.remove") { _, app in pool.navigation.remove(app); return nil }
        on("commands.set") { a, app in
            guard let list = a.first as? [[String: Any]] else {
                throw PageError.fail("Argument 1 must be a list of commands")
            }
            try commands.set(app, list)
            return nil
        }
        on("cookies.export") { a, app in
            let profile = try a.string(0)
            return try await withCheckedThrowingContinuation { done in
                do { try pool.exportCookies(app, profile) { done.resume(returning: $0) } }
                catch { done.resume(throwing: error) }
            }
        }
        on("cookies.import") { a, app in
            let profile = try a.string(0)
            let list = (a.count > 1 ? a[1] as? [[String: Any]] : nil) ?? []
            return try await withCheckedThrowingContinuation { done in
                do { try pool.importCookies(app, profile, list) { done.resume(returning: $0) } }
                catch { done.resume(throwing: error) }
            }
        }
        on("profiles.list") { _, app in
            try await withCheckedThrowingContinuation { done in
                do { try pool.listProfiles(app) { done.resume(returning: $0) } }
                catch { done.resume(throwing: error) }
            }
        }
        on("profiles.remove") { a, app in
            let profile = try a.string(0)
            return try await withCheckedThrowingContinuation { (done: CheckedContinuation<Any?, Error>) in
                do {
                    try pool.removeProfile(app, profile) { message in
                        if let message { done.resume(throwing: PageError.fail(message)) }
                        else { done.resume(returning: nil) }
                    }
                } catch { done.resume(throwing: error) }
            }
        }
    }

    private static func frame(_ d: [String: Any]) -> PageFrame {
        PageFrame(x: d.number("x"), y: d.number("y"), width: d.number("width"), height: d.number("height"))
    }

    private static func request(_ d: [String: Any]) throws -> PageOpenRequest {
        let proxy = (d["proxy"] as? [String: Any]).map {
            PageProxySetting(host: $0["host"] as? String ?? "", port: Int($0.number("port")),
                             kind: $0["kind"] as? String ?? "socks5")
        }
        return PageOpenRequest(
            profile: d["profile"] as? String ?? "",
            url: d["url"] as? String ?? "",
            rect: frame(d["rect"] as? [String: Any] ?? [:]),
            proxy: proxy,
            trustCertDER: d["trustCertDER"] as? [String] ?? [],
            userAgent: d["userAgent"] as? String,
            contentMode: d["contentMode"] as? String
        )
    }
}

private extension Array where Element == Any {
    func string(_ i: Int) throws -> String {
        guard i < count, let value = self[i] as? String else {
            throw PageError.fail("Argument \(i + 1) must be a string")
        }
        return value
    }

    func bool(_ i: Int) throws -> Bool {
        guard i < count, let value = self[i] as? Bool else {
            throw PageError.fail("Argument \(i + 1) must be true or false")
        }
        return value
    }

    func double(_ i: Int) throws -> Double {
        guard i < count, let value = (self[i] as? NSNumber)?.doubleValue else {
            throw PageError.fail("Argument \(i + 1) must be a number")
        }
        return value
    }

    func optionalString(_ i: Int) -> String? {
        i < count ? self[i] as? String : nil
    }

    func objects(_ i: Int) throws -> [[String: Any]] {
        guard i < count, let value = self[i] as? [[String: Any]] else {
            throw PageError.fail("Argument \(i + 1) must be a list of objects")
        }
        return value
    }

    func object(_ i: Int) throws -> [String: Any] {
        guard i < count, let value = self[i] as? [String: Any] else {
            throw PageError.fail("Argument \(i + 1) must be an object")
        }
        return value
    }
}

private extension Dictionary where Key == String, Value == Any {
    func number(_ key: String) -> Double {
        (self[key] as? NSNumber)?.doubleValue ?? 0
    }
}
