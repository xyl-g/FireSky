#include "firesky_ffi_client.hpp"

#include <gtk/gtk.h>

#include <algorithm>
#include <atomic>
#include <cmath>
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
    GtkWidget* check_button = nullptr;
    GtkWidget* prepare_button = nullptr;
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
    std::unique_ptr<firesky::Engine> engine;
    std::string session;
    bool ready = false;
    std::atomic<bool> closing = false;
    std::mutex worker_mutex;
    std::vector<std::thread> workers;
    std::mutex task_mutex;
    std::shared_ptr<firesky::Task> active_task;
};

struct UiCall {
    GtkApp* app;
    std::function<void()> function;
};

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
    gtk_widget_set_sensitive(app->connect_button, !busy);
    gtk_widget_set_sensitive(app->mode_dropdown, !busy);
    gtk_widget_set_sensitive(app->check_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(app->prepare_button, !busy && !app->session.empty() && !app->ready);
    gtk_widget_set_sensitive(app->forecast_button, !busy && !app->session.empty() && app->ready);
    gtk_widget_set_sensitive(
        app->retention_reload_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(
        app->retention_save_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(
        app->profile_query_button, !busy && app->engine != nullptr);
    gtk_widget_set_sensitive(app->cancel_button, busy);
    gtk_widget_set_visible(app->progress_bar, busy);
    if (!busy) {
        gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(app->progress_bar), 0.0);
    }
    gtk_progress_bar_set_pulse_step(GTK_PROGRESS_BAR(app->progress_bar), 0.05);
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

std::string selected_mode(GtkApp* app) {
    GObject* selected = gtk_drop_down_get_selected_item(GTK_DROP_DOWN(app->mode_dropdown));
    if (selected == nullptr) {
        return "point";
    }
    return gtk_string_object_get_string(GTK_STRING_OBJECT(selected));
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

std::string trim_copy(const std::string& value) {
    const auto first = value.find_first_not_of(" \t\n\r");
    if (first == std::string::npos) {
        return {};
    }
    const auto last = value.find_last_not_of(" \t\n\r");
    return value.substr(first, last - first + 1);
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
    GObject* selected =
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

void launch_request(GtkApp* app, const std::string& method,
                    const std::string& params, RequestFinished finished) {
    if (app->engine == nullptr) {
        set_status(app, "Connect the Rust engine first.");
        return;
    }
    try {
        auto task = std::make_shared<firesky::Task>(app->engine->start(
            method, params,
            [app](const std::string& event) {
                const std::string message =
                    json_string_field(event, "message").value_or(event);
                const double percent = std::clamp(
                    json_number_field(event, "percent").value_or(0.0), 0.0, 100.0);
                post_to_ui(app, [app, message, percent] {
                    set_status(app, message);
                    gtk_progress_bar_set_fraction(
                        GTK_PROGRESS_BAR(app->progress_bar), percent / 100.0);
                });
            }));
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
    } catch (const std::exception& error) {
        set_status(app, error.what());
        set_busy(app, false);
    }
}

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

void on_check_clicked(GtkButton*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    const std::string mode = selected_mode(app);
    std::string error;
    const auto payload = build_payload(app, mode, error);
    if (!payload) {
        set_status(app, error);
        return;
    }
    app->session.clear();
    app->ready = false;
    set_status(app, "Checking data completeness...");
    launch_request(app, "check", "{\"kind\":\"" + mode + "\",\"payload\":" + *payload + "}",
                   [app](const std::string& response, const std::string& failure) {
                       if (!failure.empty()) {
                           set_status(app, failure);
                           set_busy(app, false);
                           return;
                       }
                       app->session = json_string_field(response, "session").value_or("");
                       app->ready = json_bool_field(response, "ready");
                       set_output(app, response);
                       set_status(app, app->ready
                                             ? "Inputs are complete. Forecast is ready to confirm."
                                             : "Inputs are incomplete. Prepare missing data, then forecast.");
                       set_busy(app, false);
                       if (!app->session.empty()) {
                           launch_request(
                               app, "publications",
                               "{\"session\":\"" + json_escape(app->session) + "\"}",
                               [app](const std::string& publication,
                                     const std::string& publication_error) {
                                   if (publication_error.empty()) {
                                       app->ready = json_bool_field(publication, "ready");
                                       set_output(app, publication);
                                       set_status(app, app->ready
                                                                 ? "Inputs are complete. Forecast is ready to confirm."
                                                                 : "Inputs are incomplete. Prepare missing data, then forecast.");
                                   }
                                   set_busy(app, false);
                               });
                       }
                   });
}

void on_prepare_clicked(GtkButton*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (app->session.empty()) {
        set_status(app, "Run the completeness check first.");
        return;
    }
    set_status(app, "Preparing missing inputs...");
    launch_request(
        app, "prepare",
        "{\"session\":\"" + json_escape(app->session) + "\"}",
        [app](const std::string& response, const std::string& failure) {
            if (!failure.empty()) {
                set_status(app, failure);
                set_busy(app, false);
                return;
            }
            app->ready = json_bool_field(response, "ready");
            set_output(app, response);
            set_status(app, app->ready ? "Inputs are ready. Confirm forecast."
                                       : "Preparation finished, but inputs are still incomplete.");
            set_busy(app, false);
        });
}

void on_forecast_clicked(GtkButton*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    if (app->session.empty() || !app->ready) {
        set_status(app, "Complete the preflight and preparation first.");
        return;
    }
    set_status(app, "Running forecast...");
    launch_request(
        app, "forecast",
        "{\"session\":\"" + json_escape(app->session) + "\",\"confirmed\":true}",
        [app](const std::string& response, const std::string& failure) {
            if (!failure.empty()) {
                set_status(app, failure);
                set_busy(app, false);
                return;
            }
            set_output(app, response);
            show_city_ranking(app, response);
            app->session.clear();
            app->ready = false;
            set_status(app, "Forecast complete.");
            set_busy(app, false);
        });
}

void on_cancel_clicked(GtkButton*, gpointer data) {
    auto* app = static_cast<GtkApp*>(data);
    std::shared_ptr<firesky::Task> task;
    {
        std::lock_guard lock(app->task_mutex);
        task = app->active_task;
    }
    if (!task) {
        return;
    }
    try {
        task->cancel();
        set_status(app, "Cancellation requested...");
    } catch (const firesky::FfiError& error) {
        set_status(app, error.what());
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

GtkWidget* make_entry(const char* value, const char* placeholder) {
    GtkWidget* entry = gtk_entry_new();
    gtk_editable_set_text(GTK_EDITABLE(entry), value);
    gtk_entry_set_placeholder_text(GTK_ENTRY(entry), placeholder);
    gtk_widget_set_hexpand(entry, TRUE);
    return entry;
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
    if (!app->session.empty() && app->engine) {
        try {
            app->engine->call("dismiss", "{\"session\":\"" + json_escape(app->session) + "\"}");
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
    app->check_button = gtk_button_new_with_label("Check data");
    app->prepare_button = gtk_button_new_with_label("Prepare missing");
    app->forecast_button = gtk_button_new_with_label("Forecast");
    app->cancel_button = gtk_button_new_with_label("Cancel");
    gtk_box_append(GTK_BOX(actions), app->connect_button);
    gtk_box_append(GTK_BOX(actions), app->check_button);
    gtk_box_append(GTK_BOX(actions), app->prepare_button);
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
    g_signal_connect(app->check_button, "clicked", G_CALLBACK(on_check_clicked), app);
    g_signal_connect(app->prepare_button, "clicked", G_CALLBACK(on_prepare_clicked), app);
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

    gtk_widget_set_sensitive(app->check_button, FALSE);
    gtk_widget_set_sensitive(app->prepare_button, FALSE);
    gtk_widget_set_sensitive(app->forecast_button, FALSE);
    gtk_widget_set_sensitive(app->cancel_button, FALSE);
    gtk_widget_set_sensitive(app->retention_reload_button, FALSE);
    gtk_widget_set_sensitive(app->retention_save_button, FALSE);
    gtk_widget_set_sensitive(app->profile_query_button, FALSE);
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
