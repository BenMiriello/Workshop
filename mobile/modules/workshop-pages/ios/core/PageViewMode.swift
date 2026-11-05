import Foundation
import WebKit

/// Whether a page is served as to a phone or as to a desktop browser.
extension PageView {
    /// `"mobile"` or `"desktop"`; nil when omitted, which keeps WebKit's own choice.
    static func contentMode(_ name: String?) throws -> WKWebpagePreferences.ContentMode {
        switch name {
        case nil: return .recommended
        case "mobile": return .mobile
        case "desktop": return .desktop
        default: throw PageError.fail("Content mode must be mobile or desktop")
        }
    }

    /// Applies `mode` to this page's loads, and `userAgent` when given (empty
    /// restores WebKit's own), then reloads so the page is served for it.
    func setContentMode(_ mode: WKWebpagePreferences.ContentMode, userAgent: String?) {
        contentMode = mode
        if let userAgent {
            webView.customUserAgent = userAgent.isEmpty ? nil : userAgent
        }
        _ = webView.reload()
    }
}
