import ExpoModulesCore
import UIKit
import WebKit

// A view that holds the shell's WebView and adds one item to the system menu
// shown for a text selection in it, after the system's own (Copy, Look Up,
// Translate, Share...), which all stay. react-native-webview's `menuItems`
// cannot do this: setting it turns every system item off.

public class WorkshopSelectionMenuModule: Module {
    public func definition() -> ModuleDefinition {
        Name("WorkshopSelectionMenu")

        View(SelectionMenuView.self) {
            Events("onSelect")

            Prop("title") { (view: SelectionMenuView, title: String) in
                view.title = title
            }
        }
    }
}

/// UIKit builds the selection menu by asking each responder from the focused
/// one outward, so this view, an ancestor of the WebView, is asked too. The
/// item is a command routed back here, and shows exactly when Copy would for
/// a selection in the shell's own page: not for a page view placed over it,
/// nor with only a caret.
final class SelectionMenuView: ExpoView {
    var title = ""
    let onSelect = EventDispatcher()

    override func buildMenu(with builder: UIMenuBuilder) {
        if builder.system == .context, !title.isEmpty {
            let command = UICommand(title: title, action: #selector(selectionMenuItem(_:)))
            let group = UIMenu(title: "", options: .displayInline, children: [command])
            if builder.menu(for: .standardEdit) != nil {
                builder.insertSibling(group, afterMenu: .standardEdit)
            } else {
                builder.insertChild(group, atEndOfMenu: .root)
            }
        }
        super.buildMenu(with: builder)
    }

    override func canPerformAction(_ action: Selector, withSender sender: Any?) -> Bool {
        guard action == #selector(selectionMenuItem(_:)) else {
            return super.canPerformAction(action, withSender: sender)
        }
        let webs = webViews()
        guard !title.isEmpty, let shell = webs.first else { return false }
        if webs.dropFirst().contains(where: holdsFocus) { return false }
        let copy = #selector(UIResponderStandardEditActions.copy(_:))
        return shell.canPerformAction(copy, withSender: sender)
    }

    @objc func selectionMenuItem(_ sender: Any?) {
        onSelect()
    }

    /// The WebViews under this view, the shell's first: page views are added
    /// later, above it, so they come after it.
    private func webViews() -> [WKWebView] {
        var found: [WKWebView] = []
        var queue: [UIView] = [self]
        while !queue.isEmpty {
            let view = queue.removeFirst()
            if let web = view as? WKWebView {
                found.append(web)
            } else {
                queue.append(contentsOf: view.subviews)
            }
        }
        return found
    }

    private func holdsFocus(_ web: WKWebView) -> Bool {
        web.isFirstResponder || web.scrollView.subviews.contains { $0.isFirstResponder }
    }
}
