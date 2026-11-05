import Foundation
import WebKit

/// How large a page is drawn. The factor stays with the page view across its
/// navigations; the app decides when to change it.
extension PageView {
    func setZoom(_ factor: Double) throws {
        webView.pageZoom = CGFloat(try PageZoom.clamped(factor))
    }
}
