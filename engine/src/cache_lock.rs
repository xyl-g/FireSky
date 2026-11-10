//! Bounded inter-process advisory locks, compatible with Python's flock.
use fs2::FileExt;
use std::fs::{File, OpenOptions};
use std::path::Path;
use std::time::{Duration, Instant};

pub struct Lock(File);

impl Lock {
    pub fn acquire(path: &Path, seconds: f64) -> Result<Self, String> {
        Self::acquire_checked(path, seconds, || Ok(()))
    }

    pub(crate) fn acquire_checked(
        path: &Path,
        seconds: f64,
        check: impl Fn() -> Result<(), String>,
    ) -> Result<Self, String> {
        check()?;
        if !seconds.is_finite() || !(0.0..=3600.0).contains(&seconds) {
            return Err("cache lock timeout must be within 0..3600 seconds".into());
        }
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW);
        }
        let file = options.open(path).map_err(|e| format!("cache lock: {e}"))?;
        if !file.metadata().map_err(|e| e.to_string())?.is_file() {
            return Err("cache lock must be a regular file".into());
        }
        let deadline = Instant::now() + Duration::from_secs_f64(seconds);
        loop {
            check()?;
            match file.try_lock_exclusive() {
                Ok(()) => {
                    let lock = Self(file);
                    check()?;
                    return Ok(lock);
                }
                Err(error)
                    if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() =>
                {
                    if Instant::now() >= deadline {
                        return Err("GFS cache lock timed out".into());
                    }
                    std::thread::sleep(
                        Duration::from_millis(50)
                            .min(deadline.saturating_duration_since(Instant::now())),
                    );
                }
                Err(error) => return Err(format!("cache lock: {error}")),
            }
        }
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.0);
    }
}
