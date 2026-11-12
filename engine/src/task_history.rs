//! Durable workflow receipts. Owner locks distinguish a crash from another live runtime.
use crate::cache_lock::Lock;
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::Read,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{SystemTime, UNIX_EPOCH},
};

const SCHEMA: &str = "firesky-task-history-v1";
const MAX_RECORD_BYTES: u64 = 1_048_576;
const MAX_EVENTS: usize = 256;

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}

fn utc(millis: u64) -> String {
    chrono::DateTime::from_timestamp_millis(millis.min(i64::MAX as u64) as i64)
        .map(|value| value.to_rfc3339_opts(chrono::SecondsFormat::Millis, true))
        .unwrap_or_default()
}

fn identifier(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn regular(path: &Path, create: bool) -> Result<File, String> {
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err("task history requires a regular non-symlink file".into());
        }
    }
    let mut options = OpenOptions::new();
    options.read(true);
    if create {
        options.write(true).create(true).truncate(false);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW).mode(0o600);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x00200000); // FILE_FLAG_OPEN_REPARSE_POINT
    }
    let file = options.open(path).map_err(|error| error.to_string())?;
    if !file
        .metadata()
        .map_err(|error| error.to_string())?
        .is_file()
    {
        return Err("task history requires a regular file".into());
    }
    Ok(file)
}

