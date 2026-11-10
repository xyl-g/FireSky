use std::io;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let outcome = match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        [] | ["serve"] => firesky_engine::rpc::serve(io::stdin().lock(), io::stdout().lock()),
        ["desktop-location"] => firesky_engine::desktop_location::serve(
            io::stdin().lock(),
            io::stdout(),
            Default::default(),
        ),
        ["desktop-location", "--config", path] => {
            match firesky_engine::desktop_location::Settings::load(std::path::Path::new(path)) {
                Ok(settings) => firesky_engine::desktop_location::serve(
                    io::stdin().lock(),
                    io::stdout(),
                    settings,
                ),
                Err(error) => Err(io::Error::other(error)),
            }
        }
        _ => {
            eprintln!("Usage: firesky-engine [serve | desktop-location [--config PATH]]");
            std::process::exit(2);
        }
    };
    if let Err(error) = outcome {
        eprintln!("firesky-engine: {error}");
        std::process::exit(1);
    }
}
