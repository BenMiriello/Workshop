import ExpoModulesCore
import UIKit

/// Removes the rows above the keyboard at launch, before any web view exists.
public final class KeyboardRowsAppDelegate: ExpoAppDelegateSubscriber {
    public func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        KeyboardRows.hide()
        return true
    }
}
