use crate::{read_bounded, settings::gateway_origin};
use racp_contract::{digest, RacpError};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::Path;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConnectionFile {
    #[serde(default = "one")]
    pub version: u8,
    pub gateway: String,
    pub token: String,
    pub expires_at: chrono::DateTime<chrono::FixedOffset>,
    #[serde(default)]
    pub ca_pem: Option<String>,
}
fn one() -> u8 {
    1
}
pub fn validate_ca(
    raw: &[u8],
) -> Result<Vec<rustls::pki_types::CertificateDer<'static>>, RacpError> {
    let mut reader = std::io::Cursor::new(raw);
    let certs: Vec<_> = rustls_pemfile::certs(&mut reader)
        .collect::<Result<_, _>>()
        .map_err(|_| RacpError::new("CA_INVALID"))?;
    if certs.is_empty() {
        return Err(RacpError::new("CA_INVALID"));
    }
    let mut roots = rustls::RootCertStore::empty();
    for cert in &certs {
        roots
            .add(cert.clone())
            .map_err(|_| RacpError::new("CA_INVALID"))?;
    }
    Ok(certs)
}
pub fn load_connection(
    path: &Path,
    expected: Option<&str>,
) -> Result<(ConnectionFile, String), RacpError> {
    let raw =
        read_bounded(path, 32768, false).map_err(|_| RacpError::new("CONNECTION_FILE_INVALID"))?;
    let hash = digest(&raw);
    if expected.is_some_and(|e| e != hash) {
        return Err(RacpError::new("CONNECTION_FILE_CHANGED"));
    }
    let mut value: ConnectionFile =
        serde_json::from_slice(&raw).map_err(|_| RacpError::new("CONNECTION_FILE_INVALID"))?;
    if value.version != 1
        || !(20..=128).contains(&value.token.len())
        || !value
            .token
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err(RacpError::new("CONNECTION_FILE_INVALID"));
    }
    value.gateway =
        gateway_origin(&value.gateway).map_err(|_| RacpError::new("CONNECTION_FILE_INVALID"))?;
    if let Some(pem) = &mut value.ca_pem {
        if pem.len() > 16384 {
            return Err(RacpError::new("CONNECTION_FILE_INVALID"));
        }
        *pem = format!("{}\n", pem.trim());
        // Connection files permit certificates only, never private keys or arbitrary PEM blocks.
        let mut remaining = pem.as_str();
        while !remaining.trim().is_empty() {
            remaining = remaining.trim_start();
            remaining = remaining
                .strip_prefix("-----BEGIN CERTIFICATE-----")
                .ok_or_else(|| RacpError::new("CONNECTION_FILE_INVALID"))?;
            let (content, rest) = remaining
                .split_once("-----END CERTIFICATE-----")
                .ok_or_else(|| RacpError::new("CONNECTION_FILE_INVALID"))?;
            if !content.starts_with(char::is_whitespace)
                || !content.chars().all(|c| {
                    c.is_ascii_alphanumeric()
                        || c == '+'
                        || c == '/'
                        || c == '='
                        || c.is_ascii_whitespace()
                })
            {
                return Err(RacpError::new("CONNECTION_FILE_INVALID"));
            }
            remaining = rest;
        }
        validate_ca(pem.as_bytes()).map_err(|_| RacpError::new("CONNECTION_FILE_INVALID"))?;
    }
    if value.expires_at <= chrono::Utc::now() {
        return Err(RacpError::new("CONNECTION_FILE_EXPIRED"));
    }
    Ok((value, hash))
}
pub fn inspect_connection(path: &Path, expected: Option<&str>) -> Result<Value, RacpError> {
    let (value, hash) = load_connection(path, expected)?;
    Ok(
        json!({"gateway":value.gateway,"expires_at":value.expires_at.with_timezone(&chrono::Utc).to_rfc3339(),"file_sha256":hash,"ca_sha256":value.ca_pem.as_ref().map(digest)}),
    )
}
