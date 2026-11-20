import AppKit
import SwiftUI

struct PreflightView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    private var isNowcast: Bool { store.requestKind == .nowcast }
    private var confirmTitle: String { isNowcast ? "确定分析" : "确定预测" }
    private var actions: PreparationActions {
        PreparationActions(ready: store.ready, busy: store.busy, hasSession: store.session != nil)
    }
    private var summary: String {
        if !store.ready {
            let missing = store.rows.filter { !$0.ready }.map(\.title).joined(separator: "、")
            return missing.isEmpty ? store.status : "需下载 \(missing)"
        }
        let updates = store.rows.filter(\.old).map(\.title).joined(separator: "、")
        return updates.isEmpty ? (isNowcast ? "数据已齐，可开始分析" : "数据已齐，可开始预测") : "数据已齐，可更新 \(updates)"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(isNowcast ? "实况准备" : "预测准备").font(.title2.weight(.semibold))
                    Text(store.name).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { store.refreshCheck() } label: { Image(systemName: "arrow.clockwise") }
                    .help("重新检查").buttonStyle(.borderless).disabled(store.busy)
            }.padding(.bottom, 16)
            if store.rows.isEmpty {
                HStack { ProgressView().controlSize(.small); Text("正在检查本地数据") }
                    .frame(maxWidth: .infinity, minHeight: 180)
            } else {
                ForEach(store.rows) { row in
                    HStack(spacing: 12) {
                        Image(systemName: row.source == "hsd" ? "antenna.radiowaves.left.and.right" : "externaldrive")
                            .font(.title3).foregroundStyle(.secondary).frame(width: 32)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(row.title).font(.body.weight(.medium))
                            Text(row.detail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Label(row.status, systemImage: !row.ready ? "arrow.down.circle" : row.old ? "arrow.clockwise" : "checkmark.circle.fill")
                            .font(.callout).foregroundStyle(!row.ready || row.old ? Color.primary : Color.green)
                    }.frame(height: 64)
                    if row.id != store.rows.last?.id { Divider() }
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                if store.checking {
                    ProgressView(value: store.percent, total: 100)
                    Text(store.status).font(.caption).foregroundStyle(.secondary)
                } else if !store.error.isEmpty {
                    Text(store.error).font(.callout).foregroundStyle(.red).lineLimit(3).textSelection(.enabled)
                } else {
                    HStack {
                        Label(summary, systemImage: store.ready ? "checkmark.circle" : "arrow.down.circle")
                            .fontWeight(.medium)
                        if store.publicationsPending { ProgressView().controlSize(.mini) }
                    }.font(.callout).foregroundStyle(.primary)
                }
            }.frame(minHeight: 38, alignment: .leading).padding(.top, 12)
            HStack {
                Button("取消") { store.closePreflight() }.keyboardShortcut(.cancelAction)
                Spacer()
                if actions.primary == .download {
                    Button("下载缺失数据", systemImage: "arrow.down.to.line") { store.prepare() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canDownload)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                        .keyboardShortcut(.defaultAction)
                    Button(confirmTitle) {}.disabled(true)
                        .help("需先补齐本次数据")
                        .accessibilityHint("需先补齐本次数据")
                } else {
                    Button("更新数据", systemImage: "arrow.down.to.line") { store.prepare() }
                        .disabled(!actions.canDownload)
                    Button(confirmTitle) { store.confirm() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canForecast)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                        .keyboardShortcut(.defaultAction)
                }
            }.padding(.top, 14)
                .animation(.easeInOut(duration: 0.18), value: store.ready)
        }.padding(26).frame(width: 490)
            .background(Color(nsColor: .windowBackgroundColor))
            .confirmationDialog(
                "数据周期确认",
                isPresented: $store.showCycleAgeConfirm,
                titleVisibility: .visible
            ) {
                Button("继续预测") { store.confirmCycleAgeAndForecast() }
                Button("返回更新数据", role: .cancel) {}
            } message: {
                Text("\(store.cycleAgeSummary) 的发布周期未核验或不是最新发布。可以继续预测（结果可能略有偏差），也可以返回并点击「更新数据」获取最新周期。")
            }
    }
}

