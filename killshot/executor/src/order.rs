use alloy_primitives::{address, Address, B256, U256, keccak256};
use alloy_signer::Signer;
use rand::Rng;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::config::Config;

// Exchange addresses on Polygon (chain 137)
pub const EXCHANGE_NORMAL: Address = address!("4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E");
pub const EXCHANGE_NEG_RISK: Address = address!("C5d563A36AE78145C45a50134d48A1215220f80a");

const ZERO_ADDRESS: Address = Address::ZERO;

// EIP-712 domain constants
const DOMAIN_NAME: &str = "Polymarket CTF Exchange";
const DOMAIN_VERSION: &str = "1";
const CHAIN_ID: u64 = 137;

// Rounding config for tick_size "0.01" (our default)
struct RoundConfig {
    price: u32,
    size: u32,
    amount: u32,
}

fn get_round_config(tick_size: &str) -> RoundConfig {
    match tick_size {
        "0.1" => RoundConfig { price: 1, size: 2, amount: 3 },
        "0.01" => RoundConfig { price: 2, size: 2, amount: 4 },
        "0.001" => RoundConfig { price: 3, size: 2, amount: 5 },
        "0.0001" => RoundConfig { price: 4, size: 2, amount: 6 },
        _ => RoundConfig { price: 2, size: 2, amount: 4 },
    }
}

// ── Rounding helpers (must match Python py_clob_client exactly) ──

fn round_down(x: f64, digits: u32) -> f64 {
    let factor = 10_f64.powi(digits as i32);
    (x * factor).floor() / factor
}

fn round_normal(x: f64, digits: u32) -> f64 {
    let factor = 10_f64.powi(digits as i32);
    (x * factor).round() / factor
}

fn round_up(x: f64, digits: u32) -> f64 {
    let factor = 10_f64.powi(digits as i32);
    (x * factor).ceil() / factor
}

fn decimal_places(x: f64) -> u32 {
    // Match Python's Decimal(str(x)).as_tuple().exponent
    let s = format!("{}", x);
    match s.find('.') {
        Some(pos) => (s.len() - pos - 1) as u32,
        None => 0,
    }
}

fn to_token_decimals(x: f64) -> u64 {
    let f = 1_000_000.0 * x;
    if decimal_places(f) > 0 {
        round_normal(f, 0) as u64
    } else {
        f as u64
    }
}

// ── Amount calculation (matches Python OrderBuilder.get_order_amounts) ──

pub fn get_order_amounts(side: &str, size: f64, price: f64, tick_size: &str) -> (u8, u64, u64) {
    let rc = get_round_config(tick_size);
    let raw_price = round_normal(price, rc.price);

    match side {
        "BUY" => {
            let raw_taker_amt = round_down(size, rc.size);
            let mut raw_maker_amt = raw_taker_amt * raw_price;
            if decimal_places(raw_maker_amt) > rc.amount {
                raw_maker_amt = round_up(raw_maker_amt, rc.amount + 4);
                if decimal_places(raw_maker_amt) > rc.amount {
                    raw_maker_amt = round_down(raw_maker_amt, rc.amount);
                }
            }
            let maker_amount = to_token_decimals(raw_maker_amt);
            let taker_amount = to_token_decimals(raw_taker_amt);
            (0, maker_amount, taker_amount) // 0 = BUY
        }
        "SELL" => {
            let raw_maker_amt = round_down(size, rc.size);
            let mut raw_taker_amt = raw_maker_amt * raw_price;
            if decimal_places(raw_taker_amt) > rc.amount {
                raw_taker_amt = round_up(raw_taker_amt, rc.amount + 4);
                if decimal_places(raw_taker_amt) > rc.amount {
                    raw_taker_amt = round_down(raw_taker_amt, rc.amount);
                }
            }
            let maker_amount = to_token_decimals(raw_maker_amt);
            let taker_amount = to_token_decimals(raw_taker_amt);
            (1, maker_amount, taker_amount) // 1 = SELL
        }
        _ => panic!("side must be BUY or SELL"),
    }
}

// ── Salt generation (matches Python generate_seed) ──

