#if canImport(UIKit)
import UIKit
import UIKit.UIGestureRecognizerSubclass

/// Notes a tap on a page view without taking part in it: it never
/// recognizes, so the page and its scrolling get every touch as before. A
/// touch counts when it lifts close to where it went down; a scroll or a hold
/// that moves does not, as in browsers, where only a tap or a key gives the
/// page the right to act for the person.
final class PageTouchWatch: UIGestureRecognizer, UIGestureRecognizerDelegate {
    private let onTap: () -> Void
    private var start: CGPoint?
    private static let slop: CGFloat = 10

    init(onTap: @escaping () -> Void) {
        self.onTap = onTap
        super.init(target: nil, action: nil)
        cancelsTouchesInView = false
        delaysTouchesBegan = false
        delaysTouchesEnded = false
        delegate = self
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        guard touches.count == 1, start == nil, let touch = touches.first else {
            state = .failed
            return
        }
        start = touch.location(in: view)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent) {
        if let start, let touch = touches.first, Self.distance(touch.location(in: view), start) > Self.slop {
            state = .failed
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent) {
        if let start, let touch = touches.first, Self.distance(touch.location(in: view), start) <= Self.slop {
            onTap()
        }
        state = .failed
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent) {
        state = .failed
    }

    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent) {
        onTap()
        super.pressesBegan(presses, with: event)
    }

    override func reset() {
        start = nil
    }

    func gestureRecognizer(_ gesture: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
        true
    }

    private static func distance(_ a: CGPoint, _ b: CGPoint) -> CGFloat {
        hypot(a.x - b.x, a.y - b.y)
    }
}
#endif
