import Foundation

/// Which page views are drawn and which may play (`PageShowing`): every
/// change to an app's ask, to the app in front, to whether the shell is on
/// screen, or to where pages stand and which is on top is applied here, to
/// every page, since one page moving can cover or uncover another.
extension PagePool {
    /// Hides or shows page `id`. A hidden page's media is held unless
    /// `keepsPlaying`, which lets it play on while its app is in front.
    func setHidden(_ app: String, _ id: String, _ hidden: Bool, keepsPlaying: Bool = false) throws {
        let target = try page(app, id)
        target.ask = PageShowingAsk(hidden: hidden, keepsPlaying: hidden && keepsPlaying)
        applyShowing()
    }

    /// The app whose frame the web UI shows now, or nil for none.
    func setForeground(_ app: String?) {
        front = app.map { .app($0) } ?? PageFront.none
        applyShowing()
    }

    /// Whether the shell's window or app is on screen.
    func setShellOnScreen(_ onScreen: Bool) {
        guard shellOnScreen != onScreen else { return }
        shellOnScreen = onScreen
        applyShowing()
    }

    func applyShowing() {
        let all = Array(pages.values)
        let drawn = all.filter {
            PageShowing.drawn(app: $0.app, ask: $0.ask, front: front, fullscreen: $0.fullscreen)
        }.map(\.webView)
        for page in all {
            let mine = order(page.webView)
            let above = drawn.filter { $0 !== page.webView && order($0) > mine }.map(\.frame)
            let now = PageShowing.rule(app: page.app, ask: page.ask, front: front, shellOnScreen: shellOnScreen,
                                       fullscreen: page.fullscreen,
                                       covered: PageShowing.covered(page.webView.frame, by: above))
            page.apply(drawn: now.drawn, plays: now.plays)
        }
    }
}
