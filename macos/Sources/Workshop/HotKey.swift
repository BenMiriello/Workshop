import Carbon.HIToolbox

/// A shortcut taken in every app, through Carbon's hot keys, which need no
/// Accessibility permission. While `enabled`, pressing it runs `action` and
/// the app in front never sees the keys.
@MainActor
final class HotKey {
    private static var actions: [UInt32: () -> Void] = [:]
    private static var installed = false
    private static var lastID: UInt32 = 0
    /// "WkHk": marks this app's hot keys among the system's.
    nonisolated private static let signature: OSType = 0x576B_486B

    private let id: UInt32
    private let keyCode: UInt32
    private let modifiers: UInt32
    private var ref: EventHotKeyRef?

    /// `keyCode` is a `kVK_` code, `modifiers` Carbon's (`cmdKey | shiftKey`).
    init(keyCode: Int, modifiers: Int, action: @escaping () -> Void) {
        Self.lastID += 1
        id = Self.lastID
        self.keyCode = UInt32(keyCode)
        self.modifiers = UInt32(modifiers)
        Self.actions[id] = action
    }

    var enabled = false {
        didSet {
            guard enabled != oldValue else { return }
            enabled ? register() : unregister()
        }
    }

    private func register() {
        Self.install()
        let key = EventHotKeyID(signature: Self.signature, id: id)
        let status = RegisterEventHotKey(keyCode, modifiers, key, GetApplicationEventTarget(), 0, &ref)
        if status != noErr { NSLog("Workshop: hot key %u not registered (%d)", id, status) }
    }

    private func unregister() {
        if let ref { UnregisterEventHotKey(ref) }
        ref = nil
    }

    private static func install() {
        guard !installed else { return }
        installed = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var key = EventHotKeyID()
            let status = GetEventParameter(
                event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                nil, MemoryLayout<EventHotKeyID>.size, nil, &key
            )
            guard status == noErr, key.signature == HotKey.signature else { return OSStatus(eventNotHandledErr) }
            MainActor.assumeIsolated { HotKey.actions[key.id]?() }
            return noErr
        }, 1, &spec, nil, nil)
    }
}
