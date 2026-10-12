//! Shared native enrollment HTTP transport. Registration authority lives in native_client.
use racp_contract::RacpError;
use racp_core::{read_bounded, validate_ca};
use std::{path::Path, time::Duration};

pub fn network_error(error: &reqwest::Error) -> RacpError {
    if error.is_timeout() {
        return RacpError::new("GATEWAY_TIMEOUT");
    }
    use std::error::Error;
    let mut source = error.source();
    while let Some(cause) = source {
        if cause.downcast_ref::<rustls::Error>().is_some() {
            return RacpError::new("TLS_FAILED");
        }
        source = cause.source();
    }
    RacpError::new("GATEWAY_UNREACHABLE")
}
pub fn http_client(ca: Option<&Path>) -> Result<reqwest::Client, RacpError> {
    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(15));
    if let Some(ca) = ca {
        let raw = read_bounded(ca, 1024 * 1024, false).map_err(|_| RacpError::new("CA_INVALID"))?;
        for cert in validate_ca(&raw)? {
            builder = builder.add_root_certificate(
                reqwest::Certificate::from_der(cert.as_ref())
                    .map_err(|_| RacpError::new("CA_INVALID"))?,
            );
        }
    }
    builder.build().map_err(|_| RacpError::new("CA_INVALID"))
}
