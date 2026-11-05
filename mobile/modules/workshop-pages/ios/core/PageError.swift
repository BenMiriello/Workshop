import Foundation

/// A refusal with a message for the caller; each platform hands it on in its
/// own error type.
struct PageError: LocalizedError {
    let name: String
    let message: String

    var errorDescription: String? { message }

    static func fail(_ message: String) -> PageError {
        PageError(name: "PageError", message: message)
    }

    static let unsupported = PageError(name: "Unsupported", message: "Page views need iOS 17 or later")
}
