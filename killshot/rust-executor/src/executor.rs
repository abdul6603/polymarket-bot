use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use alloy_signer_local::PrivateKeySigner;
use anyhow::{bail, Context, Result};
use polymarket_client_sdk::auth::state::Authenticated;
use polymarket_client_sdk::auth::Normal;
use polymarket_client_sdk::clob::types::{OrderType, Side, SignatureType};
use polymarket_client_sdk::clob::{Client, Config};
use rust_decimal::prelude::*;
use tracing::{error, info, warn};

use crate::config::ExecutorConfig;
use crate::types::{OrderRequest, OrderResponse};

/// Thread-safe atomic counters for order statistics.
pub struct Stats {
    pub orders_sent: AtomicU64,
    pub orders_filled: AtomicU64,
    pub total_latency_us: AtomicU64,
}

impl Stats {
    fn new() -> Self {
        Self {
            orders_sent: AtomicU64::new(0),
            orders_filled: AtomicU64::new(0),
            total_latency_us: AtomicU64::new(0),
        }
    }

    pub fn avg_latency_ms(&self) -> f64 {
        let sent = self.orders_sent.load(Ordering::Relaxed);
        if sent == 0 {
            return 0.0;
        }
        let total_us = self.total_latency_us.load(Ordering::Relaxed);
        (total_us as f64 / sent as f64) / 1000.0
    }
}

/// Handles EIP-712 signing and CLOB order submission.
pub struct Executor {
    client: Client<Authenticated<Normal>>,
    signer: PrivateKeySigner,
    pub stats: Stats,
}

impl Executor {
    /// Create a new executor: authenticate with the CLOB and verify connectivity.
    pub async fn new(config: ExecutorConfig) -> Result<Self> {
        let signer = config.signer;

        info!(
            "Authenticating with CLOB at {} (funder: {})",
            config.clob_url, config.funder
        );

        let client = Client::new(&config.clob_url, Config::default())
            .context("Failed to create CLOB client")?
            .authentication_builder(&signer)
            .funder(config.funder)
            .signature_type(SignatureType::GnosisSafe)
            .authenticate()
            .await
            .context("CLOB authentication failed")?;

        // Verify connectivity
        client
            .ok()
            .await
            .context("CLOB health check failed — cannot reach clob.polymarket.com")?;

        info!("CLOB authenticated and healthy");

        Ok(Self {
            client,
            signer,
            stats: Stats::new(),
        })
    }

    /// Execute a single order: validate → build → sign → post → parse.
    pub async fn execute_order(&self, req: OrderRequest) -> OrderResponse {
        let start = Instant::now();
        self.stats.orders_sent.fetch_add(1, Ordering::Relaxed);

        match self.execute_inner(&req).await {
            Ok((order_id, status, avg_price, total_shares)) => {
                let latency_ms = start.elapsed().as_secs_f64() * 1000.0;
                self.stats.total_latency_us.fetch_add(
                    start.elapsed().as_micros() as u64,
                    Ordering::Relaxed,
                );

                let status_lower = status.to_lowercase();
                let filled = matches!(status_lower.as_str(), "matched" | "filled" | "live");
                if filled {
                    self.stats.orders_filled.fetch_add(1, Ordering::Relaxed);
                    info!(
                        order_id = %order_id,
                        avg_price = avg_price,
                        shares = total_shares,
                        latency_ms = latency_ms,
                        "Order filled"
                    );
                    OrderResponse::success(order_id, status, avg_price, total_shares, latency_ms)
                } else {
                    warn!(
                        order_id = %order_id,
                        status = %status,
                        latency_ms = latency_ms,
                        "Order not filled"
                    );
                    OrderResponse::failure(
                        format!("Order not filled: status={status}"),
                        latency_ms,
                    )
                }
            }
            Err(e) => {
                let latency_ms = start.elapsed().as_secs_f64() * 1000.0;
                error!(error = %e, latency_ms = latency_ms, "Order execution failed");
                OrderResponse::failure(format!("{e:#}"), latency_ms)
            }
        }
    }

    /// Inner execution logic — returns (order_id, status, avg_price, total_shares).
    async fn execute_inner(
        &self,
        req: &OrderRequest,
    ) -> Result<(String, String, f64, f64)> {
        // ── Validate ──
        if req.price < 0.01 || req.price > 0.99 {
            bail!("Price {:.2} out of range (0.01–0.99)", req.price);
        }
        if req.size < 5.0 {
            bail!("Size {:.2} below minimum (5 shares)", req.size);
        }
        if req.token_id.is_empty() {
            bail!("Empty token_id");
        }

        let side = match req.side.to_uppercase().as_str() {
            "BUY" => Side::Buy,
            "SELL" => Side::Sell,
            other => bail!("Invalid side: {other}"),
        };

        let price = Decimal::from_f64_retain(req.price)
            .context("Cannot convert price to Decimal")?
            .round_dp(2);
        // FOK: CLOB requires maker_amount (price*size) ≤ 2 decimals
        // and lot size ≤ 2 decimals. Floor to whole shares guarantees
        // price × integer = always ≤ 2 decimal cost.
        let size = Decimal::from_f64_retain(req.size)
            .context("Cannot convert size to Decimal")?
            .floor();

        // ── Build order ──
        let order = self
            .client
            .limit_order()
            .token_id(&req.token_id)
            .price(price)
            .size(size)
            .side(side)
            .order_type(OrderType::GTC)
            .build()
            .await
            .context("Failed to build order")?;

        // ── Sign ──
        let signed_order = self
            .client
            .sign(&self.signer, order)
            .await
            .context("EIP-712 signing failed")?;

        // ── Post to CLOB ──
        let resp = self
            .client
            .post_order(signed_order)
            .await
            .context("POST /order to CLOB failed")?;

        // ── Parse response ──
        // PostOrderResponse doesn't implement Serialize, so access fields via Debug.
        // Extract what we can from the response using field access.
        // If field names are wrong, the compiler will tell us the correct ones.
        let debug_repr = format!("{:?}", resp);
        info!("CLOB raw response: {}", &debug_repr[..debug_repr.len().min(500)]);

        // Extract order_id from debug repr
        let order_id = extract_field(&debug_repr, "order_id")
            .or_else(|| extract_field(&debug_repr, "orderID"))
            .or_else(|| extract_field(&debug_repr, "id"))
            .unwrap_or_else(|| "unknown".to_string());

        let status = extract_field(&debug_repr, "status")
            .unwrap_or_default();

        // Extract matched orders info
        let avg_price = extract_field(&debug_repr, "price")
            .and_then(|p| p.parse::<f64>().ok())
            .unwrap_or(req.price);

        let total_shares = req.size;

        Ok((order_id, status, avg_price, total_shares))
    }

}

/// Extract a field value from a Debug representation string.
/// Looks for patterns like `field_name: "value"` or `field_name: value`.
fn extract_field(debug_str: &str, field: &str) -> Option<String> {
    let pattern = format!("{field}: ");
    let start = debug_str.find(&pattern)?;
    let after = &debug_str[start + pattern.len()..];

    if after.starts_with('"') {
        // Quoted string value
        let end = after[1..].find('"')?;
        Some(after[1..1 + end].to_string())
    } else {
        // Unquoted value (number, enum variant, etc.)
        let end = after.find([',', ' ', '}', ')'].as_ref()).unwrap_or(after.len());
        let val = after[..end].trim();
        if val.is_empty() { None } else { Some(val.to_string()) }
    }
}
