import Foundation
import WebKit

/// One script the host installs in every page view.
struct PageUserScript: Codable, Equatable {
    var source: String
    /// "page" (the page's own world) or "isolated" (`PageUserScripts.world`).
    var world: String
    /// "all" frames or the "main" frame only.
    var frames: String
    /// "start" (document start) or "end" (document end).
    var at: String

    /// A script from the bridge's options, with the contract's defaults.
    static func with(source: String, world: String?, frames: String?, at: String?) -> PageUserScript {
        PageUserScript(source: source, world: world ?? "isolated", frames: frames ?? "all", at: at ?? "start")
    }

    func validated() throws -> PageUserScript {
        guard !source.isEmpty else { throw PageError.fail("A user script needs a source") }
        guard ["page", "isolated"].contains(world) else { throw PageError.fail("A script's world is page or isolated") }
        guard ["all", "main"].contains(frames) else { throw PageError.fail("A script's frames are all or main") }
        guard ["start", "end"].contains(at) else { throw PageError.fail("A script runs at start or end") }
        return self
    }

    @MainActor var contentWorld: WKContentWorld { world == "page" ? .page : PageUserScripts.world }

    @MainActor var webKitScript: WKUserScript {
        WKUserScript(source: source, injectionTime: at == "start" ? .atDocumentStart : .atDocumentEnd,
                     forMainFrameOnly: frames == "main", in: contentWorld)
    }
}

/// Scripts installed at document load in every page view of the app that set
/// them, open or opened later, and the one message channel back from them.
/// They are kept in the app's defaults by app, so pages opened at the next
/// launch have them before the web side sets them again. A new or changed
/// script takes effect from each page's next document. Scripts stored before
/// they were per app are dropped at start; the one app that set them sets
/// them again whenever its pages start.
@MainActor
final class PageUserScripts {
    /// The isolated world: shares the page's DOM, not its JavaScript.
    static let world = WKContentWorld.world(name: "workshop.pages")
    /// Scripts in either world post with
    /// `window.webkit.messageHandlers.workshop.postMessage(value)`.
    static let messageName = "workshop"
    private static let defaultsKey = "workshop.pages.appUserScripts"
    private static let legacyKey = "workshop.pages.userScripts"

    /// The controllers of an app's open page views, supplied by the pool.
    var controllers: (String) -> [WKUserContentController] = { _ in [] }
    /// By app, then identifier.
    private(set) var scripts: [String: [String: PageUserScript]] = [:]

    init() {
        UserDefaults.standard.removeObject(forKey: Self.legacyKey)
        if let data = UserDefaults.standard.data(forKey: Self.defaultsKey),
           let stored = try? JSONDecoder().decode([String: [String: PageUserScript]].self, from: data) {
            scripts = stored
        }
    }

    func set(_ app: String, _ identifier: String, _ script: PageUserScript) throws {
        guard !identifier.isEmpty else { throw PageError.fail("A user script needs an identifier") }
        let valid = try script.validated()
        guard scripts[app]?[identifier] != valid else { return }
        scripts[app, default: [:]][identifier] = valid
        changed(app)
    }

    func remove(_ app: String, _ identifier: String) {
        guard scripts[app]?.removeValue(forKey: identifier) != nil else { return }
        changed(app)
    }

    /// Sets up a new page's controller: the message handler in both worlds,
    /// once (pop-ups share their opener's controller and must not repeat it),
    /// and `app`'s scripts.
    func prepare(_ controller: WKUserContentController, app: String) {
        for world in [WKContentWorld.page, Self.world] {
            controller.add(PageMessageRouter.shared, contentWorld: world, name: Self.messageName)
        }
        install(app, on: controller)
    }

    private func install(_ app: String, on controller: WKUserContentController) {
        controller.removeAllUserScripts()
        controller.addUserScript(PageQuietStarts.script)
        for (_, script) in (scripts[app] ?? [:]).sorted(by: { $0.key < $1.key }) {
            controller.addUserScript(script.webKitScript)
        }
    }

    private func changed(_ app: String) {
        if let data = try? JSONEncoder().encode(scripts) {
            UserDefaults.standard.set(data, forKey: Self.defaultsKey)
        }
        var done = Set<ObjectIdentifier>()
        for controller in controllers(app) where done.insert(ObjectIdentifier(controller)).inserted {
            install(app, on: controller)
        }
    }
}

/// Hands each script message to the page view it came from. It holds no
/// state, so one instance serves every controller, and a message from a view
/// already torn down goes nowhere.
@MainActor
final class PageMessageRouter: NSObject, WKScriptMessageHandler {
    static let shared = PageMessageRouter()

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        (message.webView as? PageWebView)?.page?.receive(message)
    }
}
