// MM0 spike: per-target eccodes link strategy.
// host (macOS)  -> brew dylib
// android       -> MM0_ECCODES_ANDROID_LIB dir, static libeccodes.a
// ios           -> MM0_ECCODES_IOS_LIB (device) / MM0_ECCODES_IOSSIM_LIB (sim)
use std::env;

fn main() {
    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap();
    match target_os.as_str() {
        "macos" => {
            println!("cargo:rustc-link-search=native=/opt/homebrew/lib");
            println!("cargo:rustc-link-lib=dylib=eccodes");
        }
        "android" => {
            let dir = env::var("MM0_ECCODES_ANDROID_LIB")
                .expect("set MM0_ECCODES_ANDROID_LIB to the dir containing libeccodes.a");
            println!("cargo:rustc-link-search=native={dir}");
            println!("cargo:rustc-link-lib=static=eccodes");
            println!("cargo:rustc-link-lib=dylib=m");
        }
        "ios" => {
            let sim = env::var("CARGO_CFG_TARGET_ABI").unwrap_or_default() == "sim"
                || env::var("MM0_IOS_SIM").map(|v| v == "1").unwrap_or(false);
            let dir = env::var(if sim { "MM0_ECCODES_IOSSIM_LIB" } else { "MM0_ECCODES_IOS_LIB" })
                .expect("set MM0_ECCODES_IOS_LIB / MM0_ECCODES_IOSSIM_LIB to dirs containing libeccodes.a");
            println!("cargo:rustc-link-search=native={dir}");
            println!("cargo:rustc-link-lib=static=eccodes");
        }
        other => panic!("MM0 spike: unsupported target_os {other}"),
    }
}
