use axum::{
    Json,
    extract::State,
    http::StatusCode,
    response::IntoResponse,
};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tracing::{info, warn};

use crate::clob;
use crate::config::Config;
use crate::order::{build_and_sign_order, order_to_clob_json, to_checksum};

pub struct AppState {
    pub config: Config,
    pub http_client: reqwest::Client,
}

#[derive(Deserialize)]
pub struct OrderRequest {
    pub token_id: String,
    pub price: f64,
    pub size: f64,
    pub side: String,
    pub order_type: String,
    #[serde(default = "default_neg_risk")]
    pub neg_risk: bool,
}

fn default_neg_risk() -> bool {
    false
}

#[derive(Serialize)]
pub struct OrderResponse {
    pub success: bool,
    pub status: String,
    pub avg_price: f64,
    pub total_shares: f64,
    pub order_id: String,
    pub latency_ms: u64,
    pub error: Option<String>,
}

pub async fn handle_order(
    State(state): State<Arc<AppState>>,
    Json(req): Json<OrderRequest>,
) -> impl IntoResponse {
    info!(
        "Order: {} {} @ {} ({})",
        req.side, req.size, req.price, req.order_type
    );

    // Validate
    if req.side != "BUY" && req.side != "SELL" {
        return (
            StatusCode::BAD_REQUEST,
            Json(OrderResponse {
                success: false,
                status: "error".into(),
                avg_price: 0.0,
                total_shares: 0.0,
                order_id: String::new(),
                latency_ms: 0,
                error: Some("side must be BUY or SELL".into()),
            }),
        );
    }

    // Build and sign the order
    let signed = build_and_sign_order(
        &state.config,
        &req.token_id,
        req.price,
        req.size,
        &req.side,
        req.neg_risk,
    )
    .await;

    // Build CLOB JSON payload
    let clob_json = order_to_clob_json(&signed, &state.config.api_key, &req.order_type);

    // POST to CLOB
    let result = clob::post_order(&state.http_client, &state.config, clob_json).await;

    let resp = OrderResponse {
        success: result.success,
        status: result.status,
        avg_price: result.avg_price,
        total_shares: result.total_shares,
        order_id: result.order_id,
        latency_ms: result.latency_ms,
        error: result.error,
    };

    if resp.success {
        info!(
            "OK: {} {} | {}ms | id={}",
            resp.status, resp.avg_price, resp.latency_ms, resp.order_id
        );
    } else {
        warn!(
            "FAIL: {} | {}ms | {}",
            resp.status,
            resp.latency_ms,
            resp.error.as_deref().unwrap_or("unknown")
        );
    }

    (StatusCode::OK, Json(resp))
}

pub async fn handle_orders(
    State(state): State<Arc<AppState>>,
    Json(reqs): Json<Vec<OrderRequest>>,
) -> impl IntoResponse {
    info!("Batch order: {} orders", reqs.len());

    if reqs.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(BatchOrderResponse {
                results: vec![],
                latency_ms: 0,
                error: Some("empty order list".into()),
            }),
        );
    }

    // Build and sign all orders
    let mut clob_jsons = Vec::with_capacity(reqs.len());
    for req in &reqs {
        if req.side != "BUY" && req.side != "SELL" {
            return (
                StatusCode::BAD_REQUEST,
                Json(BatchOrderResponse {
                    results: vec![],
                    latency_ms: 0,
                    error: Some(format!("side must be BUY or SELL, got '{}'", req.side)),
                }),
            );
        }
        let signed = build_and_sign_order(
            &state.config,
            &req.token_id,
            req.price,
            req.size,
            &req.side,
            req.neg_risk,
        )
        .await;
        clob_jsons.push(order_to_clob_json(&signed, &state.config.api_key, &req.order_type));
    }

    // POST batch to CLOB
    let batch_result =
        clob::post_orders_batch(&state.http_client, &state.config, clob_jsons).await;

    let results: Vec<OrderResponse> = batch_result
        .results
        .into_iter()
        .map(|r| OrderResponse {
            success: r.success,
            status: r.status,
            avg_price: r.avg_price,
            total_shares: r.total_shares,
            order_id: r.order_id,
            latency_ms: r.latency_ms,
            error: r.error,
        })
        .collect();

    let ok_count = results.iter().filter(|r| r.success).count();
    info!(
        "Batch done: {}/{} OK | {}ms",
        ok_count,
        results.len(),
        batch_result.latency_ms
    );

    (
        StatusCode::OK,
        Json(BatchOrderResponse {
            results,
            latency_ms: batch_result.latency_ms,
            error: batch_result.error,
        }),
    )
}

#[derive(Serialize)]
pub struct BatchOrderResponse {
    pub results: Vec<OrderResponse>,
    pub latency_ms: u64,
    pub error: Option<String>,
}

pub async fn handle_health(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let addr = to_checksum(&state.config.funder);
    Json(serde_json::json!({
        "status": "ok",
        "wallet": addr,
        "signer": to_checksum(&state.config.signer.address()),
    }))
}
