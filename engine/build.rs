use sha2::{Digest, Sha256};
use std::{env, fs, path::Path, process::Command};

fn collect(path: &Path, files: &mut Vec<std::path::PathBuf>) {
    if path.is_dir() {
        println!("cargo:rerun-if-changed={}", path.display());
        for entry in fs::read_dir(path).expect("read source directory") {
            collect(&entry.expect("source entry").path(), files);
        }
    } else if path.extension().is_some_and(|v| v == "rs" || v == "css")
        || path
            .file_name()
            .is_some_and(|v| v == "Cargo.toml" || v == "Cargo.lock")
    {
        files.push(path.to_owned());
    }
}

fn main() {
    // MN1 mobile static diversion: iOS/Android embed ecCodes statically
    // (engine-mobile/build.rs links libeccodes.a); grib.rs then binds the C
    // symbols directly because runtime libloading is unavailable on mobile.
    println!("cargo:rustc-check-cfg=cfg(mobile_decoder)");
    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let mobile = matches!(target_os.as_str(), "ios" | "android");
    if mobile {
        println!("cargo:rustc-cfg=mobile_decoder");
    }
    // The engine itself ships cdylib/staticlib crate types, so its own link
    // step must resolve the static ecCodes symbols on mobile (P-2 full engine
    // build). engine-mobile/build.rs repeats the wiring for the final app
    // artifact; native static libraries deduplicate at the final link.
    for key in [
        "MM0_ECCODES_ANDROID_LIB",
        "MM0_ECCODES_IOS_LIB",
        "MM0_ECCODES_IOSSIM_LIB",
        "MM0_IOS_SIM",
    ] {
        println!("cargo:rerun-if-env-changed={key}");
    }
    if mobile {
        let dir = match target_os.as_str() {
            "android" => env::var_os("MM0_ECCODES_ANDROID_LIB"),
            "ios" => {
                let sim = env::var("CARGO_CFG_TARGET_ABI")
                    .map(|abi| abi == "sim")
                    .unwrap_or(false)
                    || env::var("MM0_IOS_SIM").map(|v| v == "1").unwrap_or(false);
                let key = if sim {
                    "MM0_ECCODES_IOSSIM_LIB"
                } else {
                    "MM0_ECCODES_IOS_LIB"
                };
                env::var_os(key)
            }
            _ => None,
        };
        match dir {
            Some(dir) => {
                println!("cargo:rustc-link-search=native={}", dir.to_string_lossy());
                println!("cargo:rustc-link-lib=static=eccodes");
                // ecCodes is built with ENABLE_AEC=ON (ECMWF GRIB2 CCSDS);
                // libaec.a ships next to libeccodes.a (build_libaec.sh).
                println!("cargo:rustc-link-lib=static=aec");
                if target_os == "android" {
                    println!("cargo:rustc-link-lib=dylib=m");
                }
            }
            None => panic!(
                "engine: mobile target {target_os} requires the MM0 static ecCodes library; \
                 set MM0_ECCODES_ANDROID_LIB / MM0_ECCODES_IOS_LIB / MM0_ECCODES_IOSSIM_LIB \
                 (scripts/eccodes-cross)"
            ),
        }
    }
    let mut files = Vec::new();
    for path in ["src", "build.rs", "Cargo.toml", "Cargo.lock"] {
        collect(Path::new(path), &mut files);
    }
    files.sort();
    let mut hash = Sha256::new();
    for path in files {
        println!("cargo:rerun-if-changed={}", path.display());
        let name = path.to_str().expect("UTF-8 source filename").as_bytes();
        let bytes = fs::read(&path).expect("read source file");
        hash.update((name.len() as u64).to_le_bytes());
        hash.update(name);
        hash.update((bytes.len() as u64).to_le_bytes());
        hash.update(bytes);
    }
    for name in ["TARGET", "OPT_LEVEL", "CARGO_ENCODED_RUSTFLAGS"] {
        println!("cargo:rerun-if-env-changed={name}");
        hash.update(name);
        hash.update(env::var(name).unwrap_or_default());
    }
    let compiler = Command::new(env::var_os("RUSTC").expect("RUSTC"))
        .arg("-vV")
        .output()
        .expect("compiler identity");
    assert!(compiler.status.success());
    hash.update(compiler.stdout);
    let identity: String = hash.finalize().iter().map(|b| format!("{b:02x}")).collect();
    println!("cargo:rustc-env=FIRESKY_ENGINE_SOURCE_ID={identity}");
}
