//! engine-mobile — 原生桌面 JSON-lines 服务的进程内化（iOS/Android 厚客户端宿主）。
//!
//! 服务即桌面 `desktop` 子命令本身（`firesky_engine::desktop_runtime`），经
//! 内存 duplex 承载而非 stdin/stdout：帧协议、编排、会话、协作取消与任务
//! 历史与 macOS 原生基线逐帧同源。本 crate 仅附加 C ABI 单实例契约
//! （include/fsmobile.h）、换行→回调的帧适配与同步取消镜像。

mod ffi;
mod pipe;
mod service;

pub use ffi::{fsm_cancel, fsm_last_error, fsm_send, fsm_start, fsm_stop};
pub use pipe::{channel, PipeReader, PipeWriter};
pub use service::{start, FrameSink, Started};
