import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

/// 原生文本导出：使用 NSSavePanel / NSPasteboard，不引入外部服务。
/// 导出内容全部来自已发布的 Rust 报告与 Store 参数，不在 SwiftUI 重算科学结论。
enum SummaryExport {
    @MainActor
    static func save(_ text: String, suggestedFilename: String) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggestedFilename
        panel.allowedContentTypes = [.plainText]
        panel.canCreateDirectories = true
        panel.message = "选择保存 FireSky 观测摘要的位置"
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            try? text.write(to: url, atomically: true, encoding: .utf8)
        }
    }

    @MainActor
    static func copy(_ text: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
    }

    @MainActor
    static func suggestedFilename(_ store: Store) -> String {
        let invalid = CharacterSet(charactersIn: "/\\:").union(.whitespacesAndNewlines)
        let base = store.resultTitle.components(separatedBy: invalid).joined(separator: "_")
        return "FireSky_\(base)_摘要.txt"
    }

    @MainActor
    static func summaryText(store: Store, comparison: ForecastComparison) -> String {
        var lines: [String] = []
        lines.append(L10n.text("FireSky 观测摘要", "FireSky observation summary"))
        lines.append(String(format: L10n.text("导出时间：%@", "Exported: %@"),
                            ISO8601DateFormatter().string(from: Date())))
        lines.append(String(format: L10n.text("地点：%@ · 纬度 %.4f · 经度 %.4f", "Location: %@ · lat %.4f · lon %.4f"),
                            store.name, store.lat, store.lon))
        lines.append(String(format: L10n.text("目标日期与现象：%@ · %@", "Target date and phenomenon: %@ · %@"),
                            store.resultTitle, store.resultPhenomenonLabel))
        lines.append(String(format: L10n.text("时区：%@", "Time zone: %@"), store.timezone))
        lines.append("")
        lines.append(L10n.text("结论", "Conclusion"))
        lines.append(conclusion(comparison))
        if let first = ForecastComparison.models.compactMap({ comparison.report($0) }).first {
            let best = timeText(first["best_time_local"] as? String)
            let window = windowText(first)
            if best != "—" || window != "—" {
                lines.append("\(L10n.text("最佳", "Best")) \(best) · \(L10n.text("窗口", "Window")) \(window)")
            }
        }
        lines.append("")
        lines.append(L10n.text("模型状态", "Model status"))
        for model in ForecastComparison.models {
            if let report = comparison.report(model) {
                let level = report["peak_quality_level"] as? String
                    ?? report["quality_level"] as? String
                    ?? comparison.peak(model).map { String(format: "%.2f", $0) }
                    ?? "—"
                let quality = comparison.peak(model).map { String(format: "%.2f", $0) } ?? "—"
                let best = timeText(report["best_time_local"] as? String)
                let window = windowText(report)
                let status = comparison.status(model)
                let indexLabel = L10n.text("指数", "Index")
                let bestLabel = L10n.text("最佳", "Best")
                let windowLabel = L10n.text("窗口", "Window")
                let line = "\(model.uppercased())：\(status) · \(level) · \(indexLabel) \(quality)/2.5 · \(bestLabel) \(best) · \(windowLabel) \(window)"
                lines.append(line)
                if let limit = (report["quality_components"] as? [String: Any])?["quality_limiting_factor"] as? String,
                   !limit.isEmpty {
                    lines.append("  - \(L10n.text("主要限制", "Main limit"))：\(limit)")
                }
            } else {
                lines.append("\(model.uppercased())：\(comparison.status(model)) · "
                             + (comparison.error(model) ?? L10n.text("无可用报告", "No available report")))
            }
        }
        lines.append("")
        lines.append(L10n.text("来源时效", "Source timeliness"))
        let timeliness = ForecastComparison.models.compactMap { model -> String? in
            guard let report = comparison.report(model) else { return nil }
            var parts: [String] = []
            if let cycle = report["cycle_time_utc"] as? String {
                parts.append("\(L10n.text("预测周期", "Cycle")) \(cycle)")
            }
            if let cache = report["cache"] as? [String: Any] {
                let status = cache["status"] as? String ?? "—"
                let generated = cache["generated_at_utc"] as? String
                parts.append("\(L10n.text("缓存", "Cache")) \(status)" + (generated.map { " · \($0)" } ?? ""))
            }
            if let provenance = report["data_provenance"] as? [String: Any], !provenance.isEmpty {
                let detail = provenance.keys.sorted().map { "\($0)=\(String(describing: provenance[$0] ?? ""))" }
                    .joined(separator: ", ")
                parts.append("\(L10n.text("数据来源", "Provenance")) \(detail)")
            }
            if let source = report["profile_source"] as? [String: Any],
               source["available"] as? Bool == false {
                let reason = source["reason"] as? String ?? "—"
                parts.append("\(L10n.text("冻结剖面不可用", "Frozen profile unavailable"))：\(reason)")
            }
            return parts.isEmpty ? nil : "\(model.uppercased())：\(parts.joined(separator: "；"))"
        }
        lines.append(contentsOf: timeliness.isEmpty
                     ? [L10n.text("未提供来源时效字段。", "No source-timeliness fields provided.")]
                     : timeliness)
        lines.append("")
        lines.append(L10n.text("缺测与限制", "Missing data and limits"))
        var missing: [String] = []
        for model in ForecastComparison.models {
            if let error = comparison.error(model) { missing.append("\(model.uppercased())：\(error)") }
        }
        if store.resultParametersStale {
            missing.append(L10n.text("当前结果对应旧参数，可点「按新参数重算」更新。",
                                     "This result uses earlier parameters; recompute with current parameters to update."))
        }
        if missing.isEmpty {
            lines.append(L10n.text("报告未提供额外缺测说明；缺测不按 0 处理，失败模式不平均或补齐。",
                                   "No additional missing-data note was provided. Missing values are not treated as zero and failed models are not averaged or filled."))
        } else {
            lines.append(contentsOf: missing.map { "- \($0)" })
        }
        if let reportPath = ForecastComparison.models.compactMap({ comparison.report($0)?["html_report_path"] as? String }).first,
           !reportPath.isEmpty {
            lines.append("")
            lines.append("\(L10n.text("完整报告", "Full report"))：\(reportPath)")
        }
        return lines.joined(separator: "\n")
    }

    @MainActor
    private static func conclusion(_ comparison: ForecastComparison) -> String {
        let available = ForecastComparison.models.compactMap { model -> (String, String)? in
            guard let report = comparison.report(model) else { return nil }
            let level = report["peak_quality_level"] as? String ?? report["quality_level"] as? String ?? "—"
            return (model.uppercased(), level)
        }
        if available.isEmpty { return L10n.text("尚无可用模型结果。", "No model result is available yet.") }
        if available.count == 1 {
            return String(format: L10n.text("仅 %1$@ 可用：%2$@", "Only %1$@ is available: %2$@"),
                          available[0].0, available[0].1)
        }
        if Set(available.map { $0.1 }).count == 1 {
            return String(format: L10n.text("两个模式结论一致：%@", "Both models agree: %@"), available[0].1)
        }
        return available.map { "\($0.0) \($0.1)" }.joined(separator: " · ")
    }

    private static func timeText(_ value: String?) -> String {
        guard let value, !value.isEmpty else { return "—" }
        let replaced = value.replacingOccurrences(of: "T", with: " ")
        return String(replaced.prefix(16))
    }

    private static func windowText(_ report: [String: Any]) -> String {
        guard let timing = report["timing_window"] as? [String: Any],
              let start = timing["start_time_local"] as? String,
              let end = timing["end_time_local"] as? String else { return "—" }
        return "\(timeText(start)) – \(timeText(end))"
    }

    @MainActor
    static func posterImage(store: Store, comparison: ForecastComparison) -> NSImage? {
        let view = GlowPosterCardView(store: store, comparison: comparison)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2.0
        return renderer.nsImage
    }

    @MainActor
    static func copyPoster(store: Store, comparison: ForecastComparison) {
        guard let image = posterImage(store: store, comparison: comparison) else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.writeObjects([image])
    }

    @MainActor
    static func savePoster(store: Store, comparison: ForecastComparison) {
        guard let image = posterImage(store: store, comparison: comparison),
              let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(using: .png, properties: [:]) else { return }
        let panel = NSSavePanel()
        let invalid = CharacterSet(charactersIn: "/\\:").union(.whitespacesAndNewlines)
        let base = store.resultTitle.components(separatedBy: invalid).joined(separator: "_")
        panel.nameFieldStringValue = "FireSky_\(base)_追霞卡.png"
        panel.allowedContentTypes = [.png]
        panel.canCreateDirectories = true
        panel.message = L10n.text("选择保存追霞海报卡片 PNG 的位置", "Choose where to save the FireSky glow poster PNG")
        panel.begin { response in
            guard response == .OK, let url = panel.url else { return }
            try? png.write(to: url, options: .atomic)
        }
    }
}

