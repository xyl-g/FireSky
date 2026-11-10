//! Native desktop session authority.
//!
//! The Python desktop service keeps a compatibility mirror of the report
//! object, but the Rust child owns token generation, expiry and the
//! single-use/dismissed state for the opt-in native backend.

use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Mutex, OnceLock,
};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const SESSION_SECONDS: u64 = 1800;
const MAX_SESSIONS_PER_OWNER: usize = 32;
const MAX_OWNER_BYTES: usize = 128;
const MAX_TOKEN_BYTES: usize = 128;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    action: String,
    owner: String,
    #[serde(default)]
    session: Option<String>,
    #[serde(default)]
    kind: Option<String>,
    #[serde(default)]
    payload: Option<Value>,
    #[serde(default)]
    report: Option<Value>,
}

struct Session {
    kind: String,
    payload: Value,
    report: Value,
    created: Instant,
    dismissed: bool,
    consumed: bool,
}

static SESSIONS: OnceLock<Mutex<HashMap<(String, String), Session>>> = OnceLock::new();
static TOKEN_SEQUENCE: AtomicU64 = AtomicU64::new(0);

fn store() -> &'static Mutex<HashMap<(String, String), Session>> {
    SESSIONS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn validate_owner(owner: &str) -> Result<(), String> {
    if owner.is_empty() || owner.len() > MAX_OWNER_BYTES {
        return Err("desktop session owner is invalid".into());
    }
    Ok(())
}

fn validate_token(token: &str) -> Result<(), String> {
    if token.is_empty() || token.len() > MAX_TOKEN_BYTES {
        return Err("desktop session token is invalid".into());
    }
    Ok(())
}

fn object(value: Option<Value>, field: &str) -> Result<Value, String> {
    match value {
        Some(value) if value.is_object() => Ok(value),
        _ => Err(format!("desktop session {field} must be an object")),
    }
}

fn kind(value: Option<String>) -> Result<String, String> {
    let value = value.ok_or("desktop session kind is missing")?;
    if matches!(value.as_str(), "point" | "regional" | "nowcast") {
        Ok(value)
    } else {
        Err("desktop session kind is invalid".into())
    }
}

fn token(owner: &str) -> String {
    let sequence = TOKEN_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let mut digest = Sha256::new();
    digest.update(owner.as_bytes());
    digest.update(nanos.to_le_bytes());
    digest.update(sequence.to_le_bytes());
    digest.update(std::process::id().to_le_bytes());
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn expired(session: &Session) -> bool {
    session.created.elapsed() > Duration::from_secs(SESSION_SECONDS)
}

fn purge(sessions: &mut HashMap<(String, String), Session>) {
    sessions.retain(|_, session| !session.dismissed && !session.consumed && !expired(session));
}

fn active_session<'a>(
    sessions: &'a mut HashMap<(String, String), Session>,
    owner: &str,
    token: &str,
) -> Result<&'a mut Session, String> {
    validate_token(token)?;
    let key = (owner.to_owned(), token.to_owned());
    let Some(session) = sessions.get_mut(&key) else {
        return Err("desktop session is expired or unknown".into());
    };
    if session.dismissed || session.consumed || expired(session) {
        return Err("desktop session is expired or closed".into());
    }
    Ok(session)
}

fn snapshot(token: &str, session: &Session) -> Value {
    json!({
        "contract": "rust-desktop-session-v1",
        "runtime_python_used": false,
        "session": token,
        "kind": session.kind,
        "payload": session.payload,
        "report": session.report,
    })
}

fn base_response(value: Value) -> Value {
    let mut response = Map::new();
    response.insert("contract".into(), json!("rust-desktop-session-v1"));
    response.insert("runtime_python_used".into(), json!(false));
    if let Value::Object(fields) = value {
        response.extend(fields);
    }
    Value::Object(response)
}

