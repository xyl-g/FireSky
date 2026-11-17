#include "firesky_ffi_client.hpp"
#include "firesky_ffi.h"

#include <array>
#include <cstdlib>
#include <iostream>
#include <string>

namespace {

bool require_success(const std::string& label, const std::string& response,
                     const std::string& needle) {
    if (response.find("\"contract\":\"firesky-ffi-call-v1\"") == std::string::npos ||
        response.find("\"ok\":true") == std::string::npos ||
        (!needle.empty() && response.find(needle) == std::string::npos)) {
        std::cerr << label << " returned an unexpected envelope\n";
        return false;
    }
    return true;
}

bool require_error(const firesky::Engine& engine, const std::string& label,
                   const std::string& method, const std::string& params,
                   std::int32_t expected_code,
                   const std::string& needle) {
    try {
        (void)engine.call(method, params);
    } catch (const firesky::FfiError& error) {
        if (error.code() == expected_code &&
            error.envelope().find("\"contract\":\"firesky-ffi-error-v1\"") !=
                std::string::npos &&
            error.envelope().find("\"ok\":false") != std::string::npos &&
            (needle.empty() ||
             error.envelope().find(needle) != std::string::npos)) {
            return true;
        }
        std::cerr << label << " returned an unexpected error envelope (code "
                  << error.code() << ")\n";
        return false;
    }
    std::cerr << label << " unexpectedly succeeded\n";
    return false;
}

bool check_preflight(const firesky::Engine& engine, const std::string& kind,
                     const std::string& payload) {
    const auto response = engine.call(
        "check", "{\"kind\":\"" + kind + "\",\"payload\":" + payload + "}");
    return require_success(kind + " check", response, "\"method\":\"check\"") &&
           response.find("\"report\"") != std::string::npos &&
           response.find("\"ready\":false") != std::string::npos;
}

} // namespace

