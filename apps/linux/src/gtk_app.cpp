#include "firesky_ffi_client.hpp"

#include <gtk/gtk.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <functional>
#include <iomanip>
#include <limits>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

struct GtkApp;

struct UiCall {
    GtkApp* app;
    std::function<void()> function;
};

std::string json_escape(const std::string& value) {
    std::ostringstream output;
    for (const unsigned char character : value) {
        switch (character) {
        case '"':
            output << "\\\"";
            break;
        case '\\':
            output << "\\\\";
            break;
        case '\b':
            output << "\\b";
            break;
        case '\f':
            output << "\\f";
            break;
        case '\n':
            output << "\\n";
            break;
        case '\r':
            output << "\\r";
            break;
        case '\t':
            output << "\\t";
            break;
        default:
            if (character < 0x20) {
                output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                       << static_cast<unsigned>(character) << std::dec;
            } else {
                output << character;
            }
            break;
        }
    }
    return output.str();
}

std::optional<std::string> json_string_field(const std::string& json,
                                             const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    std::size_t cursor = 0;
    while ((cursor = json.find(needle, cursor)) != std::string::npos) {
        cursor += needle.size();
        cursor = json.find(':', cursor);
        if (cursor == std::string::npos) {
            return std::nullopt;
        }
        ++cursor;
        while (cursor < json.size() &&
               (json[cursor] == ' ' || json[cursor] == '\t' ||
                json[cursor] == '\n' || json[cursor] == '\r')) {
            ++cursor;
        }
        if (cursor >= json.size() || json[cursor] != '"') {
            continue;
        }
        ++cursor;
        std::string result;
        bool escaped = false;
        for (; cursor < json.size(); ++cursor) {
            const char character = json[cursor];
            if (escaped) {
                switch (character) {
                case '"':
                case '\\':
                case '/':
                    result.push_back(character);
                    break;
                case 'n':
                    result.push_back('\n');
                    break;
                case 'r':
                    result.push_back('\r');
                    break;
                case 't':
                    result.push_back('\t');
                    break;
                default:
                    result.push_back(character);
                    break;
                }
                escaped = false;
            } else if (character == '\\') {
                escaped = true;
            } else if (character == '"') {
                return result;
            } else {
                result.push_back(character);
            }
        }
        return std::nullopt;
    }
    return std::nullopt;
}

bool json_bool_field(const std::string& json, const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    std::size_t cursor = json.find(needle);
    while (cursor != std::string::npos) {
        cursor = json.find(':', cursor + needle.size());
        if (cursor == std::string::npos) {
            return false;
        }
        ++cursor;
        while (cursor < json.size() &&
               (json[cursor] == ' ' || json[cursor] == '\t' ||
                json[cursor] == '\n' || json[cursor] == '\r')) {
            ++cursor;
        }
        if (json.compare(cursor, 4, "true") == 0) {
            return true;
        }
        cursor = json.find(needle, cursor);
    }
    return false;
}

std::optional<double> json_number_field(const std::string& json,
                                        const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    std::size_t cursor = json.find(needle);
    while (cursor != std::string::npos) {
        cursor = json.find(':', cursor + needle.size());
        if (cursor == std::string::npos) {
            return std::nullopt;
        }
        ++cursor;
        while (cursor < json.size() &&
               (json[cursor] == ' ' || json[cursor] == '\t' ||
                json[cursor] == '\n' || json[cursor] == '\r')) {
            ++cursor;
        }
        const char* start = json.c_str() + cursor;
        char* end = nullptr;
        const double value = std::strtod(start, &end);
        if (end != start && std::isfinite(value)) {
            return value;
        }
        cursor = json.find(needle, cursor);
    }
    return std::nullopt;
}

std::optional<std::string> json_value_fragment(const std::string& json,
                                               const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    std::size_t cursor = 0;
    while ((cursor = json.find(needle, cursor)) != std::string::npos) {
        cursor += needle.size();
        cursor = json.find(':', cursor);
        if (cursor == std::string::npos) {
            return std::nullopt;
        }
        ++cursor;
        while (cursor < json.size() &&
               (json[cursor] == ' ' || json[cursor] == '\t' ||
                json[cursor] == '\n' || json[cursor] == '\r')) {
            ++cursor;
        }
        if (cursor >= json.size()) {
            return std::nullopt;
        }

        const char opening = json[cursor];
        if (opening == '"' || opening == '{' || opening == '[') {
            const std::size_t start = cursor;
            bool in_string = opening == '"';
            bool escaped = false;
            std::vector<char> closers;
            if (opening == '{') {
                closers.push_back('}');
            } else if (opening == '[') {
                closers.push_back(']');
            }
            ++cursor;
            for (; cursor < json.size(); ++cursor) {
                const char character = json[cursor];
                if (in_string) {
                    if (escaped) {
                        escaped = false;
                    } else if (character == '\\') {
                        escaped = true;
                    } else if (character == '"') {
                        if (opening == '"') {
                            return json.substr(start, cursor - start + 1);
                        }
                        in_string = false;
                    }
                    continue;
                }
                if (character == '"') {
                    in_string = true;
                } else if (character == '{') {
                    closers.push_back('}');
                } else if (character == '[') {
                    closers.push_back(']');
                } else if (!closers.empty() && character == closers.back()) {
                    closers.pop_back();
                    if (closers.empty()) {
                        return json.substr(start, cursor - start + 1);
                    }
                }
            }
            return std::nullopt;
        }

        const std::size_t start = cursor;
        while (cursor < json.size() && json[cursor] != ',' &&
               json[cursor] != '}' && json[cursor] != ']') {
            ++cursor;
        }
        while (cursor > start &&
               (json[cursor - 1] == ' ' || json[cursor - 1] == '\t' ||
                json[cursor - 1] == '\n' || json[cursor - 1] == '\r')) {
            --cursor;
        }
        return json.substr(start, cursor - start);
    }
    return std::nullopt;
}

std::string trim_copy(const std::string& value) {
    const auto first = value.find_first_not_of(" \t\n\r");
    if (first == std::string::npos) {
        return {};
    }
    const auto last = value.find_last_not_of(" \t\n\r");
    return value.substr(first, last - first + 1);
}

// Splits the raw array value of `key` into balanced element fragments. The
// hand-rolled JSON helpers have no real parser, so element boundaries are
// tracked with string/brace depth, mirroring json_value_fragment.
std::optional<std::vector<std::string>> json_array_items(
    const std::string& json, const std::string& key) {
    const auto value = json_value_fragment(json, key);
    if (!value || value->size() < 2 || value->front() != '[') {
        return std::nullopt;
    }
    std::vector<std::string> items;
    std::size_t depth = 0;
    std::size_t start = std::string::npos;
    bool in_string = false;
    bool escaped = false;
    const std::size_t last = value->size() - 1;
    for (std::size_t cursor = 1; cursor < value->size(); ++cursor) {
        const char character = (*value)[cursor];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (character == '\\') {
                escaped = true;
            } else if (character == '"') {
                in_string = false;
            }
            continue;
        }
        if (character == '"') {
            in_string = true;
            if (start == std::string::npos) {
                start = cursor;
            }
            continue;
        }
        if (character == '{' || character == '[') {
            if (depth == 0 && start == std::string::npos) {
                start = cursor;
            }
            ++depth;
            continue;
        }
        if (character == '}' || character == ']') {
            if (depth > 0) {
                --depth;
            }
            continue;
        }
        if (character == ',' && depth == 0) {
            if (start != std::string::npos) {
                items.push_back(trim_copy((*value).substr(start, cursor - start)));
                start = std::string::npos;
            }
            continue;
        }
        if (start == std::string::npos && character != ' ' && character != '\t' &&
            character != '\n' && character != '\r') {
            start = cursor;
        }
    }
    if (start != std::string::npos && depth == 0 && !in_string) {
        items.push_back(trim_copy((*value).substr(start, last - start)));
    }
    return items;
}

std::optional<std::vector<std::string>> json_array_strings(
    const std::string& json, const std::string& key) {
    const auto items = json_array_items(json, key);
    if (!items) {
        return std::nullopt;
    }
    std::vector<std::string> values;
    for (const auto& item : *items) {
        // Reuse the string-field unescaping by wrapping the literal in a
        // one-key object; the key always precedes the literal.
        const auto value = json_string_field("{\"v\":" + item + "}", "v");
        if (!value) {
            return std::nullopt;
        }
        values.push_back(*value);
    }
    return values;
}

// Validates ISO8601 stamps the way TaskHistory.swift does: both
// "2026-11-12T08:00:00Z" and "2026-11-12T08:00:00.300Z" must parse.
bool valid_iso8601_utc(const std::string& value) {
    const auto digits_at = [&value](std::size_t from, std::size_t count) {
        if (from + count > value.size()) {
            return false;
        }
        for (std::size_t index = 0; index < count; ++index) {
            if (value[from + index] < '0' || value[from + index] > '9') {
                return false;
            }
        }
        return true;
    };
    const auto two_digits = [&value](std::size_t from) {
        return (value[from] - '0') * 10 + (value[from + 1] - '0');
    };
    if (value.size() < 20) {
        return false;
    }
    if (!digits_at(0, 4) || value[4] != '-' || !digits_at(5, 2) ||
        value[7] != '-' || !digits_at(8, 2) ||
        (value[10] != 'T' && value[10] != 't') || !digits_at(11, 2) ||
        value[13] != ':' || !digits_at(14, 2) || value[16] != ':' ||
        !digits_at(17, 2)) {
        return false;
    }
    const int month = two_digits(5);
    const int day = two_digits(8);
    const int hour = two_digits(11);
    const int minute = two_digits(14);
    const int second = two_digits(17);
    if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 ||
        minute > 59 || second > 60) {
        return false;
    }
    std::size_t cursor = 19;
    if (cursor < value.size() && value[cursor] == '.') {
        ++cursor;
        std::size_t fraction = 0;
        while (cursor < value.size() && value[cursor] >= '0' &&
               value[cursor] <= '9') {
            ++cursor;
            ++fraction;
        }
        if (fraction == 0) {
            return false;
        }
    }
    if (cursor >= value.size()) {
        return false;
    }
    const char zone = value[cursor];
    if (zone == 'Z' || zone == 'z') {
        return cursor + 1 == value.size();
    }
    if (zone == '+' || zone == '-') {
        const std::string offset = value.substr(cursor + 1);
        const auto offset_digits = [&offset](std::size_t from, std::size_t count) {
            if (from + count > offset.size()) {
                return false;
            }
            for (std::size_t index = 0; index < count; ++index) {
                if (offset[from + index] < '0' || offset[from + index] > '9') {
                    return false;
                }
            }
            return true;
        };
        if (offset.size() == 5 && offset[2] == ':' && offset_digits(0, 2) &&
            offset_digits(3, 2)) {
            const int offset_hour = (offset[0] - '0') * 10 + (offset[1] - '0');
            const int offset_minute = (offset[3] - '0') * 10 + (offset[4] - '0');
            return offset_hour <= 23 && offset_minute <= 59;
        }
        if (offset.size() == 4 && offset_digits(0, 2) && offset_digits(2, 2)) {
            const int offset_hour = (offset[0] - '0') * 10 + (offset[1] - '0');
            const int offset_minute = (offset[2] - '0') * 10 + (offset[3] - '0');
            return offset_hour <= 23 && offset_minute <= 59;
        }
    }
    return false;
}

bool is_hex_task_id(const std::string& value) {
    if (value.size() != 64) {
        return false;
    }
    for (const char character : value) {
        const bool hex = (character >= '0' && character <= '9') ||
                         (character >= 'a' && character <= 'f') ||
                         (character >= 'A' && character <= 'F');
        if (!hex) {
            return false;
        }
    }
    return true;
}

bool is_known_task_status(const std::string& status) {
    return status == "running" || status == "done" || status == "failed" ||
           status == "cancelled" || status == "interrupted";
}

// Swift's String.prefix(128) counts code points, so cut on UTF-8 boundaries.
std::string utf8_prefix(const std::string& value, std::size_t max_codepoints) {
    std::size_t codepoints = 0;
    std::size_t cursor = 0;
    while (cursor < value.size() && codepoints < max_codepoints) {
        const unsigned char lead = static_cast<unsigned char>(value[cursor]);
        std::size_t width = 1;
        if ((lead & 0xE0U) == 0xC0U) {
            width = 2;
        } else if ((lead & 0xF0U) == 0xE0U) {
            width = 3;
        } else if ((lead & 0xF8U) == 0xF0U) {
            width = 4;
        }
        cursor = std::min(cursor + width, value.size());
        ++codepoints;
    }
    return value.substr(0, cursor);
}

std::string join_strings(const std::vector<std::string>& values,
                         const std::string& separator) {
    std::string result;
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            result += separator;
        }
        result += values[index];
    }
    return result;
}

std::string upper_ascii(const std::string& value) {
    std::string output = value;
    for (char& character : output) {
        if (character >= 'a' && character <= 'z') {
            character = static_cast<char>(character - 'a' + 'A');
        }
    }
    return output;
}

std::string percent_text(double value) {
    std::ostringstream output;
    output << std::lround(value) << "%";
    return output.str();
}

// FFI errors carry the versioned envelope as what(); surface the inner
// message the way macOS friendlyEngineMessage does, falling back to the
// raw envelope.
std::string error_message(const std::string& envelope) {
    if (auto message = json_string_field(envelope, "message");
        message && !message->empty()) {
        return *message;
    }
    return envelope;
}

std::string local_date() {
    const std::time_t now = std::time(nullptr);
    std::tm local{};
    if (localtime_r(&now, &local) == nullptr) {
        return {};
    }
    char buffer[sizeof("0000-00-00")] = {};
    if (std::strftime(buffer, sizeof(buffer), "%Y-%m-%d", &local) == 0) {
        return {};
    }
    return buffer;
}

// Mirrors PreparationActions.swift's DataRow (macOS preflight report rows).
struct PreflightRow {
    std::string source;
    bool ready = false;
    bool old = false;
    bool verified = false;
    bool latest = false;
    long long missing = 0;

    std::string title() const {
        if (source == "ecmwf") {
            return "EC";
        }
        if (source == "hsd") {
            return "卫星帧";
        }
        return upper_ascii(source);
    }

    std::string status_title() const {
        if (!ready) {
            return "需下载";
        }
        if (old) {
            return "可更新";
        }
        return "已下载";
    }

    std::string detail() const {
        if (!ready) {
            return missing > 0 ? "本次预测数据不完整" : "尚未下载本次预测数据";
        }
        if (old) {
            return verified ? "有新发布的数据可更新" : "非最新发布周期";
        }
        return verified ? "发布周期已核验" : "更新待核验";
    }
};

