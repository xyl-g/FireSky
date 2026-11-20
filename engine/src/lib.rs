#![recursion_limit = "256"]

pub mod ads;
pub mod app_resources;
pub mod assessment;
pub mod astronomy;
mod cache_lock;
pub mod cache_retention;
pub mod cams;
mod cams_cache;
pub mod cams_data;
pub mod characteristics;
mod continents;
pub mod cross_section;
mod derived_cache;
pub mod desktop_context;
pub mod desktop_delete;
pub mod desktop_credentials;
pub mod desktop_hsd;
pub mod desktop_hsd_advection;
pub mod desktop_hsd_motion;
pub mod desktop_hsd_retrieval;
mod desktop_inventory;
pub mod desktop_location;
pub mod desktop_network;
pub mod desktop_nowcast;
pub mod desktop_nowcast_adjustment;
pub mod desktop_nowcast_assessment;
pub mod desktop_nowcast_prepare;
pub mod desktop_nowcast_prior;
pub mod desktop_nowcast_retrieval;
pub mod desktop_preflight;
pub mod desktop_prepare;
pub mod desktop_profiles;
pub mod desktop_publications;
pub mod desktop_runtime;
pub mod desktop_session;
pub mod desktop_sync;
pub mod diagnostics;
pub mod earth_engine_aod;
pub mod ecmwf;
pub mod ecmwf_data;
pub mod ecmwf_plan;
pub mod ecmwf_point;
pub mod ecmwf_service;
pub mod ffi;
pub mod frames;
pub mod gfs_data;
/// Shared HTTP client posture: ambient proxy by default, direct egress when
/// FIRESKY_NO_PROXY=1 (stalled system proxies must not block data downloads).
pub mod http_client {
    pub fn builder() -> reqwest::blocking::ClientBuilder {
        let builder = reqwest::blocking::Client::builder();
        if std::env::var_os("FIRESKY_NO_PROXY").is_some_and(|v| v == "1") {
            builder.no_proxy()
        } else {
            builder
        }
    }
}

/// Cancellation plumbing shared by the download providers.
pub mod cancel_scope {
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };

    /// Run `body` with an Arc cancel flag that mirrors the caller's borrowed
    /// flag every 100 ms. Providers hold the Arc for mid-transfer checks; the
    /// scoped mirror thread is joined before the borrow can end, so no unsafe
    /// lifetime extension is needed. The Arc is also set on normal completion
    /// to stop the mirror promptly.
    pub fn with_shared_cancel<T>(
        cancel: &AtomicBool,
        body: impl FnOnce(Arc<AtomicBool>) -> Result<T, String>,
    ) -> Result<T, String> {
        let shared = Arc::new(AtomicBool::new(cancel.load(Ordering::Acquire)));
        std::thread::scope(|scope| {
            let mirror_flag = shared.clone();
            let mirror = scope.spawn(move || loop {
                if cancel.load(Ordering::Acquire) {
                    mirror_flag.store(true, Ordering::Release);
                    return;
                }
                if mirror_flag.load(Ordering::Acquire) {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            });
            let result = body(shared.clone());
            shared.store(true, Ordering::Release);
            let _ = mirror.join();
            result
        })
    }
}
pub mod grib;
pub mod hsd_provider;
pub mod hsd_raw;
pub mod html_report;
pub mod http;
pub mod input_plan;
pub mod location;
pub mod map_frames;
/// Shared CPU optics/scoring core (merged from the former standalone
/// `firesky-native` crate; one workspace crate, one build).
pub mod native;
pub mod native_data;
pub mod nowcast_runtime;
pub mod point_core;
pub mod profile_diagnostics;
pub mod quality;
pub mod radar;
pub mod report_publication;
mod request_file;
mod response_cache;
pub mod result_snapshot;
pub mod retention_settings;
pub mod rpc;
pub mod spectral;
pub mod task_history;
pub mod tensor_bundle;
pub mod time_contract;
pub mod timeline;
mod timezone_lookup;
