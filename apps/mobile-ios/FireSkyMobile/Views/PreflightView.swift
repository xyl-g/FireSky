import SwiftUI

/// 预测准备弹层（对齐 macOS v1.9.10 PreflightView + CycleAgeConfirmSheet）。
struct PreflightView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    private var actions: PreparationActions {
        PreparationActions(ready: store.ready, busy: store.busy, hasSession: store.session != nil)
    }
    private var summary: String {
        if !store.ready {
            let missing = store.rows.filter { !$0.ready }.map(\.title).joined(separator: "、")
            return missing.isEmpty ? store.status : L10n.tf("需下载 %@", missing)
        }
        let updates = store.rows.filter(\.old).map(\.title).joined(separator: "、")
        return updates.isEmpty ? L10n.t("数据已齐，可开始预测") : L10n.tf("数据已齐，可更新 %@", updates)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L10n.t("预测准备")).font(.title2.weight(.semibold))
                    Text(store.name).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { store.refreshCheck() } label: {
                    Image(systemName: "arrow.clockwise").frame(minWidth: 36, minHeight: 36)
                }
                .disabled(store.busy)
                .accessibilityLabel(L10n.t("重新检查"))
            }.padding(.bottom, 16)
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if store.rows.isEmpty {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text(L10n.t("正在检查本地数据"))
                        }
                        .frame(maxWidth: .infinity, minHeight: 140)
                    } else {
                        ForEach(store.rows) { row in
                            HStack(spacing: 12) {
                                Image(systemName: row.source == "hsd" ? "antenna.radiowaves.left.and.right" : "externaldrive")
                                    .font(.title3).foregroundStyle(.secondary).frame(width: 32)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(row.title).font(.body.weight(.medium))
                                    Text(L10n.status(row.detail)).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                StatusChip(
                                    status: !row.ready ? "pending" : row.old ? "interrupted" : "done",
                                    title: L10n.status(row.status)
                                )
                            }.frame(minHeight: 64).padding(.vertical, 8)
                            if row.id != store.rows.last?.id { Divider() }
                        }
                        VStack(alignment: .leading, spacing: 4) {
                            Label(L10n.t("下载前先知道"), systemImage: "info.circle")
                                .font(.caption.weight(.semibold)).foregroundStyle(.primary)
                            Text(L10n.t("首次下载耗时取决于本机缺失的数据集与网络。上方只列本次预测所需文件；已下载文件不会重复下载，过程中可取消，失败项会保留并提示重试。"))
                                .font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            if !store.camsConfigured {
                                Text(L10n.t("CAMS Key 未配置时仍可使用本地缓存；如需气溶胶数据，可在设置中配置后重新预检。"))
                                    .font(.caption2).foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .padding(12)
                        .background(Color.primary.opacity(scheme == .dark ? 0.06 : 0.04), in: RoundedRectangle(cornerRadius: 10))
                        .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(warm.opacity(0.22), lineWidth: 1) }
                        .padding(.top, 10)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        if store.checking {
                            ProgressView(value: store.percent, total: 100)
                            Text(store.status).font(.caption).foregroundStyle(.secondary)
                        } else if !store.error.isEmpty {
                            HStack(alignment: .top, spacing: 8) {
                                Text(store.error).font(.callout).foregroundStyle(.red)
                                    .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                                Spacer(minLength: 4)
                                Button { store.clearError() } label: {
                                    Image(systemName: "xmark").font(.caption).foregroundStyle(.secondary)
                                }.buttonStyle(.borderless)
                            }
                        } else {
                            HStack {
                                Label(summary, systemImage: store.ready ? "checkmark.circle" : "arrow.down.circle")
                                    .fontWeight(.medium)
                                if store.publicationsPending { ProgressView().controlSize(.mini) }
                            }.font(.callout).foregroundStyle(.primary)
                        }
                    }.frame(minHeight: 38, alignment: .leading).padding(.top, 12)
                }
            }
            .frame(maxWidth: .infinity)
            VStack(spacing: 12) {
                if actions.primary == .download {
                    AccentButton(title: L10n.t("下载缺失数据"),
                                 systemImage: "arrow.down.to.line", disabled: !actions.canDownload) { store.prepare() }
                } else {
                    AccentButton(title: L10n.t("确定预测"),
                                 systemImage: "sparkles", disabled: !actions.canForecast) { store.confirm() }
                }
                ViewThatFits(in: .horizontal) {
                    HStack {
                        cancelButton
                        Spacer()
                        if actions.primary != .download { updateButton }
                    }
                    VStack(spacing: 8) {
                        if actions.primary != .download { updateButton }
                        cancelButton
                    }
                }
            }.padding(.top, 14)
        }.padding(20).frame(maxWidth: 520, maxHeight: .infinity, alignment: .top)
            .workPageBackground()
            .presentationDragIndicator(.visible)
            .sheet(isPresented: $store.showCycleAgeConfirm) {
                CycleAgeConfirmSheet()
                    .environmentObject(store)
            }
    }

    private var cancelButton: some View {
        Button(L10n.cancel) { store.closePreflight() }
            .frame(minHeight: 44)
    }

    private var updateButton: some View {
        Button(L10n.t("更新数据"), systemImage: "arrow.down.to.line") { store.prepare() }
            .frame(minHeight: 44).disabled(!actions.canDownload)
    }
}