// Mirrors TaskHistory.swift's NativeTaskHistoryItem validation and titles.
struct HistoryItem {
    std::string task_id;
    std::string method;
    std::string status;
    std::string created_at_utc;
    std::string updated_at_utc;
    bool cancel_requested = false;
    long long event_seq = 0;
    bool has_retry = false;
    std::string retry_kind;
    std::string retry_payload;
    bool has_snapshot = false;
    bool has_snapshot_sha256 = false;
    std::string snapshot_contract;
    std::string snapshot_sha256;
    std::string progress = "{}";

    std::string kind_title() const {
        if (has_retry) {
            if (retry_kind == "point") {
                return "单点";
            }
            if (retry_kind == "regional") {
                return "区域";
            }
            if (retry_kind == "nowcast") {
                return "实况";
            }
        }
        return method_title();
    }

    std::string method_title() const {
        if (method == "check") {
            return "数据检查";
        }
        if (method == "prepare") {
            return "数据准备";
        }
        if (method == "forecast") {
            return "预测计算";
        }
        if (method == "publications") {
            return "发布核验";
        }
        if (method == "network_probe") {
            return "下载探测";
        }
        return "任务";
    }

    std::string status_title() const {
        if (status == "running") {
            return cancel_requested ? "取消中" : "运行中";
        }
        if (status == "done") {
            return "已完成";
        }
        if (status == "failed") {
            return "失败";
        }
        if (status == "cancelled") {
            return "已取消";
        }
        if (status == "interrupted") {
            return "已中断";
        }
        return status;
    }

    std::string context_title() const {
        if (!has_retry) {
            return {};
        }
        std::string name;
        if (auto value = json_string_field(retry_payload, "name")) {
            name = *value;
        } else if (auto regions = json_array_strings(retry_payload, "regions")) {
            name = join_strings(*regions, "、");
        }
        std::string date;
        if (auto value = json_string_field(retry_payload, "target_date")) {
            date = *value;
        }
        std::string phenomenon;
        if (auto value = json_string_field(retry_payload, "phenomenon")) {
            if (*value == "dawn") {
                phenomenon = "朝霞";
            } else if (*value == "dusk") {
                phenomenon = "晚霞";
            }
        }
        std::vector<std::string> parts;
        if (!name.empty()) {
            parts.push_back(name);
        }
        if (!date.empty()) {
            parts.push_back(date);
        }
        if (!phenomenon.empty()) {
            parts.push_back(phenomenon);
        }
        return join_strings(parts, " · ");
    }

    bool can_restore_result() const {
        if (status != "done" || !has_snapshot) {
            return false;
        }
        return snapshot_contract == "firesky-result-snapshot-v1" &&
               has_snapshot_sha256;
    }

    std::optional<double> progress_percent() const {
        return json_number_field(progress, "percent");
    }
};

// Mirrors TaskHistory.swift's NativeTaskEvent titles and detail join.
struct HistoryEvent {
    long long seq = 0;
    std::string at_utc;
    std::string event;
    std::string data;

    std::string title() const {
        if (event == "running") {
            return "开始";
        }
        if (event == "progress") {
            return "进度";
        }
        if (event == "cancel_requested") {
            return "请求取消";
        }
        if (event == "done") {
            return "完成";
        }
        if (event == "failed") {
            return "失败";
        }
        if (event == "cancelled") {
            return "已取消";
        }
        if (event == "interrupted") {
            return "已中断";
        }
        return "状态更新";
    }

    std::string detail() const {
        std::vector<std::string> values;
        for (const char* key : {"stage", "source"}) {
            if (auto value = json_string_field(data, key);
                value && !value->empty()) {
                values.push_back(utf8_prefix(*value, 128));
            }
        }
        if (auto percent = json_number_field(data, "percent");
            percent && std::isfinite(*percent)) {
            values.push_back(percent_text(*percent));
        }
        return join_strings(values, " · ");
    }
};

struct GtkApp {
    GtkApplication* application = nullptr;
    GtkWidget* window = nullptr;
    GtkWidget* library_entry = nullptr;
    GtkWidget* workspace_entry = nullptr;
    GtkWidget* latitude_entry = nullptr;
    GtkWidget* longitude_entry = nullptr;
    GtkWidget* west_entry = nullptr;
    GtkWidget* east_entry = nullptr;
    GtkWidget* south_entry = nullptr;
    GtkWidget* north_entry = nullptr;
    GtkWidget* elevation_entry = nullptr;
    GtkWidget* timezone_entry = nullptr;
    GtkWidget* date_entry = nullptr;
    GtkWidget* phenomenon_entry = nullptr;
    GtkWidget* mode_dropdown = nullptr;
    GtkWidget* regional_grid = nullptr;
    GtkWidget* connect_button = nullptr;
    GtkWidget* forecast_button = nullptr;
    GtkWidget* cancel_button = nullptr;
    GtkWidget* status_label = nullptr;
    GtkWidget* progress_bar = nullptr;
    GtkWidget* output_view = nullptr;
    GtkWidget* ranking_view = nullptr;
    GtkWidget* retention_gfs_entry = nullptr;
    GtkWidget* retention_ecmwf_entry = nullptr;
    GtkWidget* retention_cams_entry = nullptr;
    GtkWidget* retention_satellite_entry = nullptr;
    GtkWidget* retention_report_entry = nullptr;
    GtkWidget* retention_map_entry = nullptr;
    GtkWidget* retention_reload_button = nullptr;
    GtkWidget* retention_save_button = nullptr;
    GtkWidget* profile_model_dropdown = nullptr;
    GtkWidget* profile_result_id_entry = nullptr;
    GtkWidget* profile_bytes_entry = nullptr;
    GtkWidget* profile_sha256_entry = nullptr;
    GtkWidget* profile_lat_entry = nullptr;
    GtkWidget* profile_lon_entry = nullptr;
    GtkWidget* profile_valid_time_entry = nullptr;
    GtkWidget* profile_query_button = nullptr;
    GtkWidget* result_model_dropdown = nullptr;
    std::unique_ptr<firesky::Engine> engine;
    std::atomic<bool> closing = false;
    bool busy = false;
    bool result_model_updating = false;
    std::mutex worker_mutex;
    std::vector<std::thread> workers;
    std::mutex task_mutex;
    std::shared_ptr<firesky::Task> active_task;

    // Unified preflight sheet: check -> prepare -> forecast(confirmed=true).
    // Cancelling the sheet dismisses the request token (workflow contract).
    GtkWidget* preflight_dialog = nullptr;
    GtkWidget* preflight_name_label = nullptr;
    GtkWidget* preflight_refresh_button = nullptr;
    GtkWidget* preflight_rows_list = nullptr;
    GtkWidget* preflight_progress = nullptr;
    GtkWidget* preflight_state_label = nullptr;
    GtkWidget* preflight_prepare_button = nullptr;
    GtkWidget* preflight_confirm_button = nullptr;
    std::string request_kind;
    std::string request_payload;
    std::string preflight_session;
    std::string preflight_stage;
    std::string preflight_error;
    std::string preflight_report_status;
    bool preflight_ready = false;
    bool preflight_working = false;
    bool preflight_publications_pending = false;
    bool preflight_open = false;
    long long preflight_revision = 0;
    GtkWidget* cycle_age_dialog = nullptr;
    std::vector<PreflightRow> preflight_rows;

    // Task history view (list + events + restore + retry re-check + forget).
    GtkWidget* history_expander = nullptr;
    GtkWidget* history_subtitle_label = nullptr;
    GtkWidget* history_refresh_button = nullptr;
    GtkWidget* history_error_label = nullptr;
    GtkWidget* history_loading_label = nullptr;
    GtkWidget* history_empty_box = nullptr;
    GtkWidget* history_empty_hint_label = nullptr;
    GtkWidget* history_scroller = nullptr;
    GtkWidget* history_list = nullptr;
    GtkWidget* history_issues_label = nullptr;
    GtkWidget* history_events_box = nullptr;
    GtkWidget* history_events_title_label = nullptr;
    GtkWidget* history_events_truncated_label = nullptr;
    GtkWidget* history_events_error_label = nullptr;
    GtkWidget* history_events_loading_label = nullptr;
    GtkWidget* history_events_scroller = nullptr;
    GtkWidget* history_events_list = nullptr;
    GtkWidget* forget_confirm_dialog = nullptr;
    std::vector<HistoryItem> history_items;
    std::vector<HistoryEvent> history_events;
    long long history_total = 0;
    long long history_issues = 0;
    bool history_loaded = false;
    bool history_loading = false;
    bool history_events_loading = false;
    bool history_events_truncated = false;
    std::string history_events_error;
    std::string history_error;
    std::string history_selected_id;
    std::string forgetting_task_id;
    std::string restoring_task_id;

    // Last published result and its selected model (gfs/ec).
    std::string last_result_json;
    std::string last_result_kind;
};

void history_render(GtkApp* app);

std::string editable_text(GtkWidget* widget) {
    return gtk_editable_get_text(GTK_EDITABLE(widget));
}

void set_status(GtkApp* app, const std::string& value) {
    gtk_label_set_text(GTK_LABEL(app->status_label), value.c_str());
}

void set_output(GtkApp* app, const std::string& value) {
    GtkTextBuffer* buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(app->output_view));
    gtk_text_buffer_set_text(buffer, value.c_str(), -1);
}

