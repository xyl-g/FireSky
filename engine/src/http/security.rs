//! Single-user HTTP boundary. Secrets are never included in diagnostics.
use axum::{
    extract::{Request, State},
    http::{header, HeaderValue, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    net::{IpAddr, SocketAddr},
    path::Path,
    sync::Arc,
};

#[derive(Clone)]
pub(super) struct Policy {
    allow_remote: bool,
    token_digest: Option<Arc<[u8; 32]>>,
}

impl Policy {
    pub(super) fn local() -> Self {
        Self {
            allow_remote: false,
            token_digest: None,
        }
    }

    fn configured(allow_remote: bool, token: Option<String>) -> Result<Self, String> {
        if allow_remote && token.is_none() {
            return Err(
                "remote HTTP requires FIRESKY_HTTP_TOKEN or FIRESKY_HTTP_TOKEN_FILE".into(),
            );
        }
        let token_digest = token
            .map(|token| -> Result<Arc<[u8; 32]>, String> {
                if !(32..=4096).contains(&token.len())
                    || !token
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || b"-_.~".contains(&byte))
                {
                    return Err(
                        "HTTP token must contain 32..4096 ASCII letters, digits, or -_.~".into(),
                    );
                }
                Ok(Arc::new(Sha256::digest(token.as_bytes()).into()))
            })
            .transpose()?;
        Ok(Self {
            allow_remote,
            token_digest,
        })
    }

    pub(super) fn from_env() -> Result<Self, String> {
        let allow_remote = match std::env::var("FIRESKY_HTTP_ALLOW_REMOTE").as_deref() {
            Err(std::env::VarError::NotPresent) | Ok("0") => false,
            Ok("1") => true,
            _ => return Err("FIRESKY_HTTP_ALLOW_REMOTE must be 0 or 1".into()),
        };
        let token = std::env::var("FIRESKY_HTTP_TOKEN")
            .map(Some)
            .or_else(|error| match error {
                std::env::VarError::NotPresent => Ok(None),
                _ => Err("HTTP token is not valid UTF-8"),
            })?;
        let token_file = std::env::var_os("FIRESKY_HTTP_TOKEN_FILE");
        if token.is_some() && token_file.is_some() {
            return Err("choose FIRESKY_HTTP_TOKEN or FIRESKY_HTTP_TOKEN_FILE, not both".into());
        }
        let token = match token_file {
            Some(path) => Some(read_token(Path::new(&path))?),
            None => token,
        };
        Self::configured(allow_remote, token)
    }

    pub(super) fn validate_bind(&self, address: SocketAddr) -> Result<(), String> {
        if !address.ip().is_loopback() && !self.allow_remote {
            return Err(
                "non-loopback HTTP binding requires FIRESKY_HTTP_ALLOW_REMOTE=1 and an HTTP token"
                    .into(),
            );
        }
        Ok(())
    }

    fn authenticated(&self, request: &Request) -> bool {
        let Some(expected) = &self.token_digest else {
            return true;
        };
        let Some(value) = request
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .filter(|value| value.len() <= 8192)
        else {
            return false;
        };
        if request
            .headers()
            .get_all(header::AUTHORIZATION)
            .iter()
            .count()
            != 1
        {
            return false;
        }
        let Some((scheme, value)) = value.split_once(' ') else {
            return false;
        };
        let token = if scheme.eq_ignore_ascii_case("Bearer") {
            value.as_bytes().to_vec()
        } else if scheme.eq_ignore_ascii_case("Basic") {
            let Ok(decoded) = STANDARD.decode(value) else {
                return false;
            };
            let Some(token) = decoded.strip_prefix(b"firesky:") else {
                return false;
            };
            token.to_vec()
        } else {
            return false;
        };
        let actual = Sha256::digest(&token);
        actual
            .iter()
            .zip(expected.iter())
            .fold(0u8, |diff, (a, b)| diff | (a ^ b))
            == 0
    }
}