fn directory(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|error| error.to_string())?;
    let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err("task history directory must not be a symlink".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Event {
    pub seq: u64,
    pub at_utc: String,
    pub event: String,
    pub data: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub schema: String,
    pub task_id: String,
    pub request_id: String,
    pub owner: String,
    pub method: String,
    pub status: String,
    pub created_at_utc: String,
    pub updated_at_utc: String,
    pub cancel_requested: bool,
    pub retry_request: Value,
    pub result_ref: Value,
    pub progress: Value,
    pub event_seq: u64,
    pub events: Vec<Event>,
}

impl Record {
    fn event(&mut self, event: &str, data: Value) -> Result<(), String> {
        self.event_seq = self
            .event_seq
            .checked_add(1)
            .ok_or("task history event sequence exhausted")?;
        self.updated_at_utc = utc(now());
        self.events.push(Event {
            seq: self.event_seq,
            at_utc: self.updated_at_utc.clone(),
            event: event.into(),
            data,
        });
        if self.events.len() > MAX_EVENTS {
            self.events.drain(..self.events.len() - MAX_EVENTS);
        }
        Ok(())
    }
}

pub struct TaskHistory {
    root: PathBuf,
    owner: String,
    _owner_lock: File,
    sequence: AtomicU64,
    active: Mutex<BTreeMap<String, String>>,
}

pub struct Task {
    history: Arc<TaskHistory>,
    id: String,
    request_id: String,
    method: String,
    finished: bool,
}

impl TaskHistory {
    pub fn open(root: &Path) -> Result<Arc<Self>, String> {
        directory(root)?;
        let root = root.canonicalize().map_err(|error| error.to_string())?;
        directory(&root.join("jobs"))?;
        directory(&root.join("owners"))?;
        directory(&root.join("snapshots"))?;
        let owner_file = tempfile::Builder::new()
            .prefix("owner-")
            .suffix(".lock")
            .tempfile_in(root.join("owners"))
            .map_err(|error| error.to_string())?;
        owner_file
            .as_file()
            .try_lock_exclusive()
            .map_err(|error| error.to_string())?;
        let (owner_lock, owner_path) = owner_file.keep().map_err(|error| error.to_string())?;
        owner_lock.sync_all().map_err(|error| error.to_string())?;
        #[cfg(unix)]
        File::open(root.join("owners"))
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        let owner = owner_path
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or("invalid task history owner")?
            .to_owned();
        let history = Arc::new(Self {
            root,
            owner,
            _owner_lock: owner_lock,
            sequence: AtomicU64::new(0),
            active: Mutex::new(BTreeMap::new()),
        });
        history.list(1)?;
        Ok(history)
    }

    fn lock(&self) -> Result<Lock, String> {
        Lock::acquire(&self.root.join("store.lock"), 5.0)
    }

    fn path(&self, id: &str) -> Result<PathBuf, String> {
        if !identifier(id) {
            return Err("invalid task history id".into());
        }
        Ok(self.root.join("jobs").join(format!("{id}.json")))
    }

    fn snapshot_root(&self) -> PathBuf {
        self.root.join("snapshots")
    }

    fn read(&self, id: &str) -> Result<Record, String> {
        let mut file = regular(&self.path(id)?, false)?;
        if file.metadata().map_err(|error| error.to_string())?.len() > MAX_RECORD_BYTES {
            return Err("task history record exceeds size limit".into());
        }
        let mut bytes = Vec::new();
        file.by_ref()
            .take(MAX_RECORD_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| error.to_string())?;
        if bytes.len() as u64 > MAX_RECORD_BYTES {
            return Err("task history record exceeds size limit".into());
        }
        let record: Record = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
        if record.schema != SCHEMA
            || record.task_id != id
            || !matches!(
                record.status.as_str(),
                "running" | "done" | "failed" | "cancelled" | "interrupted"
            )
            || record.events.len() > MAX_EVENTS
            || record.events.last().map(|event| event.seq) != Some(record.event_seq)
            || record
                .events
                .windows(2)
                .any(|pair| pair[0].seq.checked_add(1) != Some(pair[1].seq))
        {
            return Err("invalid task history contract".into());
        }
        Ok(record)
    }

    fn write(&self, record: &Record) -> Result<(), String> {
        let bytes = serde_json::to_vec(record).map_err(|error| error.to_string())?;
        if bytes.len() as u64 > MAX_RECORD_BYTES {
            return Err("task history record exceeds size limit".into());
        }
        let mut temporary = tempfile::NamedTempFile::new_in(self.root.join("jobs"))
            .map_err(|error| error.to_string())?;
        use std::io::Write;
        temporary
            .write_all(&bytes)
            .map_err(|error| error.to_string())?;
        temporary
            .as_file()
            .sync_all()
            .map_err(|error| error.to_string())?;
        temporary
            .persist(self.path(&record.task_id)?)
            .map_err(|error| error.to_string())?;
        #[cfg(unix)]
        File::open(self.root.join("jobs"))
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        Ok(())
    }

    fn alive(&self, owner: &str) -> Result<bool, String> {
        if !owner.starts_with("owner-")
            || !owner.ends_with(".lock")
            || !owner
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_'))
        {
            return Err("invalid task history owner".into());
        }
        let path = self.root.join("owners").join(owner);
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error.to_string()),
        };
        if !metadata.is_file() || metadata.file_type().is_symlink() {
            return Err("task history owner must be a regular non-symlink file".into());
        }
        // Only explicit NotFound proves absence. An unreadable or replaced
        // owner lock leaves liveness unknown and must not interrupt its jobs.
        let file = regular(&path, false)?;
        match file.try_lock_exclusive() {
            Ok(()) => {
                FileExt::unlock(&file).map_err(|error| error.to_string())?;
                Ok(false)
            }
            Err(error) if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
                Ok(true)
            }
            Err(error) => Err(error.to_string()),
        }
    }

    fn recover(&self, mut record: Record) -> Result<Record, String> {
        if record.status == "running" && !self.alive(&record.owner)? {
            record.status = "interrupted".into();
            record.event(
                "interrupted",
                json!({"reason":"runtime_exited","retry_requires_check":true}),
            )?;
            self.write(&record)?;
        }
        Ok(record)
    }

    pub fn begin(
        self: &Arc<Self>,
        request_id: &str,
        method: &str,
        retry_request: Value,
    ) -> Result<Task, String> {
        if request_id.is_empty() || request_id.len() > 256 {
            return Err("task request id must contain 1..256 bytes".into());
        }
        if !matches!(
            method,
            "check" | "prepare" | "forecast" | "publications" | "network_probe"
        ) {
            return Err("method does not create workflow history".into());
        }
        let id: String = Sha256::digest(format!(
            "{}\0{}\0{}",
            self.owner,
            self.sequence.fetch_add(1, Ordering::Relaxed),
            request_id
        ))
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
        let mut record = Record {
            schema: SCHEMA.into(),
            task_id: id.clone(),
            request_id: request_id.into(),
            owner: self.owner.clone(),
            method: method.into(),
            status: "running".into(),
            created_at_utc: utc(now()),
            updated_at_utc: utc(now()),
            cancel_requested: false,
            retry_request: retry_request_snapshot(&retry_request),
            result_ref: Value::Null,
            progress: Value::Null,
            event_seq: 0,
            events: Vec::new(),
        };
        record.event("running", json!({"method":method}))?;
        let _guard = self.lock()?;
        let mut active = self.active.lock().map_err(|_| "task active map poisoned")?;
        if active.contains_key(request_id) {
            return Err("workflow request id already active".into());
        }
        self.write(&record)?;
        active.insert(request_id.into(), id.clone());
        Ok(Task {
            history: self.clone(),
            id,
            request_id: request_id.into(),
            method: method.into(),
            finished: false,
        })
    }

    fn update(
        &self,
        id: &str,
        change: impl FnOnce(&mut Record) -> Result<(), String>,
    ) -> Result<(), String> {
        let _guard = self.lock()?;
        let mut record = self.read(id)?;
        change(&mut record)?;
        self.write(&record)
    }

    pub fn request_cancel(&self, request_id: &str) -> Result<(), String> {
        let id = self
            .active
            .lock()
            .map_err(|_| "task active map poisoned")?
            .get(request_id)
            .cloned();
        if let Some(id) = id {
            self.update(&id, |record| {
                if record.status == "running" && !record.cancel_requested {
                    record.cancel_requested = true;
                    record.event("cancel_requested", json!({}))?;
                }
                Ok(())
            })?;
        }
        Ok(())
    }

    pub fn list(&self, limit: usize) -> Result<Value, String> {
        if !(1..=100).contains(&limit) {
            return Err("task history limit must be within 1..100".into());
        }
        let _guard = self.lock()?;
        let mut records = Vec::new();
        let mut issues = Vec::new();
        for entry in fs::read_dir(self.root.join("jobs")).map_err(|error| error.to_string())? {
            let entry = entry.map_err(|error| error.to_string())?;
            let name = entry.file_name().to_string_lossy().into_owned();
            let Some(id) = name.strip_suffix(".json").filter(|id| identifier(id)) else {
                continue;
            };
            match self.read(id).and_then(|record| self.recover(record)) {
                Ok(record) => records.push(record),
                Err(_) => issues.push(json!({"task_id":id,"code":"invalid_record"})),
            }
        }
        records.sort_by(|left, right| {
            right
                .created_at_utc
                .cmp(&left.created_at_utc)
                .then_with(|| right.task_id.cmp(&left.task_id))
        });
        let total = records.len();
        records.truncate(limit);
        let rows: Vec<_> = records
            .into_iter()
            .map(|record| {
                let mut value = serde_json::to_value(record).expect("task record is serializable");
                value.as_object_mut().unwrap().remove("events");
                value.as_object_mut().unwrap().remove("owner");
                value
            })
            .collect();
        Ok(
            json!({"schema":SCHEMA,"tasks":rows,"total":total,"issues":issues,"automatic_restart":false}),
        )
    }

    pub fn detail(&self, id: &str) -> Result<Value, String> {
        let _guard = self.lock()?;
        let record = self.recover(self.read(id)?)?;
        let mut value = serde_json::to_value(record).expect("task record is serializable");
        if let Some(object) = value.as_object_mut() {
            object.remove("events");
            object.remove("owner");
        }
        Ok(value)
    }

    pub fn events(&self, id: &str, after: u64) -> Result<Value, String> {
        let _guard = self.lock()?;
        let record = self.recover(self.read(id)?)?;
        if after > record.event_seq {
            return Err("task event cursor is ahead of stored events".into());
        }
        let first = record.events.first().map(|event| event.seq).unwrap_or(1);
        let events: Vec<_> = record
            .events
            .iter()
            .filter(|event| event.seq > after)
            .collect();
        Ok(json!({
            "task_id":id,"status":record.status,"events":events,"next_seq":record.event_seq,
            "result_ref":record.result_ref,
            "truncated":after.saturating_add(1)<first,"first_available_seq":first,
        }))
    }

    pub fn forget(&self, id: &str) -> Result<Value, String> {
        let _guard = self.lock()?;
        let path = self.path(id)?;
        if !path.exists() {
            return Ok(json!({"forgotten":false}));
        }
        let record = self.recover(self.read(id)?)?;
        if record.status == "running" {
            return Err("cannot forget a running task".into());
        }
        fs::remove_file(path).map_err(|error| error.to_string())?;
        #[cfg(unix)]
        File::open(self.root.join("jobs"))
            .and_then(|directory| directory.sync_all())
            .map_err(|error| error.to_string())?;
        Ok(json!({"forgotten":true}))
    }

    /// Restore the immutable result snapshot of a completed forecast task.
    /// Verifies terminal state, method and snapshot integrity only; it never
    /// revalidates raw inputs, the engine source id or the active model cycle,
    /// and never downloads or recomputes.
    pub fn restore(&self, id: &str) -> Result<Value, String> {
        let _guard = self.lock()?;
        let record = self.recover(self.read(id)?)?;
        if record.status != "done" {
            return Err("only completed forecast tasks can be restored".into());
        }
        if record.method != "forecast" {
            return Err("this task method has no restorable result".into());
        }
        let identity = &record.result_ref["snapshot"];
        if identity.is_null() {
            return Err("task has no immutable result snapshot".into());
        }
        crate::result_snapshot::read(&self.snapshot_root(), identity)
    }
}

