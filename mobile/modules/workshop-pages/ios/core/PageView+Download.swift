import Foundation
import WebKit

/// A file a page offers, handed to the app when its navigation rules say
/// `downloadsToApp`: the load is cancelled, the page stays where it was, and
/// the file is reported as "pageDownload" with the page it was offered from
/// and, when a redirect led to it, the address that was asked for. The
/// shell fetches nothing.
extension PageView {
    func webView(
        _ webView: WKWebView,
        decidePolicyFor navigationResponse: WKNavigationResponse,
        decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
    ) {
        let response = navigationResponse.response
        let http = response as? HTTPURLResponse
        let facts = PageResponseFacts(
            url: response.url,
            mainFrame: navigationResponse.isForMainFrame,
            canShow: navigationResponse.canShowMIMEType,
            contentDisposition: http?.value(forHTTPHeaderField: "Content-Disposition")
        )
        guard let reason = navigation?.rules(app)?.downloadReason(facts) else {
            decisionHandler(.allow)
            return
        }
        decisionHandler(.cancel)
        var event: [String: Any?] = [
            "id": id,
            "url": response.url?.absoluteString ?? "",
            "from": shownURL?.absoluteString ?? "",
            "reason": reason,
        ]
        if let asked = askedURL, asked != response.url { event["link"] = asked.absoluteString }
        if let name = response.suggestedFilename, !name.isEmpty { event["filename"] = name }
        if let mime = response.mimeType, !mime.isEmpty { event["mime"] = mime }
        if response.expectedContentLength >= 0 { event["bytes"] = response.expectedContentLength }
        emit("pageDownload", event)
    }
}