void set_busy(GtkApp* app, bool busy) {
    app->busy = busy;
    gtk_widget_set_sensitive(app->connect_button, !busy);
    gtk_widget_set_sensitive(app->mode_dropdown, !busy);
    gtk_widget_set_sensitive(app->forecast_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(
        app->retention_reload_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(
        app->retention_save_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(
        app->profile_query_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(
        app->history_refresh_button,
        !busy && !app->history_loading && app->engine != nullptr);
    gtk_widget_set_sensitive(app->cancel_button, busy);
    gtk_widget_set_visible(app->progress_bar, busy);
    if (!busy) {
        gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(app->progress_bar), 0.0);
    }
    gtk_progress_bar_set_pulse_step(GTK_PROGRESS_BAR(app->progress_bar), 0.05);
    history_render(app);
}

void post_to_ui(GtkApp* app, std::function<void()> function) {
    if (app->closing.load()) {
        return;
    }
    auto* call = new UiCall{app, std::move(function)};
    g_main_context_invoke(
        nullptr,
        [](gpointer data) -> gboolean {
            std::unique_ptr<UiCall> call(static_cast<UiCall*>(data));
            if (!call->app->closing.load()) {
                call->function();
            }
            return G_SOURCE_REMOVE;
        },
        call);
}

void clear_list(GtkWidget* list) {
    GtkWidget* child = gtk_widget_get_first_child(GTK_WIDGET(list));
    while (child != nullptr) {
        GtkWidget* next = gtk_widget_get_next_sibling(child);
        gtk_list_box_remove(GTK_LIST_BOX(list), child);
        child = next;
    }
}

std::optional<double> parse_number(GtkWidget* widget) {
    const std::string text = editable_text(widget);
    if (text.empty()) {
        return std::nullopt;
    }
    try {
        std::size_t consumed = 0;
        const double value = std::stod(text, &consumed);
        if (consumed != text.size() || !std::isfinite(value)) {
            return std::nullopt;
        }
        return value;
    } catch (...) {
        return std::nullopt;
    }
}

void set_entry_number(GtkWidget* widget, double value) {
    std::ostringstream output;
    if (std::floor(value) == value && std::abs(value) < 1e15) {
        output << static_cast<long long>(value);
    } else {
        output << std::setprecision(12) << value;
    }
    gtk_editable_set_text(GTK_EDITABLE(widget), output.str().c_str());
}

std::string selected_mode(GtkApp* app) {
    // GTK 4.18 changed this return type from GObject* to gpointer; keeping a
    // gpointer binding compiles on both 4.14 (ubuntu-24.04 CI) and 4.18+.
    gpointer selected = gtk_drop_down_get_selected_item(GTK_DROP_DOWN(app->mode_dropdown));
    if (selected == nullptr) {
        return "point";
    }
    return gtk_string_object_get_string(GTK_STRING_OBJECT(selected));
}

// Switches the single mode selector to the restored task's page kind
// (macOS Store sets `page = retry.kind`, defaulting to point).
void select_mode(GtkApp* app, const std::string& kind) {
    if (app->mode_dropdown == nullptr) {
        return;
    }
    const guint target = kind == "regional" ? 1U : (kind == "nowcast" ? 2U : 0U);
    if (gtk_drop_down_get_selected(GTK_DROP_DOWN(app->mode_dropdown)) != target) {
        gtk_drop_down_set_selected(GTK_DROP_DOWN(app->mode_dropdown), target);
    }
}

std::string selected_result_model(GtkApp* app) {
    gpointer selected =
        gtk_drop_down_get_selected_item(GTK_DROP_DOWN(app->result_model_dropdown));
    if (selected == nullptr) {
        return "gfs";
    }
    return gtk_string_object_get_string(GTK_STRING_OBJECT(selected));
}

// Forecast completion selects the model like macOS Store.runForecast:
// model_results.gfs missing -> "ec", otherwise "gfs".
void update_result_model(GtkApp* app, const std::string& response) {
    if (app->result_model_dropdown == nullptr) {
        return;
    }
    const auto models = json_value_fragment(response, "model_results");
    if (!models || models->empty() || models->front() != '{') {
        gtk_widget_set_sensitive(app->result_model_dropdown, FALSE);
        return;
    }
    gtk_widget_set_sensitive(app->result_model_dropdown, TRUE);
    app->result_model_updating = true;
    gtk_drop_down_set_selected(
        GTK_DROP_DOWN(app->result_model_dropdown),
        json_value_fragment(*models, "gfs") ? 0U : 1U);
    app->result_model_updating = false;
}

std::optional<std::string> build_payload(GtkApp* app, const std::string& mode,
                                         std::string& error) {
    const auto latitude = parse_number(app->latitude_entry);
    const auto longitude = parse_number(app->longitude_entry);
    const auto elevation = parse_number(app->elevation_entry);
    if (!latitude || *latitude < -90.0 || *latitude > 90.0) {
        error = "Latitude must be between -90 and 90.";
        return std::nullopt;
    }
    if (!longitude || *longitude < -180.0 || *longitude > 180.0) {
        error = "Longitude must be between -180 and 180.";
        return std::nullopt;
    }
    if (!elevation) {
        error = "Elevation must be a number.";
        return std::nullopt;
    }
    const std::string date = editable_text(app->date_entry);
    const std::string timezone = editable_text(app->timezone_entry);
    const std::string phenomenon = editable_text(app->phenomenon_entry);
    if (date.empty() || timezone.empty() || phenomenon.empty()) {
        error = "Date, timezone and phenomenon are required.";
        return std::nullopt;
    }

    std::ostringstream payload;
    payload << std::setprecision(12)
            << "{\"name\":\"" << (mode == "regional" ? "Linux region" : "Linux point")
            << "\",\"lat\":" << *latitude
            << ",\"lon\":" << *longitude
            << ",\"elevation_m\":" << *elevation
            << ",\"timezone_name\":\"" << json_escape(timezone)
            << "\",\"target_date\":\"" << json_escape(date)
            << "\",\"phenomenon\":\"" << json_escape(phenomenon)
            << "\",\"model_source\":\""
            << (mode == "nowcast" ? "satellite" : "both")
            << "\",\"workers\":1"
            << ",\"forecast_mode\":\""
            << (mode == "nowcast" ? "nowcast" : "model")
            << "\",\"satellite_source\":\""
            << (mode == "nowcast" ? "auto" : "off")
            << "\",\"ecmwf_source\":\"ecmwf\"";
    if (mode == "regional") {
        const auto west = parse_number(app->west_entry);
        const auto east = parse_number(app->east_entry);
        const auto south = parse_number(app->south_entry);
        const auto north = parse_number(app->north_entry);
        if (!west || !east || !south || !north ||
            *west < -180.0 || *west > 180.0 ||
            *east < -180.0 || *east > 180.0 ||
            *south < -90.0 || *south > 90.0 ||
            *north < -90.0 || *north > 90.0 ||
            *west == *east || (*west == 180.0 && *east == -180.0) ||
            *north <= *south) {
            error = "Regional bounds must have nonzero longitude span and south < north.";
            return std::nullopt;
        }
        payload << ",\"regions\":[\"custom\"]"
                << ",\"custom_bounds\":{\"west\":" << *west
                << ",\"east\":" << *east
                << ",\"south\":" << *south
                << ",\"north\":" << *north << "}"
                << ",\"custom_timezone_name\":\"" << json_escape(timezone)
                << "\",\"spacing_deg\":0.25";
    }
    payload << "}";
    return payload.str();
}

std::optional<std::string> retention_value(GtkWidget* widget, bool integer,
                                           const char* label, std::string& error) {
    const std::string text = trim_copy(editable_text(widget));
    if (text.empty()) {
        return std::string("null");
    }
    try {
        std::size_t consumed = 0;
        if (integer) {
            const long long value = std::stoll(text, &consumed);
            if (consumed != text.size() || value < 1 || value > 10000) {
                error = std::string(label) + " must be an integer in 1..10000 or blank.";
                return std::nullopt;
            }
            return std::to_string(value);
        }
        const double value = std::stod(text, &consumed);
        if (consumed != text.size() || !std::isfinite(value) ||
            value < 0.0 || value > 3650.0) {
            error = std::string(label) + " must be within 0..3650 or blank.";
            return std::nullopt;
        }
        std::ostringstream output;
        output << std::setprecision(12) << value;
        return output.str();
    } catch (...) {
        error = std::string(label) + " is invalid.";
        return std::nullopt;
    }
}

std::optional<std::string> build_retention_payload(GtkApp* app, std::string& error) {
    const auto gfs = retention_value(app->retention_gfs_entry, true, "GFS cycles", error);
    const auto ecmwf =
        retention_value(app->retention_ecmwf_entry, true, "ECMWF cycles", error);
    const auto cams =
        retention_value(app->retention_cams_entry, true, "CAMS cycles", error);
    const auto satellite =
        retention_value(app->retention_satellite_entry, false, "Satellite days", error);
    const auto report =
        retention_value(app->retention_report_entry, false, "Report days", error);
    const auto map = retention_value(app->retention_map_entry, false, "Map days", error);
    if (!gfs || !ecmwf || !cams || !satellite || !report || !map) {
        return std::nullopt;
    }
    return "{\"settings\":{\"gfs_cycles\":" + *gfs +
           ",\"ecmwf_cycles\":" + *ecmwf +
           ",\"cams_cycles\":" + *cams +
           ",\"satellite_days\":" + *satellite +
           ",\"report_days\":" + *report +
           ",\"map_days\":" + *map + "}}";
}

void set_retention_number(GtkWidget* widget, const std::optional<double>& value) {
    if (!value) {
        gtk_editable_set_text(GTK_EDITABLE(widget), "");
        return;
    }
    std::ostringstream output;
    if (std::floor(*value) == *value) {
        output << static_cast<long long>(*value);
    } else {
        output << std::setprecision(12) << *value;
    }
    gtk_editable_set_text(GTK_EDITABLE(widget), output.str().c_str());
}

std::string selected_profile_model(GtkApp* app) {
    gpointer selected =
        gtk_drop_down_get_selected_item(GTK_DROP_DOWN(app->profile_model_dropdown));
    if (selected == nullptr) {
        return "gfs";
    }
    return gtk_string_object_get_string(GTK_STRING_OBJECT(selected));
}

std::optional<std::string> build_profile_payload(GtkApp* app, std::string& error) {
    const std::string result_id = trim_copy(editable_text(app->profile_result_id_entry));
    const std::string source_sha =
        trim_copy(editable_text(app->profile_sha256_entry));
    const std::string valid_time =
        trim_copy(editable_text(app->profile_valid_time_entry));
    if (result_id.empty() || source_sha.empty() || valid_time.empty()) {
        error = "Profile result ID, source SHA-256 and valid UTC time are required.";
        return std::nullopt;
    }
    const std::string bytes_text = trim_copy(editable_text(app->profile_bytes_entry));
    std::optional<std::string> bytes;
    if (!bytes_text.empty()) {
        try {
            std::size_t consumed = 0;
            const auto value = std::stoull(bytes_text, &consumed);
            if (consumed != bytes_text.size() ||
                value > std::numeric_limits<std::uint64_t>::max()) {
                error = "Profile source bytes must be a nonnegative integer.";
                return std::nullopt;
            }
            bytes = std::to_string(value);
        } catch (...) {
            error = "Profile source bytes must be a nonnegative integer.";
            return std::nullopt;
        }
    }
    const auto latitude = parse_number(app->profile_lat_entry);
    const auto longitude = parse_number(app->profile_lon_entry);
    if (!bytes) {
        error = "Profile source bytes must be a nonnegative integer.";
        return std::nullopt;
    }
    if (!latitude || *latitude < -90.0 || *latitude > 90.0) {
        error = "Profile latitude must be between -90 and 90.";
        return std::nullopt;
    }
    if (!longitude || *longitude < -180.0 || *longitude > 180.0) {
        error = "Profile longitude must be between -180 and 180.";
        return std::nullopt;
    }
    std::ostringstream payload;
    payload << std::setprecision(12)
            << "{\"result_id\":\"" << json_escape(result_id)
            << "\",\"source_identity\":{\"bytes\":" << *bytes
            << ",\"sha256\":\"" << json_escape(source_sha)
            << "\"},\"model_source\":\"" << selected_profile_model(app)
            << "\",\"lat\":" << *latitude << ",\"lon\":" << *longitude
            << ",\"valid_time_utc\":\"" << json_escape(valid_time) << "\"}";
    return payload.str();
}

void show_city_ranking(GtkApp* app, const std::string& response) {
    const auto ranking = json_value_fragment(response, "city_ranking");
    std::ostringstream summary;
    summary << (ranking ? *ranking
                        : "No city_ranking contract was returned for this result.");
    if (app->ranking_view != nullptr) {
        GtkTextBuffer* buffer =
            gtk_text_view_get_buffer(GTK_TEXT_VIEW(app->ranking_view));
        gtk_text_buffer_set_text(buffer, summary.str().c_str(), -1);
    }
}

void clear_active_task(GtkApp* app, const std::shared_ptr<firesky::Task>& task) {
    std::lock_guard lock(app->task_mutex);
    if (app->active_task == task) {
        app->active_task.reset();
    }
}

using RequestFinished = std::function<void(const std::string&, const std::string&)>;
using ProgressHandler = std::function<void(const std::string&)>;

ProgressHandler default_progress_handler(GtkApp* app) {
    return [app](const std::string& event) {
        const std::string message =
            json_string_field(event, "message").value_or(event);
        const double percent = std::clamp(
            json_number_field(event, "percent").value_or(0.0), 0.0, 100.0);
        post_to_ui(app, [app, message, percent] {
            set_status(app, message);
            gtk_progress_bar_set_fraction(
                GTK_PROGRESS_BAR(app->progress_bar), percent / 100.0);
        });
    };
}

void launch_request(GtkApp* app, const std::string& method,
                    const std::string& params, RequestFinished finished,
                    ProgressHandler progress = {}) {
    if (app->engine == nullptr) {
        set_status(app, "Connect the Rust engine first.");
        return;
    }
    try {
        ProgressHandler handler =
            progress ? std::move(progress) : default_progress_handler(app);
        auto task = std::make_shared<firesky::Task>(app->engine->start(
            method, params, std::move(handler)));
        {
            std::lock_guard lock(app->task_mutex);
            app->active_task = task;
        }
        set_busy(app, true);
        std::lock_guard lock(app->worker_mutex);
        app->workers.emplace_back([app, task, finished = std::move(finished)]() mutable {
            std::string response;
            std::string error;
            try {
                response = task->join();
            } catch (const firesky::FfiError& failure) {
                error = failure.what();
            } catch (const std::exception& failure) {
                error = failure.what();
            }
            clear_active_task(app, task);
            post_to_ui(app, [app, response = std::move(response),
                             error = std::move(error), finished = std::move(finished)]() mutable {
                finished(response, error);
            });
        });
    } catch (const firesky::FfiError& error) {
        set_status(app, error.what());
        set_busy(app, false);
        // Route synchronous start failures through the same failure path so
        // per-feature in-flight flags (history_loading, restoring_task_id…)
        // are always released by the callback. `finished` may already have
        // been moved into the worker lambda when thread creation throws.
        if (finished) {
            finished(std::string(), std::string(error.what()));
        }
    } catch (const std::exception& error) {
        set_status(app, error.what());
        set_busy(app, false);
        if (finished) {
            finished(std::string(), std::string(error.what()));
        }
    }
}

bool cancel_active_task(GtkApp* app) {
    std::shared_ptr<firesky::Task> task;
    {
        std::lock_guard lock(app->task_mutex);
        task = app->active_task;
    }
    if (!task) {
        return false;
    }
    try {
        task->cancel();
    } catch (const firesky::FfiError& error) {
        set_status(app, error.what());
    } catch (const std::exception& error) {
        set_status(app, error.what());
    }
    return true;
}

// ---------------------------------------------------------------------------
// Unified preflight sheet (contract 1): one entry runs
// check -> prepare -> forecast(confirmed=true) inside a modal dialog.
// ---------------------------------------------------------------------------

void preflight_render_state(GtkApp* app);

void dismiss_response_token(GtkApp* app, const std::string& response) {
    const std::string stale = json_string_field(response, "session").value_or("");
    if (stale.empty() || app->engine == nullptr) {
        return;
    }
    try {
        (void)app->engine->call("dismiss", "{\"session\":\"" + json_escape(stale) + "\"}");
    } catch (...) {
    }
}

void dismiss_preflight_token(GtkApp* app) {
    if (app->preflight_session.empty() || app->engine == nullptr) {
        return;
    }
    try {
        (void)app->engine->call(
            "dismiss", "{\"session\":\"" + json_escape(app->preflight_session) + "\"}");
    } catch (...) {
    }
}

std::string preflight_headline(GtkApp* app) {
    return json_string_field(app->request_payload, "name").value_or("本次预测");
}

std::string preflight_summary_text(GtkApp* app) {
    const bool is_nowcast = app->request_kind == "nowcast";
    if (!app->preflight_ready) {
        std::vector<std::string> missing;
        for (const auto& row : app->preflight_rows) {
            if (!row.ready) {
                missing.push_back(row.title());
            }
        }
        if (!missing.empty()) {
            return "需下载 " + join_strings(missing, "、");
        }
        return app->preflight_report_status.empty() ? std::string("正在检查本地数据")
                                                    : app->preflight_report_status;
    }
    std::vector<std::string> updates;
    for (const auto& row : app->preflight_rows) {
        if (row.old) {
            updates.push_back(row.title());
        }
    }
    return updates.empty()
               ? (is_nowcast ? "数据已齐，可开始分析" : "数据已齐，可开始预测")
               : "数据已齐，可更新 " + join_strings(updates, "、");
}

void preflight_render_state(GtkApp* app) {
    if (app->preflight_state_label == nullptr) {
        return;
    }
    std::string text;
    bool failure = false;
    if (app->preflight_working) {
        text = app->preflight_stage.empty() ? std::string("正在检查本地数据")
                                            : app->preflight_stage;
    } else if (!app->preflight_error.empty()) {
        text = app->preflight_error;
        failure = true;
    } else {
        text = preflight_summary_text(app);
        if (app->preflight_publications_pending) {
            text += "（发布核验中）";
        }
    }
    gtk_label_set_text(GTK_LABEL(app->preflight_state_label), text.c_str());
    if (failure) {
        gtk_widget_add_css_class(app->preflight_state_label, "error");
    } else {
        gtk_widget_remove_css_class(app->preflight_state_label, "error");
    }
}

GtkWidget* build_preflight_row(const PreflightRow& row) {
    GtkWidget* list_row = gtk_list_box_row_new();
    GtkWidget* content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 12);
    gtk_widget_set_margin_top(content, 10);
    gtk_widget_set_margin_bottom(content, 10);
    GtkWidget* text_column = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    gtk_widget_set_hexpand(text_column, TRUE);
    GtkWidget* title = gtk_label_new(row.title().c_str());
    gtk_label_set_xalign(GTK_LABEL(title), 0.0F);
    gtk_box_append(GTK_BOX(text_column), title);
    GtkWidget* detail = gtk_label_new(row.detail().c_str());
    gtk_label_set_xalign(GTK_LABEL(detail), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(detail), TRUE);
    gtk_widget_add_css_class(detail, "dim-label");
    gtk_box_append(GTK_BOX(text_column), detail);
    gtk_box_append(GTK_BOX(content), text_column);
    GtkWidget* badge = gtk_label_new(row.status_title().c_str());
    gtk_label_set_xalign(GTK_LABEL(badge), 1.0F);
    if (row.ready && !row.old) {
        gtk_widget_add_css_class(badge, "success");
    }
    gtk_box_append(GTK_BOX(content), badge);
    gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(list_row), content);
    return list_row;
}

void preflight_render_rows(GtkApp* app) {
    if (app->preflight_rows_list == nullptr) {
        return;
    }
    clear_list(app->preflight_rows_list);
    for (const auto& row : app->preflight_rows) {
        gtk_list_box_append(GTK_LIST_BOX(app->preflight_rows_list),
                            build_preflight_row(row));
    }
}

// Mirrors PreparationActions: primary is download while !ready, forecast only
// once a session exists and the report is ready.
void preflight_update_buttons(GtkApp* app) {
    if (app->preflight_prepare_button == nullptr) {
        return;
    }
    const bool has_session = !app->preflight_session.empty();
    const bool working = app->preflight_working;
    gtk_widget_set_sensitive(app->preflight_refresh_button, !working);
    gtk_button_set_label(GTK_BUTTON(app->preflight_prepare_button),
                         app->preflight_ready ? "更新数据" : "下载缺失数据");
    gtk_widget_set_sensitive(app->preflight_prepare_button, has_session && !working);
    gtk_button_set_label(GTK_BUTTON(app->preflight_confirm_button),
                         app->request_kind == "nowcast" ? "确定分析" : "确定预测");
    gtk_widget_set_sensitive(app->preflight_confirm_button,
                             has_session && app->preflight_ready && !working);
    gtk_widget_set_tooltip_text(
        app->preflight_confirm_button,
        app->preflight_ready ? nullptr : "需先补齐本次数据");
    gtk_widget_set_visible(app->preflight_progress, working);
}

void preflight_fail(GtkApp* app, const std::string& failure) {
    app->preflight_working = false;
    app->preflight_publications_pending = false;
    app->preflight_error = error_message(failure);
    set_status(app, "操作未完成");
    set_busy(app, false);
    preflight_render_state(app);
    preflight_update_buttons(app);
}

ProgressHandler make_preflight_progress(GtkApp* app, bool checking) {
    return [app, checking](const std::string& event) {
        const double percent = std::clamp(
            json_number_field(event, "percent").value_or(0.0), 0.0, 100.0);
        std::string message = json_string_field(event, "message").value_or("");
        std::string source = json_string_field(event, "source").value_or("");
        post_to_ui(app, [app, percent, message = std::move(message),
                         source = std::move(source), checking] {
            gtk_progress_bar_set_fraction(
                GTK_PROGRESS_BAR(app->progress_bar), percent / 100.0);
            if (app->preflight_progress != nullptr) {
                gtk_progress_bar_set_fraction(
                    GTK_PROGRESS_BAR(app->preflight_progress), percent / 100.0);
            }
            if (!app->preflight_working || !app->preflight_open) {
                return;
            }
            std::string status;
            if (!message.empty()) {
                status = message;
            } else if (checking) {
                status = source.empty() ? std::string("正在准备数据")
                                        : "正在准备 " + source + " 数据";
            } else {
                status = source.empty() ? std::string("正在计算")
                                        : "正在计算 " + source;
            }
            app->preflight_stage = status;
            set_status(app, status);
            preflight_render_state(app);
        });
    };
}

// Applies a check/publications/prepare report (macOS Store.applyReport).
void preflight_apply_report(GtkApp* app, const std::string& response) {
    const auto report = json_value_fragment(response, "report");
    if (!report || report->empty() || report->front() != '{') {
        return;
    }
    app->preflight_ready = json_bool_field(*report, "ready");
    std::vector<PreflightRow> rows;
    if (const auto datasets = json_array_items(*report, "datasets")) {
        for (const auto& raw : *datasets) {
            PreflightRow row;
            row.source = json_string_field(raw, "source").value_or("");
            row.ready = json_bool_field(raw, "ready");
            row.verified = json_bool_field(raw, "verified");
            row.latest = json_bool_field(raw, "is_latest");
            row.old = !row.latest;
            row.missing = static_cast<long long>(
                json_number_field(raw, "missing_count").value_or(0.0));
            rows.push_back(std::move(row));
        }
    }
    app->preflight_rows = std::move(rows);
    std::vector<std::string> missing_titles;
    for (const auto& row : app->preflight_rows) {
        if (!row.ready) {
            missing_titles.push_back(row.title());
        }
    }
    app->preflight_report_status =
        app->preflight_ready
            ? std::string("本次数据已下载")
            : "缺少 " + join_strings(missing_titles, "、");
    set_status(app, app->preflight_report_status);
    preflight_render_rows(app);
    preflight_render_state(app);
}

// Publications verify upstream asynchronously without blocking local
// readiness (workflow contract); an outage must not invalidate the report.
void preflight_start_publications(GtkApp* app, long long revision) {
    if (app->preflight_session.empty() || !app->preflight_open) {
        return;
    }
    app->preflight_publications_pending = true;
    preflight_render_state(app);
    launch_request(
        app, "publications",
        "{\"session\":\"" + json_escape(app->preflight_session) + "\"}",
        [app, revision](const std::string& response, const std::string& failure) {
            if (revision != app->preflight_revision || !app->preflight_open) {
                return;
            }
            app->preflight_publications_pending = false;
            if (failure.empty()) {
                preflight_apply_report(app, response);
            } else {
                // 本地可用性不依赖发布核验可用（macOS 同语义）。
            }
            set_busy(app, false);
            preflight_render_state(app);
            preflight_update_buttons(app);
        });
}

void preflight_start_check(GtkApp* app) {
    if (app->preflight_working || !app->preflight_open) {
        return;
    }
    const long long revision = ++app->preflight_revision;
    dismiss_preflight_token(app);
    app->preflight_session.clear();
    app->preflight_ready = false;
    app->preflight_rows.clear();
    app->preflight_working = true;
    app->preflight_publications_pending = false;
    app->preflight_error.clear();
    app->preflight_stage = "正在检查本地数据";
    gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(app->preflight_progress), 0.0);
    preflight_render_rows(app);
    preflight_render_state(app);
    preflight_update_buttons(app);
    launch_request(
        app, "check",
        "{\"kind\":\"" + app->request_kind + "\",\"payload\":" + app->request_payload + "}",
        [app, revision](const std::string& response, const std::string& failure) {
            if (revision != app->preflight_revision || !app->preflight_open) {
                dismiss_response_token(app, response);
                return;
            }
            if (!failure.empty()) {
                preflight_fail(app, failure);
                return;
            }
            app->preflight_session =
                json_string_field(response, "session").value_or("");
            app->preflight_working = false;
            preflight_apply_report(app, response);
            preflight_start_publications(app, revision);
        },
        make_preflight_progress(app, true));
}

