import SwiftUI

struct DownloadProbeView: View {
    @EnvironmentObject private var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("下载连通性").font(.headline)
                    Text("先验证这台电脑能否真正收到 GFS、EC、CAMS 数据，再开始预测。")
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
                        if row.active { ProgressView().controlSize(.small) }
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
                        .buttonStyle(.borderless).help("复制探测报告")
                }
            }.font(.caption).foregroundStyle(.secondary)
            if !store.downloadProbeError.isEmpty {
                Text(store.downloadProbeError).font(.caption).foregroundStyle(.red).textSelection(.enabled)
            }
        }.padding(18)
            .glassCard()
    }
}
