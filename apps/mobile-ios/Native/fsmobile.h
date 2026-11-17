/*
 * FireSky engine-mobile C ABI（固定契约）。
 *
 * 取代此前 apps/ 骨架占位用的 handle 型 fsmobile_* 草案（2026-11-12 裁定）：
 * 服务为单实例（一个 App 一个引擎，桌面 desktop_rpc.serve 同语义），不透传句柄。
 * 桌面 JSON-lines 服务进程内化：帧协议与桌面 stdio 服务逐帧一致
 * （request / result / error / progress / cancel），帧为 UTF-8 单行 JSON、
 * 上限 1 MiB。stdio 契约中的换行分隔符在进程内省略：回调收到 NUL 结尾的
 * 单行 JSON（不含换行），fsm_send 接受不含换行的单行 JSON（容忍一个结尾换行）。
 *
 * 线程模型：fsm_start 启动后台线程；帧回调可能从工作线程触发，但同一时刻
 * 最多一个回调在执行（内部串行锁，与桌面 serve 的 write_lock 等价）——宿主
 * 回调不得假定与 fsm_start 同线程。
 *
 * 约束：不得在帧回调内调用 fsm_stop()；单实例（重复 fsm_start 报错）。
 */
#ifndef FSMOBILE_H
#define FSMOBILE_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 引擎下行帧回调（on_frame 原语）：result / error / progress 帧，单行 UTF-8
 * JSON，NUL 结尾、不含换行；由引擎侧分配、回调返回后即失效，需保存请复制。 */
typedef void (*fsm_frame_cb)(const char *frame, void *userdata);

/*
 * 启动进程内 JSON-lines 服务。
 *
 * workspace_root    UTF-8 路径，工作区根（桌面语义下的 cwd）；不得为空。
 * definitions_path  UTF-8 路径，ecCodes definitions 资源目录；NULL/空则不注入。
 *                   非空时先注入单数 ECCODES_DEFINITION_PATH 环境变量再初始化
 *                   引擎（MM0 坑 10）。
 * cache_root        UTF-8 路径，gfs/ecmwf/cams 缓存根；NULL/空则用引擎默认。
 * callback          帧回调，不得为 NULL。
 * userdata          透传给回调。
 *
 * 返回 0 成功；-1 失败（fsm_last_error 可取原因）。
 */
int fsm_start(const char *workspace_root,
              const char *definitions_path,
              const char *cache_root,
              fsm_frame_cb callback,
              void *userdata);

/*
 * 发送一帧请求：UTF-8 单行 JSON（等价桌面 stdin 行），≤1 MiB。
 * 超限帧由服务循环处理（回 invalid_request 错误帧并丢弃整行）。
 * 返回 0 成功；-1 失败（未启动/已停止/非 UTF-8/含内嵌换行）。
 */
int fsm_send(const char *line);

/*
 * 协作取消：对进行中的 request_id 置取消标志
 * （等价发送 {"method":"cancel","params":{"request_id"}}）。
 * 返回 0=已置位；1=无此进行中请求；-1=未启动或参数错误。
 */
int fsm_cancel(const char *request_id);

/*
 * 停止并回收线程：关闭输入侧（等价桌面 EOF），取消全部在途请求，等待
 * 进行中与已排队任务收尾后返回。幂等；重复调用返回 0。
 */
int fsm_stop(void);

/*
 * 本线程最近一次 fsm_* 失败原因（UTF-8，NUL 结尾）；无错误返回 NULL。
 * 指针在本线程下一次 fsm_* 调用前有效。
 */
const char *fsm_last_error(void);

#ifdef __cplusplus
}
#endif

#endif /* FSMOBILE_H */