fn read_token(path: &Path) -> Result<String, String> {
    let metadata = fs::symlink_metadata(path).map_err(|_| "cannot read HTTP token file")?;
    if !metadata.is_file() || metadata.len() > 4097 {
        return Err("HTTP token file must be a regular file of at most 4097 bytes".into());
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options
        .open(path)
        .map_err(|_| "cannot read HTTP token file")?;
    let metadata = file.metadata().map_err(|_| "cannot read HTTP token file")?;
    if !metadata.is_file() || metadata.len() > 4097 {
        return Err("HTTP token file must be a regular file of at most 4097 bytes".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err("HTTP token file must not be accessible to group or other users".into());
        }
    }
    let mut text = String::new();
    file.take(4098)
        .read_to_string(&mut text)
        .map_err(|_| "cannot read HTTP token file")?;
    Ok(text.trim_end_matches(['\r', '\n']).to_owned())
}

fn authority(request: &Request) -> Option<&str> {
    request
        .headers()
        .get(header::HOST)
        .and_then(|value| value.to_str().ok())
        .or_else(|| request.uri().authority().map(|value| value.as_str()))
}

fn loopback_authority(authority: &str) -> bool {
    let Ok(value) = authority.parse::<axum::http::uri::Authority>() else {
        return false;
    };
    let host = value.host().trim_matches(['[', ']']);
    host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<IpAddr>()
            .is_ok_and(|address| address.is_loopback())
}

fn same_origin(request: &Request) -> bool {
    if request.headers().get_all(header::HOST).iter().count() > 1
        || request.headers().get_all(header::ORIGIN).iter().count() > 1
    {
        return false;
    }
    if request
        .headers()
        .get("sec-fetch-site")
        .is_some_and(|value| value == "cross-site")
    {
        return false;
    }
    let Some(raw) = request.headers().get(header::ORIGIN) else {
        return true;
    };
    let Some(host) = authority(request) else {
        return false;
    };
    let Ok(origin) = raw
        .to_str()
        .ok()
        .and_then(|raw| reqwest::Url::parse(raw).ok())
        .ok_or(())
    else {
        return false;
    };
    if !matches!(origin.scheme(), "http" | "https")
        || !origin.username().is_empty()
        || origin.password().is_some()
        || origin.query().is_some()
        || origin.fragment().is_some()
        || origin.path() != "/"
    {
        return false;
    }
    let Ok(expected) = reqwest::Url::parse(&format!("{}://{host}/", origin.scheme())) else {
        return false;
    };
    origin.origin() == expected.origin()
}

fn denied(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"error":{"code":code,"message":message}})),
    )
        .into_response()
}

