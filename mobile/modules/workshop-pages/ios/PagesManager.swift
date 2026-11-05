import Foundation
import UIKit
import WebKit

/// The iOS host for page views: they are added to the shell WebView's
/// superview, under it (the newest on top of the other pages), and placed
/// from rects in the shell page's CSS pixels. The shell's web view is
/// see-through, so whatever the web UI draws shows over a page, and a
/// `PageTouchRouter` over it hands a page its touches. The pages themselves
/// are the shared `PagePool`.
@MainActor
final class PagesManager: PageHost {
    static let shared = PagesManager()

    private(set) lazy var pool: PagePool = {
        let pool = PagePool(host: self)
        watchApp(pool)
        return pool
    }()
    private weak var host: WKWebView?
    private lazy var router: PageTouchRouter = {
        let router = PageTouchRouter()
        router.route = { [weak self] point in self?.pool.pageTaking(point) }
        return router
    }()
    private var appWatch: [NSObjectProtocol] = []

    /// Pages are off screen while Workshop is in the background, so their
    /// media is held there (`PageShowing`), whatever the app's audio mode.
    private func watchApp(_ pool: PagePool) {
        let center = NotificationCenter.default
        let watch = { [weak pool] (name: Notification.Name, onScreen: Bool) in
            center.addObserver(forName: name, object: nil, queue: .main) { _ in
                MainActor.assumeIsolated { pool?.setShellOnScreen(onScreen) }
            }
        }
        appWatch = [
            watch(UIApplication.didEnterBackgroundNotification, false),
            watch(UIApplication.willEnterForegroundNotification, true),
        ]
        pool.setShellOnScreen(UIApplication.shared.applicationState != .background)
    }

    func ensureReady() throws {
        guard let host = hostWebView(), host.superview != nil else {
            throw PageError.fail("The shell's web view was not found")
        }
    }

    func attach(_ view: PageWebView) {
        guard let host = hostWebView(), let container = host.superview else { return }
        container.insertSubview(view, belowSubview: host)
        keepRouterOnTop(container)
    }

    /// Sets `view`'s frame, putting it back under the shell's web view if it
    /// is not there (a new shell view, WebKit's full screen giving it back).
    func place(_ view: PageWebView, _ rect: PageFrame) {
        guard let host = hostWebView(), let container = host.superview else { return }
        let views = container.subviews
        if view.superview !== container
            || (views.firstIndex(of: view) ?? 0) > (views.firstIndex(of: host) ?? 0) {
            container.insertSubview(view, belowSubview: host)
        }
        keepRouterOnTop(container)
        view.frame = frame(rect)
    }

    private func keepRouterOnTop(_ container: UIView) {
        if router.superview !== container { container.addSubview(router) }
        if container.subviews.last !== router { container.bringSubviewToFront(router) }
        router.frame = container.bounds
    }

    func frame(_ rect: PageFrame) -> CGRect {
        guard let host = hostWebView() else { return .zero }
        let scale = host.scrollView.zoomScale > 0 ? host.scrollView.zoomScale : 1
        let inset = host.scrollView.adjustedContentInset
        let origin = host.frame.origin
        return CGRect(
            x: origin.x + inset.left + CGFloat(rect.x) * scale,
            y: origin.y + inset.top + CGFloat(rect.y) * scale,
            width: max(0, CGFloat(rect.width) * scale),
            height: max(0, CGFloat(rect.height) * scale)
        )
    }

    func bringToFront(_ view: PageWebView) {
        guard let host = hostWebView(), let container = host.superview, view.superview === container else { return }
        container.insertSubview(view, belowSubview: host)
    }

    /// An alert over whatever the page's window shows on top, named by the
    /// site that asked. Every way out answers once, and an alert UIKit will
    /// not present answers as dismissed.
    func present(_ dialog: PageDialog, from view: PageWebView) async -> PageDialogAnswer {
        guard var top = view.window?.rootViewController else { return .dismissed }
        while let next = top.presentedViewController, !next.isBeingDismissed { top = next }
        let alert = UIAlertController(title: dialog.site?.host ?? "This page says",
                                      message: dialog.message, preferredStyle: .alert)
        if case .prompt(let defaultText) = dialog.kind {
            alert.addTextField { $0.text = defaultText }
        }
        return await withCheckedContinuation { done in
            var answered = false
            let answer = { (result: PageDialogAnswer) in
                guard !answered else { return }
                answered = true
                done.resume(returning: result)
            }
            alert.addAction(UIAlertAction(title: "OK", style: .default) { [weak alert] _ in
                answer(PageDialogAnswer(ok: true, text: alert?.textFields?.first?.text))
            })
            if case .alert = dialog.kind {} else {
                alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in answer(.dismissed) })
            }
            top.present(alert, animated: true)
            if alert.presentingViewController == nil { answer(.dismissed) }
        }
    }

    /// The shell's WebView: the largest visible WKWebView that is not a page view.
    private func hostWebView() -> WKWebView? {
        if let current = host, current.window != nil { return current }
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap { $0.windows }
        let ordered = windows.filter { $0.isKeyWindow } + windows.filter { !$0.isKeyWindow }
        for window in ordered {
            if let found = largestWebView(in: window) {
                host = found
                return found
            }
        }
        return nil
    }

    private func largestWebView(in root: UIView) -> WKWebView? {
        var best: WKWebView?
        var bestArea: CGFloat = 0
        var stack: [UIView] = [root]
        while let view = stack.popLast() {
            if let web = view as? WKWebView {
                let area = web.bounds.width * web.bounds.height
                if !(web is PageWebView), !web.isHidden, area > bestArea {
                    best = web
                    bestArea = area
                }
                continue
            }
            stack.append(contentsOf: view.subviews)
        }
        return best
    }
}
