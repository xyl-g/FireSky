import AppKit
import SwiftUI

struct PreflightView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    private var isNowcast: Bool { store.requestKind == .nowcast }
    private var confirmTitle: String { L10n.t(isNowcast ? "确定分析" : "确定预测") }
    private var actions: PreparationActions {
        PreparationActions(ready: store.ready, busy: store.busy, hasSession: store.session != nil)
    }
    private var summary: String {
        if !store.ready {
            let missing = store.rows.filter { !$0.ready }.map(\.title).joined(separator: "、")
            return missing.isEmpty ? store.status
            : L10n.tf("需下载 %@", missing)
        }
        let updates = store.rows.filter(\.old).map(\.title).joined(separator: "、")
        return updates.isEmpty
            ? (isNowcast ? L10n.t("数据已齐，可开始分析") : L10n.t("数据已齐，可开始预测"))
            : L10n.tf("数据已齐，可更新 %@", updates)
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
                    .help(L10n.t("重新检查")).accessibilityLabel(L10n.t("重新检查"))
                    .buttonStyle(.borderless).disabled(store.busy)
            }.padding(.bottom, 16)
            if store.rows.isEmpty {
                HStack { ProgressView().controlSize(.small); Text(L10n.t("正在检查本地数据")) }
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
                        StatusChip(
                            status: !row.ready ? "pending" : row.old ? "interrupted" : "done",
                            title: row.status,
                        )
                    }.frame(minHeight: 64, alignment: .leading)
                    if row.id != store.rows.last?.id { Divider() }
                }
            }
            if !store.rows.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Label(L10n.t("下载前先知道"), systemImage: "info.circle")
                        .fireSkyFont(size: 11, weight: .semibold, relativeTo: .caption)
                        .foregroundStyle(.primary)
                    Text(L10n.t("首次下载耗时取决于本机缺失的数据集与网络。上方只列本次预测所需文件；已下载文件不会重复下载，过程中可取消，失败项会保留并提示重试。"))
                        .fireSkyFont(size: 11, relativeTo: .caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if !store.camsConfigured {
                        Text(L10n.t("CAMS Key 未配置时仍可使用本地缓存；如需气溶胶数据，可在设置中配置后重新预检。"))
                            .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(10)
                .background(Color.primary.opacity(scheme == .dark ? 0.05 : 0.035),
                            in: RoundedRectangle(cornerRadius: 8))
                .overlay {
                    RoundedRectangle(cornerRadius: 8)
                        .strokeBorder(warm.opacity(0.18), lineWidth: 1)
                }
                .padding(.top, 10)
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
                Button(L10n.t("取消")) { store.closePreflight() }.keyboardShortcut(.cancelAction)
                Spacer()
                if actions.primary == .download {
                    Button(L10n.t("下载缺失数据"), systemImage: "arrow.down.to.line") { store.prepare() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canDownload)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                        .keyboardShortcut(.defaultAction)
                    Button(confirmTitle) {}.disabled(true)
                        .help(L10n.t("需先补齐本次数据"))
                        .accessibilityHint("需先补齐本次数据")
                } else {
                    Button(L10n.t("更新数据"), systemImage: "arrow.down.to.line") { store.prepare() }
                        .disabled(!actions.canDownload)
                    Button(confirmTitle) { store.confirm() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canForecast)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                        .keyboardShortcut(.defaultAction)
                }
            }.padding(.top, 14)
                .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: store.ready)
        }.padding(26).frame(width: dynamicTypeSize.isAccessibilitySize ? 620 : 490)
            .background(Color(nsColor: .windowBackgroundColor))
            .sheet(isPresented: $store.showCycleAgeConfirm) {
                CycleAgeConfirmSheet()
                    .environmentObject(store)
            }
    }
}

