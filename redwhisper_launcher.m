#import <Cocoa/Cocoa.h>
#import <AVFoundation/AVFoundation.h>
#import <ApplicationServices/ApplicationServices.h>

static const int RedWhisperRestartExitCode = 75;

@interface RedWhisperAppDelegate : NSObject <NSApplicationDelegate>
@property(nonatomic, strong) NSTask *runtimeTask;
@property(nonatomic, strong) NSFileHandle *logHandle;
@property(nonatomic, assign) BOOL terminating;
@end

@implementation RedWhisperAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    [self requestPermissions];
    [self launchRuntime];
}

- (void)requestPermissions {
    NSDictionary *accessibilityOptions = @{
        (__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES
    };
    AXIsProcessTrustedWithOptions(
        (__bridge CFDictionaryRef)accessibilityOptions
    );

    if ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] ==
        AVAuthorizationStatusNotDetermined) {
        [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio
                                 completionHandler:^(BOOL granted) {
            (void)granted;
        }];
    }
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    (void)sender;
    return NO;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    (void)notification;
    self.terminating = YES;
    if (self.runtimeTask.running) {
        [self.runtimeTask terminate];
    }
}

- (void)launchRuntime {
    NSURL *runtimeURL = [[[NSBundle mainBundle] resourceURL]
        URLByAppendingPathComponent:@"runtime" isDirectory:YES];
    NSURL *pythonURL = [runtimeURL URLByAppendingPathComponent:@".venv/bin/python3"];
    NSURL *scriptURL = [runtimeURL URLByAppendingPathComponent:@"voxtape.py"];
    NSFileManager *files = [NSFileManager defaultManager];

    if (![files isExecutableFileAtPath:pythonURL.path] ||
        ![files fileExistsAtPath:scriptURL.path]) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"RedWhisper runtime is missing";
        alert.informativeText =
            @"Run build_app.sh from the MLX-Whisper folder, then launch RedWhisper.app again.";
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }

    NSString *logDirectory = [NSHomeDirectory()
        stringByAppendingPathComponent:@"Library/Logs/RedWhisper"];
    [files createDirectoryAtPath:logDirectory
      withIntermediateDirectories:YES
                       attributes:nil
                            error:nil];
    NSString *logPath = [logDirectory stringByAppendingPathComponent:@"app.log"];
    if (![files fileExistsAtPath:logPath]) {
        [files createFileAtPath:logPath contents:nil attributes:nil];
    }
    self.logHandle = [NSFileHandle fileHandleForWritingAtPath:logPath];
    [self.logHandle seekToEndOfFile];

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = pythonURL;
    task.currentDirectoryURL = runtimeURL;
    task.arguments = @[scriptURL.path, @"--no-launch-gui"];
    NSMutableDictionary<NSString *, NSString *> *environment =
        [[[NSProcessInfo processInfo] environment] mutableCopy];
    environment[@"PYTHONUNBUFFERED"] = @"1";
    environment[@"REDWHISPER_BUNDLED"] = @"1";
    environment[@"REDWHISPER_LAUNCHER_PID"] = [NSString stringWithFormat:
        @"%d", [NSProcessInfo processInfo].processIdentifier];
    task.environment = environment;
    task.standardOutput = self.logHandle;
    task.standardError = self.logHandle;

    __weak typeof(self) weakSelf = self;
    task.terminationHandler = ^(NSTask *finishedTask) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [weakSelf runtimeDidExit:finishedTask.terminationStatus];
        });
    };
    self.runtimeTask = task;

    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        NSAlert *alert = [NSAlert alertWithError:error];
        alert.messageText = @"RedWhisper could not start";
        [alert runModal];
        [NSApp terminate:nil];
    }
}

- (void)runtimeDidExit:(int)status {
    self.runtimeTask = nil;
    [self.logHandle closeFile];
    self.logHandle = nil;
    if (self.terminating) {
        return;
    }
    if (status == RedWhisperRestartExitCode) {
        [self launchRuntime];
    } else {
        [NSApp terminate:nil];
    }
}

@end

int main(int argc, const char *argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        application.activationPolicy = NSApplicationActivationPolicyRegular;
        RedWhisperAppDelegate *delegate = [[RedWhisperAppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
