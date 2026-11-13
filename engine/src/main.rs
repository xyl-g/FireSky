use serde_json::Value;
use std::{
    io::{self, Read},
    path::PathBuf,
};

fn usage() -> String {
    "Usage: firesky-engine [serve | desktop [--workspace PATH] | \
desktop-call --workspace PATH METHOD [--params JSON | --input PATH] | \
http [--workspace PATH] [--host HOST] [--port PORT] | \
call METHOD [--params JSON | --input PATH] | \
forecast --input PATH [--regional] [--workspace PATH] | \
desktop-location [--config PATH]]"
        .into()
}
fn take_option(args: &mut Vec<String>, key: &str) -> Result<Option<String>, String> {
    if let Some(index) = args.iter().position(|v| v == key) {
        if index + 1 >= args.len() {
            return Err(format!("{key} requires a value"));
        }
        args.remove(index);
        return Ok(Some(args.remove(index)));
    }
    Ok(None)
}
fn workspace(args: &mut Vec<String>) -> Result<PathBuf, String> {
    Ok(take_option(args, "--workspace")?
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("FIRESKY_WORKSPACE").map(PathBuf::from))
        .unwrap_or(std::env::current_dir().map_err(|e| e.to_string())?))
}
fn input(args: &mut Vec<String>) -> Result<Value, String> {
    let path = take_option(args, "--input")?;
    let params = take_option(args, "--params")?;
    if path.is_some() && params.is_some() {
        return Err("choose --input or --params".into());
    }
    let text = match (path, params) {
        (_, Some(text)) => text,
        (Some(path), _) => {
            let file = std::fs::File::open(path).map_err(|e| e.to_string())?;
            let mut text = String::new();
            file.take(1024 * 1024 + 1)
                .read_to_string(&mut text)
                .map_err(|e| e.to_string())?;
            text
        }
        _ => "{}".into(),
    };
    if text.len() > 1024 * 1024 {
        return Err("JSON input exceeds 1 MiB".into());
    }
    serde_json::from_str(&text).map_err(|e| e.to_string())
}
fn no_extra(args: &[String]) -> Result<(), String> {
    if args.is_empty() {
        Ok(())
    } else {
        Err(usage())
    }
}
async fn run(mut args: Vec<String>) -> Result<(), String> {
    let command = if args.is_empty() {
        "serve".to_owned()
    } else {
        args.remove(0)
    };
    if command == "http" {
        let root = workspace(&mut args)?;
        let host = take_option(&mut args, "--host")?.unwrap_or_else(|| "127.0.0.1".into());
        let port = take_option(&mut args, "--port")?.unwrap_or_else(|| "8765".into());
        let port = port.parse::<u16>().map_err(|_| "invalid HTTP port")?;
        no_extra(&args)?;
        let address = if host.contains(':') {
            format!("[{host}]:{port}")
        } else {
            format!("{host}:{port}")
        };
        return firesky_engine::http::serve(&root, &address).await;
    }
    tokio::task::spawn_blocking(move || run_sync(command, args))
        .await
        .map_err(|e| e.to_string())?
}
fn run_sync(command: String, mut args: Vec<String>) -> Result<(), String> {
    match command.as_str() {
        "serve" => {
            no_extra(&args)?;
            firesky_engine::rpc::serve(io::stdin().lock(), io::stdout().lock())
                .map_err(|e| e.to_string())
        }
        "desktop" => {
            let root = workspace(&mut args)?;
            no_extra(&args)?;
            firesky_engine::desktop_runtime::serve(io::stdin().lock(), io::stdout(), &root)
                .map_err(|e| e.to_string())
        }
        "desktop-call" => {
            let root = workspace(&mut args)?;
            if args.is_empty() {
                return Err(usage());
            }
            let method = args.remove(0);
            let params = input(&mut args)?;
            no_extra(&args)?;
            let value =
                firesky_engine::desktop_runtime::Runtime::new(&root)?.dispatch(&method, params)?;
            println!(
                "{}",
                serde_json::to_string(&value).map_err(|e| e.to_string())?
            );
            Ok(())
        }
        "call" => {
            if args.is_empty() {
                return Err(usage());
            }
            let method = args.remove(0);
            let params = input(&mut args)?;
            no_extra(&args)?;
            let value = firesky_engine::rpc::dispatch(&method, params)?;
            println!(
                "{}",
                serde_json::to_string(&value).map_err(|e| e.to_string())?
            );
            Ok(())
        }
        "forecast" => {
            let root = workspace(&mut args)?;
            let regional = args
                .iter()
                .position(|v| v == "--regional")
                .map(|i| args.remove(i))
                .is_some();
            let payload = input(&mut args)?;
            no_extra(&args)?;
            let value =
                firesky_engine::desktop_runtime::Runtime::new(&root)?.compute(payload, regional)?;
            println!(
                "{}",
                serde_json::to_string(&value).map_err(|e| e.to_string())?
            );
            Ok(())
        }
        "desktop-location" => {
            let settings = match take_option(&mut args, "--config")? {
                Some(path) => {
                    firesky_engine::desktop_location::Settings::load(std::path::Path::new(&path))?
                }
                None => Default::default(),
            };
            no_extra(&args)?;
            firesky_engine::desktop_location::serve(io::stdin().lock(), io::stdout(), settings)
                .map_err(|e| e.to_string())
        }
        "--help" | "-h" => {
            println!("{}", usage());
            Ok(())
        }
        _ => Err(usage()),
    }
}
#[tokio::main]
async fn main() {
    if let Err(error) = run(std::env::args().skip(1).collect()).await {
        eprintln!("firesky-engine: {error}");
        std::process::exit(1);
    }
}
