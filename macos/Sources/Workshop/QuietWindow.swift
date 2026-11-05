import AppKit

/// Workshop's window. A key that no view took, in the web UI or in a page
/// view, ends at `NSWindow.keyDown:`, which plays the alert sound. Web content
/// decides its own feedback, and an arrow pressed at the end of a list or a
/// letter typed with nothing focused is not an error, so such a key is dropped
/// silently.
final class QuietWindow: NSWindow {
    override func keyDown(with event: NSEvent) {}
}
