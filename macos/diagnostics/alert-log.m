// Records every system sound the app plays: when, what the person was doing
// (the event being handled, how long since the last key and mouse input), what
// the window was in (sheet, modal panel, first responder) and the call stack.
// Loaded into Workshop.app by LSEnvironment (scripts/lib/macos-build-mac.sh);
// the sound still plays. Lines go to /tmp/workshop-alerts.log, readable from
// the Mac's other accounts.
#import <AppKit/AppKit.h>
#import <AudioToolbox/AudioToolbox.h>
#import <stdio.h>
#import <sys/stat.h>
#import <unistd.h>

extern void NSBeep(void);

static const char *kLog = "/tmp/workshop-alerts.log";
static const off_t kMax = 512 * 1024;

static void append(NSString *line) {
    struct stat st;
    if (stat(kLog, &st) == 0 && st.st_size > kMax) truncate(kLog, 0);
    FILE *f = fopen(kLog, "a");
    if (!f) return;
    chmod(kLog, 0644);
    fprintf(f, "%s\n", line.UTF8String);
    fclose(f);
}

static NSString *eventText(NSEvent *e) {
    if (!e) return @"none";
    NSString *base = [NSString stringWithFormat:@"type %ld flags 0x%lx", (long)e.type, (unsigned long)e.modifierFlags];
    switch (e.type) {
        case NSEventTypeKeyDown: case NSEventTypeKeyUp:
            return [NSString stringWithFormat:@"%@ keyCode %d chars %@ repeat %d", base, e.keyCode,
                    [e.charactersIgnoringModifiers debugDescription], e.isARepeat];
        case NSEventTypeLeftMouseDown: case NSEventTypeRightMouseDown: case NSEventTypeMouseMoved:
        case NSEventTypeLeftMouseUp: case NSEventTypeScrollWheel: case NSEventTypeLeftMouseDragged:
            return [NSString stringWithFormat:@"%@ at %.0f,%.0f", base, e.locationInWindow.x, e.locationInWindow.y];
        default: return base;
    }
}

static NSString *windowText(void) {
    NSWindow *key = NSApp.keyWindow;
    NSMutableString *s = [NSMutableString stringWithFormat:@"active %d key %@ sheet %d modal %d responder %@",
        NSApp.isActive, key ? NSStringFromClass(key.class) : @"none", key.attachedSheet != nil,
        NSApp.modalWindow != nil, key.firstResponder ? NSStringFromClass([key.firstResponder class]) : @"none"];
    NSMenu *menu = NSApp.mainMenu;
    if (menu) [s appendFormat:@" menu %ld", (long)menu.numberOfItems];
    return s;
}

static double since(CGEventType t) {
    return CGEventSourceSecondsSinceLastEventType(kCGEventSourceStateCombinedSessionState, t);
}

static void record(const char *via) {
    @autoreleasepool {
        @try {
            NSString *state = NSThread.isMainThread
                ? [NSString stringWithFormat:@"event [%@] window [%@]", eventText(NSApp.currentEvent), windowText()]
                : @"off the main thread";
            NSString *idle = [NSString stringWithFormat:@"since key %.1fs click %.1fs move %.1fs",
                since(kCGEventKeyDown), since(kCGEventLeftMouseDown), since(kCGEventMouseMoved)];
            NSArray *stack = NSThread.callStackSymbols;
            NSArray *top = [stack subarrayWithRange:NSMakeRange(MIN(2, stack.count), MIN(18, stack.count - MIN(2, stack.count)))];
            NSString *when = [NSDate.date descriptionWithLocale:nil];
            append([NSString stringWithFormat:@"%@ %s %@ %@\n%@", when, via, idle, state, [top componentsJoinedByString:@"\n"]]);
        } @catch (NSException *e) {}
    }
}

static void on_beep(void) { record("NSBeep"); NSBeep(); }
static void on_alert(SystemSoundID id) { record("AlertSound"); AudioServicesPlayAlertSound(id); }
static void on_alert_with(SystemSoundID id, void (^done)(void)) { record("AlertSoundWithCompletion"); AudioServicesPlayAlertSoundWithCompletion(id, done); }
static void on_system(SystemSoundID id) { record("SystemSound"); AudioServicesPlaySystemSound(id); }
static void on_system_with(SystemSoundID id, void (^done)(void)) { record("SystemSoundWithCompletion"); AudioServicesPlaySystemSoundWithCompletion(id, done); }

#define INTERPOSE(replacement, replacee) { (const void *)(replacement), (const void *)(replacee) }
__attribute__((used)) static struct { const void *replacement; const void *replacee; } interposed[]
    __attribute__((section("__DATA,__interpose"))) = {
    INTERPOSE(on_beep, NSBeep),
    INTERPOSE(on_alert, AudioServicesPlayAlertSound),
    INTERPOSE(on_alert_with, AudioServicesPlayAlertSoundWithCompletion),
    INTERPOSE(on_system, AudioServicesPlaySystemSound),
    INTERPOSE(on_system_with, AudioServicesPlaySystemSoundWithCompletion),
};

__attribute__((constructor)) static void loaded(void) {
    append([NSString stringWithFormat:@"%@ alert log loaded in pid %d", [NSDate.date descriptionWithLocale:nil], getpid()]);
}
