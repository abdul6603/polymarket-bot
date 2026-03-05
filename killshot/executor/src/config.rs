use alloy_primitives::Address;
use alloy_signer_local::PrivateKeySigner;

#[derive(Clone)]
pub struct Config {
    pub signer: PrivateKeySigner,
    pub funder: Address,
    pub api_key: String,
    pub api_secret: String, // base64-encoded
    pub passphrase: String,
    pub sig_type: u8,
    pub tick_size: String,
}

impl Config {
    pub fn from_env() -> Self {
        let private_key = env_required("KILLSHOT_PRIVATE_KEY");
        let api_key = env_required("KILLSHOT_CLOB_API_KEY");
        let api_secret = env_required("KILLSHOT_CLOB_API_SECRET");
        let passphrase = env_required("KILLSHOT_CLOB_API_PASSPHRASE");
        let funder_str = env_required("KILLSHOT_FUNDER_ADDRESS");

        let sig_type: u8 = std::env::var("KILLSHOT_SIGNATURE_TYPE")
            .unwrap_or_else(|_| "2".into())
            .parse()
            .expect("KILLSHOT_SIGNATURE_TYPE must be 0, 1, or 2");

        let tick_size = std::env::var("KILLSHOT_DEFAULT_TICK_SIZE")
            .unwrap_or_else(|_| "0.01".into());

        // Parse private key — strip 0x prefix if present
        let pk_hex = private_key.strip_prefix("0x").unwrap_or(&private_key);
        let signer: PrivateKeySigner = pk_hex
            .parse()
            .expect("Invalid KILLSHOT_PRIVATE_KEY");

        let funder: Address = funder_str
            .parse()
            .expect("Invalid KILLSHOT_FUNDER_ADDRESS");

        Config {
            signer,
            funder,
            api_key,
            api_secret,
            passphrase,
            sig_type,
            tick_size,
        }
    }
}

fn env_required(key: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| panic!("{key} is required"))
}
