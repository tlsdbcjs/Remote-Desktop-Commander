//! Production artifact inspection only: no extraction, process launch or installation.
use racp_contract::{distribution::*, local_ipc::reject_duplicate_keys};
use racp_core::distribution::{hash_file, pin_payload};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
fn take(file: &mut File, count: usize) -> Result<Vec<u8>> {
    let mut bytes = vec![0; count];
    file.read_exact(&mut bytes)?;
    Ok(bytes)
}
fn word(bytes: &[u8], offset: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(
        bytes
            .get(offset..offset + 2)
            .ok_or("ZIP header truncated")?
            .try_into()?,
    ))
}
fn dword(bytes: &[u8], offset: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(
        bytes
            .get(offset..offset + 4)
            .ok_or("ZIP header truncated")?
            .try_into()?,
    ))
}
fn region(file: &mut File, size: u64) -> Result<(String, u32)> {
    let mut left = size;
    let mut digest = Sha256::new();
    let mut crc = crc32fast::Hasher::new();
    let mut buffer = [0; 65536];
    while left > 0 {
        let limit = left.min(buffer.len() as u64) as usize;
        let n = file.read(&mut buffer[..limit])?;
        if n == 0 {
            return Err("archive ended early".into());
        }
        digest.update(&buffer[..n]);
        crc.update(&buffer[..n]);
        left -= n as u64;
    }
    Ok((format!("{:x}", digest.finalize()), crc.finalize()))
}
pub fn portable(path: &Path, payload: &Path, stub: &Path) -> Result<()> {
    let pins = pin_payload(payload)?;
    let mut file = racp_core::secure_read_file(path)?;
    let length = file.metadata()?.len();
    if length < 88 || length > MAX_PAYLOAD_BYTES + 128 * 1024 * 1024 {
        return Err("portable length invalid".into());
    }
    file.seek(SeekFrom::End(-88))?;
    let tail = take(&mut file, 88)?;
    if &tail[8..24] != PORTABLE_MAGIC {
        return Err("portable format invalid".into());
    }
    let offset = u64::from_le_bytes(tail[..8].try_into()?);
    let mut launcher = racp_core::secure_read_file(stub)?;
    let (size, expected) = hash_file(&mut launcher, 128 * 1024 * 1024)?;
    if offset != size || offset >= length - 88 {
        return Err("portable stub boundary invalid".into());
    }
    file.seek(SeekFrom::Start(0))?;
    if region(&mut file, offset)?.0 != expected {
        return Err("portable stub mismatch".into());
    }
    let (digest, _) = region(&mut file, length - offset - 88)?;
    if digest.as_bytes() != &tail[24..] {
        return Err("portable archive digest mismatch".into());
    }
    file.seek(SeekFrom::Start(offset))?;
    let prefix = take(&mut file, 4)?;
    let size = u32::from_le_bytes(prefix.as_slice().try_into()?) as usize;
    if size == 0 || size > 1024 * 1024 {
        return Err("portable manifest bound exceeded".into());
    }
    let bytes = take(&mut file, size)?;
    reject_duplicate_keys(&bytes)?;
    let manifest: NativePayload = serde_json::from_slice(&bytes)?;
    manifest.validate()?;
    if serde_json::to_value(&manifest)? != serde_json::to_value(&pins.manifest)? {
        return Err("portable manifest mismatch".into());
    }
    for entry in &manifest.files {
        if region(&mut file, entry.size_bytes)?.0 != entry.sha256 {
            return Err("portable entry mismatch".into());
        }
    }
    if file.stream_position()? != length - 88 {
        return Err("portable unclaimed bytes".into());
    }
    Ok(())
}
pub fn zip(path: &Path, payload: &Path) -> Result<()> {
    let pins = pin_payload(payload)?;
    let mut file = racp_core::secure_read_file(path)?;
    let length = file.metadata()?.len();
    if length > MAX_PAYLOAD_BYTES + 1024 * 1024 {
        return Err("ZIP bound exceeded".into());
    }
    let mut expected = std::collections::BTreeMap::new();
    for entry in &pins.manifest.files {
        expected.insert(entry.path.clone(), (entry.size_bytes, entry.sha256.clone()));
    }
    let mut metadata = racp_core::secure_read_file(&payload.join(MANIFEST_FILE))?;
    expected.insert(MANIFEST_FILE.into(), hash_file(&mut metadata, 1024 * 1024)?);
    let mut entries = Vec::new();
    for name in BASE_FILES.into_iter().chain([MANIFEST_FILE]) {
        let offset = u32::try_from(file.stream_position()?)?;
        let header = take(&mut file, 30)?;
        if dword(&header, 0)? != 0x04034b50
            || word(&header, 4)? != 20
            || word(&header, 6)? != 0
            || word(&header, 8)? != 0
            || word(&header, 28)? != 0
            || word(&header, 26)? as usize != name.len()
        {
            return Err("ZIP local header invalid".into());
        }
        if take(&mut file, name.len())? != name.as_bytes() {
            return Err("ZIP entry name mismatch".into());
        }
        let size = dword(&header, 22)?;
        let crc = dword(&header, 14)?;
        let (expected_size, expected_digest) = expected.get(name).ok_or("ZIP extra entry")?;
        if size as u64 != *expected_size || dword(&header, 18)? != size {
            return Err("ZIP entry size mismatch".into());
        }
        let (digest, actual_crc) = region(&mut file, size as u64)?;
        if &digest != expected_digest || crc != actual_crc {
            return Err("ZIP entry CRC/hash mismatch".into());
        }
        entries.push((name, size, crc, offset));
    }
    let start = u32::try_from(file.stream_position()?)?;
    for (name, size, crc, offset) in &entries {
        let header = take(&mut file, 46)?;
        if dword(&header, 0)? != 0x02014b50
            || word(&header, 6)? != 20
            || word(&header, 8)? != 0
            || word(&header, 10)? != 0
            || dword(&header, 16)? != *crc
            || dword(&header, 20)? != *size
            || dword(&header, 24)? != *size
            || word(&header, 28)? as usize != name.len()
            || word(&header, 30)? != 0
            || word(&header, 32)? != 0
            || word(&header, 34)? != 0
            || dword(&header, 42)? != *offset
        {
            return Err("ZIP central directory mismatch".into());
        }
        if take(&mut file, name.len())? != name.as_bytes() {
            return Err("ZIP central name mismatch".into());
        }
    }
    let end = u32::try_from(file.stream_position()?)?;
    let header = take(&mut file, 22)?;
    if dword(&header, 0)? != 0x06054b50
        || word(&header, 4)? != 0
        || word(&header, 6)? != 0
        || word(&header, 8)? as usize != entries.len()
        || word(&header, 10)? as usize != entries.len()
        || dword(&header, 12)? != end - start
        || dword(&header, 16)? != start
        || word(&header, 20)? != 0
        || file.stream_position()? != length
    {
        return Err("ZIP end record mismatch".into());
    }
    Ok(())
}