private struct GlowPosterCardView: View {
    let store: Store
    let comparison: ForecastComparison

    private var primaryReport: JSONObject? {
        comparison.report(store.selectedModel)
            ?? comparison.report("ec")
            ?? comparison.report("gfs")
    }

    private var peakScore: Double? {
        ForecastComparison.models.compactMap { comparison.peak($0) }.max()
    }

    private var spectral: JSONObject? {
        primaryReport?["spectral_color"] as? JSONObject
    }

    private var radar: JSONObject? {
        primaryReport?["azimuth_radar"] as? JSONObject
    }

    private var skyColors: [Color] {
        if let hexes = spectral?["sky_gradient_hex"] as? [String], !hexes.isEmpty {
            return hexes.compactMap { Color(hexString: $0) }
        }
        return [Color(red: 0.08, green: 0.14, blue: 0.30), Color(red: 0.88, green: 0.42, blue: 0.22)]
    }

    private var stepScores: [Double] {
        let steps = primaryReport?["steps"] as? [JSONObject] ?? []
        return steps.map { ($0["instant_quality_index"] as? NSNumber)?.doubleValue ?? 0.0 }
    }

    var body: some View {
        let grade = primaryReport?["peak_quality_level"] as? String
            ?? primaryReport?["quality_level"] as? String
            ?? L10n.text("火烧云预报", "Glow Forecast")
        let bestTime = (primaryReport?["best_time_local"] as? String).map {
            String($0.replacingOccurrences(of: "T", with: " ").prefix(16))
        } ?? "—"
        let colorHex = spectral?["peak_hex"] as? String ?? "#E8703A"
        let colorLabel = L10n.text(
            spectral?["peak_label_zh"] as? String ?? "",
            spectral?["peak_label_en"] as? String ?? ""
        )
        let note = store.spotNote.trimmingCharacters(in: .whitespacesAndNewlines)

        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("FIRESKY · 追霞预报卡")
                    .font(.system(size: 11, weight: .bold))
                    .tracking(1.2)
                    .foregroundStyle(.white.opacity(0.85))
                Spacer()
                Text(store.resultPhenomenonLabel)
                    .font(.system(size: 11, weight: .semibold))
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(.white.opacity(0.18), in: Capsule())
                    .foregroundStyle(.white)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text(store.name)
                    .font(.system(size: 24, weight: .bold))
                    .foregroundStyle(.white)
                Text(String(format: "%@ · %.3f° N, %.3f° E%@", store.resultTitle, store.lat, store.lon,
                            note.isEmpty ? "" : " · \(note)"))
                    .font(.system(size: 12))
                    .foregroundStyle(.white.opacity(0.82))
            }

