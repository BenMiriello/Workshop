import Foundation

/// A response to a load of a page view, as far as deciding whether it is a
/// file for the app takes.
struct PageResponseFacts {
    var url: URL?
    var mainFrame: Bool
    /// WebKit can display its content type.
    var canShow: Bool
    var contentDisposition: String?
}

extension PageNavigationRules {
    /// "attachment" or "unshowable" when the response is a file to hand to the
    /// app (`downloadsToApp`); nil to let the page view carry on with it.
    func downloadReason(_ r: PageResponseFacts) -> String? {
        guard downloadsToApp, r.mainFrame, let scheme = r.url?.scheme?.lowercased(),
              ["http", "https"].contains(scheme)
        else { return nil }
        if let header = r.contentDisposition,
           header.trimmingCharacters(in: .whitespaces).lowercased().hasPrefix("attachment") {
            return "attachment"
        }
        return r.canShow ? nil : "unshowable"
    }
}
