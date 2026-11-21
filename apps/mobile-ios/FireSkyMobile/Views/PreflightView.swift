import SwiftUI

/// 预测准备弹层（DialogViews.swift PreflightView 迁移；实况分支未迁——
/// 实况链延后 MN4 裁决，requestKind 在 v1 中恒为单点）。
struct PreflightView: View {
    @EnvironmentObject private var store: Store
    @Environment(\.colorScheme) private var scheme
    private var actions: PreparationActions {
        PreparationActions(ready: store.ready, busy: store.busy, hasSession: store.session != nil)
    }
    private var summary: String {
        if !store.ready {
            let missing = store.rows.filter { !$0.ready }.map(\.title).joined(separator: "、")
            return missing.isEmpty ? store.status : L10n.text("需下载", "Download needed") + " \(missing)"
        }
        let updates = store.rows.filter(\.old).map(\.title).joined(separator: "、")
        return updates.isEmpty ? L10n.text("数据已齐，可开始预测", "Data ready — start the forecast") : L10n.text("数据已齐，可更新", "Data ready, can update") + " \(updates)"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L10n.text("预测准备", "Forecast preparation")).font(.title2.weight(.semibold))
                    Text(store.name).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { store.refreshCheck() } label: { Image(systemName: "arrow.clockwise") }
                    .disabled(store.busy)
            }.padding(.bottom, 16)
            if store.rows.isEmpty {
                HStack { ProgressView(); Text(L10n.text("正在检查本地数据", "Checking local data")) }
                    .frame(maxWidth: .infinity, minHeight: 140)
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
                        Label(L10n.status(row.status), systemImage: !row.ready ? "arrow.down.circle" : row.old ? "arrow.clockwise" : "checkmark.circle.fill")
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
                Button(L10n.cancel) { store.closePreflight() }
                Spacer()
                if actions.primary == .download {
                    Button(L10n.text("下载缺失数据", "Download missing data"), systemImage: "arrow.down.to.line") { store.prepare() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canDownload)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                    Button(L10n.text("确定预测", "Confirm forecast")) {}.disabled(true)
                } else {
                    Button(L10n.text("更新数据", "Update data"), systemImage: "arrow.down.to.line") { store.prepare() }
                        .disabled(!actions.canDownload)
                    Button(L10n.text("确定预测", "Confirm forecast")) { store.confirm() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canForecast)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                }
            }.padding(.top, 14)
                .animation(.easeInOut(duration: 0.18), value: store.ready)
        }.padding(26).frame(maxWidth: 520)
            .background(Color(uiColor: .systemBackground))
    }
}

/// 数据管理页（数据与下载 → v1.6.0 对齐 macOS）：库存（按周期/整源手动删除）
/// + 地形区域下载。探测卡迁到「设置 → 数据」（macOS v1.2.0 同口径）。
struct InventoryView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(store.workspace).font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                    Text("已统计占用仅覆盖模式数据（GFS / EC / CAMS）")
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
                            Image(systemName: "externaldrive").font(.title2).foregroundStyle(.secondary)
                            Text(title).font(.headline)
                            Spacer()
                            Text("\(dataset["total_file_count"] as? Int ?? 0) 个文件")
                            Text(String(format: "%.2f GB", (dataset["total_storage_mb"] as? Double ?? 0) / 1024))
                                .foregroundStyle(.secondary).monospacedDigit()
                            Button(role: .destructive) {
                                store.beginDataDelete(source: source, cycle: nil)
                            } label: {
                                Image(systemName: "trash")
                            }
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
                                        Image(systemName: "trash")
                                    }
                                    .buttonStyle(.borderless)
                                    .disabled(store.busy || !store.online || store.dataDeleteRunning)
                                }.font(.callout).padding(.vertical, 5)
                            }
                        }
                    }
                    Divider()
                }
                if store.inventory.isEmpty && store.error.isEmpty { ProgressView().frame(maxWidth: .infinity) }
                if !store.error.isEmpty { Text(store.error).foregroundStyle(.red) }
            }.padding(20)
        }
        .workPageBackground()
        .navigationTitle("数据管理")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await store.loadInventory() } } label: {
                    Image(systemName: "arrow.clockwise")
                }
            }
        }
        .confirmationDialog("删除本地数据？", isPresented: Binding(
            get: { store.pendingDataDelete != nil && store.dataDeletePreview != nil },
            set: { if !$0 { store.cancelDataDelete() } }
        )) {
            Button("删除", role: .destructive) { store.executeDataDelete() }
            Button("取消", role: .cancel) { store.cancelDataDelete() }
        } message: {
            Text(deleteConfirmationText)
        }
    }

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