            HStack(alignment: .lastTextBaseline, spacing: 14) {
                Text(peakScore.map { String(format: "%.2f", $0) } ?? "—")
                    .font(.system(size: 46, weight: .heavy).monospacedDigit())
                    .foregroundStyle(.white)
                VStack(alignment: .leading, spacing: 3) {
                    Text("/ 2.5 · \(L10n.qualityLevel(grade))")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white)
                    Text("最佳时刻 \(bestTime)")
                        .font(.system(size: 12).monospacedDigit())
                        .foregroundStyle(.white.opacity(0.85))
                }
                Spacer()
            }

            posterBarsSection

            HStack(spacing: 12) {
                if let swatch = Color(hexString: colorHex) {
                    HStack(spacing: 6) {
                        Circle().fill(swatch).frame(width: 12, height: 12)
                            .overlay(Circle().stroke(.white.opacity(0.8), lineWidth: 1))
                        Text(colorLabel.isEmpty ? colorHex : "\(colorLabel) \(colorHex)")
                            .font(.system(size: 11, weight: .medium))
                            .foregroundStyle(.white)
                    }
                }
                Spacer()
                if let dir = radar?["best_direction_zh"] as? String,
                   let deg = (radar?["best_azimuth_deg"] as? NSNumber)?.doubleValue {
                    Text(String(format: "推荐朝向 %@（%.0f°）", dir, deg))
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.92))
                }
            }
        }
        .padding(20)
        .frame(width: 440)
        .background(
            LinearGradient(colors: skyColors, startPoint: .top, endPoint: .bottom)
                .overlay(Color.black.opacity(0.18))
        )
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    @ViewBuilder
    private var posterBarsSection: some View {
        if !stepScores.isEmpty {
            HStack(alignment: .bottom, spacing: 3) {
                ForEach(Array(stepScores.enumerated()), id: \.offset) { _, val in
                    posterBar(val)
                }
            }
            .frame(height: 36, alignment: .bottom)
            .padding(8)
            .background(.black.opacity(0.18), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private func posterBar(_ val: Double) -> some View {
        let opacity: Double = min(1.0, max(0.28, val / 2.2))
        let ratio: Double = min(val, 2.5) / 2.5
        let barHeight: CGFloat = max(4.0, CGFloat(ratio) * 34.0)
        return RoundedRectangle(cornerRadius: 2)
            .fill(Color.white.opacity(opacity))
            .frame(maxWidth: .infinity, minHeight: barHeight, maxHeight: barHeight)
    }
}

private extension Color {
    init?(hexString: String) {
        let cleaned = hexString.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard cleaned.count == 6, let val = UInt64(cleaned, radix: 16) else { return nil }
        let r = Double((val >> 16) & 0xFF) / 255.0
        let g = Double((val >> 8) & 0xFF) / 255.0
        let b = Double(val & 0xFF) / 255.0
        self.init(red: r, green: g, blue: b)
    }
}