int main(int argc, char** argv) {
    const std::string library =
        argc > 1 ? argv[1] : "libfiresky_engine.so";
    const std::string workspace =
        argc > 2 ? argv[2] : ".";

    try {
        firesky::Engine engine(library, workspace);
        const auto sync = engine.call("hello");
        if (sync.find("\"contract\":\"firesky-ffi-call-v1\"") ==
                std::string::npos ||
            sync.find("\"ok\":true") == std::string::npos ||
            sync.find("\"engine\":\"firesky-rust\"") == std::string::npos) {
            std::cerr << "hello returned a non-success envelope\n";
            return EXIT_FAILURE;
        }
        for (const auto* capability : std::array{
                 "point", "regional", "nowcast", "inventory", "search",
                 "metadata", "network_probe", "cancel", "profile_query",
                 "task_history", "task_events", "result_restore"}) {
            if (sync.find(std::string{"\""} + capability + "\"") ==
                std::string::npos) {
                std::cerr << "hello omitted capability: " << capability << '\n';
                return EXIT_FAILURE;
            }
        }

        auto task = engine.start("hello");
        const auto async = task.join();
        if (async.find("\"ok\":true") == std::string::npos) {
            std::cerr << "async hello returned a non-success envelope\n";
            return EXIT_FAILURE;
        }
        const auto retention = engine.call("retention_settings");
        if (!require_success("retention settings", retention,
                             "\"contract\":\"rust-retention-settings-v1\"") ||
            retention.find("\"settings\"") == std::string::npos) {
            return EXIT_FAILURE;
        }
        const auto saved_retention = engine.call(
            "save_retention_settings",
            "{\"settings\":{\"gfs_cycles\":5,\"ecmwf_cycles\":3,"
            "\"cams_cycles\":2,\"satellite_days\":7,\"report_days\":30,"
            "\"map_days\":14}}");
        if (!require_success("save retention settings", saved_retention,
                             "\"contract\":\"rust-retention-settings-v1\"") ||
            saved_retention.find("\"configured\":true") == std::string::npos ||
            saved_retention.find("\"gfs_cycles\":5") == std::string::npos) {
            return EXIT_FAILURE;
        }
        const auto restored_retention = engine.call("retention_settings");
        if (!require_success("restored retention settings", restored_retention,
                             "\"contract\":\"rust-retention-settings-v1\"") ||
            restored_retention.find("\"configured\":true") == std::string::npos ||
            restored_retention.find("\"gfs_cycles\":5") == std::string::npos) {
            return EXIT_FAILURE;
        }

        const auto profile = engine.call(
            "profile_query",
            "{\"result_id\":\"0000000000000000000000000000000000000000000000000000000000000000\","
            "\"source_identity\":{\"bytes\":0,\"sha256\":\"0000000000000000000000000000000000000000000000000000000000000000\"},"
            "\"model_source\":\"gfs\",\"lat\":30.25,\"lon\":120.25,"
            "\"valid_time_utc\":\"2026-11-12T09:00:00Z\"}");
        if (!require_success("profile query", profile,
                             "\"contract\":\"rust-desktop-profile-query-v1\"") ||
            profile.find("\"status\":\"missing\"") == std::string::npos ||
            profile.find("\"model_source\":\"gfs\"") == std::string::npos) {
            return EXIT_FAILURE;
        }
        const auto history = engine.call("task_history", "{\"limit\":1}");
        if (!require_success("task history", history,
                             "\"schema\":\"firesky-task-history-v1\"") ||
            history.find("\"tasks\"") == std::string::npos ||
            history.find("\"issues\"") == std::string::npos) {
            return EXIT_FAILURE;
        }
        if (!require_error(
                engine, "task events", "task_events",
                "{\"task_id\":\"0000000000000000000000000000000000000000000000000000000000000000\"}",
                FIRESKY_FFI_RUNTIME_ERROR, "")) {
            return EXIT_FAILURE;
        }
        // task_forget on a missing record is a soft no-op (forgotten=false);
        // result_restore on a missing record must fail closed.
        const auto forgotten = engine.call(
            "task_forget",
            "{\"task_id\":\"0000000000000000000000000000000000000000000000000000000000000000\"}");
        if (!require_success("task forget unknown id", forgotten,
                             "\"forgotten\":false")) {
            return EXIT_FAILURE;
        }
        if (!require_error(
                engine, "result restore", "result_restore",
                "{\"task_id\":\"0000000000000000000000000000000000000000000000000000000000000000\"}",
                FIRESKY_FFI_RUNTIME_ERROR, "")) {
            return EXIT_FAILURE;
        }

        if (!check_preflight(
                engine, "point",
                "{\"name\":\"Hangzhou\",\"lat\":30.2741,\"lon\":120.1551,"
                "\"elevation_m\":8,\"timezone_name\":\"Asia/Shanghai\","
                "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
                "\"model_source\":\"both\",\"forecast_mode\":\"model\","
                "\"satellite_source\":\"off\",\"ecmwf_source\":\"ecmwf\"}") ||
            !check_preflight(
                engine, "regional",
                "{\"name\":\"Hangzhou region\",\"lat\":30.2741,\"lon\":120.1551,"
                "\"elevation_m\":8,\"timezone_name\":\"Asia/Shanghai\","
                "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
                "\"model_source\":\"both\",\"forecast_mode\":\"model\","
                "\"satellite_source\":\"off\",\"regions\":[\"custom\"],"
                "\"custom_bounds\":{\"west\":120.0,\"east\":120.5,"
                "\"south\":30.0,\"north\":30.5},"
                "\"custom_timezone_name\":\"Asia/Shanghai\","
                "\"spacing_deg\":0.25,\"ecmwf_source\":\"ecmwf\"}") ||
            !check_preflight(
                engine, "regional",
                "{\"name\":\"Dateline region\",\"lat\":0,\"lon\":179.8,"
                "\"elevation_m\":0,\"timezone_name\":\"Etc/UTC\","
                "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
                "\"model_source\":\"gfs\",\"forecast_mode\":\"model\","
                "\"satellite_source\":\"off\",\"regions\":[\"custom\"],"
                "\"custom_bounds\":{\"west\":170.0,\"east\":-170.0,"
                "\"south\":-1.0,\"north\":1.0},"
                "\"spacing_deg\":0.25,\"ecmwf_source\":\"ecmwf\"}") ||
            !check_preflight(
                engine, "nowcast",
                "{\"name\":\"Hangzhou\",\"lat\":30.2741,\"lon\":120.1551,"
                "\"elevation_m\":8,\"timezone_name\":\"Asia/Shanghai\","
                "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
                "\"forecast_mode\":\"nowcast\",\"model_source\":\"satellite\","
                "\"satellite_source\":\"auto\",\"radius_km\":250,"
                "\"reference_time_utc\":\"2026-11-12T08:00:00Z\"}")) {
            return EXIT_FAILURE;
        }

        std::cout << "Linux FFI smoke: ok\n";
        return EXIT_SUCCESS;
    } catch (const firesky::FfiError& error) {
        std::cerr << "Linux FFI smoke failed (code " << error.code()
                  << "): " << error.what() << '\n';
        return EXIT_FAILURE;
    } catch (const std::exception& error) {
        std::cerr << "Linux FFI smoke failed: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}

