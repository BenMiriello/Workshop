#if os(macOS)
import AppKit
import WebKit

/// A web view drawn under another (the shell's see-through web view), which
/// keeps the pointer from its page wherever the view over it takes input
/// (`covered`). A web view gets pointer moves through its own tracking even
/// where another view is drawn over it, so without this a link under an open
/// dialog keeps its hover and sets the pointer cursor. Moving onto the
/// covered part tells the page the pointer left; moving back, it came back.
class PointerSharingWebView: WKWebView {
    /// The pointer is over a covered part, and the page was told it left.
    private var pointerCovered = false

    /// Whether the view over this one takes input at `point`, in the
    /// superview's coordinates.
    func covered(_ point: CGPoint) -> Bool { false }

    override func mouseMoved(with event: NSEvent) {
        if pointerIsOurs(event) { super.mouseMoved(with: event) }
    }

    override func mouseEntered(with event: NSEvent) {
        if pointerIsOurs(event) { super.mouseEntered(with: event) }
    }

    override func mouseExited(with event: NSEvent) {
        pointerCovered = false
        super.mouseExited(with: event)
    }

    /// Call when what is covered changes: a pointer resting on the page when
    /// a dialog opens over it leaves the page at once, not on its next move.
    func coverChanged() {
        guard let window, let superview, !pointerCovered else { return }
        let at = superview.convert(window.mouseLocationOutsideOfEventStream, from: nil)
        guard frame.contains(at), covered(at), let left = pointerLeft() else { return }
        pointerCovered = true
        super.mouseExited(with: left)
    }

    private func pointerIsOurs(_ event: NSEvent) -> Bool {
        guard let at = superview?.convert(event.locationInWindow, from: nil) else { return true }
        let now = frame.contains(at) && covered(at)
        if now != pointerCovered {
            pointerCovered = now
            if now, let left = pointerLeft() { super.mouseExited(with: left) }
        }
        return !now
    }

    /// A mouse-exited event just outside this view, so the page drops its
    /// hover rather than moving it to the point under the covering view.
    private func pointerLeft() -> NSEvent? {
        guard let window, let superview else { return nil }
        let outside = superview.convert(NSPoint(x: frame.minX - 1, y: frame.minY - 1), to: nil)
        return NSEvent.enterExitEvent(with: .mouseExited, location: outside, modifierFlags: [],
                                      timestamp: ProcessInfo.processInfo.systemUptime,
                                      windowNumber: window.windowNumber, context: nil,
                                      eventNumber: 0, trackingNumber: 0, userData: nil)
    }
}
#endif
