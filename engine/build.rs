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
    let mut files = Vec::new();
    for path in [
        "src",
        "../native/src",
        "build.rs",
        "Cargo.toml",
        "Cargo.lock",
        "../native/Cargo.toml",
        "../native/Cargo.lock",
    ] {
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
