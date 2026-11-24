import AppKit
import Foundation

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
}
