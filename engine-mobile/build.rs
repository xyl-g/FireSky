//! ecCodes link strategy per the MM0 spike (spikes/mm0-eccodes-cross/README.md).
//!
//! host (macOS/Linux/Windows): engine/src/grib.rs resolves ecCodes at runtime
//! via libloading (desktop parity) — nothing is linked at build time.
//! android/ios: MN1 landed — engine/src/grib.rs binds the ecCodes C API
//! directly (cfg mobile_decoder), so the static lib built by the spike
//! scripts is REQUIRED: `MM0_ECCODES_ANDROID_LIB` / `MM0_ECCODES_IOS_LIB` /
//! `MM0_ECCODES_IOSSIM_LIB` must point at its directory. engine/build.rs
//! emits the same wiring for the engine's own cdylib/staticlib link; the
//! duplicate native static lib deduplicates at the final artifact link.

fn main() {
    for key in [
        "MM0_ECCODES_ANDROID_LIB",
        "MM0_ECCODES_IOS_LIB",
        "MM0_ECCODES_IOSSIM_LIB",
        "MM0_IOS_SIM",
    ] {
        println!("cargo:rerun-if-env-changed={key}");
    }
    let os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let dir = match os.as_str() {
        "macos" | "windows" | "linux" => return,
        "android" => std::env::var_os("MM0_ECCODES_ANDROID_LIB"),
        "ios" => {
            let sim = std::env::var("CARGO_CFG_TARGET_ABI")
                .map(|abi| abi == "sim")
                .unwrap_or(false)
                || std::env::var("MM0_IOS_SIM").map(|v| v == "1").unwrap_or(false);
            let key = if sim {
                "MM0_ECCODES_IOSSIM_LIB"
            } else {
                "MM0_ECCODES_IOS_LIB"
            };
            std::env::var_os(key)
        }
        other => panic!("engine-mobile: unsupported target_os {other}"),
    };
    let Some(dir) = dir else { return };
    println!("cargo:rustc-link-search=native={}", dir.to_string_lossy());
    println!("cargo:rustc-link-lib=static=eccodes");
    // ENABLE_AEC=ON ecCodes needs libaec.a from the same directory.
    println!("cargo:rustc-link-lib=static=aec");
    if os == "android" {
        println!("cargo:rustc-link-lib=dylib=m");
    }
}