private struct CycleAgeConfirmSheet: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme

    private var flaggedRows: [DataRow] {
        store.rows.filter { $0.ready && (!$0.verified || !$0.latest) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 12)
                        .fill(warm.opacity(scheme == .dark ? 0.18 : 0.12))
                        .frame(width: 42, height: 42)
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 19, weight: .semibold))
                        .foregroundStyle(warm)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(L10n.t("数据周期确认"))
                        .font(.title3.weight(.semibold))
                    Text(L10n.text(
                        "以下本地数据源尚未核验为最新发布周期；使用旧周期可立即出图，但临近起报切换时结果可能略有偏差。",
                        "The datasets below are unverified or behind the latest published cycle. Continuing now uses local cache and may slightly differ near a cycle switch."
                    ))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }

            VStack(spacing: 0) {
                ForEach(Array(flaggedRows.enumerated()), id: \.element.id) { index, row in
                    HStack(spacing: 10) {
                        Image(systemName: row.source == "hsd" ? "antenna.radiowaves.left.and.right" : "externaldrive")
                            .font(.callout).foregroundStyle(.secondary).frame(width: 22)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.title).font(.callout.weight(.semibold))
                            Text(row.detail).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        StatusChip(status: row.old ? "interrupted" : "pending", title: row.status)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 10)
                    if index < flaggedRows.count - 1 { Divider().opacity(0.5) }
                }
            }
            .fireSkySurface(role: .content, cornerRadius: 12)

            HStack(spacing: 10) {
                Button(L10n.t("返回")) {
                    store.showCycleAgeConfirm = false
                }
                .keyboardShortcut(.cancelAction)
                Spacer()
                Button(L10n.text("使用当前缓存继续", "Continue with Cache")) {
                    store.confirmCycleAgeAndForecast()
                }
                Button {
                    store.showCycleAgeConfirm = false
                    store.prepare()
                } label: {
                    Label(L10n.text("更新到最新数据", "Update to Latest"), systemImage: "arrow.down.circle.fill")
                }
                .buttonStyle(.borderedProminent)
                .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 470)
        .background(Color(nsColor: .windowBackgroundColor))
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
                        Text(L10n.t("已统计占用仅覆盖模式数据（GFS / EC / CAMS）；地形与卫星以目录为准。"))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(L10n.t("同步设置"), systemImage: "arrow.triangle.2.circlepath") {
                        store.page = .settings
                    }
                    .help(L10n.t("后台同步与下载探测已移到「设置 → 数据」"))
                    Button { Task { await store.loadInventory() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }.help(L10n.t("刷新本地清单")).accessibilityLabel(L10n.t("刷新本地清单"))
                    Button {
                        if !store.workspace.isEmpty {
                            NSWorkspace.shared.open(URL(fileURLWithPath: store.workspace))
                        }
                    } label: { Image(systemName: "folder") }
                    .help(L10n.t("在访达中打开数据目录"))
                    .accessibilityLabel(L10n.t("在访达中打开数据目录"))
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
                            } label: { Image(systemName: "folder") }
                            .help(L10n.t("在访达中打开"))
                            .accessibilityLabel(L10n.t("在访达中打开"))
                            Button {
                                store.beginDataDelete(source: source, cycle: nil)
                            } label: { Image(systemName: "trash") }
                                .help(L10n.t("清空该数据源"))
                                .accessibilityLabel("\(L10n.t("清空该数据源"))：\(title)")
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
                                        .help(L10n.t("删除该周期"))
                                        .accessibilityLabel("\(L10n.t("删除该周期"))：\(title) \(key)")
                                        .disabled(store.busy || !store.online || store.dataDeleteRunning)
                                }.font(.callout).padding(.vertical, 5)
                            }
                        }
                    }
                    .padding(16)
                    .glassCard()
                }
                if store.inventory.isEmpty && store.errorText(for: .data).isEmpty && !store.layoutLoading {
                    VStack(spacing: 10) {
                        FireSkyMarkScene(size: 64)
                        Text(L10n.t("本地缓存为空")).font(.system(size: 13, weight: .medium))
                        Text(L10n.t("完成一次预测或在设置中配置数据源后，这里会显示库存。"))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 24)
                    .glassCard()
                }
                intermediateCachesSection
                let dataError = store.errorText(for: .data)
                if !dataError.isEmpty {
                    HStack(alignment: .top, spacing: 8) {
                        Text(dataError).foregroundStyle(.red).textSelection(.enabled)
                        Spacer()
                        Button { store.clearError() } label: { Image(systemName: "xmark") }
                            .buttonStyle(.borderless).foregroundStyle(.secondary)
                    }
                }
            }.padding(30)
        }
        .confirmationDialog(L10n.t("删除本地数据？"), isPresented: Binding(
            get: { store.pendingDataDelete != nil && store.dataDeletePreview != nil },
            set: { if !$0 { store.cancelDataDelete() } }
        )) {
            Button(L10n.t("删除"), role: .destructive) { store.executeDataDelete() }
                .disabled(store.dataDeleteRunning)
            Button(L10n.t("取消"), role: .cancel) { store.cancelDataDelete() }
        } message: {
            Text(deleteConfirmationText)
        }
    }

    @ViewBuilder
    private var intermediateCachesSection: some View {
        if !store.inventoryCategories.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Image(systemName: "tray.2").font(.title3).foregroundStyle(warm)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(L10n.text("预测结果与附加缓存", "Forecast Results & Auxiliary Data"))
                            .font(.headline)
                        Text(L10n.text(
                            "计算过程中的中间产物（衍生张量、卫星解包帧、临时排行缓存）会在计算结束后立即自动删除，磁盘仅保留最终结果与原始气象/地形数据。",
                            "Temporary intermediate artifacts (derived tensors, unpacked satellite bundles, ranking scratch) are deleted immediately after computation; only final results and raw meteorological/terrain data are kept."
                        ))
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                }
                Divider().opacity(0.5)
                ForEach(Array(store.inventoryCategories.enumerated()), id: \.offset) { index, item in
                    let key = item["category"] as? String ?? ""
                    let files = (item["file_count"] as? NSNumber)?.intValue ?? 0
                    let bytes = (item["bytes"] as? NSNumber)?.int64Value ?? 0
                    let path = item["path"] as? String ?? ""
                    HStack(spacing: 10) {
                        Image(systemName: categoryIcon(key))
                            .font(.callout).foregroundStyle(.secondary).frame(width: 22)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(categoryTitle(key)).font(.callout.weight(.medium))
                            Text(categorySubtitle(key)).font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(L10n.text("\(files) 个文件", "\(files) files"))
                            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        Text(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file))
                            .font(.caption.weight(.medium)).monospacedDigit()
                            .frame(minWidth: 68, alignment: .trailing)
                        if !path.isEmpty {
                            Button {
                                NSWorkspace.shared.open(URL(fileURLWithPath: path))
                            } label: { Image(systemName: "folder") }
                            .buttonStyle(.borderless)
                            .help(L10n.t("在访达中打开"))
                            .accessibilityLabel(L10n.t("在访达中打开"))
                        }
                    }
                    .padding(.vertical, 4)
                    if index < store.inventoryCategories.count - 1 { Divider().opacity(0.35) }
                }
            }
            .padding(16)
            .glassCard()
        }
    }

    private func categoryTitle(_ key: String) -> String {
        switch key {
        case "point-forecasts": return L10n.text("单点预测结果缓存", "Point Forecast Cache")
        case "regional-forecasts": return L10n.text("区域预测结果缓存", "Regional Forecast Results")
        case "map-frames": return L10n.text("地图渲染帧缓存", "Map Frame Cache")
        case "reports": return L10n.text("历史报告产物", "Forecast Reports")
        case "satellite": return L10n.text("卫星实况帧（Himawari HSD）", "Satellite Frames (Himawari HSD)")
        case "srtm": return L10n.text("SRTM 地形瓦片", "SRTM Terrain Tiles")
        default: return key
        }
    }

    private func categorySubtitle(_ key: String) -> String {
        switch key {
        case "point-forecasts": return L10n.text("按地点与起报周期复用单点计算结果", "Reused for matching point + model cycle")
        case "regional-forecasts": return L10n.text("按区域范围、模式与周期复用计算结果", "Reused for matching region + model cycle")
        case "map-frames": return L10n.text("地图与剖面可视化中间帧", "Intermediate rendered frames")
        case "reports": return L10n.text("已归档预测报告与快照", "Archived forecast reports")
        case "satellite": return L10n.text("葵花卫星多波段观测帧", "Himawari multi-band scan frames")
        case "srtm": return L10n.text("离线高程与地形遮挡计算瓦片", "Offline elevation & horizon blocking tiles")
        default: return ""
        }
    }

    private func categoryIcon(_ key: String) -> String {
        switch key {
        case "point-forecasts": return "location.circle"
        case "regional-forecasts": return "map"
        case "map-frames": return "photo.stack"
        case "reports": return "doc.text"
        case "satellite": return "antenna.radiowaves.left.and.right"
        case "srtm": return "mountain.2"
        default: return "folder"
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
            return L10n.tf("删除 %@ 的周期 %@：共 %d 个文件，预计释放 %.1f MB。占用中的文件会跳过；报告与任务记录不受影响。", title, cycle, files, mb)
        }
        var text = L10n.tf("清空 %@ 的全部本地数据：共 %d 个文件，预计释放 %.1f MB。占用中的文件会跳过。", title, files, mb)
        if protected > 0 {
            text += L10n.tf("当前有 %d 个文件被下载任务占用，将跳过。", protected)
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
                    Text(L10n.t("地形 SRTM")).font(.headline)
                    Text(terrainSubtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if store.terrainLoading || store.terrainEstimateLoading {
                    ProgressView().controlSize(.small)
                }
                if store.terrainDownloading {
                    Button(L10n.t("取消"), systemImage: "stop.fill") { store.cancelTerrainDownload() }
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
                    Button(L10n.t("下载区域地形"), systemImage: "globe") {
                        store.loadTerrainEstimate()
                    }
                    .disabled(!store.online || store.busy)
                    .help(L10n.t("确认估算后下载所选区域的 SRTM 瓦片（跳过已有；可随时取消续传）"))
                    if store.terrainLastFailedCount > 0 {
                        Button(L10n.text("重试失败项（\(store.terrainLastFailedCount)）",
                                         "Retry failed (\(store.terrainLastFailedCount))"),
                               systemImage: "arrow.clockwise.circle") {
                            store.retryFailedTerrainTiles()
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(!store.online || store.busy)
                        .help(L10n.text("定向重试上次失败的 SRTM 瓦片，自动跳过已完成瓦片",
                                        "Retry only the failed SRTM tiles from the previous run"))
                    }
                    Button(L10n.t("仅当前地点"), systemImage: "arrow.down.to.line") {
                        store.downloadTerrainPoint()
                    }
                    .buttonStyle(.borderless)
                    .disabled(!store.online || store.busy || coverageMissing)
                    .help(L10n.t("只下载当前地点对应的一块瓦片"))
                }
                Button { store.loadTerrain() } label: { Image(systemName: "arrow.clockwise") }
                    .help(L10n.t("刷新地形状态"))
                    .accessibilityLabel(L10n.t("刷新地形状态"))
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
            .glassCard()
            .confirmationDialog(downloadConfirmTitle, isPresented: Binding(
                get: { store.showGlobalTerrainConfirm },
                set: { if !$0 { store.showGlobalTerrainConfirm = false } }
            )) {
                Button(L10n.t("开始下载")) {
                    store.downloadTerrain(region: store.terrainRegion)
                }
                Button(L10n.t("取消"), role: .cancel) { store.showGlobalTerrainConfirm = false }
            } message: {
                Text(globalConfirmText)
            }
    }

    private var isBulkRun: Bool {
        store.terrainProgress["candidates"] != nil
    }

    private var downloadConfirmTitle: String {
        store.terrainRegion == .global ? L10n.t("下载全球地形数据？") : L10n.tf("下载%@地形数据？", store.terrainRegion.rawValue)
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
                Text(L10n.tf("%.0f%% · 已下载 %d/%d · 已有 %d · 缺失 %d · 失败 %d", Double(Int(percent)), downloaded, candidates, existing, absent, failed))
                Spacer()
                if !current.isEmpty { Text(L10n.tf("当前 %@", current)) }
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
            Text(L10n.tf("本地已有 %d 块瓦片。", count)).font(.caption).foregroundStyle(.secondary)
        } else if let tiles = store.terrain?["tiles"] as? [JSONObject], !tiles.isEmpty {
            Text(L10n.tf("本地瓦片：%@", tiles.map { $0["name"] as? String ?? "" }.joined(separator: "、")))
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
        return L10n.tf("目标 %@（%@）· 瓦片 %@ · %@", store.name, location, name, exists ? L10n.t("已有本地瓦片") : L10n.t("未下载"))
    }

    /// 确认文案：所选区域候选数 + 抽样估算体积 + 已有数量 + 续传说明。
    private var globalConfirmText: String {
        let global = store.terrainEstimate?["global"] as? JSONObject ?? [:]
        let candidates = (global["candidates"] as? NSNumber)?.intValue ?? 0
        let existing = (global["existing"] as? NSNumber)?.intValue ?? 0
        var text = L10n.tf("将下载%@的 SRTM 瓦片：候选 %d 块，本地已有 %d 块会自动跳过（海洋上的瓦片是很小的空值瓦片，同样会下载）。下载耗时较长且占用磁盘，可随时取消，重新开始会跳过已下载瓦片。", store.terrainRegion.rawValue, candidates, existing)
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
    case general, data, advanced
    var id: String { rawValue }
    var title: String {
        switch self {
        case .general: "通用"
        case .data: "数据"
        case .advanced: "高级"
        }
    }
    var symbol: String {
        switch self {
        case .general: "paintpalette"
        case .data: "externaldrive"
        case .advanced: "wrench.and.screwdriver"
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: Store
    @EnvironmentObject private var updater: Updater
    @EnvironmentObject private var localServer: LocalServerController
    @Environment(\.colorScheme) private var scheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Namespace private var categoryIndicator
    /// 分类与卸载确认状态都在 store：跨分区动作（CAMS 提醒跳转、助手卸载）可直接落位。
    private var category: Binding<SettingsCategory> {
        Binding(get: { store.settingsCategory }, set: { store.settingsCategory = $0 })
    }

    private var currentVersionLabel: String {
        let raw = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        return raw.isEmpty ? L10n.text("开发构建", "Dev Build") : "v\(raw)"
    }

    var body: some View {
        VStack(spacing: 0) {
            // 顶部胶囊式分类导航：消除双侧栏挤压，让设置区聚焦可读宽度。
            HStack {
                Spacer(minLength: 0)
                HStack(spacing: 4) {
                    ForEach(SettingsCategory.allCases) { item in
                        let selected = category.wrappedValue == item
                        Button {
                            withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
                                category.wrappedValue = item
                            }
                        } label: {
                            HStack(spacing: 6) {
                                Image(systemName: item.symbol)
                                    .font(.system(size: 12, weight: selected ? .semibold : .regular))
                                Text(L10n.t(item.title))
                                    .fireSkyFont(size: 12, weight: selected ? .semibold : .regular)
                            }
                            .padding(.horizontal, 16).padding(.vertical, 7)
                            .foregroundStyle(selected ? Color.primary : Color.secondary)
                            .background {
                                if selected {
                                    Capsule()
                                        .fill(scheme == .dark ? Color.white.opacity(0.11) : Color.white.opacity(0.95))
                                        .matchedGeometryEffect(id: "settings-cat-pill", in: categoryIndicator)
                                }
                            }
                            .overlay(alignment: .bottom) {
                                if selected {
                                    Capsule().fill(warm).frame(width: 22, height: 2.5)
                                        .offset(y: -2)
                                        .matchedGeometryEffect(id: "settings-cat-line", in: categoryIndicator)
                                }
                            }
                            .contentShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(selected ? .isSelected : [])
                    }
                }
                .padding(4)
                .background(
                    Capsule().fill(scheme == .dark ? Color(0x16, 0x1E, 0x2E) : Color(0xEA, 0xF0, 0xF6))
                )
                .overlay {
                    Capsule().strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: scheme == .dark, emphasized: false), lineWidth: 1)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 24)
            .padding(.top, 14)
            .padding(.bottom, 10)

            // 切换分类保留各表单草稿：所有分区都常驻在 store，不随视图销毁。
            ScrollView {
                Group {
                    switch category.wrappedValue {
                    case .general: generalSection
                    case .data: dataSection
                    case .advanced: advancedSection
                    }
                }
                .frame(maxWidth: 820, alignment: .leading)
                .padding(.horizontal, 28)
                .padding(.top, 10)
                .padding(.bottom, 32)
                .frame(maxWidth: .infinity, alignment: .top)
            }
            .defaultScrollAnchor(.top)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .confirmationDialog(L10n.t("卸载后台同步助手？"), isPresented: $store.confirmUninstallHelper) {
            Button(L10n.t("卸载助手"), role: .destructive) { store.uninstallHelper() }
            Button(L10n.t("保留助手"), role: .cancel) {}
        } message: {
            Text(L10n.t("卸载后，关闭应用时的后台同步将停止；已下载的数据会保留，可随时重新安装助手。"))
        }
    }

    // MARK: - 通用

    private var generalSection: some View {
        VStack(alignment: .leading, spacing: 20) {
            settingsHeader(L10n.text("外观与通用", "Appearance & General"),
                           subtitle: L10n.text("本机显示偏好、语言与应用更新", "Display preferences, language and updates"))
            settingsStatusSummary

            VStack(alignment: .leading, spacing: 0) {
                settingsSectionLabel(L10n.text("界面", "Interface"))
                VStack(spacing: 0) {
                    settingsRow(
                        symbol: "paintpalette",
                        title: L10n.text("外观", "Appearance"),
                        subtitle: appearanceSubtitle
                    ) {
                        Picker("", selection: $store.appearance) {
                            Text(L10n.t("浅色")).tag("light")
                            Text(L10n.t("深色")).tag("dark")
                            Text(L10n.t("跟随系统")).tag("system")
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 224)
                        .onChange(of: store.appearance) { _, _ in store.saveAppearance() }
                        .accessibilityLabel(L10n.t("主题"))
                    }
                    Divider().opacity(0.55).padding(.leading, 48)
                    settingsRow(
                        symbol: "globe",
                        title: L10n.text("语言", "Language"),
                        subtitle: store.appLanguage.label
                    ) {
                        Picker("", selection: $store.appLanguage) {
                            Text(L10n.text("跟随系统", "System")).tag(L10n.Language.system)
                            Text("中文").tag(L10n.Language.chinese)
                            Text("English").tag(L10n.Language.english)
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 224)
                        .accessibilityLabel(L10n.text("语言", "Language"))
                    }
                }
                .padding(.horizontal, 16)
                .fireSkySurface(role: .content, cornerRadius: 14)
            }

            VStack(alignment: .leading, spacing: 0) {
                settingsSectionLabel(L10n.text("局域网与外网映射（Cloudflare Tunnel）", "LAN & Cloudflare Tunnel Access"))
                localServerBlock
                    .padding(.horizontal, 16)
                    .fireSkySurface(role: .content, cornerRadius: 14)
            }

            VStack(alignment: .leading, spacing: 0) {
                settingsSectionLabel(L10n.t("软件更新"))
                updaterBlock
                    .padding(.horizontal, 16)
                    .fireSkySurface(role: .content, cornerRadius: 14)
            }
        }
    }

    private var localServerBlock: some View {
        VStack(alignment: .leading, spacing: 0) {
            settingsRow(
                symbol: localServer.running ? "network" : "network.slash",
                title: L10n.text("内置 Web 与 API 服务", "Built-in Web & API Server"),
                subtitle: localServer.running
                    ? localServer.statusMessage
                    : (localServer.starting
                        ? L10n.text("正在启动…", "Starting…")
                        : L10n.text("启动后可在局域网访问或配合 cloudflared 映射公网",
                                    "Serve Web UI & API on LAN or expose via Cloudflare Tunnel"))
            ) {
                HStack(spacing: 8) {
                    if localServer.starting {
                        ProgressView().controlSize(.small)
                    }
                    StatusChip(
                        status: localServer.running ? "done" : "pending",
                        title: localServer.running ? L10n.text("运行中", "Running") : L10n.text("未启动", "Stopped")
                    )
                    if localServer.running {
                        Button(L10n.text("停止服务", "Stop")) {
                            localServer.stop()
                        }
                        .controlSize(.small)
                    } else {
                        Button(L10n.text("启动服务", "Start")) {
                            localServer.start(workspace: store.workspace, customEngineBinary: store.engineBinary)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                        .disabled(localServer.starting || store.workspace.isEmpty)
                    }
                }
            }
            Divider().opacity(0.55).padding(.leading, 48)
            settingsRow(
                symbol: "antenna.radiowaves.left.and.right",
                title: L10n.text("监听模式", "Bind Mode"),
                subtitle: localServer.bindLAN
                    ? L10n.text("监听 0.0.0.0：局域网设备与本机均可访问（强制开启令牌鉴权）",
                                "Listening on 0.0.0.0 for LAN & local devices (Bearer token required)")
                    : L10n.text("仅监听 127.0.0.1：推荐配合 Cloudflare Tunnel 反向代理到外网",
                                "Loopback 127.0.0.1 only: recommended for Cloudflare Tunnel")
            ) {
                Picker("", selection: $localServer.bindLAN) {
                    Text(L10n.text("仅本机 (CF Tunnel)", "Local (CF Tunnel)")).tag(false)
                    Text(L10n.text("局域网 (0.0.0.0)", "LAN (0.0.0.0)")).tag(true)
                }
                .pickerStyle(.segmented)
                .frame(width: 248)
                .disabled(localServer.running || localServer.starting)
            }
            Divider().opacity(0.55).padding(.leading, 48)
            settingsRow(
                symbol: "number",
                title: L10n.text("监听端口与自启动", "Port & Auto-Start"),
                subtitle: L10n.text("修改端口或监听模式需先停止服务后生效", "Stop service before changing port or bind mode")
            ) {
                HStack(spacing: 12) {
                    TextField("8765", value: $localServer.port, format: .number.grouping(.never))
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 72)
                        .disabled(localServer.running || localServer.starting)
                    Toggle(L10n.text("打开应用时自动启动", "Auto-start on launch"), isOn: $localServer.autoStart)
                        .toggleStyle(.checkbox)
                }
            }
            Divider().opacity(0.55).padding(.leading, 48)
            settingsRow(
                symbol: "key.fill",
                title: L10n.text("访问令牌鉴权", "Access Token Auth"),
                subtitle: L10n.text("浏览器弹窗登录时：用户名固定填 firesky，密码粘贴下方令牌。令牌以 0600 权限保存于本地 state/http-token；局域网强制启用，仅本机使用可关闭免密。",
                                    "Browser login: username is firesky, password is the token below. Stored locally at state/http-token (0600); required on LAN, optional for local-only.")
            ) {
                HStack(spacing: 8) {
                    Toggle("", isOn: Binding(
                        get: { localServer.bindLAN || localServer.requireToken },
                        set: { localServer.requireToken = $0 }
                    ))
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .disabled(localServer.bindLAN || localServer.running || localServer.starting)
                    if !localServer.token.isEmpty {
                        Button(L10n.text("复制令牌", "Copy Token")) {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(localServer.token, forType: .string)
                        }
                        .controlSize(.small)
                    }
                    Button(L10n.text("重置令牌", "Regenerate")) {
                        localServer.regenerateToken(workspace: store.workspace)
                    }
                    .controlSize(.small)
                    .disabled(localServer.running || localServer.starting)
                }
            }

            Divider().opacity(0.55).padding(.leading, 48)
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Text(L10n.text("本机地址", "Local URL"))
                        .font(.caption).foregroundStyle(.secondary).frame(width: 72, alignment: .leading)
                    Text(localServer.localURL).font(.caption.monospaced()).textSelection(.enabled)
                    Spacer()
                    Button(L10n.text("打开", "Open")) {
                        if let url = URL(string: localServer.localURL) { NSWorkspace.shared.open(url) }
                    }
                    .controlSize(.mini).disabled(!localServer.running)
                    Button(L10n.text("复制", "Copy")) {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(localServer.localURL, forType: .string)
                    }
                    .controlSize(.mini)
                }
                if localServer.bindLAN, let lan = localServer.lanURL {
                    HStack(spacing: 8) {
                        Text(L10n.text("局域网地址", "LAN URL"))
                            .font(.caption).foregroundStyle(.secondary).frame(width: 72, alignment: .leading)
                        Text(lan).font(.caption.monospaced()).textSelection(.enabled)
                        Spacer()
                        Button(L10n.text("复制", "Copy")) {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(lan, forType: .string)
                        }
                        .controlSize(.mini)
                    }
                }
                HStack(spacing: 8) {
                    Text("CF Tunnel")
                        .font(.caption).foregroundStyle(.secondary).frame(width: 72, alignment: .leading)
                    Text(localServer.cloudflaredCommand)
                        .font(.caption.monospaced())
                        .lineLimit(1).truncationMode(.middle)
                        .textSelection(.enabled)
                    Spacer()
                    Button(L10n.text("复制命令", "Copy Cmd")) {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(localServer.cloudflaredCommand, forType: .string)
                    }
                    .controlSize(.mini)
                }
                if !localServer.errorMessage.isEmpty {
                    Text(localServer.errorMessage)
                        .font(.caption).foregroundStyle(.red)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                if !localServer.webRootAvailable {
                    Text(L10n.text("提示：未检测到内置 Web 前端静态资源，当前仍可访问 HTTP API。",
                                   "Note: Bundled Web UI assets were not found; HTTP API remains available."))
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 12)
            .padding(.leading, 48)
        }
        .onAppear {
            localServer.refreshTokenPreview(workspace: store.workspace)
            localServer.refreshLANAddress()
        }
    }

    private var appearanceSubtitle: String {
        switch store.appearance {
        case "light": return L10n.t("浅色")
        case "dark": return L10n.t("深色")
        default: return L10n.t("跟随系统")
        }
    }

    /// 只读状态摘要：把引擎、数据源和后台助手状态放在设置入口，
    /// 并给出回到数据管理分区的明确入口；不新增任何不可用开关。
    private var settingsStatusSummary: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L10n.text("运行状态摘要", "Runtime Summary")).fireSkyFont(size: 13, weight: .semibold)
                Spacer()
                Button {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.18)) {
                        store.settingsCategory = .data
                    }
                } label: {
                    Label(L10n.text("管理数据源与同步", "Manage data & sync"),
                          systemImage: "arrow.right.circle")
                        .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                }
                .buttonStyle(.borderless)
                .accessibilityLabel(L10n.text("查看数据管理与连接状态", "Open data & connection settings"))
            }
            HStack(spacing: 10) {
                statusBadge(title: L10n.text("本地引擎", "Engine"),
                            value: store.online ? L10n.text("已连接", "Connected") : L10n.text("未连接", "Offline"),
                            healthy: store.online)
                statusBadge(title: L10n.text("数据源与同步", "Data & Sync"),
                            value: store.dataSyncConfigured ? L10n.text("已配置", "Configured") : L10n.text("按需准备", "On-demand"),
                            healthy: store.dataSyncConfigured || store.camsConfigured)
                statusBadge(title: L10n.text("后台助手", "Helper"),
                            value: store.helperLoaded ? L10n.text("运行中", "Running")
                                : store.helperInstalled ? L10n.text("未加载", "Not loaded")
                                : L10n.text("未安装", "Not installed"),
                            healthy: store.helperLoaded)
                statusBadge(title: L10n.text("局域网服务", "LAN / Tunnel"),
                            value: localServer.running ? ":\(localServer.port)" : L10n.text("未启动", "Stopped"),
                            healthy: localServer.running)
            }
        }
        .padding(16)
        .fireSkySurface(role: .content, cornerRadius: 14)
    }

    private func statusBadge(title: String, value: String, healthy: Bool) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
            HStack(spacing: 6) {
                Circle().fill(healthy ? Color.green : Color.orange).frame(width: 6, height: 6)
                Text(value).fireSkyFont(size: 12, weight: .semibold).lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(Color.primary.opacity(scheme == .dark ? 0.045 : 0.03),
                    in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(Color.secondary.opacity(0.14), lineWidth: 1)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title)：\(value)")
    }

    /// 软件更新块：当前版本、检查/弹窗详情与自动检查开关。
    @ViewBuilder
    private var updaterBlock: some View {
        VStack(spacing: 0) {
            settingsRow(
                symbol: updater.phase == .available ? "arrow.up.circle.fill" : "arrow.triangle.2.circlepath",
                title: L10n.tf("当前版本 %@", currentVersionLabel),
                subtitle: updaterSummarySubtitle
            ) {
                HStack(spacing: 8) {
                    if updater.phase == .checking || updater.phase == .installing {
                        ProgressView().controlSize(.small)
                    }
                    if updater.phase == .available || updater.phase == .downloading || updater.phase == .failed {
                        Button(L10n.text("查看更新", "View Update")) {
                            store.showUpdateSheet = true
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                    }
                    Button(L10n.t("检查更新")) {
                        updater.checkForUpdates(interactive: true)
                        store.showUpdateSheet = true
                    }
                    .controlSize(.small)
                    .disabled(updater.phase == .checking || updater.phase == .downloading || updater.phase == .installing)
                }
            }
            if updater.phase == .downloading {
                Divider().opacity(0.55).padding(.leading, 48)
                VStack(alignment: .leading, spacing: 6) {
                    ProgressView(value: max(Double(updater.receivedBytes), 1),
                                 total: max(Double(updater.totalBytes), 1))
                    HStack {
                        Text(L10n.tf("已下载 %@ / %@",
                                     ByteCountFormatter.string(fromByteCount: updater.receivedBytes, countStyle: .file),
                                     ByteCountFormatter.string(fromByteCount: updater.totalBytes, countStyle: .file)))
                            .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        Spacer()
                        Button(L10n.t("取消")) { updater.cancelDownload() }
                            .controlSize(.small)
                    }
                }
                .padding(.vertical, 10)
            }
            Divider().opacity(0.55).padding(.leading, 48)
            settingsRow(
                symbol: "sparkles",
                title: L10n.t("启动时自动检查更新"),
                subtitle: L10n.text("启动 8 秒后静默检查 GitHub Releases，失败不打扰",
                                    "Silently check GitHub Releases after launch")
            ) {
                Toggle("", isOn: $updater.autoCheck)
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .disabled(updater.phase == .downloading || updater.phase == .installing)
                    .accessibilityLabel(L10n.t("启动时自动检查更新"))
            }
        }
    }

    private var updaterSummarySubtitle: String {
        switch updater.phase {
        case .idle:
            return L10n.text("点击检查 GitHub Releases 最新版本", "Check GitHub Releases for updates")
        case .checking:
            return L10n.text("正在检查更新…", "Checking for updates…")
        case .current:
            return updater.message.isEmpty ? L10n.text("已是最新版本", "Up to date") : updater.message
        case .available:
            return L10n.tf("发现新版本 %@，可在下载后自动安装。", updater.info?.version ?? "")
        case .downloading:
            return L10n.t("下载中…")
        case .installing:
            return L10n.t("正在安装，完成后应用将自动重启。")
        case .failed:
            return updater.message.isEmpty ? L10n.text("更新检查失败", "Update check failed") : updater.message
        }
    }

    // MARK: - 数据

    /// 数据分类锚点（方案 D7）：6 大块快速定位。
    private let dataAnchors: [(String, String)] = [
        ("下载源", "anchor-sources"), ("CAMS", "anchor-cams"),
        ("连通性", "anchor-probe"), ("同步", "anchor-sync"),
        ("保留", "anchor-retention"), ("目录", "anchor-directory"),
    ]

    private var dataSection: some View {
        ScrollViewReader { proxy in
            VStack(alignment: .leading, spacing: 18) {
                settingsHeader(L10n.text("数据与同步", "Data & Sync"),
                               subtitle: L10n.text("下载源、Copernicus ADS 凭据、连通性探测、后台同步与保留策略",
                                                   "Sources, ADS credentials, connectivity probes, background sync and retention"))
                HStack(spacing: 6) {
                    ForEach(dataAnchors, id: \.1) { anchor in
                        Button {
                            if reduceMotion {
                                proxy.scrollTo(anchor.1, anchor: .top)
                            } else {
                                withAnimation { proxy.scrollTo(anchor.1, anchor: .top) }
                            }
                        } label: {
                            Text(L10n.t(anchor.0))
                                .fireSkyFont(size: 11, weight: .medium, relativeTo: .caption)
                                .padding(.horizontal, 11).padding(.vertical, 5)
                                .background(Color.primary.opacity(scheme == .dark ? 0.06 : 0.045),
                                            in: Capsule())
                                .overlay {
                                    Capsule().strokeBorder(Color.secondary.opacity(0.16), lineWidth: 1)
                                }
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(.secondary)
                    }
                }
                dataSourcesBlock
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .fireSkySurface(role: .content, cornerRadius: 14)
                    .id("anchor-sources")
                camsBlock
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .fireSkySurface(role: .content, cornerRadius: 14)
                    .id("anchor-cams")
                DownloadProbeView()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .id("anchor-probe")
                dataSyncBlock
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .fireSkySurface(role: .content, cornerRadius: 14)
                    .id("anchor-sync")
                retentionBlock
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .fireSkySurface(role: .content, cornerRadius: 14)
                    .id("anchor-retention")
                dataDirectoryBlock
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(18)
                    .fireSkySurface(role: .content, cornerRadius: 14)
                    .id("anchor-directory")
            }
        }
    }

    private var dataSourcesBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            settingsBlockTitle(L10n.t("下载源"))
            Picker(L10n.t("EC 下载源"), selection: $store.ecmwfSource) {
                Text(L10n.t("ECMWF 官方")).tag("ecmwf")
                Text(L10n.t("AWS 欧洲镜像")).tag("aws")
                Text(L10n.t("Google Cloud 镜像")).tag("google")
            }.pickerStyle(.radioGroup).disabled(store.probingDownloads)
                .accessibilityLabel("EC 下载源")
            Text(L10n.t("EC 下载源同时用于下载探测、数据准备和预测。GFS 使用 NOAA NOMADS，CAMS 使用 Copernicus ADS。"))
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
                }.buttonStyle(.borderless).help(L10n.t("重新检查 CAMS 配置"))
                    .accessibilityLabel(L10n.t("重新检查 CAMS 配置"))
                    .disabled(!store.online || store.savingCAMS)
            }
            HStack(spacing: 10) {
                SecureField(L10n.t("粘贴 ADS Personal Access Token"), text: $store.camsKeyDraft)
                    .textFieldStyle(.roundedBorder).onSubmit { store.saveCAMSKey() }
                    .disabled(!store.canSaveCAMS || store.savingCAMS || store.probingDownloads)
                if store.savingCAMS { ProgressView().controlSize(.small) }
                Button(L10n.t("保存 Key"), systemImage: "key.fill") { store.saveCAMSKey() }
                    .disabled(!store.online || !store.canSaveCAMS || store.savingCAMS || store.probingDownloads
                              || store.camsKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            Text(L10n.t("仅以明文保存在本机配置文件，文件权限 0600；不会写入工程或显示已有 Key。"))
                .font(.caption).foregroundStyle(.secondary)
            if store.camsConfiguration != nil && !store.canSaveCAMS {
                Text(L10n.t("环境变量正在覆盖配置。修改 CDSAPI_KEY / CDSAPI_URL 后需重新启动应用。"))
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
                    Text(L10n.t("这里只检查本地配置是否完整；Token 是否有效、数据集是否已授权，在实际下载时验证。"))
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(.top, 12).padding(.bottom, 6)
            }
        }
    }

    private var dataSyncBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            settingsBlockTitle(L10n.t("后台同步"))
            Toggle(L10n.t("启用后台同步"), isOn: $store.dataSyncEnabled)
            if store.dataSyncEnabled && !store.dataSyncRetentionConfigured {
                Label(L10n.t("未配置缓存保留策略，缓存将随同步增长；可在下方「存储与保留」中设置。"), systemImage: "exclamationmark.triangle.fill")
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
            Picker(L10n.t("锚定区域"), selection: $store.dataSyncAnchor) {
                Text(L10n.t("未设置")).tag("")
                ForEach(store.regionDefinitions.indices, id: \.self) { index in
                    let region = store.regionDefinitions[index]
                    Text(region["label"] as? String ?? region["code"] as? String ?? "")
                        .tag(region["code"] as? String ?? "")
                }
            }
            Text(L10n.text(
                "监控间隔 5–1440 分钟（建议设为 10–15 分钟轻量监控上游更新，仅检测到新起报周期才下载，无更新不占带宽）；时效上限仅 GFS/EC 需要（3–240 小时、3 的倍数），CAMS 自动拉齐全部时效；锚定区域决定 CAMS 洲包络与 GFS/EC 下载范围。",
                "Polling interval 5–1440 min (10–15 min recommended for lightweight upstream monitoring; downloads only when a newer cycle is published). Lead horizon applies to GFS/EC (3–240 h, multiples of 3)."
            ))
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
                Button(L10n.t("立即同步"), systemImage: "arrow.triangle.2.circlepath") {
                    store.runDataSyncNow()
                }
                .disabled(!store.online || store.busy || store.dataSyncRunning)
                .help(store.dataSyncEnabled
                      ? "立即同步已启用的数据源（可解除失败熔断）"
                      : "先启用并保存后台同步")
                Button(L10n.t("保存同步设置"), systemImage: "externaldrive.badge.checkmark") {
                    store.saveDataSyncSettings()
                }
                .disabled(!store.online || store.savingDataSync || store.busy)
            }
            if store.dataSyncLoaded {
                VStack(alignment: .leading, spacing: 8) {
                    settingsBlockTitle(L10n.t("同步状态"))
                    dataSyncSourceRow("cams", "CAMS")
                    dataSyncSourceRow("gfs", "GFS")
                    dataSyncSourceRow("ec", "EC")
                }
            }
            Divider()
            VStack(alignment: .leading, spacing: 8) {
                settingsBlockTitle(L10n.t("后台助手（关闭应用后仍同步）"))

                        // v1.9.8 一致性校验：plist 工作区与当前数据目录不一致。
                        if store.helperWorkspaceMismatch {
                            Text("后台助手配置的工作区与当前数据目录不一致，请重新安装助手（上次同步可能写入了别的目录）。")
                                .font(.caption).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                Text(L10n.t("安装为系统 launchd 代理，按同步间隔在后台自动拉齐最新已发布周期。"))
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
                            Label(L10n.t("未安装"), systemImage: "circle").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if store.helperBusy { ProgressView().controlSize(.small) }
                        Button(store.helperInstalled ? "重新安装" : "安装助手", systemImage: "arrow.down.app") {
                            store.installHelper()
                        }
                        .disabled(store.helperBusy || store.workspace.isEmpty)
                        if store.helperInstalled {
                            Button(L10n.t("卸载"), systemImage: "trash") { store.confirmUninstallHelper = true }
                                .disabled(store.helperBusy)
                        }
                        Button(L10n.t("刷新"), systemImage: "arrow.clockwise") { store.refreshHelperStatus() }
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
                    Text(L10n.t("当前为源码运行环境，后台助手仅在正式 FireSky 安装包中可用。"))
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var retentionBlock: some View {
        let activeRulesCount = [
            store.retentionGFS, store.retentionECMWF, store.retentionCAMS,
            store.retentionSatelliteDays, store.retentionReportDays, store.retentionMapDays,
        ].filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }.count
        return VStack(alignment: .leading, spacing: 12) {
            HStack {
                settingsBlockTitle(L10n.t("存储与保留"))
                Spacer()
                if activeRulesCount > 0 {
                    Text(L10n.text("已设 \(activeRulesCount) 项规则", "\(activeRulesCount) rules active"))
                        .fireSkyFont(size: 10, weight: .medium, relativeTo: .caption)
                        .foregroundStyle(warm)
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(warm.opacity(0.12), in: Capsule())
                }
            }
            Text(L10n.t("自动清理按周期、扫描或生成时间滚动保留；保存策略后可点击「立即按策略清理」立刻释放空间，后续也会在新数据校验成功后自动清理旧文件。占用中的文件会自动跳过。"))
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
            HStack(spacing: 10) {
                if store.savingRetention || store.applyingRetention { ProgressView().controlSize(.small) }
                if !store.retentionMessage.isEmpty {
                    Text(store.retentionMessage)
                        .font(.caption)
                        .foregroundStyle(store.retentionSaveFailed ? Color.red : Color.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                } else if store.retentionConfigured {
                    Text(L10n.t("已启用自定义保留策略")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(L10n.text("立即按策略清理", "Clean up now"), systemImage: "sparkles") {
                    store.applyRetentionNow()
                }
                .disabled(!store.online || !store.retentionConfigured || store.busy || store.savingRetention || store.applyingRetention)
                .help(store.retentionConfigured
                      ? L10n.text("立即按已保存规则清理超出保留范围的旧文件（跳过占用中文件）",
                                  "Immediately prune old files beyond saved retention rules (skips busy files)")
                      : L10n.text("请先填写并保存至少一项保留规则", "Save at least one retention rule first"))
                Button(L10n.t("保存保留设置"), systemImage: "externaldrive.badge.checkmark") {
                    store.saveRetentionSettings()
                }
                .disabled(!store.online || store.savingRetention || store.applyingRetention || store.busy)
            }
        }
    }

    /// 数据目录独立分区（v1.8.1 用户裁定）：目录固定在系统默认位置
    /// （Application Support），不再提供选择/编辑；保留迁移入口与重连。
    private var dataDirectoryBlock: some View {
        VStack(alignment: .leading, spacing: 8) {
            settingsBlockTitle(L10n.t("数据目录"))
            Text(store.workspace)
                .font(.caption).foregroundStyle(.secondary)
                .textSelection(.enabled)
                .lineLimit(2)
                .truncationMode(.middle)
            Text(L10n.t("数据固定保存在 macOS 系统数据目录（可在访达中前往）。开发/运维可用 FIRESKY_WORKSPACE 环境变量覆盖。"))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Button(L10n.t("在访达中打开"), systemImage: "folder") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: store.workspace))
                }
                Spacer()
                Button(L10n.t("重新连接"), systemImage: "arrow.clockwise") { Task { await store.connect() } }
                    .disabled(store.busy || store.probingDownloads)
                    .help(L10n.t("重新连接引擎"))
            }
            Divider()
            layoutStatusBlock
        }
        .onAppear { store.loadLayout() }
        .confirmationDialog(L10n.t("迁移到可见目录布局？"), isPresented: Binding(
            get: { store.showLayoutConfirm },
            set: { if !$0 { store.dismissLayoutConfirm() } }
        )) {
            Button(L10n.t("迁移"), role: .destructive) { store.migrateLayout() }
            Button(L10n.t("取消"), role: .cancel) { store.dismissLayoutConfirm() }
        } message: {
            Text(migrationConfirmText)
        }
    }

    /// 布局状态：Legacy 提供迁移入口；Visible 只显示口径说明。
    @ViewBuilder
    private var layoutStatusBlock: some View {
        let generation = store.layout?["generation"] as? String ?? ""
        let busy = (store.layout?["operation_busy"] as? Bool ?? false)
            || (store.layout?["sync_busy"] as? Bool ?? false)
        if generation == "legacy" {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 4) {
                    Text(L10n.t("当前使用旧版隐藏目录布局（.cache / .firesky）。"))
                        .font(.callout)
                    Text(L10n.t("迁移会把数据移动到工作区下的可见目录（gfs/、ecmwf/、cams/、reports/ 等），旧目录残壳保留，可随时确认后手动删除。迁移期间会暂停任务与同步。"))
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    if store.layoutMessageFailed {
                        Text(store.layoutMessage).font(.caption).foregroundStyle(.red)
                            .textSelection(.enabled)
                    }
                }
                Spacer()
                if store.layoutMigrating {
                    VStack(spacing: 4) {
                        ProgressView().controlSize(.small)
                        Text(L10n.t("迁移中…")).font(.caption).foregroundStyle(.secondary)
                    }
                } else {
                    Button(L10n.t("迁移到可见目录"), systemImage: "arrow.right.square.fill") {
                        store.loadLayout()
                        store.showLayoutConfirm = true
                    }
                    .disabled(!store.online || busy || store.layoutLoading)
                    .help(busy ? "有任务或同步正在进行，完成后再迁移" : "迁移数据到工作区可见目录并重连引擎")
                }
            }
        } else {
            Text(store.layoutMessage.isEmpty
                 ? "已使用可见目录布局（gfs/、reports/ 等直接位于工作区下）。"
                 : store.layoutMessage)
                .font(.caption).foregroundStyle(
                    store.layoutMessageFailed ? Color.red : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// 迁移确认文案：列出将迁移的条目数与总规模（来自 data_layout_status）。
    private var migrationConfirmText: String {
        let items = (store.layout?["items"] as? [JSONObject]) ?? []
        let pending = items.filter { ($0["exists"] as? Bool) == true }
        if pending.isEmpty {
            return "没有检测到旧布局数据（.cache / .firesky），迁移将只写入布局标记。"
        }
        let files = pending.reduce(0) { $0 + (($1["file_count"] as? NSNumber)?.intValue ?? 0) }
        let bytes = pending.reduce(0.0) { $0 + (($1["bytes"] as? NSNumber)?.doubleValue ?? 0) }
        let names = pending.prefix(4).compactMap { $0["from"] as? String }.joined(separator: "、")
        let more = pending.count > 4 ? L10n.tf(" 等 %d 项", pending.count) : ""
        return L10n.tf("将迁移 %d 个文件（共 %.1f MB）：%@%@。迁移使用同卷移动，失败自动回滚；迁移完成后引擎将按新布局重连。", files, bytes / 1_048_576, names, more)
    }

    // MARK: - 高级

    private var advancedSection: some View {
        VStack(alignment: .leading, spacing: 20) {
            settingsHeader(L10n.text("高级与调试", "Advanced & Diagnostics"),
                           subtitle: L10n.text("开发调试与底层引擎覆写选项", "Developer overrides and engine diagnostics"))
            VStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 12) {
                        settingsIconBadge("cpu")
                        VStack(alignment: .leading, spacing: 2) {
                            Text(L10n.t("Rust 引擎")).fireSkyFont(size: 13, weight: .semibold)
                            Text(L10n.t("自定义引擎路径仅供开发调试；日常使用保持留空即可。"))
                                .fireSkyFont(size: 11, relativeTo: .caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if !store.engineBinary.isEmpty {
                            Button(L10n.text("恢复内置", "Use Built-in")) {
                                store.engineBinary = ""
                            }
                            .controlSize(.small)
                        }
                    }
                    TextField(L10n.t("留空使用内置引擎"), text: $store.engineBinary)
                        .textFieldStyle(.roundedBorder)
                }
                .padding(.vertical, 14)

                Divider().opacity(0.55).padding(.leading, 48)

                settingsRow(
                    symbol: "folder.badge.gearshape",
                    title: L10n.text("当前工作区", "Active Workspace"),
                    subtitle: store.workspace.isEmpty ? L10n.t("数据目录未设置") : store.workspace
                ) {
                    Button(L10n.t("在访达中打开")) {
                        if !store.workspace.isEmpty {
                            NSWorkspace.shared.open(URL(fileURLWithPath: store.workspace))
                        }
                    }
                    .controlSize(.small)
                    .disabled(store.workspace.isEmpty)
                }

                Divider().opacity(0.55).padding(.leading, 48)

                settingsRow(
                    symbol: "bolt.horizontal.circle",
                    title: L10n.text("引擎连接", "Engine Connection"),
                    subtitle: store.online
                        ? L10n.text("已连接到本地 Rust FFI 引擎", "Connected to local Rust FFI engine")
                        : L10n.text("当前未连接，可尝试重新建立连接", "Offline — reconnect to restore engine")
                ) {
                    Button(L10n.t("重新连接")) {
                        Task { await store.connect() }
                    }
                    .controlSize(.small)
                    .disabled(store.busy || store.probingDownloads)
                }
            }
            .padding(.horizontal, 16)
            .fireSkySurface(role: .content, cornerRadius: 14)
        }
    }

    // MARK: - 共用小组件

    private func settingsHeader(_ title: String, subtitle: String? = nil) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).fireSkyFont(size: 22, weight: .bold, relativeTo: .title2)
            if let subtitle {
                Text(subtitle).fireSkyFont(size: 12, relativeTo: .caption).foregroundStyle(.secondary)
            }
        }
    }

    private func settingsSectionLabel(_ title: String) -> some View {
        Text(title)
            .fireSkyFont(size: 12, weight: .semibold, relativeTo: .caption)
            .foregroundStyle(.secondary)
            .padding(.leading, 4)
            .padding(.bottom, 8)
    }

    private func settingsIconBadge(_ symbol: String) -> some View {
        Image(systemName: symbol)
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(Color.primary.opacity(0.82))
            .frame(width: 34, height: 34)
            .background(Color.primary.opacity(scheme == .dark ? 0.07 : 0.05),
                        in: RoundedRectangle(cornerRadius: 8))
    }

    private func settingsRow<Control: View>(
        symbol: String,
        title: String,
        subtitle: String,
        @ViewBuilder control: () -> Control
    ) -> some View {
        HStack(alignment: .center, spacing: 14) {
            settingsIconBadge(symbol)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).fireSkyFont(size: 13, weight: .medium)
                Text(subtitle)
                    .fireSkyFont(size: 11, relativeTo: .caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .truncationMode(.middle)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 16)
            control()
        }
        .padding(.vertical, 13)
    }

    private func settingsBlockTitle(_ title: String) -> some View {
        Text(title).font(.headline)
    }

    private func retentionField(_ title: String, text: Binding<String>, suffix: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 5) {
                TextField(L10n.t("不限制"), text: text)
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
                TextField(L10n.t("未设置"), text: text)
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
        if !cycle.isEmpty { details.append(L10n.tf("最近周期 %@", cycle)) }
        if files > 0 { details.append("\(files) 个文件") }
        if let attempt = state["last_attempt_utc"] as? String, !attempt.isEmpty {
            details.append(L10n.tf("上次同步 %@", dataSyncTimeText(attempt)))
        }
        // v1.9.6：显示上一轮实际耗时——串行时代 GFS 一轮 20+ 分钟，用户
        // 需要知道"慢"的具体量级（引擎 elapsed_ms）。
        if let elapsed = (result["elapsed_ms"] as? NSNumber)?.doubleValue, elapsed > 0 {
            let seconds = elapsed / 1000
            details.append(seconds >= 60
                ? L10n.text(String(format: "耗时 %.0f 分 %.0f 秒", seconds / 60, seconds.truncatingRemainder(dividingBy: 60)),
                            String(format: "Took %.0f min %.0f s", seconds / 60, seconds.truncatingRemainder(dividingBy: 60)))
                : L10n.text(String(format: "耗时 %.0f 秒", seconds), String(format: "Took %.0f s", seconds)))
        }
        if let errors = result["errors"] as? [String], let first = errors.first, !first.isEmpty {
            details.append(L10n.tf("最近错误：%@", first))
        }
        // v1.9.7 欠账可视化：CAMS 缺哪些周期一眼可见，补齐进展随轮次更新。
        if let missing = result["missing_cycles"] as? [String], !missing.isEmpty {
            details.append(L10n.tf("缺 %d 个周期：%@", missing.count, missing.joined(separator: "、")))
        }
        if failures > 0 { details.append(L10n.tf("连续失败 %d 次", failures)) }
        if suspended { details.append("已暂停：立即同步或重新保存设置可解锁") }
        let live = (store.dataSyncRunning && store.dataSyncActiveSource == name)
            ? (store.dataSyncProgress[name] as? JSONObject)
            : nil
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

/// 独立软件更新弹窗：参考 macOS 现代更新弹层层级（顶光、状态图标、版本对比、更新说明与进度操作）。
struct UpdateSheetView: View {
    @EnvironmentObject private var store: Store
    @EnvironmentObject private var updater: Updater
    @Environment(\.colorScheme) private var scheme

    private var currentVersion: String {
        let raw = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        return raw.isEmpty ? "Dev" : raw
    }

    private var progressFraction: Double {
        guard updater.totalBytes > 0 else { return 0 }
        return min(max(Double(updater.receivedBytes) / Double(updater.totalBytes), 0), 1)
    }

    private var progressPercent: Int { Int((progressFraction * 100).rounded()) }

    private var headerIcon: String {
        switch updater.phase {
        case .available: return "arrow.up"
        case .downloading: return "arrow.down.to.line"
        case .installing: return "shippingbox.fill"
        case .current: return "checkmark"
        case .failed: return "exclamationmark.triangle"
        default: return "arrow.triangle.2.circlepath"
        }
    }

    private var headerTitle: String {
        switch updater.phase {
        case .available: return L10n.text("发现新版本", "Update Available")
        case .downloading: return L10n.text("正在下载更新", "Downloading Update")
        case .installing: return L10n.text("正在安装更新", "Installing Update")
        case .current: return L10n.text("已是最新版本", "You're Up to Date")
        case .failed: return L10n.text("更新未完成", "Update Failed")
        case .checking: return L10n.text("正在检查更新", "Checking for Updates")
        case .idle: return L10n.t("软件更新")
        }
    }

    private var headerSubtitle: String {
        switch updater.phase {
        case .available:
            return L10n.tf("%@ 已准备好下载与 SHA256 校验", updater.info?.version ?? "")
        case .downloading:
            return L10n.text("\(progressPercent)% · 下载完成后自动校验并安装",
                             "\(progressPercent)% · Verifies SHA256 & installs automatically")
        case .installing:
            return L10n.t("正在安装，完成后应用将自动重启。")
        case .current:
            return updater.message.isEmpty ? L10n.tf("当前版本 %@", currentVersion) : updater.message
        case .failed:
            return L10n.text("可重新检查，或前往 GitHub Releases 手动下载。",
                             "Retry check or download manually from GitHub Releases.")
        case .checking:
            return L10n.text("正在连接 GitHub Releases…", "Contacting GitHub Releases…")
        case .idle:
            return L10n.tf("当前版本 %@", currentVersion)
        }
    }

    var body: some View {
        VStack(spacing: 18) {
            ZStack(alignment: .topTrailing) {
                VStack(spacing: 12) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 14)
                            .fill(
                                updater.phase == .available
                                    ? AnyShapeStyle(FSDesign.accentGradient(dark: scheme == .dark))
                                    : AnyShapeStyle(Color.primary.opacity(scheme == .dark ? 0.08 : 0.06))
                            )
                            .frame(width: 52, height: 52)
                            .overlay {
                                RoundedRectangle(cornerRadius: 14)
                                    .strokeBorder(warm.opacity(updater.phase == .downloading ? 0.4 : 0.16), lineWidth: 1)
                            }
                        if updater.phase == .checking || updater.phase == .installing {
                            ProgressView().controlSize(.regular)
                        } else {
                            Image(systemName: headerIcon)
                                .font(.system(size: 20, weight: .semibold))
                                .foregroundStyle(
                                    updater.phase == .available
                                        ? (scheme == .dark ? Color(0x1E, 0x15, 0x10) : Color.white)
                                        : (updater.phase == .failed ? Color.orange : warm)
                                )
                        }
                    }
                    VStack(spacing: 5) {
                        Text(headerTitle).fireSkyFont(size: 20, weight: .bold, relativeTo: .title2)
                        Text(headerSubtitle)
                            .fireSkyFont(size: 12, relativeTo: .caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 8)

                Button {
                    store.showUpdateSheet = false
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 26, height: 26)
                        .background(Color.primary.opacity(scheme == .dark ? 0.07 : 0.05), in: Circle())
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.cancelAction)
                .help(L10n.text("关闭", "Close"))
                .accessibilityLabel(L10n.text("关闭更新窗口", "Close update sheet"))
            }

            if let info = updater.info {
                HStack(spacing: 18) {
                    VStack(spacing: 4) {
                        Text(L10n.text("当前版本", "Current"))
                            .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                        Text(currentVersion)
                            .font(.system(size: 15, weight: .medium, design: .monospaced))
                            .foregroundStyle(.secondary)
                    }
                    HStack(spacing: 5) {
                        Circle().fill(warm.opacity(0.45)).frame(width: 4, height: 4)
                        Circle().fill(warm.opacity(0.72)).frame(width: 4, height: 4)
                        Circle().fill(warm).frame(width: 4, height: 4)
                    }
                    VStack(spacing: 4) {
                        Text(L10n.text("最新版本", "Latest"))
                            .fireSkyFont(size: 10, relativeTo: .caption).foregroundStyle(.secondary)
                        Text(info.version)
                            .font(.system(size: 19, weight: .bold, design: .monospaced))
                            .foregroundStyle(warm)
                    }
                }
                .padding(.vertical, 4)
            }

            if updater.phase == .downloading {
                VStack(spacing: 6) {
                    ProgressView(value: max(Double(updater.receivedBytes), 1),
                                 total: max(Double(updater.totalBytes), 1))
                        .tint(warm)
                    HStack {
                        Text(ByteCountFormatter.string(fromByteCount: updater.receivedBytes, countStyle: .file))
                        Spacer()
                        Text(ByteCountFormatter.string(fromByteCount: updater.totalBytes, countStyle: .file))
                    }
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                }
            }

            if let info = updater.info, !info.notes.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 6) {
                        Image(systemName: "sparkles").font(.system(size: 11, weight: .semibold)).foregroundStyle(warm)
                        Text(L10n.text("更新说明", "Release Notes"))
                            .fireSkyFont(size: 12, weight: .semibold)
                        Spacer()
                        if info.assetBytes > 0 {
                            Text(ByteCountFormatter.string(fromByteCount: info.assetBytes, countStyle: .file))
                                .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        }
                    }
                    ScrollView {
                        Text(info.notes)
                            .fireSkyFont(size: 12)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 190)
                }
                .padding(14)
                .background(Color.primary.opacity(scheme == .dark ? 0.04 : 0.03),
                            in: RoundedRectangle(cornerRadius: 12))
                .overlay {
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(FSDesign.surfaceBorder(role: .grouped, dark: scheme == .dark, emphasized: false), lineWidth: 1)
                }
            }

            if updater.phase == .failed && !updater.message.isEmpty {
                Text(updater.message)
                    .fireSkyFont(size: 11, relativeTo: .caption)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            }

            HStack(spacing: 10) {
                Button {
                    updater.checkForUpdates(interactive: true)
                } label: {
                    Label(L10n.text("重新检查", "Check Again"), systemImage: "arrow.clockwise")
                        .fireSkyFont(size: 12, weight: .medium)
                        .padding(.horizontal, 16).padding(.vertical, 9)
                        .background(Color.primary.opacity(scheme == .dark ? 0.08 : 0.06), in: Capsule())
                }
                .buttonStyle(.plain)
                .disabled(updater.phase == .checking || updater.phase == .downloading || updater.phase == .installing)

                if updater.phase == .available {
                    Button {
                        updater.downloadAndInstall()
                    } label: {
                        Label(L10n.text("立即下载", "Download & Install"), systemImage: "arrow.down.to.line")
                            .fireSkyFont(size: 12, weight: .semibold)
                            .foregroundStyle(scheme == .dark ? Color(0x1E, 0x15, 0x10) : Color.white)
                            .padding(.horizontal, 20).padding(.vertical, 9)
                            .background(FSDesign.accentGradient(dark: scheme == .dark), in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .keyboardShortcut(.defaultAction)
                } else if updater.phase == .downloading {
                    Button {
                        updater.cancelDownload()
                    } label: {
                        Text(L10n.text("取消下载 · \(progressPercent)%", "Cancel · \(progressPercent)%"))
                            .fireSkyFont(size: 12, weight: .semibold).monospacedDigit()
                            .padding(.horizontal, 18).padding(.vertical, 9)
                            .background(Color.primary.opacity(scheme == .dark ? 0.12 : 0.08), in: Capsule())
                    }
                    .buttonStyle(.plain)
                } else {
                    Button {
                        store.showUpdateSheet = false
                    } label: {
                        Text(L10n.text("完成", "Done"))
                            .fireSkyFont(size: 12, weight: .semibold)
                            .padding(.horizontal, 20).padding(.vertical, 9)
                            .background(Color.primary.opacity(scheme == .dark ? 0.12 : 0.08), in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 4)
        }
        .padding(24)
        .frame(width: 450)
        .background {
            ZStack {
                FSDesign.surfaceFill(role: .content, dark: scheme == .dark)
                RadialGradient(
                    colors: [warm.opacity(scheme == .dark ? 0.22 : 0.14), Color.clear],
                    center: .top, startRadius: 10, endRadius: 240
                )
            }
        }
    }
}
