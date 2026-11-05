/// Which app answers a registered page command, and for which page view,
/// kept free of AppKit so macos/checks/page-commands can run it.
///
/// A page view with keyboard focus answers, with its own app's commands.
/// With focus anywhere else in the window (Workshop's web UI, an app's own
/// controls in it) the app on screen answers: the one whose page view is the
/// topmost of those visible, for that page. With no page view visible, the
/// app whose frame Workshop's web UI shows (`shell.foreground`) answers with
/// no page. Only that one app is asked; when it has no matching command, or
/// no app is on screen, nothing answers and the shell's own menu item of that
/// key applies.
enum PageCommandRoute {
    /// `stack` is the window's page views back to front; `owner` is a page
    /// view's app.
    static func answer<View, Command>(
        focused: View?,
        stack: [View],
        visible: (View) -> Bool,
        foreground: String?,
        owner: (View) -> String?,
        commands: (String) -> [Command],
        matches: (Command) -> Bool
    ) -> (view: View?, app: String, command: Command)? {
        let view = focused ?? stack.last(where: visible)
        let app: String?
        if let view { app = owner(view) } else { app = foreground }
        guard let app,
              let command = commands(app).first(where: matches)
        else { return nil }
        return (view, app, command)
    }
}