impl Task {
    pub fn id(&self) -> &str {
        &self.id
    }

    pub fn progress(&self, value: &Value) -> Result<(), String> {
        let progress = progress_snapshot(value);
        self.history.update(&self.id, |record| {
            if record.status == "running" && record.progress != progress {
                record.progress = progress.clone();
                record.event("progress", progress)?;
            }
            Ok(())
        })
    }

    pub fn finish(
        mut self,
        outcome: &Result<Value, String>,
        cancelled: bool,
    ) -> Result<(), String> {
        // Publish the immutable snapshot before committing the receipt so a
        // refresh of the same request can never alter this task's result.
        let mut snapshot = Value::Null;
        if !cancelled && self.method == "forecast" {
            if let Ok(result) = outcome {
                if let Ok(identity) =
                    crate::result_snapshot::publish(&self.history.snapshot_root(), result)
                {
                    snapshot = identity;
                }
            }
        }
        self.history.update(&self.id, |record| {
            record.status = if cancelled {
                "cancelled"
            } else if outcome.is_ok() {
                "done"
            } else {
                "failed"
            }
            .into();
            record.cancel_requested |= cancelled;
            if !cancelled {
                if let Ok(result) = outcome {
                    let mut reference = result_reference(result);
                    if !snapshot.is_null() {
                        reference["snapshot"] = snapshot.clone();
                    }
                    record.result_ref = reference;
                }
            }
            let status = record.status.clone();
            record.event(&status, json!({"retry_requires_check":true}))?;
            Ok(())
        })?;
        self.finished = true;
        self.history
            .active
            .lock()
            .map_err(|_| "task active map poisoned")?
            .remove(&self.request_id);
        Ok(())
    }
}

