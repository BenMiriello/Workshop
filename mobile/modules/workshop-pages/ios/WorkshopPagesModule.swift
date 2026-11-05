import ExpoModulesCore
import Foundation

// Native page views for the shell: WKWebViews placed above its WebView, each
// in a profile's own website data store (iOS 17+). The JS side is
// mobile/native/pages.ts; the contract is shared/native-pages.ts. Every
// function but `isSupported` takes the calling app's id first (`PageApps`).
//
// Every function runs on the main queue, and does its work through
// `withPages`, which asserts that isolation for the main-actor manager.

public class WorkshopPagesModule: Module {
    public func definition() -> ModuleDefinition {
        Name("WorkshopPages")

        Events("pageState", "pageError", "pageNewWindow", "pageMessage", "pageNavigationBlocked", "pageDownload")

        // Stored content rule lists are back in force before any page opens.
        OnCreate {
            Task { @MainActor in _ = PagesManager.shared.pool }
        }

        Function("isSupported") { () -> Bool in
            if #available(iOS 17.0, *) {
                return true
            }
            return false
        }

        AsyncFunction("open") { (app: String, id: String, options: PageOpenOptions) in
            try self.withPages(app) { try $0.open(app: $1, id: id, options.request) }
        }.runOnQueue(.main)

        AsyncFunction("close") { (app: String, id: String) in
            try self.withPages(app) { $0.close($1, id) }
        }.runOnQueue(.main)

        // Without an app, every app's pages: a new shell document.
        AsyncFunction("closeAll") { (app: String?) in
            try self.withPages(nil) { pool, _ in
                pool.closeAll(try app.map { try PageApps.check($0) })
            }
        }.runOnQueue(.main)

        AsyncFunction("setRect") { (app: String, id: String, rect: PageRect) in
            try self.withPages(app) { try $0.setRect($1, id, rect.frame) }
        }.runOnQueue(.main)

        AsyncFunction("show") { (app: String, id: String) in
            try self.withPages(app) { try $0.setHidden($1, id, false) }
        }.runOnQueue(.main)

        AsyncFunction("hide") { (app: String, id: String, keepsPlaying: Bool?) in
            try self.withPages(app) { try $0.setHidden($1, id, true, keepsPlaying: keepsPlaying ?? false) }
        }.runOnQueue(.main)

        // The app whose frame the web UI shows (shared/native-bridge.ts `shell`).
        AsyncFunction("setForeground") { (app: String?) in
            try self.withPages(nil) { pool, _ in
                pool.setForeground(try app.map { try PageApps.check($0) })
            }
        }.runOnQueue(.main)

        AsyncFunction("bringToFront") { (app: String, id: String) in
            try self.withPages(app) { try $0.bringToFront($1, id) }
        }.runOnQueue(.main)

        AsyncFunction("adopt") { (app: String, popupId: String, id: String) in
            try self.withPages(app) { try $0.adopt($1, popupId, as: id) }
        }.runOnQueue(.main)

        AsyncFunction("navigate") { (app: String, id: String, url: String) in
            try self.withPages(app) { try $0.navigate($1, id, url) }
        }.runOnQueue(.main)

        AsyncFunction("back") { (app: String, id: String) in
            try self.withPages(app) { _ = try $0.page($1, id).webView.goBack() }
        }.runOnQueue(.main)

        AsyncFunction("forward") { (app: String, id: String) in
            try self.withPages(app) { _ = try $0.page($1, id).webView.goForward() }
        }.runOnQueue(.main)

        AsyncFunction("reload") { (app: String, id: String) in
            try self.withPages(app) { _ = try $0.page($1, id).webView.reload() }
        }.runOnQueue(.main)

        AsyncFunction("stop") { (app: String, id: String) in
            try self.withPages(app) { try $0.page($1, id).webView.stopLoading() }
        }.runOnQueue(.main)

        AsyncFunction("setContentMode") { (app: String, id: String, mode: String, userAgent: String?) in
            try self.withPages(app) { try $0.setContentMode($1, id, mode, userAgent: userAgent) }
        }.runOnQueue(.main)

        AsyncFunction("setZoom") { (app: String, id: String, factor: Double) in
            try self.withPages(app) { try $0.setZoom($1, id, factor) }
        }.runOnQueue(.main)

        AsyncFunction("setCutouts") { (app: String, id: String, rects: [PageRect], modal: Bool) in
            try self.withPages(app) { try $0.setCutouts($1, id, rects.map(\.frame), modal: modal) }
        }.runOnQueue(.main)

        AsyncFunction("setOverlays") { (rects: [PageRect], modal: Bool) in
            try self.withPages(nil) { pool, _ in pool.setOverlays(rects.map(\.frame), modal: modal) }
        }.runOnQueue(.main)