void preflight_run_prepare(GtkApp* app) {
    if (app->preflight_session.empty() || app->preflight_working) {
        return;
    }
    const long long revision = ++app->preflight_revision;
    app->preflight_ready = false;
    app->preflight_working = true;
    app->preflight_publications_pending = false;
    app->preflight_error.clear();
    app->preflight_stage = "正在准备数据";
    gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(app->preflight_progress), 0.0);
    preflight_render_state(app);
    preflight_update_buttons(app);
    launch_request(
        app, "prepare",
        "{\"session\":\"" + json_escape(app->preflight_session) + "\"}",
        [app, revision](const std::string& response, const std::string& failure) {
            if (revision != app->preflight_revision || !app->preflight_open) {
                return;
            }
            app->preflight_working = false;
            if (!failure.empty()) {
                preflight_fail(app, failure);
                return;
            }
            preflight_apply_report(app, response);
            set_busy(app, false);
            preflight_update_buttons(app);
            // Deliberately stay in the sheet until the user confirms.
        },
        make_preflight_progress(app, true));
}

void preflight_run_forecast(GtkApp* app, bool cycle_age_confirmed) {
    if (app->preflight_session.empty() || !app->preflight_ready ||
        app->preflight_working) {
        return;
    }
    const std::string session = app->preflight_session;
    const std::string kind = app->request_kind;
    // Detach the sheet without dismissing the token (macOS sets
    // showPreflight = false before running the forecast).
    app->preflight_open = false;
    ++app->preflight_revision;
    app->preflight_working = false;
    app->preflight_publications_pending = false;
    app->preflight_ready = false;
    app->preflight_session.clear();
    if (app->preflight_dialog != nullptr) {
        GtkWidget* dialog = app->preflight_dialog;
        app->preflight_dialog = nullptr;
        gtk_window_destroy(GTK_WINDOW(dialog));
    }
    if (app->cycle_age_dialog != nullptr) {
        GtkWidget* dialog = app->cycle_age_dialog;
        app->cycle_age_dialog = nullptr;
        gtk_window_destroy(GTK_WINDOW(dialog));
    }
    set_status(app, "正在预测");
    set_busy(app, true);
    std::string params =
        "{\"session\":\"" + json_escape(session) + "\",\"confirmed\":true";
    if (cycle_age_confirmed) {
        params += ",\"cycle_age_confirmed\":true";
    }
    params += "}";
    launch_request(
        app, "forecast", params,
        [app, kind](const std::string& response, const std::string& failure) {
            if (!failure.empty()) {
                set_status(app, error_message(failure));
                set_busy(app, false);
                return;
            }
            set_output(app, response);
            show_city_ranking(app, response);
            app->last_result_json = response;
            app->last_result_kind = kind;
            update_result_model(app, response);
            set_status(app, "预测完成");
            set_busy(app, false);
        });
}

// Mirrors Store.needsCycleAgeConfirmation: a ready dataset that is unverified
// or not the latest release needs an explicit user acknowledgment (B6).
void on_cycle_age_response(GtkDialog*, gint response_id, gpointer data);
void on_cycle_age_destroy(GtkWidget*, gpointer data);

bool preflight_needs_cycle_age_confirmation(GtkApp* app) {
    if (!app->preflight_ready) {
        return false;
    }
    for (const auto& row : app->preflight_rows) {
        if (row.ready && (!row.verified || !row.latest)) {
            return true;
        }
    }
    return false;
}

void preflight_show_cycle_age_confirm(GtkApp* app) {
    if (app->cycle_age_dialog != nullptr) {
        return;
    }
    std::vector<std::string> titles;
    for (const auto& row : app->preflight_rows) {
        if (row.ready && (!row.verified || !row.latest)) {
            titles.push_back(row.title());
        }
    }
    const std::string summary =
        titles.empty() ? std::string("部分数据") : join_strings(titles, "、");
    GtkWidget* dialog = GTK_WIDGET(gtk_message_dialog_new(
        GTK_WINDOW(app->preflight_dialog != nullptr ? app->preflight_dialog
                                                    : app->window),
        static_cast<GtkDialogFlags>(GTK_DIALOG_MODAL | GTK_DIALOG_DESTROY_WITH_PARENT),
        GTK_MESSAGE_QUESTION, GTK_BUTTONS_NONE, "%s", "数据周期确认"));
    gtk_message_dialog_format_secondary_text(
        GTK_MESSAGE_DIALOG(dialog), "%s",
        (summary +
         " 的发布周期未核验或不是最新发布。可以继续预测（结果可能略有偏差），"
         "也可以返回并点击「更新数据」获取最新周期。")
            .c_str());
    gtk_dialog_add_button(GTK_DIALOG(dialog), "返回更新数据", GTK_RESPONSE_CANCEL);
    gtk_dialog_add_button(GTK_DIALOG(dialog), "继续预测", GTK_RESPONSE_ACCEPT);
    g_signal_connect(dialog, "response", G_CALLBACK(on_cycle_age_response), app);
    g_signal_connect(dialog, "destroy", G_CALLBACK(on_cycle_age_destroy), app);
    app->cycle_age_dialog = dialog;
    gtk_widget_set_visible(dialog, TRUE);
}

void on_cycle_age_response(GtkDialog*, gint response_id, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (response_id == GTK_RESPONSE_ACCEPT) {
        preflight_run_forecast(app, true);
    }
    if (app->cycle_age_dialog != nullptr) {
        GtkWidget* dialog = app->cycle_age_dialog;
        app->cycle_age_dialog = nullptr;
        gtk_window_destroy(GTK_WINDOW(dialog));
    }
}

void on_cycle_age_destroy(GtkWidget*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    app->cycle_age_dialog = nullptr;
}

void on_preflight_confirm_clicked(GtkButton*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (app->preflight_session.empty() || !app->preflight_ready ||
        app->preflight_working) {
        return;
    }
    if (preflight_needs_cycle_age_confirmation(app)) {
        preflight_show_cycle_age_confirm(app);
        return;
    }
    preflight_run_forecast(app, false);
}

// Cancelling the sheet (取消 button or title-bar close) dismisses the token.
void preflight_cancel(GtkApp* app) {
    const bool was_open = app->preflight_open;
    app->preflight_open = false;
    ++app->preflight_revision;
    app->preflight_working = false;
    app->preflight_publications_pending = false;
    app->preflight_ready = false;
    cancel_active_task(app);
    dismiss_preflight_token(app);
    app->preflight_session.clear();
    if (was_open) {
        set_status(app, "已取消");
    }
    set_busy(app, false);
    if (app->preflight_dialog != nullptr) {
        GtkWidget* dialog = app->preflight_dialog;
        app->preflight_dialog = nullptr;
        gtk_window_destroy(GTK_WINDOW(dialog));
    }
    if (app->cycle_age_dialog != nullptr) {
        GtkWidget* dialog = app->cycle_age_dialog;
        app->cycle_age_dialog = nullptr;
        gtk_window_destroy(GTK_WINDOW(dialog));
    }
}

void on_preflight_refresh_clicked(GtkButton*, gpointer data) {
    preflight_start_check(static_cast<GtkApp*>(data));
}

void on_preflight_prepare_clicked(GtkButton*, gpointer data) {
    preflight_run_prepare(static_cast<GtkApp*>(data));
}

void on_preflight_cancel_clicked(GtkButton*, gpointer data) {
    preflight_cancel(static_cast<GtkApp*>(data));
}

void on_preflight_response(GtkDialog*, gint response_id, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (response_id == GTK_RESPONSE_DELETE_EVENT) {
        preflight_cancel(app);
    }
}

void on_preflight_destroy(GtkWidget*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    app->preflight_dialog = nullptr;
    app->preflight_open = false;
}

