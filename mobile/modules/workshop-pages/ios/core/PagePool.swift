import Foundation
import WebKit

// The platform-neutral half of the native page views, compiled by the iOS
// module (Expo) and by the Mac app (macos/, which copies this folder in at
// build time). It owns every open page and each profile's data store; where
// a page's view goes on screen is the platform host's part.
//
// Everything is kept per app (`PageApps`): an app opens, lists and closes only
// its own pages, and its pages get only its own rule lists and scripts. Events
// carry the `app` of the page they are about.

@MainActor
final class PagePool {
    var emit: PageEmit?
    let contentRules = PageContentRules()
    let userScripts = PageUserScripts()
    let navigation = PageNavigation()
    unowned let host: PageHost
    /// Every open page by `PageApps.key(app, id)`, pop-ups not yet adopted included.
    var pages: [String: PageView] = [:]
    /// Each profile's data store by `PageApps.key(app, profile)`, made once per app session.
    var stores: [String: WKWebsiteDataStore] = [:]
    /// The app whose frame the web UI shows, and whether the shell is on
    /// screen at all; with each page's ask they decide what it may do
    /// (`PageShowing`, `PagePool+Showing`).
    var front = PageFront.unknown
    var shellOnScreen = true
    /// Where the web UI's own layers sit over every page (`shell.overlays`).
    var overlays = PageOverlays()

    init(host: PageHost) {
        self.host = host
        contentRules.controllers = { [weak self] app in
            self?.pages.values.filter { $0.app == app }.map { $0.webView.configuration.userContentController } ?? []
        }
        userScripts.controllers = contentRules.controllers
        contentRules.loadStored()
    }

    /// Sends a page's events on, marked with the app it belongs to.
    private func emitter(_ app: String) -> PageEmit {
        { [weak self] name, body in
            var tagged = body
            tagged["app"] = app
            self?.emit?(name, tagged)
        }
    }

    func open(app: String, id: String, _ request: PageOpenRequest) throws {
        guard #available(iOS 17.0, *) else { throw PageError.unsupported }
        guard !request.profile.isEmpty else { throw PageError.fail("A page needs a profile name") }
        guard let url = URL(string: request.url), url.scheme != nil else {
            throw PageError.fail("Not a URL: \(request.url)")
        }
        try host.ensureReady()
        let store = dataStore(app, request.profile)
        if let proxy = request.proxy {
            try applyProxy(proxy, to: store)
        }
        let anchors = try certificates(request.trustCertDER)
        let contentMode = try PageView.contentMode(request.contentMode)
        close(app, id)
        let page = PageView(
            app: app,
            id: id,
            profile: request.profile,
            store: store,
            anchors: anchors,
            userAgent: request.userAgent,
            contentMode: contentMode,
            rules: contentRules.lists(for: app),
            emit: emitter(app)
        )
        userScripts.prepare(page.webView.configuration.userContentController, app: app)
        register(page)
        host.place(page.webView, request.rect)
        // A page opened again under its id keeps its back and forward list.
        if let shown = PageHistory.restore(page), PageHistory.same(shown, url) {
            page.noteInput()
        } else {
            page.load(URLRequest(url: url), byPerson: true)
        }
        PageHistory.prune(app)
    }

    /// Keeps `page`, puts its view under the shell, and lets it open pop-ups
    /// and close itself.
    private func register(_ page: PageView) {
        pages[PageApps.key(page.app, page.id)] = page
        host.attach(page.webView)
        page.dialogs = host
        page.navigation = navigation
        page.contentRules = contentRules
        page.onClose = { [weak self] page in self?.close(page.app, page.id) }
        page.onFullscreen = { [weak self] _ in self?.applyShowing() }
        page.onAudio = { [weak self] page in self?.audioChanged(page) }
        applyShowing()
        applyOverlays(page)
        page.makePopup = { [weak self, weak page] configuration in
            guard let self, let page else { return nil }
            let popup = PageView(
                app: page.app,
                id: "popup-\(UUID().uuidString)",
                profile: page.profile,
                configuration: configuration,
                anchors: page.anchors,
                userAgent: page.webView.customUserAgent,
                contentMode: page.contentMode,
                emit: self.emitter(page.app)
            )
            popup.ask.hidden = true
            self.register(popup)
            return popup
        }
    }

