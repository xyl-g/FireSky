// MM0 spike: decode a fixed GRIB file via C ecCodes on cross-compiled targets.
// Usage: spike-decode <file.grib> [definitions_path]
// Prints one line per message with keys that are comparable against `grib_get`.
use std::env;

#[allow(non_camel_case_types)]
type size_t = usize;

#[repr(C)]
struct FILE_ {
    _private: [u8; 0],
}

#[repr(C)]
struct grib_handle {
    _private: [u8; 0],
}

unsafe extern "C" {
    fn codes_get_api_version() -> u32;
    fn codes_handle_new_from_file(
        c: *mut core::ffi::c_void,
        f: *mut FILE_,
        product: i32,
        err: *mut i32,
    ) -> *mut grib_handle;
    fn codes_handle_delete(h: *mut grib_handle) -> i32;
    fn codes_get_long(h: *mut grib_handle, name: *const core::ffi::c_char, v: *mut i64) -> i32;
    fn codes_get_string(h: *mut grib_handle, name: *const core::ffi::c_char, buf: *mut core::ffi::c_char, len: *mut size_t) -> i32;
    fn codes_get_size(h: *mut grib_handle, name: *const core::ffi::c_char, size: *mut size_t) -> i32;
    fn codes_get_double_array(
        h: *mut grib_handle,
        name: *const core::ffi::c_char,
        v: *mut f64,
        len: *mut size_t,
    ) -> i32;
}

const PRODUCT_GRIB: i32 = 1;

fn get_string(h: *mut grib_handle, key: &str) -> String {
    let ckey = std::ffi::CString::new(key).unwrap();
    let mut buf = vec![0 as core::ffi::c_char; 256];
    let mut len = buf.len();
    let err = unsafe {
        codes_get_string(h, ckey.as_ptr(), buf.as_mut_ptr(), &mut len)
    };
    if err != 0 {
        return format!("<err {err}>");
    }
    let bytes: Vec<u8> = buf[..len.saturating_sub(1).min(buf.len())]
        .iter()
        .take_while(|b| **b != 0)
        .map(|b| *b as u8)
        .collect();
    String::from_utf8_lossy(&bytes).into_owned()
}

fn get_long(h: *mut grib_handle, key: &str) -> i64 {
    let ckey = std::ffi::CString::new(key).unwrap();
    let mut v: i64 = 0;
    let err = unsafe { codes_get_long(h, ckey.as_ptr(), &mut v) };
    if err != 0 { -err as i64 } else { v }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: spike-decode <file.grib> [definitions_path]");
        std::process::exit(2);
    }
    if let Some(defs) = args.get(2) {
        // must be set before any codes_* call resolves definitions
        env::set_var("ECCODES_DEFINITION_PATH", defs);
    }

    let version = unsafe { codes_get_api_version() };
    println!("eccodes-api-version {}.{}.{}", version / 10000, (version / 100) % 100, version % 100);
    println!("definitions-path={}", env::var("ECCODES_DEFINITION_PATH").unwrap_or_else(|_| "<default>".into()));

    let path = std::ffi::CString::new(args[1].as_str()).unwrap();
    let mode = std::ffi::CString::new("rb").unwrap();
    let f = unsafe { libc::fopen(path.as_ptr(), mode.as_ptr()) as *mut FILE_ };
    if f.is_null() {
        eprintln!("cannot open {}", args[1]);
        std::process::exit(1);
    }

    let mut msg_no = 0usize;
    loop {
        let mut err: i32 = 0;
        let h = unsafe { codes_handle_new_from_file(core::ptr::null_mut(), f, PRODUCT_GRIB, &mut err) };
        if err != 0 {
            eprintln!("new_from_file error {err}");
            std::process::exit(1);
        }
        if h.is_null() {
            break; // EOF
        }
        msg_no += 1;

        let n_values = {
            let mut n: size_t = 0;
            unsafe { codes_get_size(h, b"values\0".as_ptr() as *const core::ffi::c_char, &mut n) };
            n
        };
        let mut values = vec![0f64; n_values];
        let mut len = n_values;
        let arr_err =
            unsafe { codes_get_double_array(h, b"values\0".as_ptr() as *const core::ffi::c_char, values.as_mut_ptr(), &mut len) };
        let min = values.iter().cloned().reduce(f64::min).unwrap_or(f64::NAN);
        let max = values.iter().cloned().reduce(f64::max).unwrap_or(f64::NAN);
        let first = values.first().copied().unwrap_or(f64::NAN);
        let last = values.last().copied().unwrap_or(f64::NAN);

        println!(
            "msg={msg_no} shortName={} gridType={} points={n_values} arrErr={arr_err} \
             first={first} last={last} min={min:.6} max={max:.6} \
             lat1={} lon1={} jScansPositively={}",
            get_string(h, "shortName"),
            get_string(h, "gridType"),
            get_long(h, "latitudeOfFirstGridPointInDegrees"),
            get_long(h, "longitudeOfFirstGridPointInDegrees"),
            get_long(h, "jScansPositively"),
        );

        unsafe { codes_handle_delete(h) };
    }
    println!("messages={msg_no}");
    unsafe { libc::fclose(f as *mut libc::FILE) };
    if msg_no == 0 {
        std::process::exit(1);
    }
}
