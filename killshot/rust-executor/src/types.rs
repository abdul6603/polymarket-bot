use serde::{Deserialize, Serialize};

/// Incoming order request from Python engine.
#[derive(Debug, Deserialize)]
pub struct OrderRequest {
    pub token_id: String,
    /// Limit price (0.01–0.99).
    pub price: f64,
    /// Size in shares (minimum 5).
    pub size: f64,
    /// "BUY" or "SELL".
    pub side: String,
    /// Order type — defaults to "FOK".
    #[serde(default = "default_order_type")]
    pub order_type: String,
    /// Negative-risk market flag — defaults to true (crypto up/down).
    #[serde(default = "default_neg_risk")]
    pub neg_risk: bool,
}

fn default_order_type() -> String {
    "FOK".to_string()
}

fn default_neg_risk() -> bool {
    true
}

/// Response sent back to Python engine.
#[derive(Debug, Serialize)]
pub struct OrderResponse {
    pub success: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub order_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub avg_price: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_shares: Option<f64>,
    pub latency_ms: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl OrderResponse {
    pub fn success(
        order_id: String,
        status: String,
        avg_price: f64,
        total_shares: f64,
        latency_ms: f64,
    ) -> Self {
        Self {
            success: true,
            order_id: Some(order_id),
            status: Some(status),
            avg_price: Some(avg_price),
            total_shares: Some(total_shares),
            latency_ms,
            error: None,
        }
    }

    pub fn failure(error: String, latency_ms: f64) -> Self {
        Self {
            success: false,
            order_id: None,
            status: None,
            avg_price: None,
            total_shares: None,
            latency_ms,
            error: Some(error),
        }
    }
}

/// Health check response.
#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: String,
    pub uptime_s: f64,
    pub orders_sent: u64,
    pub orders_filled: u64,
    pub avg_latency_ms: f64,
}
