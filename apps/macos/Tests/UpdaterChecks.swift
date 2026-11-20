import Foundation

@main
enum UpdaterChecksMain {
    static func main() {
        UpdaterChecks.run()
    }
}

struct UpdaterChecks {
    static func run() {
        // tag 归一化
        precondition(Updater.normalizeTag("v1.5.0") == "1.5.0")
        precondition(Updater.normalizeTag("1.5.0") == "1.5.0")
        precondition(Updater.normalizeTag("v2.0") == "2.0")
        // 语义化版本比较（逐段数值，缺失段补 0）
        precondition(Updater.compareVersions("1.5.0", "1.4.0") == .orderedDescending)
        precondition(Updater.compareVersions("1.4.0", "1.4.0") == .orderedSame)
        precondition(Updater.compareVersions("1.4", "1.4.0") == .orderedSame)
        precondition(Updater.compareVersions("1.10.0", "1.9.9") == .orderedDescending)
        precondition(Updater.compareVersions("2.0.0", "1.9.9") == .orderedDescending)
        // 资产名匹配
        precondition(Updater.assetName(version: "1.5.0", architecture: "arm64")
                     == "FireSky-1.5.0-macos-arm64.dmg")
        // 架构探测（本机 arm64 或 x86_64 之一）
        let architecture = Updater.machineArchitecture()
        precondition(architecture == "arm64" || architecture == "x86_64", architecture)
        // release 解析：正常 + 架构资产缺失 + 草稿 + 校验和地址
        let release: [String: Any] = [
            "tag_name": "v1.5.0",
            "body": "更新说明\n第二行",
            "assets": [
                ["name": "FireSky-1.5.0-macos-arm64.dmg",
                 "browser_download_url": "https://github.com/xyl-g/FireSky/releases/download/v1.5.0/FireSky-1.5.0-macos-arm64.dmg",
                 "size": 210_000_000],
                ["name": "SHA256SUMS.txt",
                 "browser_download_url": "https://github.com/xyl-g/FireSky/releases/download/v1.5.0/SHA256SUMS.txt"],
            ],
        ]
        let parsed = Updater.parseRelease(release, architecture: "arm64")
        precondition(parsed?.version == "1.5.0")
        precondition(parsed?.notes.hasPrefix("更新说明") == true)
        precondition(parsed?.assetBytes == 210_000_000)
        precondition(parsed?.checksumURL != nil)
        precondition(parsed?.assetURL.absoluteString.contains("/releases/download/") == true)
        precondition(Updater.parseRelease(release, architecture: "x86_64") == nil)
        var draft = release
        draft["draft"] = true
        precondition(Updater.parseRelease(draft, architecture: "arm64") == nil)
        var prerelease = release
        prerelease["prerelease"] = true
        precondition(Updater.parseRelease(prerelease, architecture: "arm64") == nil)
        var tampered = release
        tampered["tag_name"] = "v../../etc"
        precondition(Updater.parseRelease(tampered, architecture: "arm64") == nil)
        // SHA256SUMS.txt 行解析与真实文件哈希一致
        let sums = "abc123  FireSky-1.4.0-macos-arm64.dmg\n\nde4d3f2a  FireSky-1.5.0-macos-arm64.dmg\n"
        let rows = sums.split(separator: "\n").compactMap { line -> String? in
            let parts = line.split(separator: " ", omittingEmptySubsequences: true)
            guard parts.count >= 2 else { return nil }
            let name = parts[1].trimmingCharacters(in: CharacterSet(charactersIn: "*"))
            return name == "FireSky-1.5.0-macos-arm64.dmg" ? String(parts[0]) : nil
        }
        precondition(rows == ["de4d3f2a"])
        let payload = Data("firesky".utf8)
        let temporary = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("firesky-updater-check-\(UUID().uuidString)")
        try? payload.write(to: temporary)
        if let digest = try? Updater.sha256(of: temporary) {
            precondition(digest == "a4dcddaefc731b421ab6b7ac4b119dfc02f68a4f10bddfa14dd39fc83205b97d",
                         digest)
        }
        try? FileManager.default.removeItem(at: temporary)
    }
}