void preflight_build_dialog(GtkApp* app) {
    GtkWidget* dialog = gtk_dialog_new();
    app->preflight_dialog = dialog;
    app->preflight_open = true;
    gtk_window_set_title(GTK_WINDOW(dialog),
                         app->request_kind == "nowcast" ? "实况准备" : "预测准备");
    gtk_window_set_transient_for(GTK_WINDOW(dialog), GTK_WINDOW(app->window));
    gtk_window_set_modal(GTK_WINDOW(dialog), TRUE);
    gtk_window_set_default_size(GTK_WINDOW(dialog), 490, -1);
    g_signal_connect(dialog, "response", G_CALLBACK(on_preflight_response), app);
    g_signal_connect(dialog, "destroy", G_CALLBACK(on_preflight_destroy), app);

    GtkWidget* content = gtk_dialog_get_content_area(GTK_DIALOG(dialog));
    gtk_box_set_spacing(GTK_BOX(content), 12);
    gtk_widget_set_margin_top(content, 22);
    gtk_widget_set_margin_bottom(content, 22);
    gtk_widget_set_margin_start(content, 24);
    gtk_widget_set_margin_end(content, 24);

    GtkWidget* header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget* title_column = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    gtk_widget_set_hexpand(title_column, TRUE);
    GtkWidget* title = gtk_label_new(app->request_kind == "nowcast" ? "实况准备"
                                                                    : "预测准备");
    gtk_label_set_xalign(GTK_LABEL(title), 0.0F);
    gtk_widget_add_css_class(title, "title-3");
    app->preflight_name_label = gtk_label_new(preflight_headline(app).c_str());
    gtk_label_set_xalign(GTK_LABEL(app->preflight_name_label), 0.0F);
    gtk_widget_add_css_class(app->preflight_name_label, "dim-label");
    gtk_box_append(GTK_BOX(title_column), title);
    gtk_box_append(GTK_BOX(title_column), app->preflight_name_label);
    app->preflight_refresh_button = gtk_button_new_with_label("重新检查");
    gtk_widget_set_tooltip_text(app->preflight_refresh_button, "重新检查");
    gtk_widget_set_valign(app->preflight_refresh_button, GTK_ALIGN_START);
    g_signal_connect(app->preflight_refresh_button, "clicked",
                     G_CALLBACK(on_preflight_refresh_clicked), app);
    gtk_box_append(GTK_BOX(header), title_column);
    gtk_box_append(GTK_BOX(header), app->preflight_refresh_button);
    gtk_box_append(GTK_BOX(content), header);

    GtkWidget* rows_scroller = gtk_scrolled_window_new();
    gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(rows_scroller),
                                   GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
    gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(rows_scroller), 180);
    app->preflight_rows_list = gtk_list_box_new();
    gtk_list_box_set_selection_mode(GTK_LIST_BOX(app->preflight_rows_list),
                                    GTK_SELECTION_NONE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(rows_scroller),
                                  app->preflight_rows_list);
    gtk_box_append(GTK_BOX(content), rows_scroller);

    app->preflight_progress = gtk_progress_bar_new();
    gtk_widget_set_visible(app->preflight_progress, FALSE);
    gtk_box_append(GTK_BOX(content), app->preflight_progress);

    app->preflight_state_label = gtk_label_new("正在检查本地数据");
    gtk_label_set_xalign(GTK_LABEL(app->preflight_state_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(app->preflight_state_label), TRUE);
    gtk_box_append(GTK_BOX(content), app->preflight_state_label);

    GtkWidget* actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget* cancel_button = gtk_button_new_with_label("取消");
    g_signal_connect(cancel_button, "clicked",
                     G_CALLBACK(on_preflight_cancel_clicked), app);
    gtk_box_append(GTK_BOX(actions), cancel_button);
    GtkWidget* spacer = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_hexpand(spacer, TRUE);
    gtk_box_append(GTK_BOX(actions), spacer);
    app->preflight_prepare_button = gtk_button_new_with_label("下载缺失数据");
    g_signal_connect(app->preflight_prepare_button, "clicked",
                     G_CALLBACK(on_preflight_prepare_clicked), app);
    gtk_box_append(GTK_BOX(actions), app->preflight_prepare_button);
    app->preflight_confirm_button = gtk_button_new_with_label("确定预测");
    g_signal_connect(app->preflight_confirm_button, "clicked",
                     G_CALLBACK(on_preflight_confirm_clicked), app);
    gtk_box_append(GTK_BOX(actions), app->preflight_confirm_button);
    gtk_box_append(GTK_BOX(content), actions);

    gtk_widget_set_visible(dialog, TRUE);
}

void preflight_open_dialog(GtkApp* app) {
    if (app->engine == nullptr) {
        set_status(app, "Connect the Rust engine first.");
        return;
    }
    if (app->busy) {
        return;
    }
    const std::string mode = selected_mode(app);
    std::string error;
    const auto payload = build_payload(app, mode, error);
    if (!payload) {
        set_status(app, error);
        return;
    }
    if (app->preflight_dialog != nullptr) {
        preflight_cancel(app);
    }
    app->request_kind = mode;
    app->request_payload = *payload;
    app->preflight_session.clear();
    app->preflight_ready = false;
    app->preflight_working = false;
    app->preflight_publications_pending = false;
    app->preflight_error.clear();
    app->preflight_report_status.clear();
    app->preflight_stage = "正在检查本地数据";
    preflight_build_dialog(app);
    preflight_render_rows(app);
    preflight_render_state(app);
    preflight_update_buttons(app);
    set_busy(app, true);
    preflight_start_check(app);
}

// ---------------------------------------------------------------------------
// Task history view (contract 2/4) and published result restore (contract 5).
// ---------------------------------------------------------------------------

const char* history_status_style(const std::string& status) {
    if (status == "done") {
        return "success";
    }
    if (status == "failed") {
        return "error";
    }
    if (status == "interrupted") {
        return "warning";
    }
    return nullptr;
}

// Row buttons carry their list index as object data (stored as index + 1 so
// the first row survives the nullptr sentinel of g_object_get_data).
void attach_history_button_data(GtkWidget* widget, GtkApp* app, std::size_t index) {
    g_object_set_data(G_OBJECT(widget), "firesky-app", app);
    g_object_set_data(G_OBJECT(widget), "firesky-history-index",
                      GINT_TO_POINTER(static_cast<gint>(index) + 1));
}

GtkApp* history_button_app(GtkWidget* widget) {
    return static_cast<GtkApp*>(g_object_get_data(G_OBJECT(widget), "firesky-app"));
}

std::optional<std::size_t> history_button_index(GtkWidget* widget) {
    const gpointer data = g_object_get_data(G_OBJECT(widget), "firesky-history-index");
    if (data == nullptr) {
        return std::nullopt;
    }
    const gint value = GPOINTER_TO_INT(data);
    if (value <= 0) {
        return std::nullopt;
    }
    return static_cast<std::size_t>(value - 1);
}

void on_history_events_clicked(GtkButton* button, gpointer data);
void on_history_restore_result_clicked(GtkButton* button, gpointer data);
void on_history_restore_parameters_clicked(GtkButton* button, gpointer data);
void on_history_forget_clicked(GtkButton* button, gpointer data);

const HistoryItem* find_history_item(GtkApp* app, const std::string& task_id) {
    for (const auto& item : app->history_items) {
        if (item.task_id == task_id) {
            return &item;
        }
    }
    return nullptr;
}

GtkWidget* build_history_row(GtkApp* app, std::size_t index) {
    const HistoryItem& item = app->history_items[index];
    GtkWidget* list_row = gtk_list_box_row_new();
    GtkWidget* content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
    gtk_widget_set_margin_top(content, 10);
    gtk_widget_set_margin_bottom(content, 10);

    GtkWidget* text_column = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    gtk_widget_set_hexpand(text_column, TRUE);

    GtkWidget* headline = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 7);
    GtkWidget* kind_label =
        gtk_label_new((item.kind_title() + " · " + item.method_title()).c_str());
    gtk_label_set_xalign(GTK_LABEL(kind_label), 0.0F);
    gtk_box_append(GTK_BOX(headline), kind_label);
    GtkWidget* status_label = gtk_label_new(item.status_title().c_str());
    gtk_label_set_xalign(GTK_LABEL(status_label), 0.0F);
    if (const char* style = history_status_style(item.status)) {
        gtk_widget_add_css_class(status_label, style);
    }
    gtk_box_append(GTK_BOX(headline), status_label);
    gtk_box_append(GTK_BOX(text_column), headline);

    if (!item.context_title().empty()) {
        GtkWidget* context = gtk_label_new(item.context_title().c_str());
        gtk_label_set_xalign(GTK_LABEL(context), 0.0F);
        gtk_label_set_wrap(GTK_LABEL(context), TRUE);
        gtk_widget_add_css_class(context, "dim-label");
        gtk_box_append(GTK_BOX(text_column), context);
    }

    std::vector<std::string> meta;
    meta.push_back(item.created_at_utc);
    if (item.status == "running") {
        if (auto percent = item.progress_percent()) {
            meta.push_back(percent_text(*percent));
        }
    }
    if (item.status == "interrupted") {
        meta.push_back("不会自动重跑");
    }
    GtkWidget* meta_label = gtk_label_new(join_strings(meta, "  ").c_str());
    gtk_label_set_xalign(GTK_LABEL(meta_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(meta_label), TRUE);
    gtk_widget_add_css_class(meta_label, "dim-label");
    gtk_box_append(GTK_BOX(text_column), meta_label);
    gtk_box_append(GTK_BOX(content), text_column);

    GtkWidget* buttons = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_widget_set_valign(buttons, GTK_ALIGN_START);

    GtkWidget* events_button = gtk_button_new_with_label("事件");
    gtk_widget_set_tooltip_text(events_button, "查看任务事件");
    // GTK 的壳一次只持有一个 active task；busy 期间禁用行内操作避免
    // task_events 抢占正在运行的预测（macOS 用并发任务，无此约束）。
    gtk_widget_set_sensitive(events_button, !app->busy);
    attach_history_button_data(events_button, app, index);
    g_signal_connect(events_button, "clicked",
                     G_CALLBACK(on_history_events_clicked), nullptr);
    gtk_box_append(GTK_BOX(buttons), events_button);

    if (item.can_restore_result()) {
        GtkWidget* restore_button = gtk_button_new_with_label("恢复结果");
        gtk_widget_set_tooltip_text(restore_button, "恢复已发布结果");
        gtk_widget_set_sensitive(restore_button,
                                 !app->busy && app->restoring_task_id.empty());
        attach_history_button_data(restore_button, app, index);
        g_signal_connect(restore_button, "clicked",
                         G_CALLBACK(on_history_restore_result_clicked), nullptr);
        gtk_box_append(GTK_BOX(buttons), restore_button);
    }
    if (item.has_retry) {
        GtkWidget* retry_button = gtk_button_new_with_label("恢复参数");
        gtk_widget_set_tooltip_text(retry_button, "恢复参数并重新检查数据");
        gtk_widget_set_sensitive(retry_button,
                                 !app->busy && app->restoring_task_id.empty() &&
                                     item.status != "running");
        attach_history_button_data(retry_button, app, index);
        g_signal_connect(retry_button, "clicked",
                         G_CALLBACK(on_history_restore_parameters_clicked), nullptr);
        gtk_box_append(GTK_BOX(buttons), retry_button);
    }
    GtkWidget* forget_button = gtk_button_new_with_label("删除");
    gtk_widget_set_tooltip_text(forget_button, "删除任务记录");
    gtk_widget_set_sensitive(forget_button,
                             !app->busy && item.status != "running" &&
                                 app->forgetting_task_id.empty());
    attach_history_button_data(forget_button, app, index);
    g_signal_connect(forget_button, "clicked",
                     G_CALLBACK(on_history_forget_clicked), nullptr);
    gtk_box_append(GTK_BOX(buttons), forget_button);
    gtk_box_append(GTK_BOX(content), buttons);

    gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(list_row), content);
    return list_row;
}

GtkWidget* build_history_event_row(const HistoryEvent& event) {
    GtkWidget* list_row = gtk_list_box_row_new();
    GtkWidget* content = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 9);
    gtk_widget_set_margin_top(content, 3);
    gtk_widget_set_margin_bottom(content, 3);
    GtkWidget* at_label = gtk_label_new(event.at_utc.c_str());
    gtk_label_set_xalign(GTK_LABEL(at_label), 0.0F);
    gtk_widget_add_css_class(at_label, "dim-label");
    gtk_box_append(GTK_BOX(content), at_label);
    GtkWidget* title_label = gtk_label_new(event.title().c_str());
    gtk_label_set_xalign(GTK_LABEL(title_label), 0.0F);
    gtk_box_append(GTK_BOX(content), title_label);
    GtkWidget* detail_label = gtk_label_new(event.detail().c_str());
    gtk_label_set_xalign(GTK_LABEL(detail_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(detail_label), TRUE);
    gtk_widget_set_hexpand(detail_label, TRUE);
    gtk_widget_add_css_class(detail_label, "dim-label");
    gtk_box_append(GTK_BOX(content), detail_label);
    gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(list_row), content);
    return list_row;
}

void history_render_events(GtkApp* app) {
    if (app->history_events_box == nullptr) {
        return;
    }
    const HistoryItem* item = app->history_selected_id.empty()
                                  ? nullptr
                                  : find_history_item(app, app->history_selected_id);
    if (item == nullptr) {
        gtk_widget_set_visible(app->history_events_box, FALSE);
        return;
    }
    gtk_widget_set_visible(app->history_events_box, TRUE);
    gtk_label_set_text(
        GTK_LABEL(app->history_events_title_label),
        (item->kind_title() + " · " + item->method_title() + "事件").c_str());
    gtk_widget_set_visible(app->history_events_truncated_label,
                           app->history_events_truncated);
    gtk_label_set_text(GTK_LABEL(app->history_events_error_label),
                       app->history_events_error.c_str());
    gtk_widget_set_visible(app->history_events_error_label,
                           !app->history_events_error.empty());
    gtk_widget_set_visible(app->history_events_loading_label,
                           app->history_events_loading);
    gtk_widget_set_visible(app->history_events_scroller,
                           !app->history_events_loading &&
                               app->history_events_error.empty());
    clear_list(app->history_events_list);
    for (const auto& event : app->history_events) {
        gtk_list_box_append(GTK_LIST_BOX(app->history_events_list),
                            build_history_event_row(event));
    }
}

void history_render(GtkApp* app) {
    if (app->history_list == nullptr) {
        return;
    }
    const std::string subtitle =
        app->history_loaded
            ? "显示最近 " + std::to_string(app->history_items.size()) +
                  " 项，共 " + std::to_string(app->history_total) + " 项"
            : std::string("记录数据检查、准备、预测和下载探测任务");
    gtk_label_set_text(GTK_LABEL(app->history_subtitle_label), subtitle.c_str());
    gtk_widget_set_sensitive(
        app->history_refresh_button,
        app->engine != nullptr && !app->busy && !app->history_loading);

    gtk_label_set_text(GTK_LABEL(app->history_error_label),
                       app->history_error.c_str());
    gtk_widget_set_visible(app->history_error_label, !app->history_error.empty());
    gtk_label_set_text(GTK_LABEL(app->history_empty_hint_label),
                       app->engine != nullptr
                           ? "完成一次检查或预测后会显示在这里。"
                           : "引擎未连接。");
    const bool show_loading = app->history_loading && app->history_items.empty();
    const bool show_empty = !app->history_loading && app->history_items.empty();
    gtk_widget_set_visible(app->history_loading_label, show_loading);
    gtk_widget_set_visible(app->history_empty_box, show_empty);
    gtk_widget_set_visible(app->history_scroller, !show_loading && !show_empty);
    if (app->history_issues > 0) {
        gtk_label_set_text(
            GTK_LABEL(app->history_issues_label),
            (std::to_string(app->history_issues) +
             " 项记录无法核验，未将未知状态标为中断。")
                .c_str());
        gtk_widget_set_visible(app->history_issues_label, TRUE);
    } else {
        gtk_widget_set_visible(app->history_issues_label, FALSE);
    }
    clear_list(app->history_list);
    for (std::size_t index = 0; index < app->history_items.size(); ++index) {
        gtk_list_box_append(GTK_LIST_BOX(app->history_list),
                            build_history_row(app, index));
    }
    history_render_events(app);
}

void history_clear_selection(GtkApp* app) {
    app->history_selected_id.clear();
    app->history_events.clear();
    app->history_events_error.clear();
    app->history_events_truncated = false;
    app->history_events_loading = false;
    history_render(app);
}