impl Drop for Task {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.history.update(&self.id, |record| {
                if record.status == "running" {
                    record.status = "interrupted".into();
                    record.event(
                        "interrupted",
                        json!({"reason":"worker_exited","retry_requires_check":true}),
                    )?;
                }
                Ok(())
            });
            if let Ok(mut active) = self.history.active.lock() {
                active.remove(&self.request_id);
            }
        }
    }
}

pub fn retry_request_snapshot(value: &Value) -> Value {
    let kind = value["kind"].as_str().unwrap_or("");
    if !matches!(kind, "point" | "regional" | "nowcast") || !value["payload"].is_object() {
        return Value::Null;
    }
    let mut payload = Map::new();
    for field in [
        "name",
        "lat",
        "lon",
        "elevation_m",
        "timezone_name",
        "target_date",
        "phenomenon",
        "model_source",
        "forecast_mode",
        "satellite_source",
        "ecmwf_source",
        "regions",
        "custom_bounds",
        "custom_timezone_name",
        "spacing_deg",
        "radius_km",
        "lookback_minutes",
        "frame_count",
        "reference_time_utc",
    ] {
        if let Some(item) = value["payload"].get(field) {
            if item.is_string() || item.is_number() || item.is_boolean() {
                if item.to_string().len() <= 1024 {
                    payload.insert(field.into(), item.clone());
                }
            } else if field == "regions" {
                if let Some(regions) = item.as_array() {
                    payload.insert(
                        field.into(),
                        Value::Array(
                            regions
                                .iter()
                                .filter(|region| region.as_str().is_some_and(|s| s.len() <= 128))
                                .take(64)
                                .cloned()
                                .collect(),
                        ),
                    );
                }
            } else if field == "custom_bounds" {
                let bounds: Map<_, _> = ["west", "east", "south", "north"]
                    .iter()
                    .filter_map(|key| {
                        item.get(key)
                            .filter(|value| value.is_number())
                            .map(|value| ((*key).into(), value.clone()))
                    })
                    .collect();
                payload.insert(field.into(), Value::Object(bounds));
            }
        }
    }
    json!({"kind":kind,"payload":payload})
}

fn progress_snapshot(value: &Value) -> Value {
    let mut progress = Map::new();
    for field in ["stage", "source"] {
        if let Some(text) = value[field].as_str().filter(|text| text.len() <= 128) {
            progress.insert(field.into(), json!(text));
        }
    }
    if let Some(percent) = value["percent"]
        .as_f64()
        .filter(|value| (0.0..=100.0).contains(value))
    {
        progress.insert("percent".into(), json!(percent));
    }
    if let Some(hit) = value["cache_hit"].as_bool() {
        progress.insert("cache_hit".into(), json!(hit));
    }
    Value::Object(progress)
}

