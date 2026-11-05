import AppKit

/// The menu bar. The Edit menu is what makes copy, paste, undo and select all
/// reach a WKWebView: AppKit routes those keys only through menu items. An
/// app's page commands are added to it while registered (PageCommands.swift).
///
/// A command-key that matches a menu item never reaches the page as a keydown,
/// so the web UI cannot see Cmd+F itself: Find in Page is a menu item that
/// tells it to open its find bar.
enum MainMenu {
    @MainActor
    static func build() -> NSMenu {
        let bar = NSMenu()
        bar.addItem(submenu(appMenu()))
        bar.addItem(submenu(fileMenu()))
        bar.addItem(submenu(editMenu()))
        bar.addItem(submenu(viewMenu()))
        bar.addItem(submenu(windowMenu()))
        return bar
    }

    private static func submenu(_ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem()
        item.submenu = menu
        return item
    }

    private static func item(_ title: String, _ action: Selector?, _ key: String,
                             _ modifiers: NSEvent.ModifierFlags = .command) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        return item
    }

    private static func appMenu() -> NSMenu {
        let menu = NSMenu(title: "Workshop")
        menu.addItem(item("About Workshop", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""))
        menu.addItem(item("Relaunch Workshop", #selector(AppDelegate.relaunch(_:)), ""))
        menu.addItem(.separator())
        menu.addItem(item("Hide Workshop", #selector(NSApplication.hide(_:)), "h"))
        menu.addItem(item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]))
        menu.addItem(item("Show All", #selector(NSApplication.unhideAllApplications(_:)), ""))
        menu.addItem(.separator())
        menu.addItem(item("Quit Workshop", #selector(NSApplication.terminate(_:)), "q"))
        return menu
    }

    /// Cmd-W closes the window, unless an app registered it as a page command
    /// and that app answers it (PageCommandRoute): its page view has focus,
    /// or focus is elsewhere in the window and its page view is the topmost
    /// visible one, or no page view is visible and its frame is the one the
    /// web UI shows.
    private static func fileMenu() -> NSMenu {
        let menu = NSMenu(title: "File")
        menu.addItem(item("Close", #selector(NSWindow.performClose(_:)), "w"))
        return menu
    }

    private static func editMenu() -> NSMenu {
        let menu = NSMenu(title: "Edit")
        menu.addItem(item("Undo", Selector(("undo:")), "z"))
        menu.addItem(item("Redo", Selector(("redo:")), "z", [.command, .shift]))
        menu.addItem(.separator())
        menu.addItem(item("Cut", #selector(NSText.cut(_:)), "x"))
        menu.addItem(item("Copy", #selector(NSText.copy(_:)), "c"))
        menu.addItem(item("Paste", #selector(NSText.paste(_:)), "v"))
        menu.addItem(item("Paste and Match Style", #selector(NSTextView.pasteAsPlainText(_:)), "v", [.command, .option, .shift]))
        menu.addItem(item("Select All", #selector(NSText.selectAll(_:)), "a"))
        menu.addItem(.separator())
        menu.addItem(item("Find in Page", #selector(AppDelegate.findInPage(_:)), "f"))
        return menu
    }

    private static func viewMenu() -> NSMenu {
        let menu = NSMenu(title: "View")
        menu.addItem(item("Reload Workshop", #selector(AppDelegate.reloadWorkshop(_:)), "r", [.command, .shift]))
        menu.addItem(item("Chat Beside Page", #selector(AppDelegate.toggleBeside(_:)), "i", .control))
        menu.addItem(.separator())
        menu.addItem(item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]))
        return menu
    }

    private static func windowMenu() -> NSMenu {
        let menu = NSMenu(title: "Window")
        menu.addItem(item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"))
        menu.addItem(item("Zoom", #selector(NSWindow.performZoom(_:)), ""))
        menu.addItem(.separator())
        menu.addItem(item("Workshop", #selector(AppDelegate.showWorkshop(_:)), "0"))
        NSApp.windowsMenu = menu
        return menu
    }
}
