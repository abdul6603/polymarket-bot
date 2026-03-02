use std::str::FromStr;

use alloy_primitives::Address;
use alloy_signer_local::PrivateKeySigner;
use anyhow::{bail, Context, Result};
use polymarket_client_sdk::auth::Signer as _;
use polymarket_client_sdk::POLYGON;

/// All configuration needed to run the executor.
pub struct ExecutorConfig {
    pub signer: PrivateKeySigner,
    pub funder: Address,
    pub clob_url: String,
    pub bind_addr: String,
}

impl ExecutorConfig {
    /// Load configuration from environment variables.
    /// Requires KILLSHOT_PRIVATE_KEY and KILLSHOT_FUNDER_ADDRESS to be set.
    pub fn from_env() -> Result<Self> {
        dotenvy::dotenv().ok();

        let private_key = std::env::var("KILLSHOT_PRIVATE_KEY")
            .context("KILLSHOT_PRIVATE_KEY not set")?;
        if private_key.is_empty() {
            bail!("KILLSHOT_PRIVATE_KEY is empty — executor requires a funded wallet");
        }

        let funder_str = std::env::var("KILLSHOT_FUNDER_ADDRESS")
            .context("KILLSHOT_FUNDER_ADDRESS not set")?;
        if funder_str.is_empty() {
            bail!("KILLSHOT_FUNDER_ADDRESS is empty — set to your Polymarket wallet address");
        }

        let signer = PrivateKeySigner::from_str(&private_key)
            .context("Invalid private key")?
            .with_chain_id(Some(POLYGON));

        let funder =
            Address::from_str(&funder_str).context("Invalid KILLSHOT_FUNDER_ADDRESS")?;

        let clob_url = std::env::var("KILLSHOT_CLOB_URL")
            .unwrap_or_else(|_| "https://clob.polymarket.com".to_string());

        let bind_addr = std::env::var("EXECUTOR_BIND")
            .unwrap_or_else(|_| "127.0.0.1:9999".to_string());

        Ok(Self {
            signer,
            funder,
            clob_url,
            bind_addr,
        })
    }
}