/// 数据周期确认 sheet（对齐 macOS CycleAgeConfirmSheet）：展示未核验或非最新周期的源。
struct CycleAgeConfirmSheet: View {
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
                        .frame(width: 44, height: 44)
                    Image(systemName: "clock.badge.exclamationmark")
                        .font(.system(size: 20, weight: .semibold))
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
                            .font(.callout).foregroundStyle(.secondary).frame(width: 24)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.title).font(.callout.weight(.semibold))
                            Text(L10n.status(row.detail)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        StatusChip(status: row.old ? "interrupted" : "pending", title: L10n.status(row.status))
                    }
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    if index < flaggedRows.count - 1 { Divider().opacity(0.5) }
                }
            }
            .fireSkySurface(role: .content, cornerRadius: 12)

            Spacer(minLength: 0)

            VStack(spacing: 10) {
                AccentButton(title: L10n.text("更新到最新数据", "Update to Latest"),
                             systemImage: "arrow.down.circle.fill",
                             disabled: store.busy) {
                    store.showCycleAgeConfirm = false
                    store.prepare()
                }
                Button {
                    store.confirmCycleAgeAndForecast()
                } label: {
                    Text(L10n.text("使用当前缓存继续", "Continue with Cache"))
                        .font(.body.weight(.medium))
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .background(Color.primary.opacity(scheme == .dark ? 0.08 : 0.05),
                                    in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
                Button(L10n.t("返回")) {
                    store.showCycleAgeConfirm = false
                }
                .frame(minHeight: 44)
            }
        }
        .padding(20)
        .background(WorkBackground())
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}

