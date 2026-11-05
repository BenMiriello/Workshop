import UIKit

/// Sits over the shell's web view, which is drawn over every page view, and
/// hands a touch to the page under it where the web UI there does not take it
/// (`PagePool.pageTaking`). Every other touch falls through to the shell. The
/// shell's web view is react-native-webview's, so this view does what an
/// override of its hit test would.
final class PageTouchRouter: UIView {
    var route: ((CGPoint) -> PageWebView?)?

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        isOpaque = false
        autoresizingMask = [.flexibleWidth, .flexibleHeight]
    }

    required init?(coder: NSCoder) { nil }

    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        guard let container = superview, let page = route?(convert(point, to: container)) else { return nil }
        return page.hitTest(convert(point, to: page), with: event)
    }
}