// Per-record validation mirrors NativeTaskHistoryItem.init: an invalid
// required field rejects the record, while retry_request/result_ref/progress
// are tolerated as optional.
std::optional<HistoryItem> parse_history_item(const std::string& task) {
    HistoryItem item;
    const auto task_id = json_string_field(task, "task_id");
    if (!task_id || !is_hex_task_id(*task_id)) {
        return std::nullopt;
    }
    const auto method = json_string_field(task, "method");
    if (!method) {
        return std::nullopt;
    }
    const auto status = json_string_field(task, "status");
    if (!status || !is_known_task_status(*status)) {
        return std::nullopt;
    }
    const auto created = json_string_field(task, "created_at_utc");
    if (!created || !valid_iso8601_utc(*created)) {
        return std::nullopt;
    }
    const auto updated = json_string_field(task, "updated_at_utc");
    if (!updated || !valid_iso8601_utc(*updated)) {
        return std::nullopt;
    }
    const auto event_seq = json_number_field(task, "event_seq");
    if (!event_seq || *event_seq < 1.0 || std::floor(*event_seq) != *event_seq) {
        return std::nullopt;
    }
    item.task_id = *task_id;
    item.method = *method;
    item.status = *status;
    item.created_at_utc = *created;
    item.updated_at_utc = *updated;
    item.event_seq = static_cast<long long>(*event_seq);
    item.cancel_requested = json_bool_field(task, "cancel_requested");

    if (const auto retry = json_value_fragment(task, "retry_request");
        retry && !retry->empty() && retry->front() == '{') {
        const auto kind = json_string_field(*retry, "kind");
        const auto payload = json_value_fragment(*retry, "payload");
        if (kind && payload && !payload->empty() && payload->front() == '{' &&
            (*kind == "point" || *kind == "regional" || *kind == "nowcast")) {
            item.has_retry = true;
            item.retry_kind = *kind;
            item.retry_payload = *payload;
        }
    }
    if (const auto reference = json_value_fragment(task, "result_ref");
        reference && !reference->empty() && reference->front() == '{') {
        if (const auto snapshot = json_value_fragment(*reference, "snapshot");
            snapshot && !snapshot->empty() && snapshot->front() == '{') {
            const auto contract = json_string_field(*snapshot, "contract");
            const auto sha256 = json_string_field(*snapshot, "sha256");
            item.has_snapshot = true;
            item.snapshot_contract = contract.value_or("");
            item.has_snapshot_sha256 = sha256.has_value();
            item.snapshot_sha256 = sha256.value_or("");
        }
    }
    if (const auto progress = json_value_fragment(task, "progress");
        progress && !progress->empty() && progress->front() == '{') {
        item.progress = *progress;
    } else {
        item.progress = "{}";
    }
    return item;
}

void history_load(GtkApp* app) {
    if (app->engine == nullptr || app->busy || app->history_loading) {
        return;
    }
    app->history_loading = true;
    app->history_error.clear();
    history_render(app);
    launch_request(
        app, "task_history", "{\"limit\":100}",
        [app](const std::string& response, const std::string& failure) {
            app->history_loading = false;
            set_busy(app, false);
            if (!failure.empty()) {
                app->history_error = error_message(failure);
                history_render(app);
                return;
            }
            std::string failure_text;
            if (json_string_field(response, "schema").value_or("") !=
                "firesky-task-history-v1") {
                failure_text = "任务历史协议不匹配，请重新连接引擎。";
            } else if (const auto restart =
                           json_value_fragment(response, "automatic_restart");
                       !restart || *restart != "false") {
                failure_text = "任务历史协议不匹配，请重新连接引擎。";
            }
            std::optional<std::vector<std::string>> tasks;
            if (failure_text.empty()) {
                tasks = json_array_items(response, "tasks");
                if (!tasks) {
                    failure_text = "任务历史协议不匹配，请重新连接引擎。";
                }
            }
            std::optional<double> total;
            if (failure_text.empty()) {
                total = json_number_field(response, "total");
                if (!total || std::floor(*total) != *total ||
                    *total < static_cast<double>(tasks->size())) {
                    failure_text = "任务历史协议不匹配，请重新连接引擎。";
                }
            }
            if (failure_text.empty() && tasks->size() > 100) {
                failure_text = "任务历史协议不匹配，请重新连接引擎。";
            }
            std::vector<HistoryItem> parsed;
            if (failure_text.empty()) {
                for (const auto& task : *tasks) {
                    auto item = parse_history_item(task);
                    if (!item) {
                        failure_text = "任务历史包含无效记录，请检查引擎版本。";
                        break;
                    }
                    parsed.push_back(std::move(*item));
                }
            }
            if (failure_text.empty()) {
                for (std::size_t outer = 0;
                     outer < parsed.size() && failure_text.empty(); ++outer) {
                    for (std::size_t inner = outer + 1; inner < parsed.size();
                         ++inner) {
                        if (parsed[outer].task_id == parsed[inner].task_id) {
                            failure_text = "任务历史包含重复记录，请重新连接引擎。";
                            break;
                        }
                    }
                }
            }
            if (!failure_text.empty()) {
                app->history_error = failure_text;
                history_render(app);
                return;
            }
            app->history_items = std::move(parsed);
            app->history_total = static_cast<long long>(*total);
            app->history_issues = 0;
            if (const auto issues = json_array_items(response, "issues")) {
                app->history_issues = static_cast<long long>(issues->size());
            }
            app->history_loaded = true;
            if (!app->history_selected_id.empty() &&
                find_history_item(app, app->history_selected_id) == nullptr) {
                history_clear_selection(app);
            }
            history_render(app);
        });
}

void history_select(GtkApp* app, const std::string& task_id) {
    if (app->history_selected_id == task_id) {
        history_clear_selection(app);
        return;
    }
    if (find_history_item(app, task_id) == nullptr) {
        return;
    }
    app->history_selected_id = task_id;
    app->history_events.clear();
    app->history_events_error.clear();
    app->history_events_truncated = false;
    app->history_events_loading = true;
    history_render(app);
    launch_request(
        app, "task_events",
        "{\"task_id\":\"" + json_escape(task_id) + "\",\"after_seq\":0}",
        [app, task_id](const std::string& response, const std::string& failure) {
            app->history_events_loading = false;
            set_busy(app, false);
            if (app->history_selected_id != task_id) {
                return;
            }
            if (!failure.empty()) {
                app->history_events_error = error_message(failure);
                history_render(app);
                return;
            }
            std::string failure_text;
            if (json_string_field(response, "task_id").value_or("") != task_id) {
                failure_text = "任务事件协议不匹配。";
            }
            std::optional<std::vector<std::string>> events;
            if (failure_text.empty()) {
                events = json_array_items(response, "events");
                if (!events) {
                    failure_text = "任务事件协议不匹配。";
                }
            }
            std::optional<double> next;
            if (failure_text.empty()) {
                next = json_number_field(response, "next_seq");
                if (!next || std::floor(*next) != *next || *next < 0.0) {
                    failure_text = "任务事件协议不匹配。";
                }
            }
            if (failure_text.empty()) {
                const auto truncated = json_value_fragment(response, "truncated");
                if (!truncated || (*truncated != "true" && *truncated != "false")) {
                    failure_text = "任务事件协议不匹配。";
                }
            }
            std::vector<HistoryEvent> parsed;
            if (failure_text.empty()) {
                double previous = 0.0;
                for (const auto& raw : *events) {
                    const auto seq = json_number_field(raw, "seq");
                    if (!seq || std::floor(*seq) != *seq || *seq <= previous ||
                        *seq > *next) {
                        failure_text = "任务事件顺序或时间无效。";
                        break;
                    }
                    const auto at = json_string_field(raw, "at_utc");
                    if (!at || !valid_iso8601_utc(*at)) {
                        failure_text = "任务事件顺序或时间无效。";
                        break;
                    }
                    const auto event = json_string_field(raw, "event");
                    if (!event) {
                        failure_text = "任务事件顺序或时间无效。";
                        break;
                    }
                    std::string data = "{}";
                    if (const auto fragment = json_value_fragment(raw, "data");
                        fragment && !fragment->empty() && fragment->front() == '{') {
                        data = *fragment;
                    }
                    previous = *seq;
                    parsed.push_back(HistoryEvent{
                        static_cast<long long>(*seq), *at, *event, std::move(data)});
                }
            }
            if (!failure_text.empty()) {
                app->history_events_error = failure_text;
                history_render(app);
                return;
            }
            app->history_events = std::move(parsed);
            app->history_events_truncated =
                json_value_fragment(response, "truncated").value_or("false") == "true";
            history_render(app);
        });
}

void history_forget(GtkApp* app, const std::string& task_id) {
    if (app->engine == nullptr || app->busy || !app->forgetting_task_id.empty()) {
        return;
    }
    const HistoryItem* item = find_history_item(app, task_id);
    if (item == nullptr || item->status == "running") {
        return;
    }
    app->forgetting_task_id = task_id;
    history_render(app);
    launch_request(
        app, "task_forget", "{\"task_id\":\"" + json_escape(task_id) + "\"}",
        [app, task_id](const std::string& response, const std::string& failure) {
            app->forgetting_task_id.clear();
            set_busy(app, false);
            if (!failure.empty()) {
                app->history_error = error_message(failure);
                history_render(app);
                return;
            }
            if (!json_bool_field(response, "forgotten")) {
                app->history_error = "任务记录没有被删除。";
                history_render(app);
                return;
            }
            if (app->history_selected_id == task_id) {
                history_clear_selection(app);
            }
            history_load(app);
        });
}

void destroy_task_id_data(gpointer data) {
    delete static_cast<std::string*>(data);
}

void on_forget_confirm_response(GtkDialog* dialog, gint response_id, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    const auto* attached = static_cast<const std::string*>(
        g_object_get_data(G_OBJECT(dialog), "firesky-forget-task-id"));
    std::string task_id = attached != nullptr ? *attached : std::string();
    app->forget_confirm_dialog = nullptr;
    if (response_id == GTK_RESPONSE_ACCEPT && !task_id.empty()) {
        history_forget(app, task_id);
    }
    gtk_window_destroy(GTK_WINDOW(dialog));
}

void on_forget_confirm_destroy(GtkWidget*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    app->forget_confirm_dialog = nullptr;
}

void history_show_forget_confirm(GtkApp* app, const std::string& task_id) {
    if (app->forget_confirm_dialog != nullptr) {
        return;
    }
    GtkWidget* dialog = GTK_WIDGET(gtk_message_dialog_new(
        GTK_WINDOW(app->window),
        static_cast<GtkDialogFlags>(GTK_DIALOG_MODAL | GTK_DIALOG_DESTROY_WITH_PARENT),
        GTK_MESSAGE_QUESTION, GTK_BUTTONS_NONE, "%s", "删除任务记录？"));
    gtk_message_dialog_format_secondary_text(
        GTK_MESSAGE_DIALOG(dialog), "%s",
        "只删除状态和事件，不删除已发布报告或天气数据。");
    gtk_dialog_add_button(GTK_DIALOG(dialog), "保留记录", GTK_RESPONSE_CANCEL);
    gtk_dialog_add_button(GTK_DIALOG(dialog), "删除记录", GTK_RESPONSE_ACCEPT);
    g_object_set_data_full(G_OBJECT(dialog), "firesky-forget-task-id",
                           new std::string(task_id), destroy_task_id_data);
    g_signal_connect(dialog, "response", G_CALLBACK(on_forget_confirm_response), app);
    g_signal_connect(dialog, "destroy", G_CALLBACK(on_forget_confirm_destroy), app);
    app->forget_confirm_dialog = dialog;
    gtk_widget_set_visible(dialog, TRUE);
}

// Contract 5: restore the published snapshot, switch to the task's page kind
// (default point) and select the model like Store.restoreTaskResult.
void history_restore_result(GtkApp* app, std::size_t index) {
    if (app->engine == nullptr || app->busy || !app->restoring_task_id.empty()) {
        return;
    }
    if (index >= app->history_items.size()) {
        return;
    }
    const HistoryItem& item = app->history_items[index];
    if (!item.can_restore_result()) {
        return;
    }
    const std::string kind = item.has_retry ? item.retry_kind : std::string("point");
    const std::string retry_payload = item.retry_payload;
    const std::string task_id = item.task_id;
    app->restoring_task_id = task_id;
    history_render(app);
    launch_request(
        app, "result_restore", "{\"task_id\":\"" + json_escape(task_id) + "\"}",
        [app, kind, retry_payload](const std::string& response,
                                   const std::string& failure) {
            app->restoring_task_id.clear();
            set_busy(app, false);
            if (!failure.empty()) {
                app->history_error = error_message(failure);
                history_render(app);
                return;
            }
            const auto models = json_value_fragment(response, "model_results");
            const bool has_models =
                models && !models->empty() && models->front() == '{';
            if (kind != "nowcast" && !has_models) {
                app->history_error = "已发布结果格式无效。";
                history_render(app);
                return;
            }
            select_mode(app, kind);
            set_output(app, response);
            show_city_ranking(app, response);
            app->last_result_json = response;
            app->last_result_kind = kind;
            if (kind != "nowcast") {
                std::string model;
                const auto payload_model =
                    json_string_field(retry_payload, "model_source");
                if (payload_model && (*payload_model == "gfs" || *payload_model == "ec")) {
                    model = *payload_model;
                } else if (has_models && json_value_fragment(*models, "gfs")) {
                    model = "gfs";
                } else {
                    model = "ec";
                }
                app->result_model_updating = true;
                gtk_drop_down_set_selected(GTK_DROP_DOWN(app->result_model_dropdown),
                                           model == "gfs" ? 0U : 1U);
                app->result_model_updating = false;
            } else if (app->result_model_dropdown != nullptr) {
                gtk_widget_set_sensitive(app->result_model_dropdown, FALSE);
            }
            app->history_error.clear();
            set_status(app, "已恢复历史结果");
            history_render(app);
        });
}

// Contract 6: apply retry.payload to the matching page fields and immediately
// run that page's unified preflight check (macOS applyRetryPayload + beginCheck).
void history_restore_parameters(GtkApp* app, std::size_t index) {
    if (app->engine == nullptr || app->busy || !app->restoring_task_id.empty()) {
        return;
    }
    if (index >= app->history_items.size()) {
        return;
    }
    const HistoryItem& item = app->history_items[index];
    if (!item.has_retry || item.status == "running") {
        return;
    }
    const std::string& payload = item.retry_payload;
    if (auto value = json_number_field(payload, "lat")) {
        set_entry_number(app->latitude_entry, *value);
    }
    if (auto value = json_number_field(payload, "lon")) {
        set_entry_number(app->longitude_entry, *value);
    }
    if (auto value = json_number_field(payload, "elevation_m")) {
        set_entry_number(app->elevation_entry, *value);
    }
    if (auto value = json_string_field(payload, "timezone_name")) {
        gtk_editable_set_text(GTK_EDITABLE(app->timezone_entry), value->c_str());
    }
    if (auto value = json_string_field(payload, "target_date")) {
        gtk_editable_set_text(GTK_EDITABLE(app->date_entry), value->c_str());
    }
    if (auto value = json_string_field(payload, "phenomenon")) {
        gtk_editable_set_text(GTK_EDITABLE(app->phenomenon_entry), value->c_str());
    }
    if (const auto bounds = json_value_fragment(payload, "custom_bounds");
        bounds && !bounds->empty() && bounds->front() == '{') {
        if (auto value = json_number_field(*bounds, "west")) {
            set_entry_number(app->west_entry, *value);
        }
        if (auto value = json_number_field(*bounds, "east")) {
            set_entry_number(app->east_entry, *value);
        }
        if (auto value = json_number_field(*bounds, "south")) {
            set_entry_number(app->south_entry, *value);
        }
        if (auto value = json_number_field(*bounds, "north")) {
            set_entry_number(app->north_entry, *value);
        }
    }
    select_mode(app, item.retry_kind);
    preflight_open_dialog(app);
}