fn result_reference(value: &Value) -> Value {
    let mut references = Map::new();
    for field in ["cache_key", "source_id", "result_id"] {
        if let Some(text) = value[field].as_str().filter(|text| identifier(text)) {
            references.insert(field.into(), json!(text));
        }
    }
    let cache = &value["cache"];
    if cache["contract"] == "rust-native-runtime-cache-v1" {
        if let (Some(key), Some(source), Some(regional)) = (
            cache["key"].as_str().filter(|key| identifier(key)),
            cache["source_id"]
                .as_str()
                .filter(|source| identifier(source)),
            cache["regional"].as_bool(),
        ) {
            let report_file = format!(
                "native-{}-{key}.json",
                if regional { "regional" } else { "point" }
            );
            if cache["report_file"].as_str() == Some(report_file.as_str()) {
                references.insert(
                    "cache".into(),
                    json!({
                        "contract":"rust-native-runtime-cache-v1","key":key,
                        "source_id":source,"regional":regional,"report_file":report_file,
                    }),
                );
            }
        }
    }
    let mut profile_sources = Map::new();
    fn profile_source(value: &Value, model: &str, profiles: &mut Map<String, Value>) {
        let source = &value["profile_source"];
        if source["contract"] != "rust-desktop-profile-source-v1"
            || source["available"] != true
            || source["model_source"].as_str() != Some(model)
        {
            return;
        }
        let Some(id) = source["result_id"].as_str().filter(|id| identifier(id)) else {
            return;
        };
        let Some(sha) = source["source_identity"]["sha256"]
            .as_str()
            .filter(|sha| identifier(sha))
        else {
            return;
        };
        let Some(bytes) = source["source_identity"]["bytes"]
            .as_u64()
            .filter(|bytes| (1..=16 * 1024 * 1024).contains(bytes))
        else {
            return;
        };
        let Some(cycle) = source["cycle_utc"]
            .as_str()
            .filter(|time| crate::time_contract::parse_utc(time).is_ok())
        else {
            return;
        };
        let Some(expires) = source["expires_at_utc"]
            .as_str()
            .filter(|time| crate::time_contract::parse_utc(time).is_ok())
        else {
            return;
        };
        let Some(kind) = source["profile_kind"].as_str().filter(|kind| {
            matches!(
                *kind,
                "model-pressure-cross-section" | "satellite-step-solar-path-diagnostic"
            )
        }) else {
            return;
        };
        let Some(scope) = source["scope"]
            .as_str()
            .filter(|scope| matches!(*scope, "point" | "regional"))
        else {
            return;
        };
        profiles.insert(
            model.into(),
            json!({
                "contract":"rust-desktop-profile-source-v1","result_id":id,
                "source_identity":{"bytes":bytes,"sha256":sha},"model_source":model,
                "cycle_utc":cycle,"expires_at_utc":expires,"profile_kind":kind,"scope":scope,
            }),
        );
    }
    if let Some(model @ ("gfs" | "ec" | "satellite")) = value["model_source"].as_str() {
        profile_source(value, model, &mut profile_sources);
    }
    if let Some(models) = value["model_results"].as_object() {
        for model in ["gfs", "ec"] {
            if let Some(value) = models.get(model) {
                profile_source(value, model, &mut profile_sources);
            }
        }
    }
    references.insert("profile_sources".into(), Value::Object(profile_sources));
    let mut reports = Vec::new();
    fn report_url(value: &Value, urls: &mut Vec<Value>) {
        for key in ["report_url", "html_report_url"] {
            if let Some(url) = value[key].as_str() {
                let id = url
                    .strip_prefix("/api/v1/firesky/reports/")
                    .and_then(|tail| tail.strip_suffix(".html"));
                if id.is_some_and(identifier)
                    && !urls.iter().any(|entry| entry.as_str() == Some(url))
                {
                    urls.push(json!(url));
                }
            }
        }
        if let Some(models) = value["model_results"].as_object() {
            for model in models.values() {
                report_url(model, urls);
            }
        }
    }
    report_url(value, &mut reports);
    references.insert("report_urls".into(), json!(reports));
    Value::Object(references)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> Value {
        json!({"kind":"point","payload":{"name":"Hangzhou","lat":30.0,"lon":120.0,
            "timezone_name":"Asia/Shanghai","target_date":"2026-11-11",
            "api_key":"secret","custom_bounds":{"west":170,"east":-170,"secret":"hidden"}}})
    }

    #[test]
    fn terminal_and_retry_identity_survive_reopen_without_secrets_or_tokens() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let task = history.begin("request-1", "forecast", request()).unwrap();
        task.progress(&json!({"stage":"forecast","percent":20,"message":"token=secret","probe":{"key":"secret"}})).unwrap();
        task.finish(&Ok(json!({"session":"secret","report_url":format!("/api/v1/firesky/reports/{}.html","a".repeat(64))})), false).unwrap();
        drop(history);
        let recovered = TaskHistory::open(root.path()).unwrap().list(20).unwrap();
        assert_eq!(recovered["tasks"][0]["status"], "done");
        assert_eq!(
            recovered["tasks"][0]["retry_request"]["payload"]["lat"],
            30.0
        );
        assert_eq!(
            recovered["tasks"][0]["result_ref"]["report_urls"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert!(!recovered.to_string().contains("secret"));
        assert!(!recovered.to_string().contains("session"));
        assert_eq!(recovered["automatic_restart"], false);
    }

    fn profile_fixture(model: &str, scope: &str) -> Value {
        let id = match model {
            "gfs" => "b",
            "ec" => "e",
            _ => "f",
        };
        json!({
            "contract":"rust-desktop-profile-source-v1","available":true,
            "result_id":id.repeat(64),"source_identity":{"bytes":1024,"sha256":"c".repeat(64)},
            "model_source":model,"cycle_utc":"2026-11-11T00:00:00Z",
            "expires_at_utc":"2026-11-11T10:30:00Z",
            "profile_kind":if model=="satellite" {
                "satellite-step-solar-path-diagnostic"
            } else {"model-pressure-cross-section"},
            "scope":scope,"source_path":"/private/secret/profile.json","credentials":"secret",
        })
    }

    #[test]
    fn real_regional_runtime_result_keeps_nested_cache_and_independent_profile_identities() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let result = json!({
            "cache":{
                "contract":"rust-native-runtime-cache-v1","status":"refresh","key":"a".repeat(64),
                "source_id":"d".repeat(64),"regional":true,
                "report_file":format!("native-regional-{}.json","a".repeat(64)),
                "request":{"api_key":"secret"},"report_path":"/private/secret/report.json",
            },
            "model_results":{
                "gfs":{"scope":"regional","profile_source":profile_fixture("gfs","regional")},
                "ec":{"scope":"regional","profile_source":profile_fixture("ec","regional")},
            },
            "model_errors":{},
        });
        history
            .begin("regional", "forecast", request())
            .unwrap()
            .finish(&Ok(result), false)
            .unwrap();
        drop(history);
        let rows = TaskHistory::open(root.path()).unwrap().list(10).unwrap();
        let reference = &rows["tasks"][0]["result_ref"];
        assert_eq!(reference["cache"]["key"], "a".repeat(64));
        assert_eq!(reference["cache"]["regional"], true);
        assert_eq!(reference["cache"]["source_id"], "d".repeat(64));
        assert_eq!(reference["profile_sources"]["gfs"]["model_source"], "gfs");
        assert_eq!(reference["profile_sources"]["ec"]["model_source"], "ec");
        assert_eq!(
            reference["profile_sources"]["gfs"]["source_identity"]["sha256"],
            "c".repeat(64)
        );
        assert_ne!(
            reference["profile_sources"]["gfs"]["result_id"],
            reference["profile_sources"]["ec"]["result_id"]
        );
        assert!(!rows.to_string().contains("secret"));
        assert!(!rows.to_string().contains("source_path"));
    }

    #[test]
    fn result_references_reject_mismatched_cache_files_and_profile_models() {
        let reference = result_reference(&json!({
            "cache":{
                "contract":"rust-native-runtime-cache-v1","key":"a".repeat(64),
                "source_id":"d".repeat(64),"regional":true,"report_file":"../private/secret.json",
            },
            "model_results":{"gfs":{"profile_source":profile_fixture("ec","regional")}},
        }));
        assert!(reference.get("cache").is_none());
        assert_eq!(reference["profile_sources"], json!({}));
        assert!(!reference.to_string().contains("secret"));
        let satellite = result_reference(&json!({
            "model_source":"satellite","profile_source":profile_fixture("satellite","point"),
        }));
        assert_eq!(
            satellite["profile_sources"]["satellite"]["profile_kind"],
            "satellite-step-solar-path-diagnostic"
        );
    }

    #[test]
    fn concurrent_runtime_is_not_misclassified_as_interrupted() {
        let root = tempfile::tempdir().unwrap();
        let first = TaskHistory::open(root.path()).unwrap();
        let task = first.begin("request", "prepare", request()).unwrap();
        let second = TaskHistory::open(root.path()).unwrap();
        assert_eq!(second.list(10).unwrap()["tasks"][0]["status"], "running");
        task.finish(&Ok(json!({})), false).unwrap();
        assert_eq!(second.list(10).unwrap()["tasks"][0]["status"], "done");
    }

    #[test]
    fn abandoned_owner_is_interrupted_once_and_never_restarted() {
        let root = tempfile::tempdir().unwrap();
        let first = TaskHistory::open(root.path()).unwrap();
        let mut task = first.begin("request", "prepare", request()).unwrap();
        let id = task.id.clone();
        task.finished = true;
        drop(task);
        drop(first);
        let second = TaskHistory::open(root.path()).unwrap();
        let row = &second.list(10).unwrap()["tasks"][0];
        assert_eq!(row["status"], "interrupted");
        assert_eq!(row["event_seq"], 2);
        assert_eq!(second.list(10).unwrap()["tasks"][0]["event_seq"], 2);
        assert_eq!(
            second.events(&id, 1).unwrap()["events"][0]["event"],
            "interrupted"
        );
    }

    #[test]
    fn cancellation_stays_running_until_finish_and_never_keeps_success() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let task = history.begin("request", "forecast", request()).unwrap();
        history.request_cancel("request").unwrap();
        let row = &history.list(10).unwrap()["tasks"][0];
        let id = row["task_id"].as_str().unwrap();
        assert_eq!(row["status"], "running");
        assert_eq!(row["cancel_requested"], true);
        assert!(history.forget(id).unwrap_err().contains("running"));
        task.finish(&Ok(json!({"cache_key":"a".repeat(64)})), true)
            .unwrap();
        let row = &history.list(10).unwrap()["tasks"][0];
        assert_eq!(row["status"], "cancelled");
        assert_eq!(row["result_ref"], Value::Null);
        assert_eq!(history.forget(id).unwrap()["forgotten"], true);
        assert_eq!(history.forget(id).unwrap()["forgotten"], false);
    }

    #[test]
    fn forgotten_terminal_receipt_stays_absent_after_reopen() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let task = history.begin("forgotten", "forecast", request()).unwrap();
        let id = task.id().to_owned();
        task.finish(&Ok(json!({})), false).unwrap();
        assert_eq!(history.forget(&id).unwrap()["forgotten"], true);
        assert!(!history.path(&id).unwrap().exists());
        drop(history);
        let reopened = TaskHistory::open(root.path()).unwrap();
        assert_eq!(reopened.list(10).unwrap()["total"], 0);
        assert!(reopened.events(&id, 0).is_err());
        assert_eq!(reopened.forget(&id).unwrap()["forgotten"], false);
    }

    #[test]
    fn event_replay_reports_truncation_and_rejects_future_cursors() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let task = history.begin("request", "prepare", request()).unwrap();
        for step in 0..300 {
            task.progress(&json!({"stage":format!("stage-{step}")}))
                .unwrap();
        }
        let events = history.events(&task.id, 0).unwrap();
        assert_eq!(events["truncated"], true);
        assert_eq!(events["events"].as_array().unwrap().len(), MAX_EVENTS);
        let next = events["next_seq"].as_u64().unwrap();
        assert!(history.events(&task.id, next).unwrap()["events"]
            .as_array()
            .unwrap()
            .is_empty());
        assert!(history.events(&task.id, next + 1).is_err());
        task.finish(&Err("private failure".into()), false).unwrap();
        assert!(!history
            .list(10)
            .unwrap()
            .to_string()
            .contains("private failure"));
    }

    #[test]
    fn dropped_worker_marks_interrupted_and_preserves_previous_terminal() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        history
            .begin("first", "forecast", request())
            .unwrap()
            .finish(&Ok(json!({})), false)
            .unwrap();
        drop(history.begin("second", "prepare", request()).unwrap());
        let rows = history.list(10).unwrap();
        let statuses: Vec<_> = rows["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|row| row["status"].as_str().unwrap())
            .collect();
        assert!(statuses.contains(&"done"));
        assert!(statuses.contains(&"interrupted"));
    }

    #[test]
    fn process_crash_recovers_only_after_the_owner_really_exits() {
        const CHILD_ENV: &str = "FIRESKY_TASK_HISTORY_TEST_CHILD";
        if let Some(root) = std::env::var_os(CHILD_ENV) {
            let root = PathBuf::from(root);
            let history = TaskHistory::open(&root.join("store")).unwrap();
            let _task = history
                .begin("crash-fixture", "prepare", request())
                .unwrap();
            fs::write(root.join("ready"), b"ready").unwrap();
            loop {
                std::thread::sleep(std::time::Duration::from_secs(1));
            }
        }
        struct Child(std::process::Child);
        impl Drop for Child {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let root = tempfile::tempdir().unwrap();
        let mut child = Child(
            std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "task_history::tests::process_crash_recovers_only_after_the_owner_really_exits",
                    "--nocapture",
                ])
                .env(CHILD_ENV, root.path())
                .stdout(std::process::Stdio::null())
                .spawn()
                .unwrap(),
        );
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !root.path().join("ready").is_file() {
            assert!(
                child.0.try_wait().unwrap().is_none(),
                "fixture child exited before readiness"
            );
            assert!(
                std::time::Instant::now() < deadline,
                "fixture child did not become ready"
            );
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let observer = TaskHistory::open(&root.path().join("store")).unwrap();
        assert_eq!(observer.list(10).unwrap()["tasks"][0]["status"], "running");
        child.0.kill().unwrap();
        assert!(!child.0.wait().unwrap().success());
        let recovered = observer.list(10).unwrap();
        assert_eq!(recovered["tasks"][0]["status"], "interrupted");
        assert_eq!(recovered["tasks"][0]["retry_request"]["kind"], "point");
        assert_eq!(recovered["automatic_restart"], false);
    }

    #[cfg(unix)]
    #[test]
    fn refuses_symlink_records_and_directories_without_following_them() {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        symlink(outside.path(), root.path().join("linked")).unwrap();
        assert!(TaskHistory::open(&root.path().join("linked")).is_err());
        let history = TaskHistory::open(&root.path().join("store")).unwrap();
        let id = "a".repeat(64);
        symlink(
            outside.path().join("absent.json"),
            history.path(&id).unwrap(),
        )
        .unwrap();
        let page = history.list(10).unwrap();
        assert_eq!(page["issues"][0]["code"], "invalid_record");
        assert!(page["tasks"].as_array().unwrap().is_empty());
        assert!(!outside.path().join("absent.json").exists());
        assert!(history.events("../outside", 0).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn unknown_owner_state_does_not_interrupt_a_live_task() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let task = history.begin("request", "prepare", request()).unwrap();
        let owner_path = history.root.join("owners").join(&history.owner);
        let held_path = history.root.join("owners").join("held-owner-fixture.lock");
        fs::rename(&owner_path, &held_path).unwrap();
        std::os::unix::fs::symlink(root.path().join("missing-owner-target"), &owner_path).unwrap();
        assert!(!owner_path.exists());
        assert!(history.alive(&history.owner).is_err());
        let observer = TaskHistory::open(root.path()).unwrap();
        let page = observer.list(10).unwrap();
        assert!(page["tasks"].as_array().unwrap().is_empty());
        assert_eq!(page["issues"][0]["code"], "invalid_record");
        assert_eq!(history.read(&task.id).unwrap().status, "running");
        fs::remove_file(&owner_path).unwrap();
        fs::rename(&held_path, &owner_path).unwrap();
        assert_eq!(observer.list(10).unwrap()["tasks"][0]["status"], "running");
        task.finish(&Ok(json!({})), false).unwrap();
    }

    #[test]
    fn forecast_snapshot_restores_exact_result_after_reopen() {
        let root = tempfile::tempdir().unwrap();
        let result = json!({"model_results":{"gfs":{"score":0.8}},"model_errors":{}});
        let id = {
            let history = TaskHistory::open(root.path()).unwrap();
            let task = history.begin("request", "forecast", request()).unwrap();
            let id = task.id().to_owned();
            task.finish(&Ok(result.clone()), false).unwrap();
            let row = &history.list(10).unwrap()["tasks"][0];
            assert_eq!(row["result_ref"]["snapshot"]["contract"], "firesky-result-snapshot-v1");
            id
        };
        let reopened = TaskHistory::open(root.path()).unwrap();
        assert_eq!(reopened.restore(&id).unwrap(), result);
    }

    #[test]
    fn refreshing_same_request_never_alters_an_earlier_task_snapshot() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let first = json!({"model_results":{"gfs":{"score":0.1}}});
        let second = json!({"model_results":{"gfs":{"score":0.9}}});
        let first_task = history.begin("same-request", "forecast", request()).unwrap();
        let first_id = first_task.id().to_owned();
        first_task.finish(&Ok(first.clone()), false).unwrap();
        let second_task = history.begin("same-request", "forecast", request()).unwrap();
        let second_id = second_task.id().to_owned();
        second_task.finish(&Ok(second.clone()), false).unwrap();
        assert_eq!(history.restore(&first_id).unwrap(), first);
        assert_eq!(history.restore(&second_id).unwrap(), second);
    }

    #[test]
    fn cancelled_and_failed_forecasts_publish_no_restorable_snapshot() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let cancelled = history.begin("cancelled", "forecast", request()).unwrap();
        let cancelled_id = cancelled.id().to_owned();
        cancelled
            .finish(&Ok(json!({"model_results":{}})), true)
            .unwrap();
        let failed = history.begin("failed", "forecast", request()).unwrap();
        let failed_id = failed.id().to_owned();
        failed.finish(&Err("boom".into()), false).unwrap();
        assert!(history.restore(&cancelled_id).is_err());
        assert!(history.restore(&failed_id).is_err());
        let rows = history.list(10).unwrap();
        for row in rows["tasks"].as_array().unwrap() {
            assert!(row["result_ref"].get("snapshot").is_none());
        }
    }

    #[test]
    fn restore_rejects_non_forecast_methods_and_non_terminal_tasks() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let prepare = history.begin("prepare", "prepare", request()).unwrap();
        let prepare_id = prepare.id().to_owned();
        prepare.finish(&Ok(json!({})), false).unwrap();
        assert!(history.restore(&prepare_id).is_err());
        let running = history.begin("running", "forecast", request()).unwrap();
        let running_id = running.id().to_owned();
        assert!(history.restore(&running_id).is_err());
        running.finish(&Ok(json!({})), false).unwrap();
    }

    #[test]
    fn restore_fails_closed_when_snapshot_content_is_tampered() {
        let root = tempfile::tempdir().unwrap();
        let history = TaskHistory::open(root.path()).unwrap();
        let task = history.begin("request", "forecast", request()).unwrap();
        let id = task.id().to_owned();
        task.finish(&Ok(json!({"model_results":{"gfs":{}}})), false)
            .unwrap();
        let row = &history.list(10).unwrap()["tasks"][0];
        let sha = row["result_ref"]["snapshot"]["sha256"].as_str().unwrap();
        let path = root.path().join("snapshots").join(format!("{sha}.json"));
        fs::write(&path, b"tampered").unwrap();
        assert!(history.restore(&id).is_err());
    }
}
