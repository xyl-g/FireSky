#include "firesky_ffi_client.hpp"

#include <dlfcn.h>

#include <condition_variable>
#include <cstdio>
#include <mutex>
#include <utility>

#include <firesky_ffi.h>

namespace firesky {
namespace {

using AbiVersion = std::uint32_t (*)();
using EngineOpen = std::int32_t (*)(const char*, FireskyEngineHandle**, char**);
using EngineFree = void (*)(FireskyEngineHandle*);
using EngineCall = std::int32_t (*)(FireskyEngineHandle*, const char*, const char*,
                                     char**, char**);
using EngineStart = std::int32_t (*)(FireskyEngineHandle*, const char*, const char*,
                                      FireskyProgressCallback, void*, FireskyTaskHandle**,
                                      char**);
using TaskCancel = std::int32_t (*)(FireskyTaskHandle*, char**);
using TaskJoin = std::int32_t (*)(FireskyTaskHandle*, char**, char**);
using TaskFree = void (*)(FireskyTaskHandle*);
using StringFree = void (*)(char*);

std::string take_string(char* value, StringFree release) {
    if (value == nullptr) {
        return {};
    }
    std::string copy(value);
    release(value);
    return copy;
}

std::string loader_error(const char* operation, const char* detail = nullptr) {
    std::string message(operation);
    if (detail != nullptr && *detail != '\0') {
        message += ": ";
        message += detail;
    }
    return message;
}

template <typename Function>
Function load_symbol(void* library, const char* name) {
    (void)::dlerror();
    void* symbol = ::dlsym(library, name);
    if (symbol == nullptr) {
        const char* error = ::dlerror();
        throw FfiError(-1, loader_error("missing Rust FFI symbol", error).append(" (").append(name).append(")"));
    }
    return reinterpret_cast<Function>(symbol);
}

struct ProgressState {
    mutable std::mutex mutex;
    std::function<void(const std::string&)> callback;
    bool closed = false;
};

void progress_trampoline(const char* event_json, void* user_data) noexcept {
    auto* state = static_cast<ProgressState*>(user_data);
    if (state == nullptr) {
        return;
    }

    std::function<void(const std::string&)> callback;
    {
        std::lock_guard lock(state->mutex);
        if (state->closed) {
            return;
        }
        callback = state->callback;
    }
    if (!callback) {
        return;
    }

    // Rust owns event_json only for the callback duration. Copy it before
    // invoking user code, which may queue work onto another thread.
    const std::string event = event_json == nullptr ? std::string{} : event_json;
    try {
        callback(event);
    } catch (...) {
        // Exceptions must never cross the C ABI callback boundary.
    }
}

} // namespace

struct EngineState {
    void* library = nullptr;
    AbiVersion abi_version = nullptr;
    EngineOpen engine_open = nullptr;
    EngineFree engine_free = nullptr;
    EngineCall engine_call = nullptr;
    EngineStart engine_start = nullptr;
    TaskCancel task_cancel = nullptr;
    TaskJoin task_join = nullptr;
    TaskFree task_free = nullptr;
    StringFree string_free = nullptr;
    FireskyEngineHandle* runtime = nullptr;

