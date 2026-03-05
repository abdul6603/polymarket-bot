mod auth;
mod clob;
mod config;
mod handler;
mod order;

use axum::{Router, routing::{get, post}};
use handler::{AppState, handle_health, handle_order, handle_orders};
use std::sync::Arc;
use tracing::info;

#[tokio::main]
async fn main() {
    // Init logging
    tracing_subscriber::fmt()
        .with_target(false)
        .with_level(true)
        .init();

    // Load .env — try explicit path first, then fall back to CWD
    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/macuser".into());
    let env_path = std::path::PathBuf::from(&home).join("polymarket-bot/.env");
    if dotenvy::from_path(&env_path).is_err() {
        let _ = dotenvy::dotenv();
    }

    // Load config from env
    let config = config::Config::from_env();
    let wallet = order::to_checksum(&config.funder);
    let signer = order::to_checksum(&config.signer.address());
    info!("Killshot Executor starting");
    info!("Wallet (funder): {wallet}");
    info!("Signer: {signer}");

    // HTTP client with connection pooling + keep-alive
    let http_client = reqwest::Client::builder()
        .pool_max_idle_per_host(4)
        .tcp_keepalive(std::time::Duration::from_secs(30))
        .timeout(std::time::Duration::from_secs(5))
        .build()
        .expect("Failed to build HTTP client");

    let state = Arc::new(AppState {
        config,
        http_client,
    });

    let app = Router::new()
        .route("/order", post(handle_order))
        .route("/orders", post(handle_orders))
        .route("/health", get(handle_health))
        .with_state(state);

    let addr = "0.0.0.0:9999";
    info!("Listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("Failed to bind port 9999");
    axum::serve(listener, app).await.expect("Server error");
}
