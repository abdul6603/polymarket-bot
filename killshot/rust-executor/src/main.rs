mod config;
mod executor;
mod types;

use std::sync::Arc;
use std::time::Instant;

use axum::extract::State;
use axum::routing::{get, post};
use axum::{Json, Router};
use std::sync::atomic::Ordering;
use tokio::net::TcpListener;
use tokio::signal;
use tracing::info;

use crate::config::ExecutorConfig;
use crate::executor::Executor;
use crate::types::{HealthResponse, OrderRequest, OrderResponse};

struct AppState {
    executor: Executor,
    start_time: Instant,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Structured logging (RUST_LOG=info by default)
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "killshot_executor=info".into()),
        )
        .init();

    info!("Killshot Rust Executor starting...");

    // Load config from .env
    let config = ExecutorConfig::from_env()?;
    let bind_addr = config.bind_addr.clone();

    // Create and authenticate the executor
    let executor = Executor::new(config).await?;

    let state = Arc::new(AppState {
        executor,
        start_time: Instant::now(),
    });

    // Routes
    let app = Router::new()
        .route("/order", post(handle_order))
        .route("/health", get(handle_health))
        .with_state(state);

    // Bind and serve
    let listener = TcpListener::bind(&bind_addr).await?;
    info!("Listening on {bind_addr}");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;

    info!("Executor shut down cleanly");
    Ok(())
}

/// POST /order — sign and submit a single order to the CLOB.
async fn handle_order(
    State(state): State<Arc<AppState>>,
    Json(req): Json<OrderRequest>,
) -> Json<OrderResponse> {
    info!(
        token_id = %req.token_id.get(..16).unwrap_or(&req.token_id),
        price = req.price,
        size = req.size,
        side = %req.side,
        "Order received"
    );
    Json(state.executor.execute_order(req).await)
}

/// GET /health — uptime and order statistics.
async fn handle_health(State(state): State<Arc<AppState>>) -> Json<HealthResponse> {
    let sent = state.executor.stats.orders_sent.load(Ordering::Relaxed);
    let filled = state.executor.stats.orders_filled.load(Ordering::Relaxed);

    Json(HealthResponse {
        status: "ok".to_string(),
        uptime_s: state.start_time.elapsed().as_secs_f64(),
        orders_sent: sent,
        orders_filled: filled,
        avg_latency_ms: state.executor.stats.avg_latency_ms(),
    })
}

/// Wait for SIGTERM or SIGINT for graceful shutdown.
async fn shutdown_signal() {
    let ctrl_c = async {
        signal::ctrl_c()
            .await
            .expect("Failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        signal::unix::signal(signal::unix::SignalKind::terminate())
            .expect("Failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => info!("Received SIGINT — shutting down"),
        _ = terminate => info!("Received SIGTERM — shutting down"),
    }
}
