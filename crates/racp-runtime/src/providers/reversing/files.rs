//! Bounded file hashing shared by native adapters; no analysis-engine execution.
use racp_contract::RacpError;
use sha2::{Digest, Sha256};
use std::{io::Read, path::Path, time::Instant};
use tokio_util::sync::CancellationToken;
pub fn file_hash(
    path: &Path,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<String, RacpError> {
    racp_core::validate_local_path(path)?;
    let mut file = racp_core::secure_read_file(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 262144];
    loop {
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
