//! ecCodes link strategy per the MM0 spike (spikes/mm0-eccodes-cross/README.md).
//!
//! host (macOS/Linux/Windows): engine/src/grib.rs resolves ecCodes at runtime
//! via libloading (desktop parity) — nothing is linked at build time.
//! android/ios: when `MM0_ECCODES_ANDROID_LIB` / `MM0_ECCODES_IOS_LIB` /
//! `MM0_ECCODES_IOSSIM_LIB` points at the static lib built by the spike
//! scripts, embed `libeccodes.a` so the symbols are available for the MN1
//! static-link diversion (pitfall: runtime libloading is unavailable on
//! mobile). Without the variable the build stays green; on-device decode
//! requires the diversion planned for MN1.

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
    if os == "android" {
        println!("cargo:rustc-link-lib=dylib=m");
    }
}
