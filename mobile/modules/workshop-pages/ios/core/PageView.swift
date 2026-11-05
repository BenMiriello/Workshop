import Foundation
import WebKit
#if canImport(UIKit)
import UIKit
#else
import AppKit
#endif

typealias PageEmit = (String, [String: Any?]) -> Void

/// One native page: a WKWebView in its profile's data store, reporting its
/// state, errors, new-window requests and blocked navigations as module events.
@MainActor
final class PageView: NSObject, WKNavigationDelegate {
    /// The app whose page this is (`PageApps`).
    let app: String
    /// The app's own id for the page; changes once, when the app adopts a
    /// pop-up under an id of its own.
    var id: String
    let profile: String
    let webView: PageWebView
    let anchors: [SecCertificate]
    let emit: PageEmit
    var contentMode: WKWebpagePreferences.ContentMode
    private var observations: [NSKeyValueObservation] = []
    /// Makes the page view for a pop-up of this page, in the configuration
    /// WebKit gives, which keeps its link to this page (window.opener).
    var makePopup: ((WKWebViewConfiguration) -> PageView?)?
    /// The page closed itself (window.close).
    var onClose: ((PageView) -> Void)?
    /// Shows the page's JavaScript alert, confirm and prompt.
    weak var dialogs: PageDialogHost?
    /// The page's web process ended; nothing runs or draws until a reload.
    private(set) var crashed = false
    /// Script messages too large or not JSON, dropped since the page opened.
    var droppedMessages = 0
    /// The app's navigation rules (`PageView+Navigation`).
    weak var navigation: PageNavigation?
    /// The app's content rule lists, for what it lifts from them.
    weak var contentRules: PageContentRules?
    /// Whether the page being loaded lifts the app's content rule lists.
    var liftsLists = false
    /// When the person last tapped, clicked or typed in the page (system uptime).
    var lastInput: TimeInterval = 0
    /// The host of a load the app or a rule started, until WebKit asks about it.
    var appLoadHost: String?
    /// The host of the page shown; nil before the first page is shown.
    var shownHost: String?
    /// The page shown, which a file this view is then given was offered from.
    var shownURL: URL?
    /// The address the current load asked for, before any redirect.
    var askedURL: URL?
    /// When navigations were last replaced by a rule, to stop a loop.
    var rewrites: [TimeInterval] = []
    /// Whether the app has the page's sound muted (`PageAudio`).
    var mutedAudio = false
    /// What the app asked of the page's showing, and whether its media is
    /// held now (`PageShowing`, `PageView+Audio`).
    var ask = PageShowingAsk()
    var mediaHeld = false
    /// Whether WebKit shows the page full screen, what the shell asked of its
    /// place meanwhile, and who hears of a change (`PageFullscreen`).
    var fullscreen = false
    var placements: [PagePlacement: () -> Void] = [:]
    var onFullscreen: ((PageView) -> Void)?
    /// Hears every change of the page's sound and every hold (`PagePool+Quiet`).
    var onAudio: ((PageView) -> Void)?
    private var audioWatch: PageAudioWatch?
    /// Covers a move through history until the page has drawn it.
    private(set) lazy var backHold = PageBackHold(webView)
    /// Pauses the page's media when it is left (`PageLeave`).
    private var leaveWatch: PageLeaveWatch?
    /// The failure page being loaded for a load that failed (`PageFailure`).
    private var failurePage: URL?

    convenience init(
        app: String,
        id: String,
        profile: String,
        store: WKWebsiteDataStore,
        anchors: [SecCertificate],
        userAgent: String?,
        contentMode: WKWebpagePreferences.ContentMode,
        rules: [WKContentRuleList],
        emit: @escaping PageEmit
    ) {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = store
        // Every new window comes to `allowsPopup`. WebKit's own check forgets
        // a click after about a second and refuses unseen, which stops sign-in
        // and account windows that open after a short wait.
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        #if canImport(UIKit)
        config.allowsInlineMediaPlayback = true
        #else
        // Without the Fullscreen API, sites' players take the browser for an
        // old one and fall back to a bare video at their lowest quality.
        config.preferences.isElementFullscreenEnabled = true
        #endif
        config.defaultWebpagePreferences.preferredContentMode = contentMode
        rules.forEach { config.userContentController.add($0) }
        self.init(app: app, id: id, profile: profile, configuration: config, anchors: anchors,
                  userAgent: userAgent, contentMode: contentMode, emit: emit)
    }

