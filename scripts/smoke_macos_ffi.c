#include "firesky_ffi.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void release_string(char **value) {
    if (value != NULL && *value != NULL) {
        firesky_ffi_string_free(*value);
        *value = NULL;
    }
}

static int require_success(const char *label, int32_t code, char **response,
                           char **error, const char *needle) {
    if (code != FIRESKY_FFI_OK || response == NULL || *response == NULL ||
        (needle != NULL && strstr(*response, needle) == NULL)) {
        fprintf(stderr, "%s failed: code=%d response=%s error=%s\n", label,
                code, response != NULL && *response != NULL ? *response : "<null>",
                error != NULL && *error != NULL ? *error : "<null>");
        release_string(response);
        release_string(error);
        return 0;
    }
    release_string(response);
    release_string(error);
    return 1;
}

static int require_error(const char *label, int32_t code, char **response,
                         char **error, int32_t expected_code) {
    if (code != expected_code || response == NULL || *response != NULL ||
        error == NULL || *error == NULL ||
        strstr(*error, "\"contract\":\"firesky-ffi-error-v1\"") == NULL ||
        strstr(*error, "\"ok\":false") == NULL) {
        fprintf(stderr, "%s failed: code=%d response=%s error=%s\n", label,
                code, response != NULL && *response != NULL ? *response : "<null>",
                error != NULL && *error != NULL ? *error : "<null>");
        release_string(response);
        release_string(error);
        return 0;
    }
    release_string(response);
    release_string(error);
    return 1;
}

static int check_preflight(FireskyEngineHandle *engine, const char *kind,
                           const char *payload) {
    char params[4096];
    int written = snprintf(params, sizeof(params),
                           "{\"kind\":\"%s\",\"payload\":%s}", kind, payload);
    if (written < 0 || (size_t)written >= sizeof(params)) {
        fprintf(stderr, "%s preflight payload is too large\n", kind);
        return 0;
    }
    char *response = NULL;
    char *error = NULL;
    int32_t code = firesky_engine_call(engine, "check", params, &response, &error);
    if (code != FIRESKY_FFI_OK || response == NULL ||
        strstr(response, "\"contract\":\"firesky-ffi-call-v1\"") == NULL ||
        strstr(response, "\"method\":\"check\"") == NULL ||
        strstr(response, "\"report\"") == NULL ||
        strstr(response, "\"ready\":false") == NULL) {
        fprintf(stderr, "%s preflight failed: code=%d response=%s error=%s\n",
                kind, code, response != NULL ? response : "<null>",
                error != NULL ? error : "<null>");
        release_string(&response);
        release_string(&error);
        return 0;
    }
    release_string(&response);
    release_string(&error);
    return 1;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s WORKSPACE\n", argv[0]);
        return 2;
    }
    if (firesky_ffi_abi_version() != 1) {
        fprintf(stderr, "unexpected FFI ABI version\n");
        return 3;
    }

    FireskyEngineHandle *engine = NULL;
    char *error = NULL;
    int32_t code = firesky_engine_open(argv[1], &engine, &error);
    if (code != FIRESKY_FFI_OK || engine == NULL) {
        fprintf(stderr, "engine_open failed: code=%d error=%s\n", code,
                error != NULL ? error : "<null>");
        release_string(&error);
        return 4;
    }
    release_string(&error);

    char *response = NULL;
    code = firesky_engine_call(engine, "hello", NULL, &response, &error);
    if (!require_success("hello", code, &response, &error,
                         "\"engine\":\"firesky-rust\"")) {
        firesky_engine_free(engine);
        return 5;
    }

    response = NULL;
    error = NULL;
    code = firesky_engine_call(engine, "inventory", NULL, &response, &error);
    if (!require_success("inventory", code, &response, &error,
                         "\"contract\":\"firesky-ffi-call-v1\"")) {
        firesky_engine_free(engine);
        return 6;
    }

    if (!check_preflight(
            engine, "point",
            "{\"name\":\"Hangzhou\",\"lat\":30.2741,\"lon\":120.1551,"
            "\"elevation_m\":8,\"timezone_name\":\"Asia/Shanghai\","
            "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
            "\"model_source\":\"both\",\"forecast_mode\":\"model\","
            "\"satellite_source\":\"off\",\"ecmwf_source\":\"ecmwf\"}")) {
        firesky_engine_free(engine);
        return 7;
    }
    if (!check_preflight(
            engine, "regional",
            "{\"name\":\"Hangzhou region\",\"lat\":30.2741,\"lon\":120.1551,"
            "\"elevation_m\":8,\"timezone_name\":\"Asia/Shanghai\","
            "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
            "\"model_source\":\"both\",\"forecast_mode\":\"model\","
            "\"satellite_source\":\"off\",\"regions\":[\"custom\"],"
            "\"custom_bounds\":{\"west\":120.0,\"east\":120.5,"
            "\"south\":30.0,\"north\":30.5},"
            "\"custom_timezone_name\":\"Asia/Shanghai\","
            "\"spacing_deg\":0.25,\"ecmwf_source\":\"ecmwf\"}")) {
        firesky_engine_free(engine);
        return 8;
    }
    if (!check_preflight(
            engine, "regional",
            "{\"name\":\"Dateline region\",\"lat\":0,\"lon\":179.8,"
            "\"elevation_m\":0,\"timezone_name\":\"Etc/UTC\","
            "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
            "\"model_source\":\"gfs\",\"forecast_mode\":\"model\","
            "\"satellite_source\":\"off\",\"regions\":[\"custom\"],"
            "\"custom_bounds\":{\"west\":170.0,\"east\":-170.0,"
            "\"south\":-1.0,\"north\":1.0},"
            "\"spacing_deg\":0.25,\"ecmwf_source\":\"ecmwf\"}")) {
        firesky_engine_free(engine);
        return 10;
    }
    if (!check_preflight(
            engine, "nowcast",
            "{\"name\":\"Hangzhou\",\"lat\":30.2741,\"lon\":120.1551,"
            "\"elevation_m\":8,\"timezone_name\":\"Asia/Shanghai\","
            "\"target_date\":\"2026-11-12\",\"phenomenon\":\"dusk\","
            "\"forecast_mode\":\"nowcast\",\"model_source\":\"satellite\","
            "\"satellite_source\":\"auto\",\"radius_km\":250,"
            "\"reference_time_utc\":\"2026-11-12T08:00:00Z\"}")) {
        firesky_engine_free(engine);
        return 11;
    }

    response = NULL;
    error = NULL;
    code = firesky_engine_call(engine, "hello", "[]", &response, &error);
    if (!require_error("invalid params", code, &response, &error,
                       FIRESKY_FFI_INVALID_ARGUMENT)) {
        firesky_engine_free(engine);
        return 12;
    }

    FireskyTaskHandle *task = NULL;
    error = NULL;
    code = firesky_engine_start(engine, "hello", NULL, NULL, NULL, &task, &error);
    if (code != FIRESKY_FFI_OK || task == NULL) {
        fprintf(stderr, "engine_start failed: code=%d error=%s\n", code,
                error != NULL ? error : "<null>");
        release_string(&error);
        firesky_engine_free(engine);
        return 13;
    }
    release_string(&error);

    response = NULL;
    error = NULL;
    code = firesky_task_join(task, &response, &error);
    if (!require_success("async hello", code, &response, &error,
                         "\"method\":\"hello\"")) {
        firesky_task_free(task);
        firesky_engine_free(engine);
        return 14;
    }
    firesky_task_free(task);

    firesky_engine_free(engine);
    puts("macOS FFI smoke: ok");
    return 0;
}