/// 地形卡（macOS TerrainPanelView 同构）：区域选择 → 估算确认 → 批量下载。
struct TerrainPanelView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("地形 SRTM").font(.headline)
                    Text(terrainSubtitle).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if store.terrainLoading { ProgressView().controlSize(.small) }
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
                Picker("区域", selection: $store.terrainRegion) {
                    ForEach(TerrainRegion.allCases) { region in
                        Text(region.rawValue).tag(region)
                    }
                }.pickerStyle(.menu)
                Button("下载区域地形", systemImage: "globe") {
                    store.loadTerrainEstimate()
                }
                .disabled(!store.online)
                Button("仅当前地点", systemImage: "arrow.down.to.line") {
                    store.downloadTerrainPoint()
                }
                .disabled(!store.online || store.terrain?["coverage_ok"] as? Bool == false)
            } else {
                Button(store.terrainCancelling ? "正在停止…" : "取消下载",
                       systemImage: "stop.fill") {
                    store.cancelTerrainDownload()
                }
                .disabled(store.terrainCancelling)
            }
        }
        .padding(18)
        .background(Color(uiColor: .secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 10))
        .confirmationDialog(downloadConfirmTitle, isPresented: Binding(
            get: { store.showTerrainConfirm },
            set: { if !$0 { store.dismissTerrainConfirm() } }
        )) {
            Button("开始下载", role: .destructive) { store.downloadTerrainRegion() }
            Button("取消", role: .cancel) { store.dismissTerrainConfirm() }
        } message: {
            Text(confirmText)
        }
    }

    private var downloadConfirmTitle: String {
        store.terrainRegion == .global ? "下载全球地形数据？" : "下载\(store.terrainRegion.rawValue)地形数据？"
    }

    private var terrainSubtitle: String {
        guard let terrain = store.terrain else {
            return "全球瓦片下载后会覆盖任意地点，无需再按点下载。"
        }
        if terrain["coverage_ok"] as? Bool == false {
            return "当前纬度不在 SRTM 覆盖范围（-56 ≤ 纬度 < 60）。"
        }
        guard let tile = terrain["tile"] as? JSONObject else {
            return "显示本地已有地形瓦片。"
        }
        let exists = tile["exists"] as? Bool == true
        return "目标 \(store.name)（\(String(format: "%.4f° N, %.4f° E", store.lat, store.lon))）· 瓦片 \(tile["name"] as? String ?? "") · \(exists ? "已有本地瓦片" : "未下载")"
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
            Text("本地已有 \(count) 块瓦片。").font(.caption).foregroundStyle(.secondary)
        } else if let tiles = store.terrain?["tiles"] as? [JSONObject], !tiles.isEmpty {
            Text("本地瓦片：\(tiles.compactMap { $0["name"] as? String }.joined(separator: "、"))")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var confirmText: String {
        let global = store.terrainEstimate?["global"] as? JSONObject ?? [:]
        let candidates = (global["candidates"] as? NSNumber)?.intValue ?? 0
        let existing = (global["existing"] as? NSNumber)?.intValue ?? 0
        var text = "将下载\(store.terrainRegion.rawValue)的 SRTM 瓦片：候选 \(candidates) 块，本地已有 \(existing) 块会自动跳过（海洋上的瓦片是很小的空值瓦片，同样会下载）。下载耗时较长且占用磁盘，可随时取消，重新开始会跳过已下载瓦片。"
        if let gb = (global["estimated_gb"] as? NSNumber)?.doubleValue {
            text += String(format: "按抽样估算体积约 %.0f GB（实际体积以运行时用量为准）。", gb)
        } else {
            text += "暂时无法估算体积，开始后会显示实际用量。"
        }
        return text
    }
}

/// 下载连通性探测（DownloadProbeView 迁移；NSPasteboard → UIPasteboard）。
struct DownloadProbeView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("下载连通性").font(.headline)
                    Text("先验证这台设备能否真正收到 GFS、EC、CAMS 数据，再开始预测。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if store.probingDownloads {
                    Button("取消", systemImage: "stop.fill") { store.cancelDownloadProbe() }
                        .disabled(store.cancellingDownloadProbe)
                } else {
                    Button("探测下载", systemImage: "network") { store.probeDownloads() }
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
        }.padding(18)
            .background(Color(uiColor: .secondarySystemBackground).opacity(0.72),
                        in: RoundedRectangle(cornerRadius: 10))
    }
}