        AsyncFunction("setMuted") { (app: String, id: String, muted: Bool) in
            try self.withPages(app) { try $0.setMuted($1, id, muted) }
        }.runOnQueue(.main)

        AsyncFunction("run") { (app: String, id: String, script: String, options: PageRunOptions?, promise: Promise) in
            try self.withPages(app) { pool, app in
                let isolated = try PageView.isolated(options?.world)
                try pool.page(app, id).run(script, isolated: isolated) { json in promise.resolve(json) }
            }
        }.runOnQueue(.main)

        AsyncFunction("setUserScript") { (app: String, identifier: String, options: PageUserScriptOptions) in
            try self.withPages(app) { try $0.userScripts.set($1, identifier, options.script) }
        }.runOnQueue(.main)

        AsyncFunction("removeUserScript") { (app: String, identifier: String) in
            try self.withPages(app) { $0.userScripts.remove($1, identifier) }
        }.runOnQueue(.main)

        AsyncFunction("setNavigationRules") { (app: String, rules: [String: Any]) in
            try self.withPages(app) { try $0.navigation.set($1, rules) }
        }.runOnQueue(.main)

        AsyncFunction("removeNavigationRules") { (app: String) in
            try self.withPages(app) { $0.navigation.remove($1) }
        }.runOnQueue(.main)

        AsyncFunction("snapshot") { (app: String, id: String, promise: Promise) in
            try self.withPages(app) { pool, app in
                try pool.page(app, id).snapshot { image, message in
                    if let image {
                        promise.resolve(image)
                    } else {
                        promise.reject("SnapshotFailed", message ?? "The page could not be drawn")
                    }
                }
            }
        }.runOnQueue(.main)

        AsyncFunction("setContentRules") { (app: String, identifier: String, json: String, promise: Promise) in
            try self.withPages(app) { pool, app in
                pool.contentRules.set(app, identifier, json) { count, message in
                    if let message {
                        promise.reject("CompileFailed", message)
                    } else {
                        promise.resolve(count.map { ["ruleCount": $0] } ?? [:])
                    }
                }
            }
        }.runOnQueue(.main)

        AsyncFunction("removeContentRules") { (app: String, identifier: String, promise: Promise) in
            try self.withPages(app) { pool, app in
                pool.contentRules.remove(app, identifier) { message in
                    if let message {
                        promise.reject("RemoveFailed", message)
                    } else {
                        promise.resolve(nil)
                    }
                }
            }
        }.runOnQueue(.main)

        AsyncFunction("listContentRules") { (app: String, promise: Promise) in
            try self.withPages(app) { pool, app in
                pool.contentRules.list(app) { names in promise.resolve(names) }
            }
        }.runOnQueue(.main)

        AsyncFunction("setLiftedContentRules") { (app: String, lifted: [String: Any]) in
            try self.withPages(app) { try $0.contentRules.setLifted($1, lifted) }
        }.runOnQueue(.main)

        AsyncFunction("exportCookies") { (app: String, profile: String, promise: Promise) in
            try self.withPages(app) { pool, app in
                try pool.exportCookies(app, profile) { cookies in promise.resolve(cookies) }
            }
        }.runOnQueue(.main)

        AsyncFunction("importCookies") { (app: String, profile: String, list: [[String: Any]], promise: Promise) in
            try self.withPages(app) { pool, app in
                try pool.importCookies(app, profile, list) { count in promise.resolve(count) }
            }
        }.runOnQueue(.main)

        AsyncFunction("listProfiles") { (app: String, promise: Promise) in
            try self.withPages(app) { pool, app in
                try pool.listProfiles(app) { names in promise.resolve(names) }
            }
        }.runOnQueue(.main)

        AsyncFunction("removeProfile") { (app: String, profile: String, promise: Promise) in
            try self.withPages(app) { pool, app in
                try pool.removeProfile(app, profile) { message in
                    if let message {
                        promise.reject("RemoveFailed", message)
                    } else {
                        promise.resolve(nil)
                    }
                }
            }
        }.runOnQueue(.main)
    }

    /// Runs `body` on the main actor with the shared page pool and the
    /// calling app (checked, or "" when `app` is nil), its refusals handed on
    /// as Expo exceptions. Every caller is declared `.runOnQueue(.main)`,
    /// which is what makes the assertion hold.
    private func withPages(_ app: String?, _ body: @MainActor (PagePool, String) throws -> Void) throws {
        try MainActor.assumeIsolated {
            let pool = PagesManager.shared.pool
            if pool.emit == nil {
                pool.emit = { [weak self] name, payload in
                    self?.sendEvent(name, payload)
                }
            }
            do {
                let caller = try app.map { try PageApps.check($0) } ?? ""
                try body(pool, caller)
            } catch let error as PageError {
                throw Exception(name: error.name, description: error.message)
            }
        }
    }
}