struct InventoryView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack {
                    // 顶部只保留实际数据目录与刷新/打开；探测与同步在设置 → 数据。
                    VStack(alignment: .leading, spacing: 4) {
                        Text(store.workspace.isEmpty ? "数据目录未设置" : store.workspace)
                            .font(.caption).foregroundStyle(.secondary)
                            .lineLimit(1).truncationMode(.middle)
                            .help(store.workspace)
                        Text("已统计占用仅覆盖模式数据（GFS / EC / CAMS）；地形与卫星以目录为准。")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("同步设置", systemImage: "arrow.triangle.2.circlepath") {
                        store.page = .settings
                    }
                    .help("后台同步与下载探测已移到「设置 → 数据」")
                    Button { Task { await store.loadInventory() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }.help("刷新本地清单")
                    Button {
                        if !store.workspace.isEmpty {
                            NSWorkspace.shared.open(URL(fileURLWithPath: store.workspace))
                        }
                    } label: { Image(systemName: "folder") }.help("在访达中打开数据目录")
                }
                if !store.dataDeleteMessage.isEmpty {
                    Text(store.dataDeleteMessage)
                        .font(.caption)
                        .foregroundStyle(store.dataDeleteFailed ? Color.red : Color.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                TerrainPanelView()
                ForEach(Array(store.inventory.enumerated()), id: \.offset) { _, dataset in
                    let source = dataset["source"] as? String ?? ""
                    let title = source == "ecmwf" ? "EC" : source.uppercased()
                    let cycles = dataset["cycles"] as? JSONObject ?? [:]
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Image(systemName: "externaldrive").font(.title2).foregroundStyle(.secondary)
                            Text(title).font(.headline)
                            Spacer()
                            Text("\(dataset["total_file_count"] as? Int ?? 0) 个文件")
                            Text(String(format: "%.2f GB", (dataset["total_storage_mb"] as? Double ?? 0) / 1024))
                                .foregroundStyle(.secondary).monospacedDigit()
                            Button {
                                if let path = dataset["path"] as? String {
                                    NSWorkspace.shared.open(URL(fileURLWithPath: path))
                                }
                            } label: { Image(systemName: "folder") }.help("在访达中打开")
                            Button {
                                store.beginDataDelete(source: source, cycle: nil)
                            } label: { Image(systemName: "trash") }
                                .help("清空该数据源")
                                .disabled(store.busy || !store.online || store.dataDeleteRunning)
                        }
                        DisclosureGroup("\(cycles.count) 个周期", isExpanded: Binding(
                            get: { store.expandedInventory.contains(source) },
                            set: { if $0 { store.expandedInventory.insert(source) } else { store.expandedInventory.remove(source) } }
                        )) {
                            ForEach(cycles.keys.sorted().reversed(), id: \.self) { key in
                                let item = cycles[key] as? JSONObject ?? [:]
                                HStack {
                                    Text(key).monospaced()
                                    Spacer()
                                    Text("\(item["files"] as? Int ?? 0) 个文件").foregroundStyle(.secondary)
                                    Button {
                                        store.beginDataDelete(source: source, cycle: key)
                                    } label: { Image(systemName: "trash") }
                                        .buttonStyle(.borderless)
                                        .help("删除该周期")
                                        .disabled(store.busy || !store.online || store.dataDeleteRunning)
                                }.font(.callout).padding(.vertical, 5)
                            }
                        }
                    }
                    Divider()
                }
                if store.inventory.isEmpty && store.error.isEmpty { ProgressView().frame(maxWidth: .infinity) }
                if !store.error.isEmpty { Text(store.error).foregroundStyle(.red) }
            }.padding(30)
        }
        .confirmationDialog("删除本地数据？", isPresented: Binding(
            get: { store.pendingDataDelete != nil && store.dataDeletePreview != nil },
            set: { if !$0 { store.cancelDataDelete() } }
        )) {
            Button("删除", role: .destructive) { store.executeDataDelete() }
                .disabled(store.dataDeleteRunning)
            Button("取消", role: .cancel) { store.cancelDataDelete() }
        } message: {
            Text(deleteConfirmationText)
        }
    }

    /// 确认文案：具体范围 + 文件数 + 预计释放空间；占用中文件会跳过。
    private var deleteConfirmationText: String {
        guard let preview = store.dataDeletePreview,
              let scope = store.pendingDataDelete else {
            return "正在生成删除预览…"
        }
        let source = scope["source"] as? String ?? ""
        let title = source == "ecmwf" ? "EC" : source.uppercased()
        let files = (preview["file_count"] as? NSNumber)?.intValue ?? 0
        let mb = (preview["total_mb"] as? NSNumber)?.doubleValue ?? 0
        let protected = (preview["protected"] as? [Any])?.count ?? 0
        if let cycle = scope["cycle"] as? String {
            return "删除 \(title) 的周期 \(cycle)：共 \(files) 个文件，预计释放 \(String(format: "%.1f", mb)) MB。占用中的文件会跳过；报告与任务记录不受影响。"
        }
        var text = "清空 \(title) 的全部本地数据：共 \(files) 个文件，预计释放 \(String(format: "%.1f", mb)) MB。占用中的文件会跳过。"
        if protected > 0 {
            text += "当前有 \(protected) 个文件被下载任务占用，将跳过。"
        }
        return text
    }
}