/// 数据管理页（库存 + 预测结果与附加缓存分类 + SRTM 地形下载）。
struct InventoryView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(store.workspace).font(.caption).foregroundStyle(.secondary)
                            .lineLimit(1).truncationMode(.middle)
                        Text(L10n.t("已统计占用仅覆盖模式数据（GFS / EC / CAMS）；地形与卫星以目录为准。"))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    if !store.dataDeleteMessage.isEmpty {
                        Text(store.dataDeleteMessage)
                            .font(.caption)
                            .foregroundStyle(store.dataDeleteFailed ? Color.red : Color.secondary)
                    }
                    TerrainPanelView()
                    ForEach(Array(store.inventory.enumerated()), id: \.offset) { _, dataset in
                        let source = dataset["source"] as? String ?? ""
                        let title = source == "ecmwf" ? "EC" : source.uppercased()
                        let cycles = dataset["cycles"] as? JSONObject ?? [:]
                        VStack(alignment: .leading, spacing: 12) {
                            HStack {
                                Image(systemName: "externaldrive").font(.title3).foregroundStyle(.secondary)
                                Text(title).font(.headline)
                                Spacer()
                                Text("\(dataset["total_file_count"] as? Int ?? 0) 个文件").font(.callout)
                                Text(String(format: "%.2f GB", (dataset["total_storage_mb"] as? Double ?? 0) / 1024))
                                    .font(.callout).foregroundStyle(.secondary).monospacedDigit()
                                Button(role: .destructive) {
                                    store.beginDataDelete(source: source, cycle: nil)
                                } label: {
                                    Image(systemName: "trash").frame(minWidth: 36, minHeight: 36)
                                }
                                .buttonStyle(.borderless)
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
                                        Button(role: .destructive) {
                                            store.beginDataDelete(source: source, cycle: key)
                                        } label: {
                                            Image(systemName: "trash").frame(minWidth: 32, minHeight: 32)
                                        }
                                        .buttonStyle(.borderless)
                                        .disabled(store.busy || !store.online || store.dataDeleteRunning)
                                    }.font(.callout).padding(.vertical, 4)
                                }
                            }
                        }
                        .padding(16)
                        .fireSkySurface(role: .content, cornerRadius: 14)
                    }
                    if store.inventory.isEmpty && store.errorText(for: .data).isEmpty {
                        VStack(spacing: 10) {
                            FireSkyMarkScene(size: 64)
                            Text(L10n.t("本地缓存为空")).font(.subheadline.weight(.medium))
                            Text(L10n.t("完成一次预测或在设置中配置数据源后，这里会显示库存。"))
                                .font(.caption).foregroundStyle(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 24)
                        .fireSkySurface(role: .content, cornerRadius: 14)
                    }
                    intermediateCachesSection
                    let dataError = store.errorText(for: .data)
                    if !dataError.isEmpty {
                        HStack(alignment: .top, spacing: 8) {
                            Text(dataError).font(.callout).foregroundStyle(.red).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 4)
                            Button { store.clearError() } label: {
                                Image(systemName: "xmark").font(.caption).foregroundStyle(.secondary)
                            }
                            .buttonStyle(.borderless)
                            .accessibilityLabel(L10n.close)
                        }
                        .padding(12)
                        .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                    }
                }.padding(16)
            }
            .workPageBackground()
            .navigationTitle(L10n.navData)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await store.loadInventory() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
            .confirmationDialog(L10n.t("删除本地数据？"), isPresented: Binding(
                get: { store.pendingDataDelete != nil && store.dataDeletePreview != nil },
                set: { if !$0 { store.cancelDataDelete() } }
            )) {
                Button(L10n.t("删除"), role: .destructive) { store.executeDataDelete() }
                Button(L10n.t("取消"), role: .cancel) { store.cancelDataDelete() }
            } message: {
                Text(deleteConfirmationText)
            }
        }
    }

    @ViewBuilder
    private var intermediateCachesSection: some View {
        if !store.inventoryCategories.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 10) {
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
                    HStack(spacing: 10) {
                        Image(systemName: categoryIcon(key))
                            .font(.callout).foregroundStyle(.secondary).frame(width: 22)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(categoryTitle(key)).font(.callout.weight(.medium))
                            Text(categorySubtitle(key)).font(.caption2).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file))
                                .font(.caption.weight(.medium)).monospacedDigit()
                            Text(L10n.text("\(files) 个文件", "\(files) files"))
                                .font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                        }
                    }
                    .padding(.vertical, 4)
                    if index < store.inventoryCategories.count - 1 { Divider().opacity(0.35) }
                }
            }
            .padding(16)
            .fireSkySurface(role: .content, cornerRadius: 14)
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

    private var deleteConfirmationText: String {
        guard let preview = store.dataDeletePreview,
              let scope = store.pendingDataDelete else {
            return L10n.t("正在生成删除预览…")
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

/// 地形卡（macOS TerrainPanelView 同构）：区域选择 → 非破坏性确认 → 批量下载 + 失败项重试。
struct TerrainPanelView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(L10n.t("地形 SRTM")).font(.headline)
                    Text(terrainSubtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if store.terrainLoading || store.terrainEstimateLoading { ProgressView().controlSize(.small) }
            }
            if store.terrainDownloading {
                terrainProgress
            }
            if !store.terrainMessage.isEmpty {
                Text(store.terrainMessage).font(.caption)
                    .foregroundStyle(store.terrainMessageFailed ? Color.red : Color.secondary)
            }
            localTilesLine
            if !store.terrainDownloading {
                HStack {
                    Text(L10n.t("下载区域"))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Picker(L10n.t("下载区域"), selection: $store.terrainRegion) {
                        ForEach(TerrainRegion.allCases) { region in
                            Text(region.rawValue).tag(region)
                        }
                    }
                    .pickerStyle(.menu)
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) {
                        terrainActionButtons
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        terrainActionButtons
                    }
                }
                .font(.callout)
            } else {
                Button(store.terrainCancelling ? L10n.text("正在停止…", "Stopping…") : L10n.text("取消下载", "Cancel download"),
                       systemImage: "stop.fill") {
                    store.cancelTerrainDownload()
                }
                .frame(minHeight: 36)
                .disabled(store.terrainCancelling)
            }
        }
        .padding(16)
        .fireSkySurface(role: .content, cornerRadius: 14)
        .confirmationDialog(downloadConfirmTitle, isPresented: Binding(
            get: { store.showTerrainConfirm },
            set: { if !$0 { store.dismissTerrainConfirm() } }
        )) {
            Button(L10n.t("开始下载")) { store.downloadTerrainRegion() }
            Button(L10n.t("取消"), role: .cancel) { store.dismissTerrainConfirm() }
        } message: {
            Text(confirmText)
        }
    }

    @ViewBuilder
    private var terrainActionButtons: some View {
        Button(L10n.t("下载区域地形"), systemImage: "globe") {
            store.loadTerrainEstimate()
        }
        .frame(minHeight: 36)
        .disabled(!store.online)
        if store.terrainLastFailedCount > 0 {
            Button(L10n.text("重试失败项（\(store.terrainLastFailedCount)）",
                             "Retry failed (\(store.terrainLastFailedCount))"),
                   systemImage: "arrow.clockwise.circle") {
                store.retryFailedTerrainTiles()
            }
            .frame(minHeight: 36)
            .disabled(!store.online)
        }
        Button(L10n.t("仅当前地点"), systemImage: "arrow.down.to.line") {
            store.downloadTerrainPoint()
        }
        .frame(minHeight: 36)
        .disabled(!store.online || store.terrain?["coverage_ok"] as? Bool == false)
    }

    private var downloadConfirmTitle: String {
        store.terrainRegion == .global ? L10n.t("下载全球地形数据？") : L10n.tf("下载%@地形数据？", store.terrainRegion.rawValue)
    }

    private var terrainSubtitle: String {
        guard let terrain = store.terrain else {
            return L10n.t("全球瓦片下载后会覆盖任意地点，无需再按点下载。")
        }
        if terrain["coverage_ok"] as? Bool == false {
            return L10n.t("当前纬度不在 SRTM 覆盖范围（-56 ≤ 纬度 < 60）。")
        }
        guard let tile = terrain["tile"] as? JSONObject else {
            return L10n.t("显示本地已有地形瓦片。")
        }
        let exists = tile["exists"] as? Bool == true
        let location = String(format: "%.4f° N, %.4f° E", store.lat, store.lon)
        return L10n.tf("目标 %@（%@）· 瓦片 %@ · %@", store.name, location, tile["name"] as? String ?? "",
                       exists ? L10n.t("已有本地瓦片") : L10n.t("未下载"))
    }

    @ViewBuilder
    private var terrainProgress: some View {
        let progress = store.terrainProgress
        let candidates = (progress["candidates"] as? NSNumber)?.intValue ?? 0
        let percent = (progress["percent"] as? NSNumber)?.doubleValue ?? 0
        VStack(alignment: .leading, spacing: 4) {
            ProgressView(value: percent, total: 100)
            let counts = "%.0f%% · 已下载 \((progress["downloaded"] as? NSNumber)?.intValue ?? 0)/\(candidates) · 已有 \((progress["existing"] as? NSNumber)?.intValue ?? 0) · 缺失 \((progress["absent"] as? NSNumber)?.intValue ?? 0) · 失败 \((progress["failed"] as? NSNumber)?.intValue ?? 0)"
            let current = (progress["current_tile"] as? String ?? "").isEmpty
                ? "" : " · 当前 \(progress["current_tile"] as? String ?? "")"
            Text(counts + current)
                .font(.caption2).foregroundStyle(.secondary).monospacedDigit()
            if let received = (progress["received_bytes"] as? NSNumber)?.int64Value, received > 0 {
                Text(ByteCountFormatter.string(fromByteCount: received, countStyle: .file))
                    .font(.caption2).foregroundStyle(.secondary).monospacedDigit()
            }
        }
    }

    @ViewBuilder
    private var localTilesLine: some View {
        let count = (store.terrain?["tile_count"] as? NSNumber)?.intValue ?? 0
        if count > 8 {
            Text(L10n.tf("本地已有 %d 块瓦片。", count)).font(.caption).foregroundStyle(.secondary)
        } else if let tiles = store.terrain?["tiles"] as? [JSONObject], !tiles.isEmpty {
            Text(L10n.tf("本地瓦片：%@", tiles.compactMap { $0["name"] as? String }.joined(separator: "、")))
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var confirmText: String {
        let global = store.terrainEstimate?["global"] as? JSONObject ?? [:]
        let candidates = (global["candidates"] as? NSNumber)?.intValue ?? 0
        let existing = (global["existing"] as? NSNumber)?.intValue ?? 0
        var text = L10n.tf("将下载%@的 SRTM 瓦片：候选 %d 块，本地已有 %d 块会自动跳过（海洋上的瓦片是很小的空值瓦片，同样会下载）。下载耗时较长且占用磁盘，可随时取消，重新开始会跳过已下载瓦片。", store.terrainRegion.rawValue, candidates, existing)
        if let gb = (global["estimated_gb"] as? NSNumber)?.doubleValue {
            text += String(format: "按抽样估算体积约 %.0f GB（实际体积以运行时用量为准）。", gb)
        } else {
            text += "暂时无法估算体积，开始后会显示实际用量。"
        }
        return text
    }
}

/// 下载连通性探测（对齐 macOS DownloadProbeView）。
struct DownloadProbeView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(L10n.text("下载连通性", "Download connectivity")).font(.headline)
                    Text(L10n.text("先验证这台设备能否真正收到 GFS、EC、CAMS 数据，再开始预测。",
                                   "Verify this device can receive GFS, EC, and CAMS data."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if store.probingDownloads {
                    Button(L10n.cancel, systemImage: "stop.fill") { store.cancelDownloadProbe() }
                        .disabled(store.cancellingDownloadProbe)
                } else {
                    Button(L10n.text("探测下载", "Probe"), systemImage: "network") { store.probeDownloads() }
                        .disabled(!store.online || store.busy || store.savingCAMS)
                }
            }
            Text("当前使用 EC \(store.ecmwfSourceLabel)。GFS 与 CAMS 使用各自官方入口。")
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(store.downloadProbeRows) { row in
                HStack(alignment: .top, spacing: 12) {
                    Group {
                        if row.active { ProgressView() }
                        else {
                            Image(systemName: row.symbol)
                                .foregroundStyle(row.passed ? Color.green :
                                    row.state == "waiting" || row.state == "cancelled" ? Color.secondary : Color.orange)
                        }
                    }.frame(width: 20, height: 20)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(row.title).font(.callout.weight(.medium))
                            Spacer(minLength: 8)
                            Text(row.label).font(.caption)
                                .foregroundStyle(row.passed ? Color.green : Color.secondary)
                        }
                        if !row.host.isEmpty {
                            Text(row.host).font(.caption).foregroundStyle(.secondary)
                        }
                        Text(row.detail).font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                        if row.bytes > 0 || row.elapsedMS > 0 {
                            Text(row.measurement).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                        }
                    }
                }
                if row.id != store.downloadProbeRows.last?.id { Divider() }
            }
            HStack {
                if store.probingDownloads {
                    Text(store.cancellingDownloadProbe ? "正在停止探测" : "正在验证真实数据下载 · CAMS 等待上限约 90 秒")
                } else if let checked = store.downloadProbeDate {
                    Text(checked, format: .dateTime.hour().minute().second())
                    Text("· \(store.downloadProbeRows.filter(\.passed).count)/3 项下载验证通过")
                } else {
                    Text("尚无本次网络的下载验证结果")
                }
                Spacer()
                if store.downloadProbeDate != nil && !store.probingDownloads {
                    Button { store.copyDownloadProbeReport() } label: { Image(systemName: "doc.on.doc") }
                        .buttonStyle(.borderless)
                }
            }.font(.caption).foregroundStyle(.secondary)
            if !store.downloadProbeError.isEmpty {
                Text(store.downloadProbeError).font(.caption).foregroundStyle(.red).textSelection(.enabled)
            }
        }
        .padding(16)
        .fireSkySurface(role: .content, cornerRadius: 14)
    }
}
