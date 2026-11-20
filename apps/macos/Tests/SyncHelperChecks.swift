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
        print("sync-helper checks: ok")
    }
}