/// 数据管理页的 SRTM 地形面板（方案 7）：目标瓦片、本地状态与显式下载。
/// 下载主动作为全球批量（用户口径）：确认估算后整域下载，跳过已有瓦片，
/// 海洋瓦片自动标记缺失；点级下载保留为次级入口。
struct TerrainPanelView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "mountain.2").font(.title2).foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text("地形 SRTM").font(.headline)
                    Text(terrainSubtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if store.terrainLoading || store.terrainEstimateLoading {
                    ProgressView().controlSize(.small)
                }
                if store.terrainDownloading {
                    Button("取消", systemImage: "stop.fill") { store.cancelTerrainDownload() }
                        .disabled(store.terrainCancelling)
                } else {
                    // 区域预设（用户口径：按区域下载；全球为其中一个预设）。
                    Picker("", selection: $store.terrainRegion) {
                        ForEach(TerrainRegion.allCases) { region in
                            Text(region.rawValue).tag(region)
                        }
                    }.pickerStyle(.menu).frame(width: 104)
                        .disabled(!store.online || store.busy)
                        .accessibilityLabel("下载区域")
                    Button("下载区域地形", systemImage: "globe") {
                        store.loadTerrainEstimate()
                    }
                    .disabled(!store.online || store.busy)
                    .help("确认估算后下载所选区域的 SRTM 瓦片（跳过已有；可随时取消续传）")
                    Button("仅当前地点", systemImage: "arrow.down.to.line") {
                        store.downloadTerrainPoint()
                    }
                    .buttonStyle(.borderless)
                    .disabled(!store.online || store.busy || coverageMissing)
                    .help("只下载当前地点对应的一块瓦片")
                }
                Button { store.loadTerrain() } label: { Image(systemName: "arrow.clockwise") }
                    .help("刷新地形状态")
                    .disabled(!store.online || store.terrainDownloading)
            }
            if store.terrainDownloading {
                if isBulkRun {
                    globalProgress
                } else {
                    pointProgress
                }
            }
            if !store.terrainMessage.isEmpty {
                Text(store.terrainMessage).font(.caption)
                    .foregroundStyle(store.terrainMessageFailed ? Color.red : Color.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            localTilesLine
        }.padding(18)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.72),
                        in: RoundedRectangle(cornerRadius: 10))
            .confirmationDialog(downloadConfirmTitle, isPresented: Binding(
                get: { store.showGlobalTerrainConfirm },
                set: { if !$0 { store.showGlobalTerrainConfirm = false } }
            )) {
                Button("开始下载", role: .destructive) {
                    store.downloadTerrain(region: store.terrainRegion)
                }
                Button("取消", role: .cancel) { store.showGlobalTerrainConfirm = false }
            } message: {
                Text(globalConfirmText)
            }
    }

    private var isBulkRun: Bool {
        store.terrainProgress["candidates"] != nil
    }

    private var downloadConfirmTitle: String {
        store.terrainRegion == .global ? "下载全球地形数据？" : "下载\(store.terrainRegion.rawValue)地形数据？"
    }

    private var coverageMissing: Bool {
        store.terrain?["coverage_ok"] as? Bool == false
    }

    /// 全球批量进度行：总体百分比 + 每类计数 + 当前瓦片。
    private var globalProgress: some View {
        let progress = store.terrainProgress
        let percent = (progress["percent"] as? NSNumber)?.doubleValue ?? 0
        let downloaded = (progress["downloaded"] as? NSNumber)?.intValue ?? 0
        let candidates = (progress["candidates"] as? NSNumber)?.intValue ?? 0
        let absent = (progress["absent"] as? NSNumber)?.intValue ?? 0
        let failed = (progress["failed"] as? NSNumber)?.intValue ?? 0
        let existing = (progress["existing"] as? NSNumber)?.intValue ?? 0
        let bytes = (progress["received_bytes"] as? NSNumber)?.int64Value ?? 0
        let current = progress["current_tile"] as? String ?? ""
        return VStack(alignment: .leading, spacing: 6) {
            ProgressView(value: percent, total: 100)
            HStack {
                Text("\(Int(percent))% · 已下载 \(downloaded)/\(candidates) · 已有 \(existing) · 缺失 \(absent) · 失败 \(failed)")
                Spacer()
                if !current.isEmpty { Text("当前 \(current)") }
            }.font(.caption).foregroundStyle(.secondary).monospacedDigit()
            Text(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file))
                .font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
    }

    /// 点级进度行：单瓦片下载百分比或校验阶段。
    private var pointProgress: some View {
        HStack(spacing: 8) {
            if let percent = (store.terrainProgress["percent"] as? NSNumber)?.doubleValue {
                ProgressView(value: percent, total: 100).frame(width: 180)
                Text("\(Int(percent))%").monospacedDigit().font(.caption)
            } else {
                ProgressView().controlSize(.small)
                Text(store.terrainProgress["stage"] as? String == "verifying"
                     ? "校验瓦片数据…" : "下载中…").font(.caption)
            }
            if let received = (store.terrainProgress["received_bytes"] as? NSNumber)?.int64Value {
                let size = ByteCountFormatter.string(fromByteCount: received, countStyle: .file)
                if let total = (store.terrainProgress["total_bytes"] as? NSNumber)?.int64Value {
                    Text("\(size) / \(ByteCountFormatter.string(fromByteCount: total, countStyle: .file))")
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                } else {
                    Text(size).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                }
            }
        }
    }

    /// 本地瓦片行：少量列出名称，大量只报数量。
    @ViewBuilder
    private var localTilesLine: some View {
        let count = (store.terrain?["tile_count"] as? NSNumber)?.intValue ?? 0
        if count > 8 {
            Text("本地已有 \(count) 块瓦片。").font(.caption).foregroundStyle(.secondary)
        } else if let tiles = store.terrain?["tiles"] as? [JSONObject], !tiles.isEmpty {
            Text("本地瓦片：\(tiles.map { $0["name"] as? String ?? "" }.joined(separator: "、"))")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var terrainSubtitle: String {
        guard let terrain = store.terrain else {
            return store.online ? "全球瓦片下载后会覆盖任意地点，无需再按点下载。" : "引擎未连接。"
        }
        if terrain["coverage_ok"] as? Bool == false {
            return "当前纬度不在 SRTM 覆盖范围（-56 ≤ 纬度 < 60）。"
        }
        guard let tile = terrain["tile"] as? JSONObject else {
            return "显示本地已有地形瓦片。"
        }
        let name = tile["name"] as? String ?? ""
        let exists = tile["exists"] as? Bool == true
        let location = String(format: "%.4f° N, %.4f° E", store.lat, store.lon)
        return "目标 \(store.name)（\(location)）· 瓦片 \(name) · \(exists ? "已有本地瓦片" : "未下载")"
    }

    /// 确认文案：所选区域候选数 + 抽样估算体积 + 已有数量 + 续传说明。
    private var globalConfirmText: String {
        let global = store.terrainEstimate?["global"] as? JSONObject ?? [:]
        let candidates = (global["candidates"] as? NSNumber)?.intValue ?? 0
        let existing = (global["existing"] as? NSNumber)?.intValue ?? 0
        var text = "将下载\(store.terrainRegion.rawValue)的 SRTM 瓦片：候选 \(candidates) 块，本地已有 \(existing) 块会自动跳过（海洋上的瓦片是很小的空值瓦片，同样会下载）。下载耗时较长且占用磁盘，可随时取消，重新开始会跳过已下载瓦片。"
        if let gb = (global["estimated_gb"] as? NSNumber)?.doubleValue {
            text += String(format: "按抽样估算体积约 %.0f GB（抽样混合了海洋小瓦片与陆地大瓦片，实际体积以运行时用量为准）。", gb)
        } else {
            text += "暂时无法估算体积（网络或服务不可用），开始后会显示实际用量。"
        }
        return text
    }
}

/// 设置分类：通用 / 数据 / 位置与海拔 / 高级。
/// 数据相关（下载源、CAMS、连通性、同步、保留、目录）统一归「数据」。
enum SettingsCategory: String, CaseIterable, Identifiable {
    case general, data, location, advanced
    var id: String { rawValue }
    var title: String {
        switch self {
        case .general: "通用"
        case .data: "数据"
        case .location: "位置与海拔"
        case .advanced: "高级"
        }
    }
    var symbol: String {
        switch self {
        case .general: "paintpalette"
        case .data: "externaldrive"
        case .location: "mountain.2"
        case .advanced: "wrench.and.screwdriver"
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: Store
    @EnvironmentObject private var updater: Updater
    /// 分类与卸载确认状态都在 store：跨分区动作（CAMS 提醒跳转、助手卸载）可直接落位。
    private var category: Binding<SettingsCategory> {
        Binding(get: { store.settingsCategory }, set: { store.settingsCategory = $0 })
    }

    var body: some View {
        HStack(spacing: 0) {
            // 固定宽度分类栏；窄窗口仍可用，分类切换只显示一个分类的内容。
            VStack(alignment: .leading, spacing: 4) {
                ForEach(SettingsCategory.allCases) { item in
                    Button {
                        category.wrappedValue = item
                    } label: {
                        HStack(spacing: 9) {
                            Image(systemName: item.symbol)
                                .font(.system(size: 13)).frame(width: 18)
                            Text(item.title).font(.system(size: 13,
                                weight: category.wrappedValue == item ? .semibold : .regular))
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, 10).frame(height: 34)
                        .foregroundStyle(category.wrappedValue == item ? warm : Color.primary.opacity(0.72))
                        .background(category.wrappedValue == item ? warm.opacity(0.12) : Color.clear,
                                    in: RoundedRectangle(cornerRadius: 7))
                    }.buttonStyle(.plain)
                        .accessibilityAddTraits(category.wrappedValue == item ? .isSelected : [])
                }
                Spacer(minLength: 0)
            }.padding(.horizontal, 12).padding(.top, 16).frame(width: 150)
            Divider()
            // 切换分类保留各表单草稿：所有分区都常驻在 store，不随视图销毁。
            ScrollView {
                Group {
                    switch category.wrappedValue {
                    case .general: generalSection
                    case .data: dataSection
                    case .location: locationSection
                    case .advanced: advancedSection
                    }
                }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .confirmationDialog("卸载后台同步助手？", isPresented: $store.confirmUninstallHelper) {
            Button("卸载助手", role: .destructive) { store.uninstallHelper() }
            Button("保留助手", role: .cancel) {}
        } message: {
            Text("卸载后，关闭应用时的后台同步将停止；已下载的数据会保留，可随时重新安装助手。")
        }
    }

    // MARK: - 通用

    private var generalSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            settingsHeader("通用")
            VStack(alignment: .leading, spacing: 8) {
                Text("主题").font(.callout.weight(.medium))
                Picker("", selection: $store.appearance) {
                    Text("跟随系统").tag("system")
                    Text("浅色").tag("light")
                    Text("深色").tag("dark")
                }.pickerStyle(.segmented).frame(width: 300)
                    .onChange(of: store.appearance) { _, _ in store.saveAppearance() }
                    .accessibilityLabel("主题")
            }
            Divider()
            updaterBlock
        }
    }

    /// 软件更新块：当前版本、检查/下载/安装状态与自动检查开关。
    @ViewBuilder
    private var updaterBlock: some View {
        let currentVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        VStack(alignment: .leading, spacing: 10) {
            Text("软件更新").font(.callout.weight(.medium))
            HStack {
                Text("当前版本 \(currentVersion)").font(.caption).foregroundStyle(.secondary)
                if updater.phase == .checking { ProgressView().controlSize(.small) }
                Spacer()
                Button("检查更新") { updater.checkForUpdates(interactive: true) }
                    .disabled(updater.phase == .checking || updater.phase == .downloading
                              || updater.phase == .installing)
            }
            switch updater.phase {
            case .current:
                Text(updater.message).font(.caption).foregroundStyle(.secondary)
            case .idle, .checking:
                EmptyView()
            case .available:
                if let info = updater.info {
                    Text("发现新版本 \(info.version)，可在下载后自动安装。")
                        .font(.callout.weight(.medium)).foregroundStyle(Color.green)
                    if !info.notes.isEmpty {
                        DisclosureGroup("发布说明") {
                            Text(info.notes)
                                .font(.caption).foregroundStyle(.secondary)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    Button("下载并安装", systemImage: "arrow.down.circle.fill") {
                        updater.downloadAndInstall()
                    }
                    .help("下载 DMG、校验 SHA256 后自动安装到「应用程序」并重启")
                }
            case .downloading:
                VStack(alignment: .leading, spacing: 6) {
                    ProgressView(value: max(Double(updater.receivedBytes), 1),
                                 total: max(Double(updater.totalBytes), 1))
                    HStack {
                        Text("已下载 \(ByteCountFormatter.string(fromByteCount: updater.receivedBytes, countStyle: .file)) / \(ByteCountFormatter.string(fromByteCount: updater.totalBytes, countStyle: .file))")
                            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        Spacer()
                        Button("取消") { updater.cancelDownload() }
                    }
                }
            case .installing:
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("正在安装，完成后应用将自动重启。").font(.caption).foregroundStyle(.secondary)
                }
            case .failed:
                Text(updater.message).font(.caption).foregroundStyle(.red)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                Button("重试") { updater.checkForUpdates(interactive: true) }
            }
            Toggle("启动时自动检查更新", isOn: $updater.autoCheck)
                .font(.caption)
                .disabled(updater.phase == .downloading || updater.phase == .installing)
        }
    }

    // MARK: - 数据

    private var dataSection: some View {
        VStack(alignment: .leading, spacing: 26) {
            settingsHeader("数据")
            dataSourcesBlock
            Divider()
            camsBlock
            Divider()
            DownloadProbeView()
            Divider()
            dataSyncBlock
            Divider()
            retentionBlock
            Divider()
            dataDirectoryBlock
        }
    }

    private var dataSourcesBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            settingsBlockTitle("下载源")
            Picker("EC 下载源", selection: $store.ecmwfSource) {
                Text("ECMWF 官方").tag("ecmwf")
                Text("AWS 欧洲镜像").tag("aws")
                Text("Google Cloud 镜像").tag("google")
            }.pickerStyle(.radioGroup).disabled(store.probingDownloads)
                .accessibilityLabel("EC 下载源")
            Text("EC 下载源同时用于下载探测、数据准备和预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var camsBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            settingsBlockTitle("CAMS · Copernicus ADS")
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: store.camsConfigured ? "checkmark.circle.fill" : "key")
                    .foregroundStyle(store.camsConfigured ? Color.green : Color.secondary)
                Text(store.camsConfigurationDescription).fixedSize(horizontal: false, vertical: true)
                Spacer()
                Button { Task { await store.refreshCAMSConfiguration() } } label: {
                    Image(systemName: "arrow.clockwise")
                }.buttonStyle(.borderless).help("重新检查 CAMS 配置")
                    .disabled(!store.online || store.savingCAMS)
            }
            HStack(spacing: 10) {
                SecureField("粘贴 ADS Personal Access Token", text: $store.camsKeyDraft)
                    .textFieldStyle(.roundedBorder).onSubmit { store.saveCAMSKey() }
                    .disabled(!store.canSaveCAMS || store.savingCAMS || store.probingDownloads)
                if store.savingCAMS { ProgressView().controlSize(.small) }
                Button("保存 Key", systemImage: "key.fill") { store.saveCAMSKey() }
                    .disabled(!store.online || !store.canSaveCAMS || store.savingCAMS || store.probingDownloads
                              || store.camsKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            Text("仅以明文保存在本机配置文件，文件权限 0600；不会写入工程或显示已有 Key。")
                .font(.caption).foregroundStyle(.secondary)
            if store.camsConfiguration != nil && !store.canSaveCAMS {
                Text("环境变量正在覆盖配置。修改 CDSAPI_KEY / CDSAPI_URL 后需重新启动应用。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if !store.camsMessage.isEmpty {
                Text(store.camsMessage).font(.callout)
                    .foregroundStyle(store.camsSaveFailed ? Color.red : Color.secondary)
            }
            DisclosureGroup("配置教程", isExpanded: $store.showCAMSTutorial) {
                VStack(alignment: .leading, spacing: 14) {
                    tutorialStep("1", "注册并登录 ADS", detail: "使用 ECMWF 账号登录 Atmosphere Data Store。") {
                        Link("打开 ADS", destination: URL(string: "https://ads.atmosphere.copernicus.eu/")!)
                    }
                    tutorialStep("2", "复制 Personal Access Token", detail: "在官方 API 配置页复制 key 后面的 Token，不要复制 url 或整段配置。") {
                        Link("获取 Token", destination: URL(string: "https://ads.atmosphere.copernicus.eu/how-to-api")!)
                    }
                    tutorialStep("3", "接受 CAMS 数据集条款", detail: "在数据集 Download 页底部接受 Terms of Use，完成网页端授权。") {
                        Link("打开数据集", destination: URL(string: "https://ads.atmosphere.copernicus.eu/datasets/cams-global-atmospheric-composition-forecasts?tab=download")!)
                    }
                    tutorialStep("4", "返回 FireSky 保存 Key", detail: "粘贴到上方输入框并保存，再到预测准备窗口下载数据。") {
                        EmptyView()
                    }
                    Text("这里只检查本地配置是否完整；Token 是否有效、数据集是否已授权，在实际下载时验证。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(.top, 12).padding(.bottom, 6)
            }
        }
    }

    private var dataSyncBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            settingsBlockTitle("后台同步")
            Toggle("启用后台同步", isOn: $store.dataSyncEnabled)
            if store.dataSyncEnabled && !store.dataSyncRetentionConfigured {
                Label("未配置缓存保留策略，缓存将随同步增长；可在下方「存储与保留」中设置。", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 12) {
                Toggle("CAMS", isOn: $store.dataSyncCAMS).disabled(!store.dataSyncEnabled)
                Toggle("GFS", isOn: $store.dataSyncGFS).disabled(!store.dataSyncEnabled)
                Toggle("EC", isOn: $store.dataSyncEC).disabled(!store.dataSyncEnabled)
            }
            HStack(spacing: 12) {
                dataSyncField("同步间隔", text: $store.dataSyncInterval, suffix: "分钟")
                dataSyncField("GFS/EC 时效上限", text: $store.dataSyncLeadHorizon, suffix: "小时")
            }
            Picker("锚定区域", selection: $store.dataSyncAnchor) {
                Text("未设置").tag("")
                ForEach(store.regionDefinitions.indices, id: \.self) { index in
                    let region = store.regionDefinitions[index]
                    Text(region["label"] as? String ?? region["code"] as? String ?? "")
                        .tag(region["code"] as? String ?? "")
                }
            }
            Text("间隔 30–1440 分钟；时效上限仅 GFS/EC 需要（3–240 小时、3 的倍数），CAMS 自动拉齐全部时效；锚定区域决定 CAMS 洲包络与 GFS/EC 下载范围。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                if store.savingDataSync { ProgressView().controlSize(.small) }
                if !store.dataSyncMessage.isEmpty {
                    Text(store.dataSyncMessage).font(.caption)
                        .foregroundStyle(store.dataSyncSaveFailed ? Color.red : Color.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else if store.dataSyncConfigured {
                    Text(store.dataSyncEnabled ? "后台同步已启用" : "已配置，当前关闭")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("立即同步", systemImage: "arrow.triangle.2.circlepath") {
                    store.runDataSyncNow()
                }
                .disabled(!store.online || store.busy || store.dataSyncRunning)
                .help(store.dataSyncEnabled
                      ? "立即同步已启用的数据源（可解除失败熔断）"
                      : "先启用并保存后台同步")
                Button("保存同步设置", systemImage: "externaldrive.badge.checkmark") {
                    store.saveDataSyncSettings()
                }
                .disabled(!store.online || store.savingDataSync || store.busy)
            }
            if store.dataSyncLoaded {
                VStack(alignment: .leading, spacing: 8) {
                    settingsBlockTitle("同步状态")
                    dataSyncSourceRow("cams", "CAMS")
                    dataSyncSourceRow("gfs", "GFS")
                    dataSyncSourceRow("ec", "EC")
                }
            }
            Divider()
            VStack(alignment: .leading, spacing: 8) {
                settingsBlockTitle("后台助手（关闭应用后仍同步）")
                Text("安装为系统 launchd 代理，按同步间隔在后台自动拉齐最新已发布周期。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if store.helperAvailable {
                    HStack(spacing: 8) {
                        if store.helperInstalled {
                            Label(store.helperLoaded ? "已安装并运行" : "已安装（未加载）",
                                  systemImage: store.helperLoaded ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                                .font(.caption)
                                .foregroundStyle(store.helperLoaded ? Color.green : Color.orange)
                        } else {
                            Label("未安装", systemImage: "circle").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if store.helperBusy { ProgressView().controlSize(.small) }
                        Button(store.helperInstalled ? "重新安装" : "安装助手", systemImage: "arrow.down.app") {
                            store.installHelper()
                        }
                        .disabled(store.helperBusy || store.workspace.isEmpty)
                        if store.helperInstalled {
                            Button("卸载", systemImage: "trash") { store.confirmUninstallHelper = true }
                                .disabled(store.helperBusy)
                        }
                        Button("刷新", systemImage: "arrow.clockwise") { store.refreshHelperStatus() }
                            .disabled(store.helperBusy)
                    }
                    if let summary = store.helperStatus["summary"] as? String, !summary.isEmpty {
                        let run = store.helperStatus["last_run_utc"] as? String ?? ""
                        let prefix = store.helperInstalled ? "上次后台运行" : "卸载前最后一次后台运行"
                        Text("\(prefix) \(dataSyncTimeText(run))：\(summary)")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !store.helperMessage.isEmpty {
                        Text(store.helperMessage).font(.caption)
                            .foregroundStyle(store.helperMessageFailed ? Color.red : Color.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    Text("当前为源码运行环境，后台助手仅在正式 FireSky 安装包中可用。")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var retentionBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            settingsBlockTitle("存储与保留")
            Text("自动清理按周期、扫描或生成时间滚动保留；新数据校验成功后才会清理旧文件。留空表示不自动清理。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                retentionField("GFS 周期", text: $store.retentionGFS, suffix: "个")
                retentionField("EC 周期", text: $store.retentionECMWF, suffix: "个")
                retentionField("CAMS 周期", text: $store.retentionCAMS, suffix: "个")
            }
            HStack(spacing: 12) {
                retentionField("卫星数据", text: $store.retentionSatelliteDays, suffix: "天")
                retentionField("历史报告", text: $store.retentionReportDays, suffix: "天")
                retentionField("报告地图", text: $store.retentionMapDays, suffix: "天")
            }
            HStack {
                if store.savingRetention { ProgressView().controlSize(.small) }
                if !store.retentionMessage.isEmpty {
                    Text(store.retentionMessage)
                        .font(.caption)
                        .foregroundStyle(store.retentionSaveFailed ? Color.red : Color.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else if store.retentionConfigured {
                    Text("已启用自定义保留策略").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("保存保留设置", systemImage: "externaldrive.badge.checkmark") {
                    store.saveRetentionSettings()
                }
                .disabled(!store.online || store.savingRetention || store.busy)
            }
        }
    }

    /// 数据目录独立分区：选择目录后通过「重新连接」生效。
    private var dataDirectoryBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            settingsBlockTitle("数据目录")
            TextField("数据目录", text: $store.workspace)
            HStack {
                Button("选择目录", systemImage: "folder") {
                    let panel = NSOpenPanel()
                    panel.canChooseFiles = false
                    panel.canChooseDirectories = true
                    if panel.runModal() == .OK, let url = panel.url {
                        store.workspace = url.path
                    }
                }
                Spacer()
                Button("重新连接", systemImage: "arrow.clockwise") { Task { await store.connect() } }
                    .disabled(store.busy || store.probingDownloads)
                    .help("应用新目录或引擎路径后重新连接引擎")
            }
        }
    }

    // MARK: - 位置与海拔

    private var locationSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            settingsHeader("位置与海拔")
            VStack(alignment: .leading, spacing: 10) {
                Text("海拔来源").font(.callout.weight(.medium))
                Picker("", selection: $store.elevationSource) {
                    Text("自动（HTTP 优先，SRTM 兜底）").tag("auto")
                    Text("仅 HTTP（open-meteo）").tag("http")
                    Text("SRTM 下载（本地时区 + 瓦片）").tag("srtm")
                }.pickerStyle(.radioGroup).accessibilityLabel("海拔来源")
                Text("「自动」先尝试 open-meteo 海拔接口，失败时回退本地时区 + SRTM 瓦片；「仅 HTTP」不下载瓦片；「SRTM 下载」首次使用会按需下载地形瓦片（每块约数 MB）。设置对之后的海拔获取与预测生效。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("地形数据可在「数据管理 → SRTM 地形」查看与管理。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - 高级

    private var advancedSection: some View {
        VStack(alignment: .leading, spacing: 18) {
            settingsHeader("高级")
            VStack(alignment: .leading, spacing: 8) {
                Text("Rust 引擎").font(.callout.weight(.medium))
                TextField("留空使用内置引擎", text: $store.engineBinary)
                Text("自定义引擎路径仅供开发调试；日常使用保持留空即可。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - 共用小组件

    private func settingsHeader(_ title: String) -> some View {
        Text(title).font(.title2.weight(.semibold))
    }

    private func settingsBlockTitle(_ title: String) -> some View {
        Text(title).font(.headline)
    }

    private func retentionField(_ title: String, text: Binding<String>, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 5) {
                TextField("不限制", text: text)
                    .textFieldStyle(.roundedBorder)
                    .frame(minWidth: 64)
                Text(suffix).font(.caption).foregroundStyle(.secondary)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private func dataSyncField(_ title: String, text: Binding<String>, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 5) {
                TextField("未设置", text: text)
                    .textFieldStyle(.roundedBorder)
                    .frame(minWidth: 64)
                Text(suffix).font(.caption).foregroundStyle(.secondary)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    /// 每源同步状态行（设计 11.1 状态行）：last_cycle + last_result 摘要 + 熔断提示。
    private func dataSyncSourceRow(_ name: String, _ label: String) -> some View {
        let state = store.dataSyncState[name] as? JSONObject ?? [:]
        let result = state["last_result"] as? JSONObject ?? [:]
        let status = result["status"] as? String ?? ""
        let files = (result["files"] as? NSNumber)?.intValue ?? 0
        let cycle = state["last_cycle"] as? String ?? ""
        let failures = (state["consecutive_failures"] as? NSNumber)?.intValue ?? 0
        let suspended = state["suspended"] as? Bool ?? false
        let badge: String
        let color: Color
        switch status {
        case "synced": badge = "已同步"; color = .green
        case "current": badge = "已是最新"; color = .green
        case "failed": badge = "失败"; color = .red
        case "cancelled": badge = "已取消"; color = .secondary
        default: badge = "从未同步"; color = .secondary
        }
        var details: [String] = []
        if !cycle.isEmpty { details.append("最近周期 \(cycle)") }
        if files > 0 { details.append("\(files) 个文件") }
        if let attempt = state["last_attempt_utc"] as? String, !attempt.isEmpty {
            details.append("上次同步 \(dataSyncTimeText(attempt))")
        }
        if let errors = result["errors"] as? [String], let first = errors.first, !first.isEmpty {
            details.append("最近错误：\(first)")
        }
        if failures > 0 { details.append("连续失败 \(failures) 次") }
        if suspended { details.append("已暂停：立即同步或重新保存设置可解锁") }
        let live = store.dataSyncRunning ? (store.dataSyncProgress[name] as? JSONObject) : nil
        let livePercent = (live?["percent"] as? NSNumber)?.doubleValue ?? 0
        let liveMessage = live?["message"] as? String ?? ""
        return HStack(alignment: .top, spacing: 10) {
            Text(label).font(.callout.weight(.medium)).frame(width: 44, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                if live != nil {
                    HStack(spacing: 6) {
                        ProgressView(value: livePercent, total: 100).frame(width: 90)
                        Text("\(Int(livePercent))%").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    }
                    if !liveMessage.isEmpty {
                        Text(liveMessage).font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    Text(badge).font(.callout).foregroundStyle(color)
                }
                if !details.isEmpty {
                    Text(details.joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
        }
    }

    /// ISO8601 UTC → 本地短时间（月日 时:分），用于「上次同步」展示。
    /// UX #4：统一走共享的 historyTimeText（兼容小数秒时间戳）。
    private func dataSyncTimeText(_ iso: String) -> String {
        historyTimeText(iso)
    }

    private func tutorialStep<Content: View>(_ number: String, _ title: String, detail: String,
                                             @ViewBuilder action: () -> Content) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(number).font(.caption.weight(.semibold)).foregroundStyle(.secondary).frame(width: 18)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.callout.weight(.medium))
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                action().font(.caption)
            }
        }
    }
}
