// Counts every call that plays the system alert sound in the process it is
// loaded into (DYLD_INSERT_LIBRARIES), and plays nothing. NSBeep reaches the
// speaker through AudioServicesPlayAlertSound, which AppKit calls across
// images, so interposing it sees AppKit's own beeps.
#include <AudioToolbox/AudioToolbox.h>

int ws_sound_count = 0;

static void count_alert(SystemSoundID id) { ws_sound_count++; }
static void count_alert_with(SystemSoundID id, void (^done)(void)) { ws_sound_count++; if (done) done(); }

#define INTERPOSE(replacement, replacee) { (const void *)(replacement), (const void *)(replacee) }
__attribute__((used)) static struct { const void *replacement; const void *replacee; } interposed[]
    __attribute__((section("__DATA,__interpose"))) = {
    INTERPOSE(count_alert, AudioServicesPlayAlertSound),
    INTERPOSE(count_alert_with, AudioServicesPlayAlertSoundWithCompletion),
};
