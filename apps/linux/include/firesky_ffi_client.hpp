#ifndef FIRESKY_LINUX_FFI_CLIENT_HPP
#define FIRESKY_LINUX_FFI_CLIENT_HPP

#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>

namespace firesky {

struct EngineState;

class FfiError final : public std::runtime_error {
public:
    FfiError(std::int32_t code, std::string envelope);

    [[nodiscard]] std::int32_t code() const noexcept { return code_; }
    [[nodiscard]] const std::string& envelope() const noexcept { return envelope_; }

private:
    std::int32_t code_;
    std::string envelope_;
};

class Engine;

class Task final {
public:
    Task(Task&& other) noexcept;
    Task& operator=(Task&& other) noexcept;
    Task(const Task&) = delete;
    Task& operator=(const Task&) = delete;
    ~Task();

    // Waits for completion, releases the native task handle and returns the
    // versioned JSON envelope. A failed task still releases its handle before
    // throwing FfiError.
    [[nodiscard]] std::string join();

    // Requests cooperative cancellation. The worker remains alive until join
    // or destruction observes its terminal state.
    void cancel();

    [[nodiscard]] bool valid() const noexcept;

private:
    struct State;
    Task(std::shared_ptr<State> state, void* task);
    void reset_noexcept() noexcept;

    std::shared_ptr<State> state_;
    void* task_ = nullptr;

    friend class Engine;
};

class Engine final {
public:
    // Loads libfiresky_engine.so (or another compatible library) with dlopen
    // and opens a Rust runtime for workspace.
    Engine(const std::string& library_path, const std::string& workspace);
    ~Engine();

    Engine(Engine&&) noexcept;
    Engine& operator=(Engine&&) noexcept;
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    // params_json may be empty, in which case Rust receives an empty params
    // object. The returned value is the raw versioned JSON envelope.
    [[nodiscard]] std::string call(const std::string& method,
                                   const std::string& params_json = "{}") const;

    // Starts a cancellable operation. Progress callbacks run on Rust worker
    // threads; a native UI must marshal callback work to its main loop.
    [[nodiscard]] Task start(
        const std::string& method,
        const std::string& params_json = "{}",
        std::function<void(const std::string&)> progress = {}) const;

    [[nodiscard]] bool valid() const noexcept { return static_cast<bool>(state_); }

private:
    std::shared_ptr<EngineState> state_;
};

} // namespace firesky

#endif