    ~EngineState() {
        if (runtime != nullptr && engine_free != nullptr) {
            engine_free(runtime);
            runtime = nullptr;
        }
        if (library != nullptr) {
            ::dlclose(library);
            library = nullptr;
        }
    }
};

struct Task::State {
    std::shared_ptr<EngineState> engine;
    std::shared_ptr<ProgressState> progress;
    mutable std::mutex mutex;
    std::condition_variable condition;
    std::size_t cancels_in_flight = 0;
    bool joining = false;
    bool joined = false;
};

FfiError::FfiError(std::int32_t code, std::string envelope)
    : std::runtime_error(envelope.empty() ? "Rust FFI operation failed" : envelope),
      code_(code),
      envelope_(std::move(envelope)) {}

Task::Task(std::shared_ptr<State> state, void* task)
    : state_(std::move(state)), task_(task) {}

Task::Task(Task&& other) noexcept
    : state_(std::move(other.state_)), task_(std::exchange(other.task_, nullptr)) {}

Task& Task::operator=(Task&& other) noexcept {
    if (this == &other) {
        return *this;
    }
    reset_noexcept();
    state_ = std::move(other.state_);
    task_ = std::exchange(other.task_, nullptr);
    return *this;
}

Task::~Task() {
    reset_noexcept();
}

bool Task::valid() const noexcept {
    if (state_ == nullptr) {
        return false;
    }
    std::lock_guard lock(state_->mutex);
    return task_ != nullptr && !state_->joined;
}

void Task::reset_noexcept() noexcept {
    if (state_ == nullptr) {
        state_.reset();
        task_ = nullptr;
        return;
    }

    try {
        cancel();
    } catch (...) {
    }
    try {
        (void)join();
    } catch (...) {
    }
    state_.reset();
    task_ = nullptr;
}

void Task::cancel() {
    if (state_ == nullptr) {
        throw FfiError(1, "Rust FFI task is empty");
    }
    FireskyTaskHandle* task = nullptr;
    {
        std::lock_guard lock(state_->mutex);
        if (task_ == nullptr || state_->joined) {
            return;
        }
        task = static_cast<FireskyTaskHandle*>(task_);
        ++state_->cancels_in_flight;
    }

    char* error = nullptr;
    std::int32_t code = FIRESKY_FFI_RUNTIME_ERROR;
    try {
        code = state_->engine->task_cancel(task, &error);
    } catch (...) {
        {
            std::lock_guard lock(state_->mutex);
            --state_->cancels_in_flight;
            state_->condition.notify_all();
        }
        throw;
    }
    const auto envelope = take_string(error, state_->engine->string_free);
    {
        std::lock_guard lock(state_->mutex);
        --state_->cancels_in_flight;
        state_->condition.notify_all();
    }
    if (code != FIRESKY_FFI_OK) {
        throw FfiError(code, envelope.empty() ? "Rust FFI task cancellation failed" : envelope);
    }
}

std::string Task::join() {
    if (state_ == nullptr) {
        throw FfiError(1, "Rust FFI task is empty");
    }
    FireskyTaskHandle* task = nullptr;
    {
        std::lock_guard lock(state_->mutex);
        if (task_ == nullptr || state_->joined || state_->joining) {
            throw FfiError(1, "Rust FFI task has already been joined");
        }
        task = static_cast<FireskyTaskHandle*>(task_);
        state_->joining = true;
    }

    char* response = nullptr;
    char* error = nullptr;
    const auto code = state_->engine->task_join(
        task, &response, &error);
    const auto response_text = take_string(response, state_->engine->string_free);
    const auto error_text_value = take_string(error, state_->engine->string_free);

    // task_join consumes the worker join handle but not the opaque allocation.
    {
        std::unique_lock lock(state_->mutex);
        task_ = nullptr;
        state_->joined = true;
        state_->joining = false;
        state_->condition.wait(lock, [this] { return state_->cancels_in_flight == 0; });
    }
    state_->engine->task_free(task);
    {
        std::lock_guard progress_lock(state_->progress->mutex);
        state_->progress->closed = true;
    }

    if (code != FIRESKY_FFI_OK) {
        throw FfiError(code, error_text_value);
    }
    return response_text;
}

Engine::Engine(const std::string& library_path, const std::string& workspace) {
    auto state = std::make_shared<EngineState>();
    state->library = ::dlopen(library_path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (state->library == nullptr) {
        throw FfiError(-1, loader_error("unable to load Rust FFI library", ::dlerror()));
    }

    try {
        state->abi_version = load_symbol<AbiVersion>(state->library, "firesky_ffi_abi_version");
        state->engine_open = load_symbol<EngineOpen>(state->library, "firesky_engine_open");
        state->engine_free = load_symbol<EngineFree>(state->library, "firesky_engine_free");
        state->engine_call = load_symbol<EngineCall>(state->library, "firesky_engine_call");
        state->engine_start = load_symbol<EngineStart>(state->library, "firesky_engine_start");
        state->task_cancel = load_symbol<TaskCancel>(state->library, "firesky_task_cancel");
        state->task_join = load_symbol<TaskJoin>(state->library, "firesky_task_join");
        state->task_free = load_symbol<TaskFree>(state->library, "firesky_task_free");
        state->string_free = load_symbol<StringFree>(state->library, "firesky_ffi_string_free");
        if (state->abi_version() != 1U) {
            throw FfiError(-1, "Rust FFI ABI version mismatch");
        }

        FireskyEngineHandle* runtime = nullptr;
        char* error = nullptr;
        const auto code = state->engine_open(workspace.c_str(), &runtime, &error);
        const auto envelope = take_string(error, state->string_free);
        if (code != FIRESKY_FFI_OK || runtime == nullptr) {
            throw FfiError(code, envelope.empty() ? "unable to open Rust runtime" : envelope);
        }
        state->runtime = runtime;
    } catch (...) {
        // shared_ptr cleanup closes the library and, when applicable, runtime.
        throw;
    }
    state_ = std::move(state);
}

Engine::~Engine() = default;

Engine::Engine(Engine&& other) noexcept
    : state_(std::move(other.state_)) {}

Engine& Engine::operator=(Engine&& other) noexcept {
    if (this != &other) {
        state_ = std::move(other.state_);
    }
    return *this;
}

std::string Engine::call(const std::string& method, const std::string& params_json) const {
    if (!state_) {
        throw FfiError(1, "Rust FFI engine is closed");
    }
    char* response = nullptr;
    char* error = nullptr;
    const auto code = state_->engine_call(
        state_->runtime, method.c_str(),
        params_json.empty() ? nullptr : params_json.c_str(), &response, &error);
    const auto response_text = take_string(response, state_->string_free);
    const auto error_text_value = take_string(error, state_->string_free);
    if (code != FIRESKY_FFI_OK) {
        throw FfiError(code, error_text_value);
    }
    return response_text;
}

Task Engine::start(const std::string& method, const std::string& params_json,
                   std::function<void(const std::string&)> progress) const {
    if (!state_) {
        throw FfiError(1, "Rust FFI engine is closed");
    }

    auto task_state = std::make_shared<Task::State>();
    task_state->engine = state_;
    task_state->progress = std::make_shared<ProgressState>();
    task_state->progress->callback = std::move(progress);

    FireskyTaskHandle* task = nullptr;
    char* error = nullptr;
    const auto code = state_->engine_start(
        state_->runtime, method.c_str(),
        params_json.empty() ? nullptr : params_json.c_str(),
        &progress_trampoline, task_state->progress.get(), &task, &error);
    const auto envelope = take_string(error, state_->string_free);
    if (code != FIRESKY_FFI_OK || task == nullptr) {
        {
            std::lock_guard lock(task_state->progress->mutex);
            task_state->progress->closed = true;
        }
        throw FfiError(code, envelope.empty() ? "unable to start Rust FFI task" : envelope);
    }
    return Task(std::move(task_state), task);
}

} // namespace firesky

