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
            return missing.isEmpty ? store.status : "需下载 \(missing)"
        }
        let updates = store.rows.filter(\.old).map(\.title).joined(separator: "、")
        return updates.isEmpty ? "数据已齐，可开始预测" : "数据已齐，可更新 \(updates)"
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 6) {
                    Text("预测准备").font(.title2.weight(.semibold))
                    Text(store.name).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Button { store.refreshCheck() } label: { Image(systemName: "arrow.clockwise") }
                    .disabled(store.busy)
            }.padding(.bottom, 16)
            if store.rows.isEmpty {
                HStack { ProgressView(); Text("正在检查本地数据") }
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
                Button("取消") { store.closePreflight() }
                Spacer()
                if actions.primary == .download {
                    Button("下载缺失数据", systemImage: "arrow.down.to.line") { store.prepare() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canDownload)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                    Button("确定预测") {}.disabled(true)
                } else {
                    Button("更新数据", systemImage: "arrow.down.to.line") { store.prepare() }
                        .disabled(!actions.canDownload)
                    Button("确定预测") { store.confirm() }
                        .buttonStyle(.borderedProminent).disabled(!actions.canForecast)
                        .foregroundStyle(scheme == .dark ? Color.black : Color.white)
                }
            }.padding(.top, 14)
                .animation(.easeInOut(duration: 0.18), value: store.ready)
        }.padding(26).frame(maxWidth: 520)
            .background(Color(uiColor: .systemBackground))
    }
}

/// 本地数据清单页（数据与下载）。桌面版「在访达中打开」按钮未迁移
/// （iOS 沙箱无 Finder 语义）。
struct InventoryView: View {
    @EnvironmentObject private var store: Store
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack {
                    Text("本地数据").font(.title.weight(.semibold))
                    Spacer()
                    Button("按本次位置下载", systemImage: "arrow.down") {
                        store.page = .point
                        store.beginCheck()
                    }.disabled(store.busy)
                    Button { Task { await store.loadInventory() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
                DownloadProbeView()
                Divider()
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