fn generate_salt() -> U256 {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let r: f64 = rand::thread_rng().gen();
    let salt = (now * r).round() as u64;
    U256::from(salt)
}

// ── EIP-712 Order struct ──

#[derive(Debug)]
#[allow(non_snake_case)]
struct Order {
    salt: U256,
    maker: Address,
    signer: Address,
    taker: Address,
    tokenId: U256,
    makerAmount: U256,
    takerAmount: U256,
    expiration: U256,
    nonce: U256,
    feeRateBps: U256,
    side: u8,
    signatureType: u8,
}

// Use alloy_sol_types to compute the domain separator
fn domain_separator(exchange: Address) -> B256 {
    // EIP-712 domain: keccak256(abi.encode(typeHash, nameHash, versionHash, chainId, verifyingContract))
    let type_hash = keccak256(
        b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)",
    );
    let name_hash = keccak256(DOMAIN_NAME.as_bytes());
    let version_hash = keccak256(DOMAIN_VERSION.as_bytes());

    let mut buf = [0u8; 160]; // 5 * 32
    buf[0..32].copy_from_slice(type_hash.as_slice());
    buf[32..64].copy_from_slice(name_hash.as_slice());
    buf[64..96].copy_from_slice(version_hash.as_slice());
    buf[96..128].copy_from_slice(&U256::from(CHAIN_ID).to_be_bytes::<32>());
    // Address is 20 bytes, left-padded to 32
    buf[140..160].copy_from_slice(exchange.as_slice());

    keccak256(&buf)
}

fn struct_hash(order: &Order) -> B256 {
    let type_hash = keccak256(
        b"Order(uint256 salt,address maker,address signer,address taker,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint256 expiration,uint256 nonce,uint256 feeRateBps,uint8 side,uint8 signatureType)",
    );

    // ABI-encode: typeHash + each field as 32-byte word
    let mut buf = Vec::with_capacity(13 * 32); // typeHash + 12 fields
    buf.extend_from_slice(type_hash.as_slice());
    buf.extend_from_slice(&order.salt.to_be_bytes::<32>());
    // Addresses: left-pad to 32 bytes
    let mut addr_buf = [0u8; 32];
    addr_buf[12..32].copy_from_slice(order.maker.as_slice());
    buf.extend_from_slice(&addr_buf);
    addr_buf = [0u8; 32];
    addr_buf[12..32].copy_from_slice(order.signer.as_slice());
    buf.extend_from_slice(&addr_buf);
    addr_buf = [0u8; 32];
    addr_buf[12..32].copy_from_slice(order.taker.as_slice());
    buf.extend_from_slice(&addr_buf);
    buf.extend_from_slice(&order.tokenId.to_be_bytes::<32>());
    buf.extend_from_slice(&order.makerAmount.to_be_bytes::<32>());
    buf.extend_from_slice(&order.takerAmount.to_be_bytes::<32>());
    buf.extend_from_slice(&order.expiration.to_be_bytes::<32>());
    buf.extend_from_slice(&order.nonce.to_be_bytes::<32>());
    buf.extend_from_slice(&order.feeRateBps.to_be_bytes::<32>());
    buf.extend_from_slice(&U256::from(order.side).to_be_bytes::<32>());
    buf.extend_from_slice(&U256::from(order.signatureType).to_be_bytes::<32>());

    keccak256(&buf)
}

// ── Build & sign order ──

pub struct SignedOrderResult {
    pub salt: u64,
    pub maker: Address,
    pub signer_addr: Address,
    pub taker: Address,
    pub token_id: String,
    pub maker_amount: String,
    pub taker_amount: String,
    pub expiration: String,
    pub nonce: String,
    pub fee_rate_bps: String,
    pub side: String, // "BUY" or "SELL"
    pub signature_type: u8,
    pub signature: String,
}

