import AppKit
import WebKit

/// The commands an app registers for its page views (`pages.commands.set`,
/// shared/native-pages.ts). Each is a menu item in the top-level menu it
/// names, after the shell's own items there, or in a menu made for it before
/// Window. The window takes their keys before any view in it, so before a
/// page or Workshop's web UI sees them. Which app answers is
/// PageCommandRoute's rule: the focused page view's, or with focus elsewhere
/// in the window the topmost visible page view's, or with none visible the
/// foreground app's; a command is enabled only while it would answer. Firing
/// one sends "pages:command" with the item's id and that page's, or a null
/// page for the foreground app. Each app's list is its own: a page answers
/// only its app's commands. A hidden command has a key and no menu item.
@MainActor
final class PageCommands: NSObject, NSMenuItemValidation {
    struct Item {
        let app: String
        let id: String
        let title: String
        let key: String
        let modifiers: NSEvent.ModifierFlags
        let menu: String
        let focusShell: Bool
        let hidden: Bool
    }

    /// The shell's web view, which takes keyboard focus for a command that
    /// types into the app.
    weak var shell: WKWebView?
    /// The app whose frame Workshop's web UI shows (`shell.foreground`), nil
    /// when none is.
    var foreground: String?
    /// Pushes a bridge event, for a command fired with no page.
    var emit: ((String, [String: Any]) -> Void)?
    /// By app.
    private var lists: [String: [Item]] = [:]
    private var added: [NSMenuItem] = []

    private static let modifierNames: [String: NSEvent.ModifierFlags] = [
        "command": .command, "shift": .shift, "option": .option, "control": .control,
    ]
    private static let modifierMask: NSEvent.ModifierFlags = [.command, .shift, .option, .control]

    /// Replaces `app`'s commands, and rebuilds the menu items of every app's.
    func set(_ app: String, _ list: [[String: Any]]) throws {
        let parsed = try list.map { try Self.item(app, $0) }
        removeEntries()
        lists[app] = parsed.isEmpty ? nil : parsed
        lists.keys.sorted().forEach { lists[$0]?.forEach(add) }
    }

    func clear() {
        removeEntries()
        lists = [:]
        foreground = nil
    }

    private func removeEntries() {
        added.forEach { $0.menu?.removeItem($0) }
        added = []
    }

    /// The command `matches` picks now, and the page view it is for (none
    /// for the foreground app); nil unless the window is the key window.
    private func answer(_ matches: (Item) -> Bool) -> (view: PageWebView?, command: Item)? {
        guard let shell, let window = shell.window, window.isKeyWindow,
              let container = shell.superview,
              let found = PageCommandRoute.answer(
                  focused: window.firstResponder as? PageWebView,
                  stack: container.subviews.compactMap { $0 as? PageWebView },
                  visible: Self.visible,
                  foreground: foreground,
                  owner: { $0.page?.app },
                  commands: { self.lists[$0] ?? [] },
                  matches: matches
              )
        else { return nil }
        return (found.view, found.command)
    }

    /// Shown, and not wholly outside the window's content.
    private static func visible(_ view: NSView) -> Bool {
        guard !view.isHiddenOrHasHiddenAncestor, let container = view.superview else { return false }
        return !view.frame.intersection(container.bounds).isEmpty
    }

    /// Fires the command `event` is the key of, if one answers; true when one fired.
    func handle(_ event: NSEvent) -> Bool {
        let key = event.charactersIgnoringModifiers?.lowercased()
        let flags = event.modifierFlags.intersection(Self.modifierMask)
        guard event.type == .keyDown,
              let found = answer({ $0.key == key && $0.modifiers == flags })
        else { return false }
        fire(found.command, on: found.view)
        return true
    }

    private func fire(_ item: Item, on view: PageWebView?) {
        if item.focusShell, let shell { shell.window?.makeFirstResponder(shell) }
        if let view {
            view.page?.command(item.id)
        } else {
            emit?("pages:command", ["id": NSNull(), "app": item.app, "command": item.id])
        }
    }

    /// The action of every registered command's menu item.
    @objc private func run(_ sender: NSMenuItem) {
        guard let item = sender.representedObject as? Item,
              let found = answer({ $0.app == item.app && $0.id == item.id })
        else { return }
        fire(found.command, on: found.view)
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        guard let item = menuItem.representedObject as? Item else { return false }
        return answer { $0.app == item.app && $0.id == item.id } != nil
    }

    private func add(_ item: Item) {
        guard !item.hidden else { return }
        let entry = NSMenuItem(title: item.title, action: #selector(run(_:)), keyEquivalent: item.key)
        entry.keyEquivalentModifierMask = item.modifiers
        entry.target = self
        entry.representedObject = item
        let target = menu(named: item.menu)
        let ours = target.items.contains { existing in added.contains { $0 === existing } }
        if !target.items.isEmpty, !ours {
            let separator = NSMenuItem.separator()
            target.addItem(separator)
            added.append(separator)
        }
        target.addItem(entry)
        added.append(entry)
    }

    /// The top-level menu titled `title`, made before Window when there is none.
    private func menu(named title: String) -> NSMenu {
        let bar = NSApp.mainMenu ?? NSMenu()
        if let found = bar.items.first(where: { $0.submenu?.title == title })?.submenu {
            return found
        }
        let menu = NSMenu(title: title)
        let holder = NSMenuItem()
        holder.submenu = menu
        let window = bar.items.firstIndex { $0.submenu != nil && $0.submenu === NSApp.windowsMenu }
        bar.insertItem(holder, at: window ?? bar.items.count)
        added.append(holder)
        return menu
    }

    private static func item(_ app: String, _ d: [String: Any]) throws -> Item {
        guard let id = d["id"] as? String, !id.isEmpty,
              let title = d["title"] as? String,
              let key = (d["key"] as? String)?.lowercased(), key.count == 1
        else { throw PageError.fail("A page command needs an id, a title and a one-character key") }
        var modifiers: NSEvent.ModifierFlags = []
        for name in d["modifiers"] as? [String] ?? ["command"] {
            guard let flag = modifierNames[name] else { throw PageError.fail("Unknown modifier \(name)") }
            modifiers.insert(flag)
        }
        return Item(app: app, id: id, title: title, key: key, modifiers: modifiers,
                    menu: d["menu"] as? String ?? "File", focusShell: d["focusShell"] as? Bool ?? false,
                    hidden: d["hidden"] as? Bool ?? false)
    }
}
