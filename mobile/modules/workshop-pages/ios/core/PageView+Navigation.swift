import Foundation
import WebKit

/// Where a page may go: its app's navigation rules (`PageNavigation`) applied
/// to each navigation WebKit asks about, with the person's own taps, clicks
/// and keys noted natively, so a page can neither fake nor hide them.
extension PageView {
    /// Loads `request` for the app. It is not judged as a jump, and when the
    /// person asked for it (`byPerson`) it counts as their tap.
    func load(_ request: URLRequest, byPerson: Bool) {
        appLoadHost = request.url?.host ?? ""
        if byPerson { noteInput() }
        webView.load(request)
    }

    func noteInput() {
        lastInput = ProcessInfo.processInfo.systemUptime
        backHold.input()
    }

    private var tappedRecently: Bool {
        guard lastInput > 0, let window = navigation?.rules(app)?.tapWindow else { return false }
        return ProcessInfo.processInfo.systemUptime - lastInput <= window
    }

    /// What to do with `action`; a block is reported as "pageNavigationBlocked".
    func verdict(_ action: WKNavigationAction) -> PageNavigationVerdict {
        guard let rules = navigation?.rules(app) else { return .allow }
        let targetsMain = action.targetFrame?.isMainFrame == true
        var fromApp = false
        if targetsMain, let pending = appLoadHost {
            appLoadHost = nil
            fromApp = pending == (action.request.url?.host ?? "")
        }
        let facts = PageNavigationFacts(
            kind: Self.kind(action.navigationType),
            targetsMain: targetsMain,
            method: action.request.httpMethod ?? "GET",
            url: action.request.url,
            sourceIsMain: action.sourceFrame.isMainFrame,
            sourceHost: action.sourceFrame.securityOrigin.host,
            pageHost: shownHost,
            redirect: Self.flag(action, "_isRedirect"),
            userInitiated: Self.flag(action, "_isUserInitiated"),
            tappedRecently: tappedRecently,
            fromApp: fromApp
        )
        let verdict = rules.judge(facts)
        switch verdict {
        case .allow:
            return .allow
        case .block(let reason):
            reportBlocked(action, reason: reason)
            return verdict
        case .load:
            return mayRewrite() ? verdict : .allow
        }
    }

    /// Whether a navigation may be replaced by another now: not when five
    /// were in the last five seconds, which stops a loop.
    func mayRewrite() -> Bool {
        let now = ProcessInfo.processInfo.systemUptime
        rewrites = rewrites.filter { now - $0 < 5 }
        guard rewrites.count < 5 else { return false }
        rewrites.append(now)
        return true
    }

    /// Whether a pop-up may open: with `popupsNeedTap`, only from the
    /// person's tap, the same window as a jump. One refused is reported, so
    /// the app can offer to open it.
    func allowsPopup(_ action: WKNavigationAction) -> Bool {
        guard navigation?.rules(app)?.popupsNeedTap == true else { return true }
        if Self.flag(action, "_isUserInitiated") || tappedRecently { return true }
        reportBlocked(action, reason: "popup")
        return false
    }

    private func reportBlocked(_ action: WKNavigationAction, reason: String) {
        var event: [String: Any?] = [
            "id": id,
            "url": action.request.url?.absoluteString ?? "",
            "from": webView.url?.absoluteString ?? "",
            "reason": reason,
        ]
        if !action.sourceFrame.isMainFrame {
            event["frameUrl"] = action.sourceFrame.request.url?.absoluteString ?? ""
        }
        emit("pageNavigationBlocked", event)
    }

    private static func kind(_ type: WKNavigationType) -> PageNavigationFacts.Kind {
        switch type {
        case .linkActivated: return .link
        case .formSubmitted, .formResubmitted: return .form
        case .backForward: return .backForward
        case .reload: return .reload
        default: return .other
        }
    }

    /// WebKit's own answer for a navigation (`_isRedirect`, `_isUserInitiated`),
    /// which it keeps out of its public interface; false where it has none.
    static func flag(_ action: WKNavigationAction, _ key: String) -> Bool {
        guard action.responds(to: NSSelectorFromString(key)) else { return false }
        return (action.value(forKey: key) as? Bool) ?? false
    }
}
