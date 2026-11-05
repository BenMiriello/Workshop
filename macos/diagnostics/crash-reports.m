// Copies the app's own crash reports to /tmp/workshop-crashes at launch. The
// system writes them into the signed-in account's Library, which the Mac's
// build account cannot read; the copies are world-readable, so a crash can be
// read over SSH once the app is opened again. Loaded with alert-log.m.
#import <Foundation/Foundation.h>
#import <sys/stat.h>

static NSString *const kOut = @"/tmp/workshop-crashes";
static const NSTimeInterval kKeep = 14 * 24 * 3600;

static void copyReports(void) {
    @autoreleasepool {
        NSFileManager *fm = NSFileManager.defaultManager;
        NSString *dir = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/DiagnosticReports"];
        NSArray<NSString *> *names = [fm contentsOfDirectoryAtPath:dir error:nil];
        if (!names) return;
        [fm createDirectoryAtPath:kOut withIntermediateDirectories:YES attributes:nil error:nil];
        chmod(kOut.fileSystemRepresentation, 0755);
        for (NSString *name in names) {
            if (![name hasPrefix:@"Workshop"]) continue;
            if (![name hasSuffix:@".ips"] && ![name hasSuffix:@".crash"]) continue;
            NSString *from = [dir stringByAppendingPathComponent:name];
            NSString *to = [kOut stringByAppendingPathComponent:name];
            if ([fm fileExistsAtPath:to]) continue;
            NSDate *made = [fm attributesOfItemAtPath:from error:nil].fileModificationDate;
            if (!made || -made.timeIntervalSinceNow > kKeep) continue;
            if ([fm copyItemAtPath:from toPath:to error:nil]) chmod(to.fileSystemRepresentation, 0644);
        }
    }
}

__attribute__((constructor)) static void loaded(void) {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ copyReports(); });
}