void on_history_events_clicked(GtkButton* button, gpointer) {
    GtkWidget* widget = GTK_WIDGET(button);
    GtkApp* app = history_button_app(widget);
    const auto index = history_button_index(widget);
    if (app == nullptr || !index || *index >= app->history_items.size()) {
        return;
    }
    history_select(app, app->history_items[*index].task_id);
}

void on_history_restore_result_clicked(GtkButton* button, gpointer) {
    GtkWidget* widget = GTK_WIDGET(button);
    GtkApp* app = history_button_app(widget);
    const auto index = history_button_index(widget);
    if (app == nullptr || !index || *index >= app->history_items.size()) {
        return;
    }
    history_restore_result(app, *index);
}

void on_history_restore_parameters_clicked(GtkButton* button, gpointer) {
    GtkWidget* widget = GTK_WIDGET(button);
    GtkApp* app = history_button_app(widget);
    const auto index = history_button_index(widget);
    if (app == nullptr || !index || *index >= app->history_items.size()) {
        return;
    }
    history_restore_parameters(app, *index);
}

void on_history_forget_clicked(GtkButton* button, gpointer) {
    GtkWidget* widget = GTK_WIDGET(button);
    GtkApp* app = history_button_app(widget);
    const auto index = history_button_index(widget);
    if (app == nullptr || !index || *index >= app->history_items.size()) {
        return;
    }
    history_show_forget_confirm(app, app->history_items[*index].task_id);
}

void on_history_events_close_clicked(GtkButton*, gpointer data) {
    history_clear_selection(static_cast<GtkApp*>(data));
}

void on_history_refresh_clicked(GtkButton*, gpointer data) {
    history_load(static_cast<GtkApp*>(data));
}

void on_history_expander_changed(GObject* object, GParamSpec*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (gtk_expander_get_expanded(GTK_EXPANDER(object)) && app->engine != nullptr) {
        history_load(app);
    }
}

// ---------------------------------------------------------------------------
// Retention / profile / connect flows (existing contracts, unchanged).
// ---------------------------------------------------------------------------

void load_retention_settings(GtkApp* app) {
    if (app->engine == nullptr) {
        return;
    }
    set_status(app, "Loading cache retention settings...");
    launch_request(
        app, "retention_settings", "{}",
        [app](const std::string& response, const std::string& failure) {
            if (!failure.empty()) {
                set_status(app, failure);
                set_busy(app, false);
                return;
            }
            set_retention_number(
                app->retention_gfs_entry, json_number_field(response, "gfs_cycles"));
            set_retention_number(
                app->retention_ecmwf_entry, json_number_field(response, "ecmwf_cycles"));
            set_retention_number(
                app->retention_cams_entry, json_number_field(response, "cams_cycles"));
            set_retention_number(app->retention_satellite_entry,
                                 json_number_field(response, "satellite_days"));
            set_retention_number(
                app->retention_report_entry, json_number_field(response, "report_days"));
            set_retention_number(
                app->retention_map_entry, json_number_field(response, "map_days"));
            set_status(app, json_bool_field(response, "configured")
                                 ? "Cache retention settings loaded."
                                 : "Cache retention is disabled; blank values mean no cleanup.");
            set_busy(app, false);
        });
}

void save_retention_settings(GtkApp* app) {
    std::string error;
    const auto payload = build_retention_payload(app, error);
    if (!payload) {
        set_status(app, error);
        return;
    }
    set_status(app, "Saving cache retention settings...");
    launch_request(
        app, "save_retention_settings", *payload,
        [app](const std::string& response, const std::string& failure) {
            if (!failure.empty()) {
                set_status(app, failure);
                set_busy(app, false);
                return;
            }
            set_retention_number(
                app->retention_gfs_entry, json_number_field(response, "gfs_cycles"));
            set_retention_number(
                app->retention_ecmwf_entry, json_number_field(response, "ecmwf_cycles"));
            set_retention_number(
                app->retention_cams_entry, json_number_field(response, "cams_cycles"));
            set_retention_number(app->retention_satellite_entry,
                                 json_number_field(response, "satellite_days"));
            set_retention_number(
                app->retention_report_entry, json_number_field(response, "report_days"));
            set_retention_number(
                app->retention_map_entry, json_number_field(response, "map_days"));
            set_status(app, json_bool_field(response, "configured")
                                 ? "Cache retention saved; cleanup runs after successful preparation."
                                 : "Cache retention cleared.");
            set_busy(app, false);
        });
}

void query_profile(GtkApp* app) {
    std::string error;
    const auto payload = build_profile_payload(app, error);
    if (!payload) {
        set_status(app, error);
        return;
    }
    set_status(app, "Querying the frozen profile snapshot...");
    launch_request(
        app, "profile_query", *payload,
        [app](const std::string& response, const std::string& failure) {
            if (!failure.empty()) {
                set_status(app, failure);
                set_busy(app, false);
                return;
            }
            set_output(app, response);
            const std::string status =
                json_string_field(response, "status").value_or("invalid");
            set_status(app, "Profile query finished: " + status + ".");
            set_busy(app, false);
        });
}

void connect_engine(GtkApp* app) {
    const std::string library = editable_text(app->library_entry);
    const std::string workspace = editable_text(app->workspace_entry);
    if (library.empty() || workspace.empty()) {
        set_status(app, "Library path and workspace are required.");
        return;
    }

    set_busy(app, true);
    set_status(app, "Connecting to the Rust engine...");
    auto engine_holder = std::make_shared<std::unique_ptr<firesky::Engine>>();
    {
        std::lock_guard lock(app->worker_mutex);
        app->workers.emplace_back([app, library, workspace, engine_holder] {
            std::string hello;
            std::string error;
            try {
                *engine_holder = std::make_unique<firesky::Engine>(library, workspace);
                auto task = (*engine_holder)->start("hello");
                hello = task.join();
            } catch (const firesky::FfiError& failure) {
                error = failure.what();
            } catch (const std::exception& failure) {
                error = failure.what();
            }
            post_to_ui(app, [app, engine_holder, hello = std::move(hello),
                             error = std::move(error)]() mutable {
                if (!error.empty()) {
                    engine_holder->reset();
                    set_status(app, error);
                    set_output(app, {});
                    set_busy(app, false);
                    return;
                }
                app->engine = std::move(*engine_holder);
                set_output(app, hello);
                set_status(app, "Rust engine connected (FFI ABI 1).");
                set_busy(app, false);
                load_retention_settings(app);
            });
        });
    }
}

void on_connect_clicked(GtkButton*, gpointer data) {
    connect_engine(static_cast<GtkApp*>(data));
}

void on_mode_changed(GObject*, GParamSpec*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    const bool regional = selected_mode(app) == "regional";
    gtk_widget_set_visible(app->regional_grid, regional);
    if (!regional) {
        set_status(app, selected_mode(app) == "nowcast"
                             ? "Nowcast mode uses satellite, GFS and CAMS inputs."
                             : "Point mode uses paired GFS and EC inputs.");
    } else {
        set_status(app, "Regional mode uses a fixed 0.25 degree grid.");
    }
}

void on_forecast_clicked(GtkButton*, gpointer data) {
    preflight_open_dialog(static_cast<GtkApp*>(data));
}

void on_cancel_clicked(GtkButton*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (cancel_active_task(app)) {
        set_status(app, "Cancellation requested...");
    }
}

void on_retention_reload_clicked(GtkButton*, gpointer data) {
    load_retention_settings(static_cast<GtkApp*>(data));
}

void on_retention_save_clicked(GtkButton*, gpointer data) {
    save_retention_settings(static_cast<GtkApp*>(data));
}

void on_profile_query_clicked(GtkButton*, gpointer data) {
    query_profile(static_cast<GtkApp*>(data));
}

void on_result_model_changed(GObject*, GParamSpec*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (app->result_model_updating || app->last_result_json.empty()) {
        return;
    }
    const auto models = json_value_fragment(app->last_result_json, "model_results");
    if (!models || models->empty() || models->front() != '{') {
        return;
    }
    const std::string model = selected_result_model(app);
    const auto fragment = json_value_fragment(*models, model);
    set_output(app, fragment ? *fragment : app->last_result_json);
}

GtkWidget* make_entry(const char* value, const char* placeholder) {
    GtkWidget* entry = gtk_entry_new();
    gtk_editable_set_text(GTK_EDITABLE(entry), value);
    gtk_entry_set_placeholder_text(GTK_ENTRY(entry), placeholder);
    gtk_widget_set_hexpand(entry, TRUE);
    return entry;
}

void attach_row(GtkWidget* grid, int row, const char* label, GtkWidget* entry) {
    GtkWidget* title = gtk_label_new(label);
    gtk_label_set_xalign(GTK_LABEL(title), 0.0F);
    gtk_grid_attach(GTK_GRID(grid), title, 0, row, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), entry, 1, row, 1, 1);
}

void on_shutdown(GApplication*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    app->closing.store(true);
    on_cancel_clicked(nullptr, app);
    {
        std::lock_guard lock(app->worker_mutex);
        for (auto& worker : app->workers) {
            if (worker.joinable()) {
                worker.join();
            }
        }
        app->workers.clear();
    }
    if (!app->preflight_session.empty() && app->engine) {
        try {
            (void)app->engine->call(
                "dismiss", "{\"session\":\"" + json_escape(app->preflight_session) + "\"}");
        } catch (...) {
        }
    }
    app->engine.reset();
    delete app;
}

