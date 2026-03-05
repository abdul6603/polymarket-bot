use base64::{engine::general_purpose::URL_SAFE, Engine};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::config::Config;
use crate::order::to_checksum;

type HmacSha256 = Hmac<Sha256>;

/// Build HMAC-SHA256 L2 authentication headers for the CLOB API.
/// Must match py_clob_client/signing/hmac.py exactly.
pub fn build_l2_headers(
    config: &Config,
    request_path: &str,
    body: &str,
) -> Vec<(String, String)> {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let timestamp_str = timestamp.to_string();

    let signature = build_hmac_signature(
        &config.api_secret,
        &timestamp_str,
        "POST",
        request_path,
        Some(body),
    );

    // POLY_ADDRESS must be signer address (API key is tied to signer, not funder)
    let address = to_checksum(&config.signer.address());

    vec![
        ("POLY_ADDRESS".into(), address),
        ("POLY_SIGNATURE".into(), signature),
        ("POLY_TIMESTAMP".into(), timestamp_str),
        ("POLY_API_KEY".into(), config.api_key.clone()),
        ("POLY_PASSPHRASE".into(), config.passphrase.clone()),
    ]
}

/// Matches py_clob_client/signing/hmac.py build_hmac_signature
fn build_hmac_signature(
    secret: &str,
    timestamp: &str,
    method: &str,
    request_path: &str,
    body: Option<&str>,
) -> String {
    // base64url-decode the secret
    let key = URL_SAFE
        .decode(secret)
        .expect("api_secret must be valid base64url");

    // Build message: timestamp + method + requestPath [+ body]
    let mut message = format!("{}{}{}", timestamp, method, request_path);
    if let Some(b) = body {
        message.push_str(b);
    }

    let mut mac =
        HmacSha256::new_from_slice(&key).expect("HMAC can take key of any size");
    mac.update(message.as_bytes());
    let result = mac.finalize().into_bytes();

    URL_SAFE.encode(result)
}