pub(super) async fn enforce(
    State(policy): State<Policy>,
    request: Request,
    next: Next,
) -> Response {
    let health = matches!(
        request.uri().path(),
        "/api/health" | "/api/v1/firesky/health"
    );
    let mut response = if health {
        next.run(request).await
    } else if !policy.allow_remote
        && authority(&request).is_some_and(|host| !loopback_authority(host))
    {
        denied(
            StatusCode::FORBIDDEN,
            "untrusted_host",
            "local HTTP requires a loopback Host",
        )
    } else if !same_origin(&request) {
        denied(
            StatusCode::FORBIDDEN,
            "untrusted_origin",
            "cross-origin HTTP requests are not allowed",
        )
    } else if !policy.authenticated(&request) {
        let mut response = denied(
            StatusCode::UNAUTHORIZED,
            "authentication_required",
            "HTTP authentication required",
        );
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            HeaderValue::from_static("Basic realm=\"FireSky\", charset=\"UTF-8\""),
        );
        response
    } else {
        next.run(request).await
    };
    response.headers_mut().insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    response
        .headers_mut()
        .insert("x-frame-options", HeaderValue::from_static("DENY"));
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, middleware, routing::get, Router};
    use tower::ServiceExt;
    const TOKEN: &str = "0123456789abcdef0123456789abcdef";

    fn app(policy: Policy) -> Router {
        Router::new()
            .route("/", get(|| async { "private" }))
            .route("/api/v1/firesky/data/prune", get(|| async { "private" }))
            .route("/api/v1/firesky/health", get(|| async { "ok" }))
            .layer(middleware::from_fn_with_state(policy, enforce))
    }
    async fn status(policy: Policy, path: &str, headers: &[(&str, &str)]) -> StatusCode {
        let mut request = Request::builder().uri(path);
        for (key, value) in headers {
            request = request.header(*key, *value);
        }
        app(policy)
            .oneshot(request.body(Body::empty()).unwrap())
            .await
            .unwrap()
            .status()
    }
    #[test]
    fn remote_bind_requires_explicit_enablement_and_valid_secret() {
        assert!(Policy::local()
            .validate_bind("127.0.0.1:0".parse().unwrap())
            .is_ok());
        assert!(Policy::local()
            .validate_bind("[::1]:0".parse().unwrap())
            .is_ok());
        assert!(Policy::local()
            .validate_bind("0.0.0.0:0".parse().unwrap())
            .is_err());
        assert!(Policy::configured(true, None).is_err());
        for token in [
            "short",
            "secret secret secret secret secret secret",
            &"x".repeat(4097),
        ] {
            assert!(Policy::configured(true, Some(token.into())).is_err());
        }
        assert!(Policy::configured(true, Some(TOKEN.into()))
            .unwrap()
            .validate_bind("0.0.0.0:0".parse().unwrap())
            .is_ok());
    }
    #[tokio::test]
    async fn local_requests_reject_rebinding_cross_origin_and_null_origin() {
        assert_eq!(
            status(Policy::local(), "/", &[("host", "localhost:8765")]).await,
            StatusCode::OK
        );
        assert_eq!(
            status(
                Policy::local(),
                "/",
                &[("host", "[::1]:8765"), ("origin", "http://[::1]:8765")]
            )
            .await,
            StatusCode::OK
        );
        for headers in [
            vec![("host", "attacker.example:8765")],
            vec![
                ("host", "localhost:8765"),
                ("origin", "https://attacker.example"),
            ],
            vec![("host", "localhost:8765"), ("origin", "null")],
            vec![("host", "localhost:8765"), ("sec-fetch-site", "cross-site")],
        ] {
            assert_eq!(
                status(Policy::local(), "/", &headers).await,
                StatusCode::FORBIDDEN
            );
        }
    }
    #[tokio::test]
    async fn remote_auth_covers_page_and_management_and_allows_health() {
        let policy = Policy::configured(true, Some(TOKEN.into())).unwrap();
        for path in ["/", "/api/v1/firesky/data/prune"] {
            assert_eq!(
                status(policy.clone(), path, &[]).await,
                StatusCode::UNAUTHORIZED
            );
            assert_eq!(
                status(policy.clone(), path, &[("authorization", "Bearer wrong")]).await,
                StatusCode::UNAUTHORIZED
            );
            assert_eq!(
                status(
                    policy.clone(),
                    path,
                    &[("authorization", &format!("Bearer {TOKEN}"))]
                )
                .await,
                StatusCode::OK
            );
            assert_eq!(
                status(
                    policy.clone(),
                    path,
                    &[(
                        "authorization",
                        &format!("Basic {}", STANDARD.encode(format!("firesky:{TOKEN}")))
                    )]
                )
                .await,
                StatusCode::OK
            );
        }
        assert_eq!(
            status(policy, "/api/v1/firesky/health", &[]).await,
            StatusCode::OK
        );
    }
    #[tokio::test]
    async fn proxy_headers_query_tokens_and_cross_origin_credentials_do_not_bypass_policy() {
        let policy = Policy::configured(true, Some(TOKEN.into())).unwrap();
        assert_eq!(
            status(
                policy.clone(),
                &format!("/?token={TOKEN}"),
                &[
                    ("x-forwarded-for", "127.0.0.1"),
                    ("x-forwarded-host", "localhost")
                ]
            )
            .await,
            StatusCode::UNAUTHORIZED
        );
        assert_eq!(
            status(
                policy.clone(),
                "/",
                &[(
                    "authorization",
                    &format!("Basic {}", STANDARD.encode(format!("admin:{TOKEN}")))
                )]
            )
            .await,
            StatusCode::UNAUTHORIZED
        );
        assert_eq!(
            status(
                policy.clone(),
                "/",
                &[
                    ("host", "firesky.example"),
                    ("authorization", &format!("Bearer {TOKEN}")),
                    ("origin", "https://attacker.example")
                ]
            )
            .await,
            StatusCode::FORBIDDEN
        );
        assert_eq!(
            status(
                policy,
                "/",
                &[
                    ("host", "firesky.example"),
                    ("authorization", &format!("Bearer {TOKEN}")),
                    ("origin", "https://firesky.example")
                ]
            )
            .await,
            StatusCode::OK
        );
    }
    #[test]
    fn token_files_are_bounded_regular_and_private() {
        use std::io::Write;
        let mut file = tempfile::NamedTempFile::new().unwrap();
        writeln!(file, "{TOKEN}").unwrap();
        assert_eq!(read_token(file.path()).unwrap(), TOKEN);
        #[cfg(unix)]
        {
            use std::os::unix::fs::{symlink, PermissionsExt};
            let dir = tempfile::tempdir().unwrap();
            let link = dir.path().join("token");
            symlink(file.path(), &link).unwrap();
            assert!(read_token(&link).is_err());
            fs::set_permissions(file.path(), fs::Permissions::from_mode(0o644)).unwrap();
            assert!(read_token(file.path()).is_err());
        }
    }
}