void on_activate(GtkApplication* application, gpointer) {
    if (auto* existing = static_cast<GtkApp*>(
            g_object_get_data(G_OBJECT(application), "firesky-gtk-state"))) {
        gtk_window_present(GTK_WINDOW(existing->window));
        return;
    }

    auto* app = new GtkApp();
    app->application = application;
    app->window = gtk_application_window_new(application);
    gtk_window_set_title(GTK_WINDOW(app->window), "FireSky Native");
    gtk_window_set_default_size(GTK_WINDOW(app->window), 900, 680);
    g_object_set_data(G_OBJECT(application), "firesky-gtk-state", app);
    g_signal_connect(application, "shutdown", G_CALLBACK(on_shutdown), app);

    GtkWidget* root = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    gtk_widget_set_margin_top(root, 18);
    gtk_widget_set_margin_bottom(root, 18);
    gtk_widget_set_margin_start(root, 18);
    gtk_widget_set_margin_end(root, 18);
    gtk_window_set_child(GTK_WINDOW(app->window), root);

    GtkWidget* title = gtk_label_new("FireSky Native Forecast");
    gtk_widget_add_css_class(title, "title-2");
    gtk_label_set_xalign(GTK_LABEL(title), 0.0F);
    gtk_box_append(GTK_BOX(root), title);

    GtkWidget* grid = gtk_grid_new();
    gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(grid), 12);
    gtk_box_append(GTK_BOX(root), grid);

    const char* modes[] = {"point", "regional", "nowcast", nullptr};
    GtkStringList* mode_list = gtk_string_list_new(modes);
    app->mode_dropdown = gtk_drop_down_new(G_LIST_MODEL(mode_list), nullptr);
    g_object_unref(mode_list);
    gtk_drop_down_set_selected(GTK_DROP_DOWN(app->mode_dropdown), 0);
    attach_row(grid, 0, "Mode", app->mode_dropdown);

    app->library_entry = make_entry("libfiresky_engine.so", "Path to libfiresky_engine.so");
    app->workspace_entry = make_entry(".", "Writable FireSky workspace");
    app->latitude_entry = make_entry("30.27", "Latitude");
    app->longitude_entry = make_entry("120.15", "Longitude");
    app->elevation_entry = make_entry("12", "Elevation (m)");
    app->timezone_entry = make_entry("Asia/Shanghai", "IANA timezone");
    const std::string date = local_date();
    app->date_entry = make_entry(date.c_str(), "YYYY-MM-DD");
    app->phenomenon_entry = make_entry("dusk", "dawn or dusk");
    attach_row(grid, 1, "Rust library", app->library_entry);
    attach_row(grid, 2, "Workspace", app->workspace_entry);
    attach_row(grid, 3, "Latitude", app->latitude_entry);
    attach_row(grid, 4, "Longitude", app->longitude_entry);
    attach_row(grid, 5, "Elevation", app->elevation_entry);
    attach_row(grid, 6, "Timezone", app->timezone_entry);
    attach_row(grid, 7, "Date", app->date_entry);
    attach_row(grid, 8, "Phenomenon", app->phenomenon_entry);

    app->regional_grid = gtk_grid_new();
    gtk_grid_set_row_spacing(GTK_GRID(app->regional_grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(app->regional_grid), 12);
    app->west_entry = make_entry("119.0", "West longitude");
    app->east_entry = make_entry("121.0", "East longitude");
    app->south_entry = make_entry("29.0", "South latitude");
    app->north_entry = make_entry("31.0", "North latitude");
    attach_row(app->regional_grid, 0, "West", app->west_entry);
    attach_row(app->regional_grid, 1, "East", app->east_entry);
    attach_row(app->regional_grid, 2, "South", app->south_entry);
    attach_row(app->regional_grid, 3, "North", app->north_entry);
    gtk_grid_attach(GTK_GRID(grid), app->regional_grid, 0, 9, 2, 1);
    gtk_widget_set_visible(app->regional_grid, FALSE);

    GtkWidget* actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    app->connect_button = gtk_button_new_with_label("Connect");
    app->forecast_button = gtk_button_new_with_label("预测");
    app->cancel_button = gtk_button_new_with_label("Cancel");
    gtk_box_append(GTK_BOX(actions), app->connect_button);
    gtk_box_append(GTK_BOX(actions), app->forecast_button);
    gtk_box_append(GTK_BOX(actions), app->cancel_button);
    gtk_box_append(GTK_BOX(root), actions);

    GtkWidget* retention_expander = gtk_expander_new("Cache retention");
    GtkWidget* retention_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget* retention_grid = gtk_grid_new();
    gtk_grid_set_row_spacing(GTK_GRID(retention_grid), 6);
    gtk_grid_set_column_spacing(GTK_GRID(retention_grid), 12);
    app->retention_gfs_entry = make_entry("", "cycles, blank disables");
    app->retention_ecmwf_entry = make_entry("", "cycles, blank disables");
    app->retention_cams_entry = make_entry("", "cycles, blank disables");
    app->retention_satellite_entry = make_entry("", "days, blank disables");
    app->retention_report_entry = make_entry("", "days, blank disables");
    app->retention_map_entry = make_entry("", "days, blank disables");
    attach_row(retention_grid, 0, "GFS cycles", app->retention_gfs_entry);
    attach_row(retention_grid, 1, "ECMWF cycles", app->retention_ecmwf_entry);
    attach_row(retention_grid, 2, "CAMS cycles", app->retention_cams_entry);
    attach_row(retention_grid, 3, "Satellite days", app->retention_satellite_entry);
    attach_row(retention_grid, 4, "Report days", app->retention_report_entry);
    attach_row(retention_grid, 5, "Map days", app->retention_map_entry);
    gtk_box_append(GTK_BOX(retention_box), retention_grid);
    GtkWidget* retention_actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    app->retention_reload_button = gtk_button_new_with_label("Reload");
    app->retention_save_button = gtk_button_new_with_label("Save");
    gtk_box_append(GTK_BOX(retention_actions), app->retention_reload_button);
    gtk_box_append(GTK_BOX(retention_actions), app->retention_save_button);
    gtk_box_append(GTK_BOX(retention_box), retention_actions);
    gtk_expander_set_child(GTK_EXPANDER(retention_expander), retention_box);
    gtk_box_append(GTK_BOX(root), retention_expander);

    GtkWidget* profile_expander = gtk_expander_new("Frozen profile query");
    GtkWidget* profile_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget* profile_grid = gtk_grid_new();
    gtk_grid_set_row_spacing(GTK_GRID(profile_grid), 6);
    gtk_grid_set_column_spacing(GTK_GRID(profile_grid), 12);
    const char* profile_models[] = {"gfs", "ec", nullptr};
    GtkStringList* profile_model_list = gtk_string_list_new(profile_models);
    app->profile_model_dropdown =
        gtk_drop_down_new(G_LIST_MODEL(profile_model_list), nullptr);
    g_object_unref(profile_model_list);
    gtk_drop_down_set_selected(GTK_DROP_DOWN(app->profile_model_dropdown), 0);
    app->profile_result_id_entry = make_entry("", "64-character frozen result ID");
    app->profile_bytes_entry = make_entry("", "source bytes");
    app->profile_sha256_entry = make_entry("", "64-character source SHA-256");
    app->profile_lat_entry = make_entry("30.2741", "latitude");
    app->profile_lon_entry = make_entry("120.1551", "longitude");
    app->profile_valid_time_entry =
        make_entry("", "RFC3339 UTC, for example 2026-11-12T09:00:00Z");
    attach_row(profile_grid, 0, "Model", app->profile_model_dropdown);
    attach_row(profile_grid, 1, "Result ID", app->profile_result_id_entry);
    attach_row(profile_grid, 2, "Source bytes", app->profile_bytes_entry);
    attach_row(profile_grid, 3, "Source SHA-256", app->profile_sha256_entry);
    attach_row(profile_grid, 4, "Latitude", app->profile_lat_entry);
    attach_row(profile_grid, 5, "Longitude", app->profile_lon_entry);
    attach_row(profile_grid, 6, "Valid UTC", app->profile_valid_time_entry);
    gtk_box_append(GTK_BOX(profile_box), profile_grid);
    app->profile_query_button = gtk_button_new_with_label("Query profile");
    gtk_box_append(GTK_BOX(profile_box), app->profile_query_button);
    gtk_expander_set_child(GTK_EXPANDER(profile_expander), profile_box);
    gtk_box_append(GTK_BOX(root), profile_expander);

    GtkWidget* ranking_expander = gtk_expander_new("Regional city ranking contract");
    GtkWidget* ranking_scroller = gtk_scrolled_window_new();
    gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(ranking_scroller), 120);
    app->ranking_view = gtk_text_view_new();
    gtk_text_view_set_editable(GTK_TEXT_VIEW(app->ranking_view), FALSE);
    gtk_text_view_set_monospace(GTK_TEXT_VIEW(app->ranking_view), TRUE);
    gtk_scrolled_window_set_child(
        GTK_SCROLLED_WINDOW(ranking_scroller), app->ranking_view);
    gtk_expander_set_child(GTK_EXPANDER(ranking_expander), ranking_scroller);
    gtk_box_append(GTK_BOX(root), ranking_expander);

    GtkWidget* history_expander = gtk_expander_new("任务历史");
    app->history_expander = history_expander;
    GtkWidget* history_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget* history_header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget* history_title_column = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    gtk_widget_set_hexpand(history_title_column, TRUE);
    GtkWidget* history_title = gtk_label_new("任务历史");
    gtk_label_set_xalign(GTK_LABEL(history_title), 0.0F);
    app->history_subtitle_label =
        gtk_label_new("记录数据检查、准备、预测和下载探测任务");
    gtk_label_set_xalign(GTK_LABEL(app->history_subtitle_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(app->history_subtitle_label), TRUE);
    gtk_widget_add_css_class(app->history_subtitle_label, "dim-label");
    gtk_box_append(GTK_BOX(history_title_column), history_title);
    gtk_box_append(GTK_BOX(history_title_column), app->history_subtitle_label);
    app->history_refresh_button = gtk_button_new_with_label("刷新");
    gtk_widget_set_tooltip_text(app->history_refresh_button, "刷新任务历史");
    gtk_widget_set_valign(app->history_refresh_button, GTK_ALIGN_START);
    g_signal_connect(app->history_refresh_button, "clicked",
                     G_CALLBACK(on_history_refresh_clicked), app);
    gtk_box_append(GTK_BOX(history_header), history_title_column);
    gtk_box_append(GTK_BOX(history_header), app->history_refresh_button);
    gtk_box_append(GTK_BOX(history_box), history_header);

    app->history_error_label = gtk_label_new("");
    gtk_label_set_xalign(GTK_LABEL(app->history_error_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(app->history_error_label), TRUE);
    gtk_widget_add_css_class(app->history_error_label, "error");
    gtk_widget_set_visible(app->history_error_label, FALSE);
    gtk_box_append(GTK_BOX(history_box), app->history_error_label);

    app->history_loading_label = gtk_label_new("正在读取任务记录…");
    gtk_label_set_xalign(GTK_LABEL(app->history_loading_label), 0.0F);
    gtk_widget_set_visible(app->history_loading_label, FALSE);
    gtk_box_append(GTK_BOX(history_box), app->history_loading_label);

    app->history_empty_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget* history_empty_title = gtk_label_new("暂无任务记录");
    gtk_label_set_xalign(GTK_LABEL(history_empty_title), 0.0F);
    app->history_empty_hint_label = gtk_label_new("引擎未连接。");
    gtk_label_set_xalign(GTK_LABEL(app->history_empty_hint_label), 0.0F);
    gtk_widget_add_css_class(app->history_empty_hint_label, "dim-label");
    gtk_box_append(GTK_BOX(app->history_empty_box), history_empty_title);
    gtk_box_append(GTK_BOX(app->history_empty_box), app->history_empty_hint_label);
    gtk_widget_set_visible(app->history_empty_box, FALSE);
    gtk_box_append(GTK_BOX(history_box), app->history_empty_box);

    app->history_scroller = gtk_scrolled_window_new();
    gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(app->history_scroller),
                                   GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
    gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(app->history_scroller),
                                               260);
    app->history_list = gtk_list_box_new();
    gtk_list_box_set_selection_mode(GTK_LIST_BOX(app->history_list), GTK_SELECTION_NONE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(app->history_scroller),
                                  app->history_list);
    gtk_widget_set_visible(app->history_scroller, FALSE);
    gtk_box_append(GTK_BOX(history_box), app->history_scroller);

    app->history_issues_label = gtk_label_new("");
    gtk_label_set_xalign(GTK_LABEL(app->history_issues_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(app->history_issues_label), TRUE);
    gtk_widget_add_css_class(app->history_issues_label, "warning");
    gtk_widget_set_visible(app->history_issues_label, FALSE);
    gtk_box_append(GTK_BOX(history_box), app->history_issues_label);

    app->history_events_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget* events_header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    app->history_events_title_label = gtk_label_new("");
    gtk_label_set_xalign(GTK_LABEL(app->history_events_title_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(app->history_events_title_label), TRUE);
    gtk_widget_set_hexpand(app->history_events_title_label, TRUE);
    GtkWidget* events_close_button = gtk_button_new_with_label("关闭");
    gtk_widget_set_tooltip_text(events_close_button, "关闭任务事件");
    g_signal_connect(events_close_button, "clicked",
                     G_CALLBACK(on_history_events_close_clicked), app);
    gtk_box_append(GTK_BOX(events_header), app->history_events_title_label);
    gtk_box_append(GTK_BOX(events_header), events_close_button);
    gtk_box_append(GTK_BOX(app->history_events_box), events_header);
    app->history_events_truncated_label = gtk_label_new("较早事件已超出保留上限。");
    gtk_label_set_xalign(GTK_LABEL(app->history_events_truncated_label), 0.0F);
    gtk_widget_add_css_class(app->history_events_truncated_label, "dim-label");
    gtk_widget_set_visible(app->history_events_truncated_label, FALSE);
    gtk_box_append(GTK_BOX(app->history_events_box), app->history_events_truncated_label);
    app->history_events_error_label = gtk_label_new("");
    gtk_label_set_xalign(GTK_LABEL(app->history_events_error_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(app->history_events_error_label), TRUE);
    gtk_widget_add_css_class(app->history_events_error_label, "error");
    gtk_widget_set_visible(app->history_events_error_label, FALSE);
    gtk_box_append(GTK_BOX(app->history_events_box), app->history_events_error_label);
    app->history_events_loading_label = gtk_label_new("正在读取任务事件…");
    gtk_label_set_xalign(GTK_LABEL(app->history_events_loading_label), 0.0F);
    gtk_widget_set_visible(app->history_events_loading_label, FALSE);
    gtk_box_append(GTK_BOX(app->history_events_box), app->history_events_loading_label);
    app->history_events_scroller = gtk_scrolled_window_new();
    gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(app->history_events_scroller),
                                   GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
    gtk_scrolled_window_set_min_content_height(
        GTK_SCROLLED_WINDOW(app->history_events_scroller), 180);
    app->history_events_list = gtk_list_box_new();
    gtk_list_box_set_selection_mode(GTK_LIST_BOX(app->history_events_list),
                                    GTK_SELECTION_NONE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(app->history_events_scroller),
                                  app->history_events_list);
    gtk_widget_set_visible(app->history_events_scroller, FALSE);
    gtk_box_append(GTK_BOX(app->history_events_box), app->history_events_scroller);
    gtk_widget_set_visible(app->history_events_box, FALSE);
    gtk_box_append(GTK_BOX(history_box), app->history_events_box);

    gtk_expander_set_child(GTK_EXPANDER(history_expander), history_box);
    gtk_box_append(GTK_BOX(root), history_expander);
    g_signal_connect(history_expander, "notify::expanded",
                     G_CALLBACK(on_history_expander_changed), app);

    GtkWidget* result_model_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget* result_model_label = gtk_label_new("结果模型");
    gtk_label_set_xalign(GTK_LABEL(result_model_label), 0.0F);
    const char* result_models[] = {"gfs", "ec", nullptr};
    GtkStringList* result_model_list = gtk_string_list_new(result_models);
    app->result_model_dropdown =
        gtk_drop_down_new(G_LIST_MODEL(result_model_list), nullptr);
    g_object_unref(result_model_list);
    gtk_drop_down_set_selected(GTK_DROP_DOWN(app->result_model_dropdown), 0);
    gtk_box_append(GTK_BOX(result_model_row), result_model_label);
    gtk_box_append(GTK_BOX(result_model_row), app->result_model_dropdown);
    gtk_box_append(GTK_BOX(root), result_model_row);
    g_signal_connect(app->result_model_dropdown, "notify::selected",
                     G_CALLBACK(on_result_model_changed), app);

    app->status_label = gtk_label_new("Connect the Rust engine to begin.");
    gtk_label_set_xalign(GTK_LABEL(app->status_label), 0.0F);
    gtk_label_set_wrap(GTK_LABEL(app->status_label), TRUE);
    gtk_box_append(GTK_BOX(root), app->status_label);

    app->progress_bar = gtk_progress_bar_new();
    gtk_widget_set_visible(app->progress_bar, FALSE);
    gtk_box_append(GTK_BOX(root), app->progress_bar);

    GtkWidget* scroller = gtk_scrolled_window_new();
    gtk_widget_set_vexpand(scroller, TRUE);
    app->output_view = gtk_text_view_new();
    gtk_text_view_set_editable(GTK_TEXT_VIEW(app->output_view), FALSE);
    gtk_text_view_set_monospace(GTK_TEXT_VIEW(app->output_view), TRUE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroller), app->output_view);
    gtk_box_append(GTK_BOX(root), scroller);

    g_signal_connect(app->connect_button, "clicked", G_CALLBACK(on_connect_clicked), app);
    g_signal_connect(app->mode_dropdown, "notify::selected", G_CALLBACK(on_mode_changed), app);
    g_signal_connect(app->forecast_button, "clicked", G_CALLBACK(on_forecast_clicked), app);
    g_signal_connect(app->cancel_button, "clicked", G_CALLBACK(on_cancel_clicked), app);
    g_signal_connect(
        app->retention_reload_button, "clicked",
        G_CALLBACK(on_retention_reload_clicked), app);
    g_signal_connect(
        app->retention_save_button, "clicked",
        G_CALLBACK(on_retention_save_clicked), app);
    g_signal_connect(
        app->profile_query_button, "clicked",
        G_CALLBACK(on_profile_query_clicked), app);

    gtk_widget_set_sensitive(app->forecast_button, FALSE);
    gtk_widget_set_sensitive(app->cancel_button, FALSE);
    gtk_widget_set_sensitive(app->retention_reload_button, FALSE);
    gtk_widget_set_sensitive(app->retention_save_button, FALSE);
    gtk_widget_set_sensitive(app->profile_query_button, FALSE);
    gtk_widget_set_sensitive(app->history_refresh_button, FALSE);
    gtk_widget_set_sensitive(app->result_model_dropdown, FALSE);
    gtk_window_present(GTK_WINDOW(app->window));
}

} // namespace

int main(int argc, char** argv) {
    GtkApplication* application = gtk_application_new(
        "app.firesky.native", G_APPLICATION_DEFAULT_FLAGS);
    g_signal_connect(application, "activate", G_CALLBACK(on_activate), nullptr);
    const int status = g_application_run(G_APPLICATION(application), argc, argv);
    g_object_unref(application);
    return status;
}
