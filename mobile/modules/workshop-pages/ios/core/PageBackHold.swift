import Foundation
import WebKit
#if canImport(UIKit)
import UIKit
typealias PageHoldImage = UIImage
#else
import AppKit
typealias PageHoldImage = NSImage
#endif

/// Back and Forward show the page they go to until it has drawn itself. A
/// swipe shows WebKit's picture of that page, then the page as it is, which
/// on a page that rebuilds itself for the entry (a single-page site) or loads
/// it again is still the one being left. So each history entry's look is
/// kept from when the page last rested on it, and a move through history
/// covers the view with it until the page has drawn the entry: its title
/// changes, its load finishes, the person touches it, or `limit` passes.
@MainActor
final class PageBackHold {
    private weak var webView: WKWebView?
    private var shots: [ObjectIdentifier: PageHoldImage] = [:]
    private var order: [ObjectIdentifier] = []
    private var item: ObjectIdentifier?
    private var back: Set<ObjectIdentifier> = []
    private var forward: Set<ObjectIdentifier> = []
    private var cover: PageHoldCover?
    private var coverTitle: String?
    private var coverAt: TimeInterval = 0
    private var capture: DispatchWorkItem?
    private var scrollWatch: NSKeyValueObservation?

    nonisolated static let keep = 8
    nonisolated static let limit: TimeInterval = 4
    nonisolated static let settle: TimeInterval = 0.5
    /// Scrolls this soon after a move are the swipe that made it.
    nonisolated static let swipeTail: TimeInterval = 0.8

    init(_ webView: WKWebView) {
        self.webView = webView
        #if canImport(UIKit)
        scrollWatch = webView.scrollView.observe(\.contentOffset) { [weak self] _, _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.scrolled(vertical: true) } }
        }
        #endif
    }

    /// The address changed: a move to an entry already in the list is a move
    /// through history (Back, Forward, a swipe, or the page's own
    /// `history.back()`).
    func addressChanged() {
        guard let list = webView?.backForwardList, let current = list.currentItem else { return }
        let id = ObjectIdentifier(current)
        let moved = id != item && (back.contains(id) || forward.contains(id))
        item = id
        back = Set(list.backList.map { ObjectIdentifier($0) })
        forward = Set(list.forwardList.map { ObjectIdentifier($0) })
        if moved { hold(id) }
        scheduleCapture()
    }

    /// A Back or Forward that loads a document: covered from the moment it
    /// is decided, with the entry it goes to.
    func traversing(to url: URL?) {
        guard let list = webView?.backForwardList, let url else { return }
        let target = (list.backList.reversed() + list.forwardList).first { $0.url == url }
        if let target { hold(ObjectIdentifier(target)) }
    }

    func titleChanged() {
        if cover != nil, webView?.title != coverTitle, !(webView?.title ?? "").isEmpty { release(after: 0.12) }
        scheduleCapture()
    }

    func finished() {
        if cover != nil { release(after: 0.12) }
        scheduleCapture()
    }

    /// A tap, click or key: the person is acting on the page as it is.
    func input() {
        if cover != nil { release(after: 0) }
        scheduleCapture(after: 0)
    }

    func scrolled(vertical: Bool) {
        if cover != nil, vertical, now - coverAt > Self.swipeTail { release(after: 0) }
        if cover == nil { scheduleCapture() }
    }

    func drop() {
        release(after: 0)
        capture?.cancel()
        scrollWatch?.invalidate()
        shots = [:]
        order = []
    }

    private var now: TimeInterval { ProcessInfo.processInfo.systemUptime }

    private func hold(_ id: ObjectIdentifier) {
        guard let webView, let image = shots[id], webView.window != nil, !webView.isHidden else { return }
        capture?.cancel()
        cover?.removeFromSuperview()
        let view = PageHoldCover(image: image, frame: webView.bounds)
        webView.addSubview(view)
        cover = view
        coverTitle = webView.title
        coverAt = now
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.limit) { [weak self, weak view] in
            if let view, self?.cover === view { self?.release(after: 0) }
        }
    }

    private func release(after delay: TimeInterval) {
        guard let view = cover else { return }
        guard delay > 0 else { return uncover(view) }
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self, weak view] in
            if let view { self?.uncover(view) }
        }
    }

    private func uncover(_ view: PageHoldCover) {
        guard cover === view else { return }
        view.removeFromSuperview()
        cover = nil
        scheduleCapture()
    }

    /// Keeps the current entry's look once the page has rested on it.
    private func scheduleCapture(after delay: TimeInterval = PageBackHold.settle) {
        capture?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.takeShot() }
        capture = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func takeShot() {
        guard cover == nil, let webView, webView.window != nil, !webView.isHidden, !webView.isLoading,
              let current = webView.backForwardList.currentItem, webView.bounds.width > 1 else { return }
        let id = ObjectIdentifier(current)
        let config = WKSnapshotConfiguration()
        config.afterScreenUpdates = false
        webView.takeSnapshot(with: config) { [weak self] image, _ in
            guard let self, let image, self.cover == nil, self.webView?.backForwardList.currentItem.map(ObjectIdentifier.init) == id else { return }
            self.shots[id] = image
            self.order.removeAll { $0 == id }
            self.order.append(id)
            while self.order.count > Self.keep { self.shots[self.order.removeFirst()] = nil }
        }
    }
}

/// The picture over a page view while it draws the entry it moved to; it
/// takes no input, which goes to the page.
final class PageHoldCover: PageHoldImageView {
    init(image: PageHoldImage, frame: CGRect) {
        super.init(frame: frame)
        self.image = image
        #if canImport(UIKit)
        contentMode = .scaleToFill
        isUserInteractionEnabled = false
        autoresizingMask = [.flexibleWidth, .flexibleHeight]
        #else
        imageScaling = .scaleAxesIndependently
        autoresizingMask = [.width, .height]
        #endif
    }

    required init?(coder: NSCoder) { nil }

    #if !canImport(UIKit)
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    #endif
}

#if canImport(UIKit)
typealias PageHoldImageView = UIImageView
#else
typealias PageHoldImageView = NSImageView
#endif
