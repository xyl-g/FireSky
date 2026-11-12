#ifndef FIRESKY_FFI_H
#define FIRESKY_FFI_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct FireskyEngineHandle FireskyEngineHandle;
typedef struct FireskyTaskHandle FireskyTaskHandle;
typedef void (*FireskyProgressCallback)(const char *event_json, void *user_data);

enum {
    FIRESKY_FFI_OK = 0,
    FIRESKY_FFI_INVALID_ARGUMENT = 1,
    FIRESKY_FFI_INVALID_UTF8 = 2,
    FIRESKY_FFI_INVALID_JSON = 3,
    FIRESKY_FFI_RUNTIME_ERROR = 4,
    FIRESKY_FFI_CANCELLED = 5,
    FIRESKY_FFI_BUSY = 6,
    FIRESKY_FFI_PANIC = 255
};

uint32_t firesky_ffi_abi_version(void);

int32_t firesky_engine_open(const char *workspace,
                            FireskyEngineHandle **out_handle,
                            char **out_error);
void firesky_engine_free(FireskyEngineHandle *handle);

int32_t firesky_engine_call(FireskyEngineHandle *handle,
                            const char *method,
                            const char *params_json,
                            char **out_response,
                            char **out_error);

int32_t firesky_engine_start(FireskyEngineHandle *handle,
                             const char *method,
                             const char *params_json,
                             FireskyProgressCallback callback,
                             void *user_data,
                             FireskyTaskHandle **out_task,
                             char **out_error);
int32_t firesky_task_cancel(FireskyTaskHandle *task, char **out_error);
int32_t firesky_task_join(FireskyTaskHandle *task,
                          char **out_response,
                          char **out_error);
void firesky_task_free(FireskyTaskHandle *task);

void firesky_ffi_string_free(char *value);

#ifdef __cplusplus
}
#endif

#endif
