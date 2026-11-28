import SwiftUI

struct DownloadProbeView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(L10n.text("下载连通性", "Download Connectivity")).font(.headline)
                    Text(store.usesGeosFP
                         ? L10n.text("先验证这台电脑能否真正收到 GFS、EC、GEOS-FP 数据，再开始预测。",
                                     "Verify that this Mac can download GFS, EC, and GEOS-FP data before running forecasts.")
                         : L10n.text("先验证这台电脑能否真正收到 GFS、EC、CAMS 数据，再开始预测。",
                                     "Verify that this Mac can download GFS, EC, and CAMS data before running forecasts."))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if store.probingDownloads {
                    Button(L10n.t("取消"), systemImage: "stop.fill") { store.cancelDownloadProbe() }
                        .disabled(store.cancellingDownloadProbe)
                } else {
                    Button(L10n.text("探测下载", "Probe Downloads"), systemImage: "network") { store.probeDownloads() }
                        .disabled(!store.online || store.busy || store.savingCAMS)
                }
            }

            Text(store.usesGeosFP
                 ? L10n.text("当前使用 EC \(store.ecmwfSourceLabel)，AOD 使用 NASA GEOS-FP（免密钥）。GFS 使用 NOAA NOMADS。",
                             "Using EC \(store.ecmwfSourceLabel), NASA GEOS-FP for AOD (keyless), and NOAA NOMADS for GFS.")
                 : L10n.text("当前使用 EC \(store.ecmwfSourceLabel)。GFS 与 CAMS 使用各自官方入口。",
                             "Using EC \(store.ecmwfSourceLabel), with official endpoints for GFS and CAMS."))
                .font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: 10) {
                ForEach(store.downloadProbeRows) { row in
                    let rowColor: Color = row.passed
                        ? .green
                        : (row.state == "waiting" || row.state == "cancelled" ? .secondary : .orange)
                    HStack(alignment: .top, spacing: 12) {
                        Group {
                            if row.active {
                                ProgressView().controlSize(.small)
                            } else {
                                Image(systemName: row.symbol)
                                    .foregroundStyle(rowColor)
                            }
                        }
                        .frame(width: 20, height: 20)

                        VStack(alignment: .leading, spacing: 4) {
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text(row.title).font(.callout.weight(.medium))
                                Spacer(minLength: 8)
                                Text(row.label)
                                    .font(.caption.weight(.medium))
                                    .foregroundStyle(rowColor)
                            }
                            if !row.host.isEmpty {
                                Text(row.host)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(.secondary)
                            }
                            if !row.detail.isEmpty {
                                Text(row.detail)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .textSelection(.enabled)
                            }
                            if row.bytes > 0 || row.elapsedMS > 0 {
                                Text(row.measurement)
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    if row.id != store.downloadProbeRows.last?.id {
                        Divider().opacity(0.5)
                    }
                }
            }

            HStack {
                if store.probingDownloads {
                    Text(store.cancellingDownloadProbe
                         ? L10n.text("正在停止探测…", "Stopping probe…")
                         : (store.usesGeosFP
                            ? L10n.text("正在验证真实数据下载 · GEOS-FP OPeNDAP", "Verifying downloads · GEOS-FP OPeNDAP")
                            : L10n.text("正在验证真实数据下载 · CAMS 等待上限约 90 秒", "Verifying downloads · CAMS ~90s limit")))
                } else if let checked = store.downloadProbeDate {
                    Text(checked, format: .dateTime.hour().minute().second())
                    Text(L10n.text(
                        "· \(store.downloadProbeRows.filter(\.passed).count)/3 项下载验证通过",
                        "· \(store.downloadProbeRows.filter(\.passed).count)/3 passed"
                    ))
                } else {
                    Text(L10n.text("尚无本次网络的下载验证结果", "No download probe results yet"))
                }
                Spacer()
                if store.downloadProbeDate != nil && !store.probingDownloads {
                    Button { store.copyDownloadProbeReport() } label: { Image(systemName: "doc.on.doc") }
                        .buttonStyle(.borderless)
                        .help("复制探测报告")
                        .accessibilityLabel("复制探测报告")
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            if !store.downloadProbeError.isEmpty {
                Text(store.downloadProbeError)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .padding(16)
        .glassCard()
    }
}
