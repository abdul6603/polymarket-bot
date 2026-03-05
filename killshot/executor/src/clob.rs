use reqwest::Client;
use serde_json::Value;
use std::time::Instant;
use tracing::{info, warn};

use crate::auth::build_l2_headers;
use crate::config::Config;

const CLOB_ORDER_URL: &str = "https://clob.polymarket.com/order";
const CLOB_ORDERS_URL: &str = "https://clob.polymarket.com/orders";

/// POST the signed order to Polymarket CLOB and parse the response.
pub async fn post_order(
    client: &Client,
    config: &Config,
    order_json: Value,
) -> ClobResult {
    let start = Instant::now();

    // Compact JSON serialization (no spaces) — must match what HMAC signs
    let body = serde_json::to_string(&order_json).unwrap();

    // Build L2 auth headers
    let headers = build_l2_headers(config, "/order", &body);

    let mut req = client
        .post(CLOB_ORDER_URL)
        .header("Content-Type", "application/json")
        .body(body);

    for (k, v) in &headers {
        req = req.header(k, v);
    }

    match req.send().await {
        Ok(resp) => {
            let status_code = resp.status();
            let text = resp.text().await.unwrap_or_default();
            let total_latency = start.elapsed().as_millis() as u64;

            info!("CLOB response ({status_code}): {}", truncate(&text, 500));

            if !status_code.is_success() {
                return ClobResult {
                    success: false,
                    status: "error".into(),
                    order_id: String::new(),
                    avg_price: 0.0,
                    total_shares: 0.0,
                    latency_ms: total_latency,
                    error: Some(format!("HTTP {}: {}", status_code, truncate(&text, 200))),
                };
            }

            parse_clob_response(&text, total_latency)
        }
        Err(e) => {
            let total_latency = start.elapsed().as_millis() as u64;
            warn!("CLOB request failed: {e}");
            ClobResult {
                success: false,
                status: "error".into(),
                order_id: String::new(),
                avg_price: 0.0,
                total_shares: 0.0,
                latency_ms: total_latency,
                error: Some(format!("Request failed: {}", truncate(&e.to_string(), 200))),
            }
        }
    }
}

/// POST batch orders to Polymarket CLOB (POST /orders with JSON array body).
pub async fn post_orders_batch(
    client: &Client,
    config: &Config,
    orders_json: Vec<Value>,
) -> BatchClobResult {
    let start = Instant::now();

    let body_value = Value::Array(orders_json);
    let body = serde_json::to_string(&body_value).unwrap();

    // HMAC signs /orders (plural)
    let headers = build_l2_headers(config, "/orders", &body);

    let mut req = client
        .post(CLOB_ORDERS_URL)
        .header("Content-Type", "application/json")
        .body(body);

    for (k, v) in &headers {
        req = req.header(k, v);
    }

    match req.send().await {
        Ok(resp) => {
            let status_code = resp.status();
            let text = resp.text().await.unwrap_or_default();
            let total_latency = start.elapsed().as_millis() as u64;

            info!("CLOB batch response ({status_code}): {}", truncate(&text, 500));

            if !status_code.is_success() {
                return BatchClobResult {
                    results: vec![],
                    latency_ms: total_latency,
                    error: Some(format!("HTTP {}: {}", status_code, truncate(&text, 200))),
                };
            }

            parse_batch_response(&text, total_latency)
        }
        Err(e) => {
            let total_latency = start.elapsed().as_millis() as u64;
            warn!("CLOB batch request failed: {e}");
            BatchClobResult {
                results: vec![],
                latency_ms: total_latency,
                error: Some(format!("Request failed: {}", truncate(&e.to_string(), 200))),
            }
        }
    }
}

fn parse_batch_response(text: &str, latency_ms: u64) -> BatchClobResult {
    // Batch response is a JSON array of individual order responses
    let v: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(e) => {
            return BatchClobResult {
                results: vec![],
                latency_ms,
                error: Some(format!("JSON parse error: {e}")),
            }
        }
    };

    let items = if let Some(arr) = v.as_array() {
        arr.clone()
    } else {
        // Single response wrapped — treat as one-element batch
        vec![v]
    };

    let results: Vec<ClobResult> = items
        .iter()
        .map(|item| {
            let text = serde_json::to_string(item).unwrap_or_default();
            parse_clob_response(&text, latency_ms)
        })
        .collect();

    BatchClobResult {
        results,
        latency_ms,
        error: None,
    }
}

fn parse_clob_response(text: &str, latency_ms: u64) -> ClobResult {
    let v: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(e) => {
            return ClobResult {
                success: false,
                status: "parse_error".into(),
                order_id: String::new(),
                avg_price: 0.0,
                total_shares: 0.0,
                latency_ms,
                error: Some(format!("JSON parse error: {e}")),
            }
        }
    };

    let order_id = v
        .get("orderID")
        .or_else(|| v.get("id"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let status = v
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    // Calculate avg_price from matched orders if available
    let (avg_price, total_shares) = extract_matched_info(&v);

    let success = !order_id.is_empty();

    ClobResult {
        success,
        status,
        order_id,
        avg_price,
        total_shares,
        latency_ms,
        error: if success {
            None
        } else {
            v.get("error_msg")
                .or_else(|| v.get("error"))
                .and_then(|v| v.as_str())
                .map(String::from)
                .or_else(|| Some("No orderID in response".into()))
        },
    }
}

/// Extract weighted average price and total shares from matched orders.
fn extract_matched_info(v: &Value) -> (f64, f64) {
    if let Some(matches) = v.get("matchedOrders").and_then(|m| m.as_array()) {
        let mut total_cost = 0.0_f64;
        let mut total_size = 0.0_f64;

        for m in matches {
            let price = val_as_f64(m.get("price"));
            let size = val_as_f64(m.get("matchSize").or_else(|| m.get("size")));
            total_cost += price * size;
            total_size += size;
        }

        if total_size > 0.0 {
            return (total_cost / total_size, total_size);
        }
    }

    // Fallback: try top-level fields
    (val_as_f64(v.get("price")), val_as_f64(v.get("size")))
}

/// Parse a JSON value as f64 — handles both number and string representations.
fn val_as_f64(v: Option<&Value>) -> f64 {
    match v {
        Some(val) => val
            .as_f64()
            .or_else(|| val.as_str().and_then(|s| s.parse().ok()))
            .unwrap_or(0.0),
        None => 0.0,
    }
}

fn truncate(s: &str, max: usize) -> &str {
    if s.len() > max {
        &s[..max]
    } else {
        s
    }
}

pub struct ClobResult {
    pub success: bool,
    pub status: String,
    pub order_id: String,
    pub avg_price: f64,
    pub total_shares: f64,
    pub latency_ms: u64,
    pub error: Option<String>,
}

pub struct BatchClobResult {
    pub results: Vec<ClobResult>,
    pub latency_ms: u64,
    pub error: Option<String>,
}
