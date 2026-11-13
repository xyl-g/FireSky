struct PreparationActions {
    enum Primary { case download, forecast }
    let ready: Bool
    let busy: Bool
    let hasSession: Bool

    var primary: Primary { ready ? .forecast : .download }
    var canDownload: Bool { hasSession && !busy }
    var canForecast: Bool { hasSession && ready && !busy }
}

struct DataRow: Identifiable {
    let source: String
    let ready: Bool
    let old: Bool
    let verified: Bool
    let latest: Bool
    let missing: Int
    var id: String { source }
    var title: String { source == "ecmwf" ? "EC" : source == "hsd" ? "卫星帧" : source.uppercased() }
    var status: String { !ready ? "需下载" : old ? "可更新" : "已下载" }
    var detail: String {
        !ready ? (missing > 0 ? "本次预测数据不完整" : "尚未下载本次预测数据")
            : old ? (verified ? "有新发布的数据可更新" : "非最新发布周期")
            : verified ? "发布周期已核验" : "更新待核验"
    }
}
