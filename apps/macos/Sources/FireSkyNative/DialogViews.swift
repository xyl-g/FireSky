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