    /// Gives pop-up `popupId` the id the app chose for it.
    func adopt(_ app: String, _ popupId: String, as id: String) throws {
        guard let page = pages.removeValue(forKey: PageApps.key(app, popupId)) else {
            throw PageError.fail("No open pop-up \(popupId)")
        }
        close(app, id)
        page.id = id
        pages[PageApps.key(app, id)] = page
    }

    func close(_ app: String, _ id: String, by reason: String? = nil) {
        guard let page = pages.removeValue(forKey: PageApps.key(app, id)) else { return }
        page.tearDown(closedBy: reason)
        applyShowing()
    }

    /// Closes `app`'s pages, or every app's when `app` is nil (a new shell document).
    func closeAll(_ app: String?) {
        let closing = pages.filter { app == nil || $0.value.app == app }
        closing.keys.forEach { pages.removeValue(forKey: $0) }
        closing.values.forEach { $0.tearDown() }
        if app == nil { front = .unknown }
        applyShowing()
    }

    func page(_ app: String, _ id: String) throws -> PageView {
        guard let page = pages[PageApps.key(app, id)] else { throw PageError.fail("No open page \(id)") }
        return page
    }

    func setRect(_ app: String, _ id: String, _ rect: PageFrame) throws {
        let target = try page(app, id)
        target.whenPlaced(.rect) { [weak self, host] in
            host.place(target.webView, rect)
            self?.applyShowing()
        }
    }

    /// Rects in the shell page's CSS pixels where the app's web UI over page
    /// `id` takes clicks and touches; `modal` gives it every one meanwhile.
    func setCutouts(_ app: String, _ id: String, _ rects: [PageFrame], modal: Bool = true) throws {
        let target = try page(app, id)
        target.whenPlaced(.cutouts) { [host] in target.webView.setCutouts(rects.map(host.frame), modal: modal) }
    }

    /// The page view a click or touch at `point` (in the views' superview)
    /// belongs to: the topmost one drawn there whose web UI over it does not
    /// take it. Nil leaves it to the shell's web view, which is drawn over
    /// every page.
    func pageTaking(_ point: CGPoint) -> PageWebView? {
        let drawn = pages.values.map(\.webView).filter { !$0.isHidden && $0.frame.contains(point) }
        guard let top = drawn.max(by: { order($0) < order($1) }) else { return nil }
        return top.takes(point) ? top : nil
    }

    func order(_ view: PageWebView) -> Int {
        view.superview?.subviews.firstIndex(of: view) ?? -1
    }

    func bringToFront(_ app: String, _ id: String) throws {
        let target = try page(app, id)
        target.whenPlaced(.front) { [weak self, host] in
            host.bringToFront(target.webView)
            self?.applyShowing()
        }
    }

    func navigate(_ app: String, _ id: String, _ urlString: String) throws {
        guard let url = URL(string: urlString), url.scheme != nil else {
            throw PageError.fail("Not a URL: \(urlString)")
        }
        try page(app, id).load(URLRequest(url: url), byPerson: true)
    }

    func setZoom(_ app: String, _ id: String, _ factor: Double) throws {
        try page(app, id).setZoom(factor)
    }

    func setMuted(_ app: String, _ id: String, _ muted: Bool) throws {
        try page(app, id).setMuted(muted)
    }

    func setContentMode(_ app: String, _ id: String, _ mode: String, userAgent: String?) throws {
        let target = try page(app, id)
        target.setContentMode(try PageView.contentMode(mode), userAgent: userAgent)
    }
}
