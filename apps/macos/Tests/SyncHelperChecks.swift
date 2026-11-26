import Foundation

/// Headless checks for the sync-helper uninstall fix: identifying the helper's
/// engine rounds from `ps` output without touching the app's own sync rounds.
@main
enum SyncHelperChecks {
    static func main() {
        let ws = "/Users/test/Library/Application Support/FireSky/Data"
        let helperLine = "44258 /Applications/FireSky.app/Contents/Resources/firesky-engine desktop-call --workspace \(ws) data_sync_tick"
        // The app's own manual/heartbeat rounds use data_sync_run, not tick.
        let appRunLine = "44100 /Applications/FireSky.app/Contents/Resources/firesky-engine desktop-call --workspace \(ws) data_sync_run"
        let otherWsLine = "44300 /Applications/FireSky.app/Contents/Resources/firesky-engine desktop-call --workspace /tmp/other data_sync_tick"
        let unrelatedLine = "44400 /bin/sleep 100"
        let editorLine = "44500 rg data_sync_tick apps/macos"
        let ps = [helperLine, appRunLine, otherWsLine, unrelatedLine, editorLine]
            .joined(separator: "\n")
        precondition(SyncHelperInstaller.roundProcessIDs(psOutput: ps, workspace: ws) == [44258])
        precondition(SyncHelperInstaller.roundProcessIDs(psOutput: ps, workspace: "").isEmpty)
        precondition(SyncHelperInstaller.roundProcessIDs(psOutput: "garbage", workspace: ws).isEmpty)
        let badPid = "x /Applications/firesky-engine desktop-call --workspace \(ws) data_sync_tick"
        precondition(SyncHelperInstaller.roundProcessIDs(psOutput: badPid, workspace: ws).isEmpty)
        let rootPid = "1 /Applications/firesky-engine desktop-call --workspace \(ws) data_sync_tick"
        precondition(SyncHelperInstaller.roundProcessIDs(psOutput: rootPid, workspace: ws).isEmpty)
        let envPlist: [String: Any] = [
            "EnvironmentVariables": ["FIRESKY_WORKSPACE": ws],
            "ProgramArguments": ["/Applications/FireSky.app/Contents/MacOS/FireSkySyncHelper", "--sync-helper"],
        ]
        precondition(SyncHelperInstaller.workspaceFromPlist(envPlist) == ws)
        precondition(SyncHelperInstaller.workspaceFromPlist(["EnvironmentVariables": ["FIRESKY_WORKSPACE": "/tmp/other"]]) == "/tmp/other")
        let legacyPlist: [String: Any] = [
            "ProgramArguments": ["/Applications/FireSky.app/Contents/MacOS/FireSkySyncHelper", "--workspace", ws],
        ]
        precondition(SyncHelperInstaller.workspaceFromPlist(legacyPlist) == ws)
        precondition(SyncHelperInstaller.workspaceFromPlist([:]) == nil)
        let helperPath = "/Applications/FireSky.app/Contents/MacOS/FireSkySyncHelper"
        precondition(SyncHelperInstaller.installedIntervalMinutes(from: ["StartInterval": 300]) == 5)
        precondition(SyncHelperInstaller.installedIntervalMinutes(from: [:]) == 30)
        let matchingPlist: [String: Any] = [
            "EnvironmentVariables": ["FIRESKY_WORKSPACE": ws, "FIRESKY_HELPER_VERSION": "2.0.1"],
            "ProgramArguments": [helperPath, "--sync-helper"],
            "StartInterval": 300,
        ]
        precondition(!SyncHelperInstaller.needsUpgradeReload(
            plist: matchingPlist, expectedWorkspace: ws, expectedHelperPath: helperPath, expectedVersion: "2.0.1"))
        precondition(SyncHelperInstaller.needsUpgradeReload(
            plist: envPlist, expectedWorkspace: ws, expectedHelperPath: helperPath, expectedVersion: "2.0.1"))
        precondition(SyncHelperInstaller.needsUpgradeReload(
            plist: [
                "EnvironmentVariables": ["FIRESKY_WORKSPACE": ws, "FIRESKY_HELPER_VERSION": "2.0.0"],
                "ProgramArguments": [helperPath, "--sync-helper"],
            ],
            expectedWorkspace: ws, expectedHelperPath: helperPath, expectedVersion: "2.0.1"))
        precondition(SyncHelperInstaller.needsUpgradeReload(
            plist: [
                "EnvironmentVariables": ["FIRESKY_WORKSPACE": ws, "FIRESKY_HELPER_VERSION": "2.0.1"],
                "ProgramArguments": ["/Users/old/FireSky.app/Contents/MacOS/FireSkySyncHelper", "--sync-helper"],
            ],
            expectedWorkspace: ws, expectedHelperPath: helperPath, expectedVersion: "2.0.1"))
        precondition(!SyncHelperInstaller.needsUpgradeReload(
            plist: [
                "EnvironmentVariables": ["FIRESKY_WORKSPACE": "/tmp/other", "FIRESKY_HELPER_VERSION": "2.0.0"],
                "ProgramArguments": [helperPath, "--sync-helper"],
            ],
            expectedWorkspace: ws, expectedHelperPath: helperPath, expectedVersion: "2.0.1"))
        print("sync-helper checks: ok")
    }
}