pub async fn build_and_sign_order(
    config: &Config,
    token_id: &str,
    price: f64,
    size: f64,
    side: &str,
    neg_risk: bool,
) -> SignedOrderResult {
    let (side_int, maker_amount, taker_amount) =
        get_order_amounts(side, size, price, &config.tick_size);

    let salt = generate_salt();
    let signer_addr = config.signer.address();
    let exchange = if neg_risk { EXCHANGE_NEG_RISK } else { EXCHANGE_NORMAL };

    let token_id_u256 = U256::from_str_radix(
        token_id.strip_prefix("0x").unwrap_or(token_id),
        if token_id.starts_with("0x") { 16 } else { 10 },
    )
    .expect("Invalid token_id");

    let order = Order {
        salt,
        maker: config.funder,
        signer: signer_addr,
        taker: ZERO_ADDRESS,
        tokenId: token_id_u256,
        makerAmount: U256::from(maker_amount),
        takerAmount: U256::from(taker_amount),
        expiration: U256::ZERO,
        nonce: U256::ZERO,
        feeRateBps: U256::ZERO,
        side: side_int,
        signatureType: config.sig_type,
    };

    // EIP-712 hash: 0x1901 + domainSeparator + structHash
    let ds = domain_separator(exchange);
    let sh = struct_hash(&order);
    let mut digest_input = [0u8; 66];
    digest_input[0] = 0x19;
    digest_input[1] = 0x01;
    digest_input[2..34].copy_from_slice(ds.as_slice());
    digest_input[34..66].copy_from_slice(sh.as_slice());
    let digest = keccak256(&digest_input);

    // Sign with ECDSA
    let sig = config
        .signer
        .sign_hash(&digest)
        .await
        .expect("Signing failed");

    // Format as 0x{r}{s}{v} (65 bytes = 130 hex chars + 0x prefix)
    // Polymarket expects legacy v values (27 or 28)
    let r_bytes = sig.r().to_be_bytes::<32>();
    let s_bytes = sig.s().to_be_bytes::<32>();
    let v = if sig.v() { 28u8 } else { 27u8 };
    let signature = format!(
        "0x{}{}{}",
        hex::encode(r_bytes),
        hex::encode(s_bytes),
        hex::encode([v]),
    );

    let side_str = if side_int == 0 { "BUY" } else { "SELL" };

    SignedOrderResult {
        salt: salt.to::<u64>(),
        maker: config.funder,
        signer_addr,
        taker: ZERO_ADDRESS,
        token_id: token_id.to_string(),
        maker_amount: maker_amount.to_string(),
        taker_amount: taker_amount.to_string(),
        expiration: "0".to_string(),
        nonce: "0".to_string(),
        fee_rate_bps: "0".to_string(),
        side: side_str.to_string(),
        signature_type: config.sig_type,
        signature,
    }
}

// Checksum address formatting (EIP-55)
pub fn to_checksum(addr: &Address) -> String {
    let hex_addr = hex::encode(addr.as_slice());
    let hash = keccak256(hex_addr.as_bytes());

    let mut checksum = String::with_capacity(42);
    checksum.push_str("0x");
    for (i, c) in hex_addr.chars().enumerate() {
        if c.is_ascii_alphabetic() {
            // If the corresponding nibble in the hash is >= 8, uppercase
            let nibble = hash[i / 2];
            let high = if i % 2 == 0 { nibble >> 4 } else { nibble & 0x0f };
            if high >= 8 {
                checksum.push(c.to_ascii_uppercase());
            } else {
                checksum.push(c.to_ascii_lowercase());
            }
        } else {
            checksum.push(c);
        }
    }
    checksum
}

// Build CLOB-compatible order JSON value
pub fn order_to_clob_json(
    signed: &SignedOrderResult,
    owner: &str,
    order_type: &str,
) -> serde_json::Value {
    serde_json::json!({
        "order": {
            "salt": signed.salt,
            "maker": to_checksum(&signed.maker),
            "signer": to_checksum(&signed.signer_addr),
            "taker": to_checksum(&signed.taker),
            "tokenId": signed.token_id,
            "makerAmount": signed.maker_amount,
            "takerAmount": signed.taker_amount,
            "expiration": signed.expiration,
            "nonce": signed.nonce,
            "feeRateBps": signed.fee_rate_bps,
            "side": signed.side,
            "signatureType": signed.signature_type,
            "signature": signed.signature,
        },
        "owner": owner,
        "orderType": order_type,
        "postOnly": false,
    })
}