pub fn execute(params: Params) -> Result<Value, String> {
    validate_owner(&params.owner)?;
    let mut sessions = store()
        .lock()
        .map_err(|_| "desktop session store is unavailable".to_owned())?;
    purge(&mut sessions);
    match params.action.as_str() {
        "create" => {
            let kind = kind(params.kind)?;
            let payload = object(params.payload, "payload")?;
            let report = object(params.report, "report")?;
            let count = sessions
                .keys()
                .filter(|(owner, _)| owner == &params.owner)
                .count();
            if count >= MAX_SESSIONS_PER_OWNER {
                return Err("desktop session limit exceeded".into());
            }
            let token = token(&params.owner);
            sessions.insert(
                (params.owner, token.clone()),
                Session {
                    kind,
                    payload,
                    report,
                    created: Instant::now(),
                    dismissed: false,
                    consumed: false,
                },
            );
            Ok(base_response(json!({"session": token})))
        }
        "get" => {
            let token = params
                .session
                .as_deref()
                .ok_or("desktop session token is missing")?;
            let session = active_session(&mut sessions, &params.owner, token)?;
            Ok(snapshot(token, session))
        }
        "validate" => {
            let token = params
                .session
                .as_deref()
                .ok_or("desktop session token is missing")?;
            let session = active_session(&mut sessions, &params.owner, token)?;
            Ok(base_response(json!({
                "session": token,
                "kind": session.kind,
            })))
        }
        "update" => {
            let token = params
                .session
                .as_deref()
                .ok_or("desktop session token is missing")?;
            let session = active_session(&mut sessions, &params.owner, token)?;
            if params.payload.is_none() && params.report.is_none() {
                return Err("desktop session update is empty".into());
            }
            if let Some(payload) = params.payload {
                session.payload = object(Some(payload), "payload")?;
            }
            if let Some(report) = params.report {
                session.report = object(Some(report), "report")?;
            }
            Ok(snapshot(token, session))
        }
        "dismiss" => {
            let token = params.session.as_deref().unwrap_or_default();
            if !token.is_empty() {
                validate_token(token)?;
                if let Some(session) = sessions.get_mut(&(params.owner, token.to_owned())) {
                    session.dismissed = true;
                }
            }
            Ok(base_response(json!({"dismissed": true})))
        }
        "consume" => {
            let token = params
                .session
                .as_deref()
                .ok_or("desktop session token is missing")?;
            let session = active_session(&mut sessions, &params.owner, token)?;
            session.consumed = true;
            Ok(snapshot(token, session))
        }
        _ => Err("desktop session action is unknown".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create(owner: &str) -> String {
        execute(Params {
            action: "create".into(),
            owner: owner.into(),
            session: None,
            kind: Some("point".into()),
            payload: Some(json!({"model_source": "both"})),
            report: Some(json!({"ready": false})),
        })
        .unwrap()["session"]
            .as_str()
            .unwrap()
            .to_owned()
    }

    #[test]
    fn creates_reads_updates_and_consumes_single_use_tokens() {
        let owner = format!(
            "test-owner-{}",
            TOKEN_SEQUENCE.fetch_add(1, Ordering::Relaxed)
        );
        let token = create(&owner);
        let read = execute(Params {
            action: "get".into(),
            owner: owner.clone(),
            session: Some(token.clone()),
            kind: None,
            payload: None,
            report: None,
        })
        .unwrap();
        assert_eq!(read["payload"]["model_source"], "both");
        let validated = execute(Params {
            action: "validate".into(),
            owner: owner.clone(),
            session: Some(token.clone()),
            kind: None,
            payload: None,
            report: None,
        })
        .unwrap();
        assert_eq!(validated["kind"], "point");
        let updated = execute(Params {
            action: "update".into(),
            owner: owner.clone(),
            session: Some(token.clone()),
            kind: None,
            payload: None,
            report: Some(json!({"ready": true})),
        })
        .unwrap();
        assert_eq!(updated["report"]["ready"], true);
        let consumed = execute(Params {
            action: "consume".into(),
            owner: owner.clone(),
            session: Some(token.clone()),
            kind: None,
            payload: None,
            report: None,
        })
        .unwrap();
        assert_eq!(consumed["session"], token);
        assert!(execute(Params {
            action: "get".into(),
            owner,
            session: Some(token),
            kind: None,
            payload: None,
            report: None,
        })
        .is_err());
    }

    #[test]
    fn owners_are_isolated_and_dismiss_is_idempotent() {
        let owner = format!("owner-a-{}", TOKEN_SEQUENCE.fetch_add(1, Ordering::Relaxed));
        let other = format!("owner-b-{}", TOKEN_SEQUENCE.fetch_add(1, Ordering::Relaxed));
        let token = create(&owner);
        assert!(execute(Params {
            action: "get".into(),
            owner: other,
            session: Some(token.clone()),
            kind: None,
            payload: None,
            report: None,
        })
        .is_err());
        execute(Params {
            action: "dismiss".into(),
            owner: owner.clone(),
            session: Some(token.clone()),
            kind: None,
            payload: None,
            report: None,
        })
        .unwrap();
        execute(Params {
            action: "dismiss".into(),
            owner,
            session: Some(token),
            kind: None,
            payload: None,
            report: None,
        })
        .unwrap();
    }
}
