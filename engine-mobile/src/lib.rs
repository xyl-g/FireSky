//! engine-mobile — 桌面 JSON-lines 服务的进程内化（iOS/Android 厚客户端宿主）。
//!
//! 帧协议与桌面 stdio 服务逐帧一致（request / result / error / progress /
//! cancel）；方法分发复用 `firesky_engine::rpc` 的方法臂；编排语义（progress
//! 三链、协作取消、会话令牌 1800s/单次/≤32、compute_lock 单飞）移植自
//! `src/firesky/desktop_rpc.py`。C 契约见 `include/fsmobile.h`。

mod backend;
mod ffi;
mod pipe;
mod service;

pub use backend::{apply_publications, DesktopBackend, EngineArmBackend};
pub use ffi::{fsm_cancel, fsm_last_error, fsm_send, fsm_start, fsm_stop};
pub use pipe::{channel, PipeReader, PipeWriter};
pub use service::{
    start, Cancelled, FrameSink, MobileConfig, OpError, RawProgress, Started, MAX_LINE_BYTES,
    PROTOCOL_VERSION, SESSION_SECONDS,
};
