import Foundation
import WebKit

/// A page's JavaScript alert, confirm or prompt.
struct PageDialog {
    enum Kind {
        case alert
        case confirm
        case prompt(defaultText: String?)
    }

    let kind: Kind
    let message: String
    /// The address of the frame that asked, so the dialog can name its site.
    let site: URL?
}

/// What the person chose: OK or not, and the text typed into a prompt.
struct PageDialogAnswer {
    var ok: Bool
    var text: String?

    static let dismissed = PageDialogAnswer(ok: false, text: nil)
}

/// Shows page dialogs over the window that holds the page views.
@MainActor
protocol PageDialogHost: AnyObject {
    /// Shows `dialog` for the page in `view` and returns once it is answered.
    /// It must return, with `.dismissed` when nothing could be shown: the
    /// page waits on the answer.
    func present(_ dialog: PageDialog, from view: PageWebView) async -> PageDialogAnswer
}

extension PageView: WKUIDelegate {
    func webView(
        _ webView: WKWebView,
        createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction,
        windowFeatures: WKWindowFeatures
    ) -> WKWebView? {
        guard allowsPopup(navigationAction) else { return nil }
        let url = navigationAction.request.url?.absoluteString ?? ""
        guard let popup = makePopup?(configuration) else {
            emit("pageNewWindow", ["id": id, "url": url])
            return nil
        }
        var event: [String: Any?] = ["id": id, "url": url, "popupId": popup.id]
        if let asked = Self.requested(windowFeatures) { event["features"] = asked }
        emit("pageNewWindow", event)
        return popup.webView
    }

    /// The size, and place when given, the page asked for its new window
    /// (`window.open`'s width, height, left and top); nil when it gave no size.
    private static func requested(_ features: WKWindowFeatures) -> [String: Double]? {
        guard features.width != nil || features.height != nil else { return nil }
        let given: [(String, NSNumber?)] = [
            ("width", features.width), ("height", features.height), ("x", features.x), ("y", features.y),
        ]
        var asked: [String: Double] = [:]
        for (key, value) in given {
            if let value { asked[key] = value.doubleValue }
        }
        return asked
    }

    func webViewDidClose(_ webView: WKWebView) {
        onClose?(self)
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo
    ) async {
        _ = await ask(PageDialog(kind: .alert, message: message, site: frame.request.url))
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo
    ) async -> Bool {
        await ask(PageDialog(kind: .confirm, message: message, site: frame.request.url)).ok
    }

    func webView(
        _ webView: WKWebView,
        runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?,
        initiatedByFrame frame: WKFrameInfo
    ) async -> String? {
        let answer = await ask(PageDialog(kind: .prompt(defaultText: defaultText), message: prompt,
                                          site: frame.request.url))
        return answer.ok ? (answer.text ?? "") : nil
    }

    /// A page that is not on screen gets no dialog: it would cover another page.
    private func ask(_ dialog: PageDialog) async -> PageDialogAnswer {
        guard let dialogs, !webView.isHidden, webView.window != nil else { return .dismissed }
        return await dialogs.present(dialog, from: webView)
    }
}
