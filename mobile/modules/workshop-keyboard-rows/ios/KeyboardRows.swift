import UIKit
import ObjectiveC

/// Removes the Previous / Next / Done bar iOS draws above the keyboard for a
/// field in a web view. Done once, on WebKit's content view class, so it
/// covers every web view in the app, the shell's own and every page view.
/// The bar is the content view's `inputAccessoryView`. Autocorrect,
/// spellcheck and the suggestions row are left to the field and the keyboard.
public enum KeyboardRows {
    private static var done = false

    public static func hide() {
        guard !done, let content = NSClassFromString("WKContentView") else { return }
        done = true

        let accessory = #selector(getter: UIResponder.inputAccessoryView)
        if let method = class_getInstanceMethod(content, accessory) {
            let none: @convention(block) (AnyObject) -> UIView? = { _ in nil }
            method_setImplementation(method, imp_implementationWithBlock(none))
        }
    }
}
