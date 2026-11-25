import Foundation

extension Store {
    var remoteServerHostLabel: String {
        guard hasSavedRemoteServer,
              let url = URL(string: remoteServerURL),
              let host = url.host,
              !host.isEmpty else {
            return L10n.text("未配置服务器", "No server")
        }
        if let port = url.port {
            return "\(host):\(port)"
        }
        return host
    }

    func testRemoteServer() async {
        guard !remoteTesting else { return }
        remoteTesting = true
        remoteTestMessage = ""
        remoteTestOK = false
        defer { remoteTesting = false }

        let draft = remoteTokenDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        let effectiveToken = draft.isEmpty ? (ServerCredentialStore.loadToken() ?? "") : draft
        do {
            let remoteStatus = try await RemoteHttpTransport.testConnection(
                urlString: remoteServerURL,
                token: effectiveToken,
                session: remoteSession
            )
            remoteWorkspace = remoteStatus.workspace
            remoteTestOK = true
            remoteTestMessage = remoteStatus.workspace.isEmpty
                ? L10n.text("连接成功（FireSky 服务在线）。", "Connected (FireSky server online).")
                : L10n.text("连接成功：\(remoteStatus.workspace)", "Connected: \(remoteStatus.workspace)")
        } catch {
            remoteTestOK = false
            remoteTestMessage = friendlyEngineMessage(error.localizedDescription)
        }
    }

    func saveAndConnectRemoteServer() async {
        guard !busy, !probingDownloads, !remoteTesting else { return }
        remoteTesting = true
        remoteTestMessage = ""
        remoteTestOK = false
        defer { remoteTesting = false }

        do {
            let normalized = try RemoteHttpTransport.normalizeBaseURL(remoteServerURL)
            remoteServerURL = normalized.absoluteString
            UserDefaults.standard.set(remoteServerURL, forKey: "remoteServerURL")
            hasSavedRemoteServer = true

            let draft = remoteTokenDraft.trimmingCharacters(in: .whitespacesAndNewlines)
            if !draft.isEmpty {
                try ServerCredentialStore.saveToken(draft)
                remoteTokenDraft = ""
                remoteTokenSaved = true
            }

            resetModeSessionState()
            status = L10n.text("正在连接远程服务器", "Connecting to remote server")
            await connect()

            if online {
                remoteTestOK = true
                remoteTestMessage = remoteWorkspace.isEmpty
                    ? L10n.text("已连接到远程服务器。", "Connected to remote server.")
                    : L10n.text("已连接：\(remoteWorkspace)", "Connected: \(remoteWorkspace)")
                await loadInventory()
                loadTaskHistory()
                await refreshCAMSConfiguration()
                await loadRetentionSettings()
                await loadDataSyncSettings()
            } else {
                remoteTestOK = false
                remoteTestMessage = error.isEmpty
                    ? L10n.text("连接远程服务器失败。", "Failed to connect to remote server.")
                    : error
            }
        } catch {
            remoteTestOK = false
            remoteTestMessage = friendlyEngineMessage(error.localizedDescription)
        }
    }

    func clearRemoteToken() {
        ServerCredentialStore.deleteToken()
        remoteTokenDraft = ""
        remoteTokenSaved = false
        remoteTestOK = false
        remoteTestMessage = L10n.text("已清除保存的访问令牌。", "Saved access token cleared.")
    }

    func clearRemoteServerConfiguration() {
        guard !busy, !probingDownloads, !remoteTesting else { return }
        UserDefaults.standard.removeObject(forKey: "remoteServerURL")
        ServerCredentialStore.deleteToken()
        remoteServerURL = ""
        hasSavedRemoteServer = false
        remoteTokenDraft = ""
        remoteTokenSaved = false
        remoteWorkspace = ""
        remoteTestOK = false
        remoteTestMessage = ""
        online = false
        engine.stop()
        resetModeSessionState()
        clearError()
        status = L10n.text("请连接 FireSky 服务器", "Connect to FireSky server")
    }

    func resetModeSessionState() {
        generation = UUID()
        session = nil
        result = nil
        resultsByMode.removeAll()
        regionalCache = RegionalForecast(result: nil)
        selectedRegionalCoordinate = nil
        inventory = []
        inventoryCategories = []
        taskHistory = []
        taskHistoryLoaded = false
        taskHistoryError = ""
        clearTaskSelection()
    }
}