    init(
        app: String,
        id: String,
        profile: String,
        configuration config: WKWebViewConfiguration,
        anchors: [SecCertificate],
        userAgent: String?,
        contentMode: WKWebpagePreferences.ContentMode,
        emit: @escaping PageEmit
    ) {
        self.app = app
        self.id = id
        self.contentMode = contentMode
        self.profile = profile
        self.anchors = anchors
        self.emit = emit
        webView = PageWebView(frame: .zero, configuration: config)
        super.init()
        webView.page = self
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        #if canImport(UIKit)
        webView.addGestureRecognizer(PageTouchWatch { [weak self] in self?.noteInput() })
        #endif
        if #available(iOS 16.4, *) {
            webView.isInspectable = true
        }
        if let userAgent, !userAgent.isEmpty {
            webView.customUserAgent = userAgent
        }
        observations = [
            webView.observe(\.url) { [weak self] _, _ in self?.scheduleState(saving: true, moved: true) },
            webView.observe(\.title) { [weak self] _, _ in self?.scheduleState(retitled: true) },
            webView.observe(\.isLoading) { [weak self] _, _ in self?.scheduleState() },
            webView.observe(\.estimatedProgress) { [weak self] _, _ in self?.scheduleState() },
            webView.observe(\.canGoBack) { [weak self] _, _ in self?.scheduleState(moved: true) },
            webView.observe(\.canGoForward) { [weak self] _, _ in self?.scheduleState(moved: true) },
        ]
        if let watch = watchFullscreen({ [weak self] in self?.fullscreenStateChanged() }) {
            observations.append(watch)
        }
        audioWatch = PageAudioWatch(webView) { [weak self] in
            self?.scheduleState()
            self?.audioMoved()
        }
        leaveWatch = PageLeaveWatch(webView)
    }

    /// Closes the page. `closedBy` says why when it was not the app's own ask,
    /// so the app can tell a page that closed itself from one the shell let go.
    func tearDown(closedBy: String? = nil) {
        PageHistory.save(self)
        backHold.drop()
        makePopup = nil
        onClose = nil
        onFullscreen = nil
        onAudio = nil
        placements = [:]
        dialogs = nil
        observations.forEach { $0.invalidate() }
        observations = []
        audioWatch?.stop()
        audioWatch = nil
        leaveWatch?.stop()
        leaveWatch = nil
        webView.stopLoading()
        var closing = state(closed: true)
        if let closedBy { closing["closedBy"] = closedBy }
        PageAudio.silence(webView)
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
        removeView()
        emit("pageState", closing)
    }

    func state(closed: Bool = false) -> [String: Any?] {
        var state: [String: Any?] = [
            "id": id,
            "url": webView.url?.absoluteString ?? "",
            "title": webView.title ?? "",
            "loading": webView.isLoading,
            "progress": webView.estimatedProgress,
            "canGoBack": webView.canGoBack,
            "canGoForward": webView.canGoForward,
            "crashed": crashed,
            "closed": closed,
            "held": mediaHeld,
        ]
        if !closed { state.merge(audioState()) { _, new in new } }
        return state
    }

    /// Reports the page's state; with `saving`, keeps its history too
    /// (`PageHistory`), as its address changed, and tells `backHold` of a
    /// change of entry (`moved`) or title (`retitled`).
    nonisolated func scheduleState(saving: Bool = false, moved: Bool = false, retitled: Bool = false) {
        DispatchQueue.main.async { [weak self] in
            MainActor.assumeIsolated {
                guard let self, self.webView.navigationDelegate != nil else { return }
                if saving { PageHistory.save(self) }
                if moved { self.backHold.addressChanged() }
                if retitled { self.backHold.titleChanged() }
                self.emit("pageState", self.state())
            }
        }
    }

    /// Reports a failed load; `shownInPage` when the page view shows it itself.
    private func report(_ error: Error, shownInPage: Bool = false) {
        guard PageFailure.failed(error) else { return }
        emit("pageError", ["id": id, "message": (error as NSError).localizedDescription, "shownInPage": shownInPage])
    }

    // MARK: WKNavigationDelegate

    /// A command the app registered, fired while this page had focus.
    func command(_ name: String) {
        emit("pageCommand", ["id": id, "command": name])
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        askedURL = webView.url
        leaveWatch?.loadStarted()
        guard crashed else { return }
        crashed = false
        emit("pageState", state())
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        shownHost = webView.url?.host ?? ""
        shownURL = webView.url
        leaveWatch?.committed()
        failurePage = nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        PageHistory.save(self)
        backHold.finished()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard PageFailure.failed(error), let url = PageFailure.address(error, asked: askedURL),
              ["http", "https"].contains(url.scheme?.lowercased() ?? "") else { return report(error) }
        report(error, shownInPage: true)
        failurePage = url
        PageFailure.show(error, at: url, in: webView)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        report(error)
    }

    /// Pages stay in their page view: web links load here without handing
    /// off to another app (WebKit's allow-without-app-link policy, the plain
    /// allow plus two), and links to other apps' schemes are refused. The
    /// app's navigation rules may cancel a load or replace it with another.
    /// Each load asks for the page's content mode.
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationAction: WKNavigationAction,
        preferences: WKWebpagePreferences,
        decisionHandler: @escaping (WKNavigationActionPolicy, WKWebpagePreferences) -> Void
    ) {
        if let page = failurePage, navigationAction.targetFrame?.isMainFrame == true,
           navigationAction.request.url == page {
            failurePage = nil
            decisionHandler(.allow, preferences)
            return
        }
        let scheme = navigationAction.request.url?.scheme?.lowercased() ?? ""
        guard PageView.webSchemes.contains(scheme) else {
            decisionHandler(.cancel, preferences)
            return
        }
        switch verdict(navigationAction) {
        case .allow:
            if navigationAction.navigationType == .backForward, navigationAction.targetFrame?.isMainFrame == true {
                backHold.traversing(to: navigationAction.request.url)
            }
        case .block:
            decisionHandler(.cancel, preferences)
            return
        case .load(let next):
            decisionHandler(.cancel, preferences)
            var request = navigationAction.request
            request.url = next
            DispatchQueue.main.async { [weak self] in self?.load(request, byPerson: false) }
            return
        }
        preferences.preferredContentMode = contentMode
        let redirect = Self.flag(navigationAction, "_isRedirect")
        if contentRules?.lift(app, navigationAction, preferences, covered: &liftsLists, redirect: redirect) == false,
           mayRewrite() {
            decisionHandler(.cancel, preferences)
            let request = navigationAction.request
            DispatchQueue.main.async { [weak self] in self?.load(request, byPerson: false) }
            return
        }
        let noAppLink = WKNavigationActionPolicy(rawValue: WKNavigationActionPolicy.allow.rawValue + 2)
        decisionHandler(noAppLink ?? .allow, preferences)
    }

    private static let webSchemes: Set<String> = ["http", "https", "about", "data", "blob"]

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        crashed = true
        emit("pageState", state())
        emit("pageError", ["id": id, "message": Self.crashedMessage])
    }

    nonisolated static let crashedMessage = "The page stopped responding and needs a reload"

    /// Server trust for this page only: the given anchors are added to the
    /// system roots. Anything that still fails evaluation gets default handling,
    /// which rejects it.
    func webView(
        _ webView: WKWebView,
        respondTo challenge: URLAuthenticationChallenge
    ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
        guard !anchors.isEmpty,
              challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust
        else { return (.performDefaultHandling, nil) }
        SecTrustSetAnchorCertificates(trust, anchors as CFArray)
        SecTrustSetAnchorCertificatesOnly(trust, false)
        var error: CFError?
        if SecTrustEvaluateWithError(trust, &error) {
            return (.useCredential, URLCredential(trust: trust))
        }
        return (.performDefaultHandling, nil)
    }
}
