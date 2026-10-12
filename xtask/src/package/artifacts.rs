//! Fixed admitted payload writers. These functions create artifacts but never execute/install them.
use racp_contract::distribution::*;
use racp_core::{
    distribution::{hash_file, pin_payload},
    Workspaces,
};
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
fn output(path: &Path) -> Result<File> {
    Ok(OpenOptions::new().write(true).create_new(true).open(path)?)
}
pub fn portable(payload: &Path, stub: &Path, destination: &Path) -> Result<()> {
    let pins = pin_payload(payload)?;
    let guards = Workspaces::new(stub.parent().ok_or("stub parent missing")?, &[])?;
    let dir = guards.directory("default", stub.parent().ok_or("stub parent missing")?)?;
    let mut launcher = dir.open_pinned_read(
        stub.file_name()
            .and_then(|n| n.to_str())
            .ok_or("stub name invalid")?,
    )?;
    racp_core::runtime_admission::validate_host_binary(&mut launcher)?;
    launcher.seek(SeekFrom::Start(0))?;
    let mut file = output(destination)?;
    let offset = std::io::copy(&mut launcher, &mut file)?;
    let bytes = serde_json::to_vec_pretty(&pins.manifest)?;
    let prefix = u32::try_from(bytes.len())?.to_le_bytes();
    let mut hash = Sha256::new();
    hash.update(prefix);
    hash.update(&bytes);
    file.write_all(&prefix)?;
    file.write_all(&bytes)?;
    let roots = Workspaces::new(payload, &[])?;
    let source = roots.directory("default", payload)?;
    let mut buffer = [0u8; 65536];
    for entry in &pins.manifest.files {
        let mut input = source.open_pinned_read(&entry.path)?;
        let mut left = entry.size_bytes;
        while left > 0 {
            let limit = left.min(buffer.len() as u64) as usize;
            let n = input.read(&mut buffer[..limit])?;
            if n == 0 {
                return Err("portable payload ended early".into());
            }
            file.write_all(&buffer[..n])?;
            hash.update(&buffer[..n]);
            left -= n as u64;
        }
    }
    file.write_all(&offset.to_le_bytes())?;
    file.write_all(PORTABLE_MAGIC)?;
    file.write_all(format!("{:x}", hash.finalize()).as_bytes())?;
    file.sync_all()?;
    Ok(())
}
fn u16le(out: &mut File, value: u16) -> Result<()> {
    out.write_all(&value.to_le_bytes())?;
    Ok(())
}
fn u32le(out: &mut File, value: u32) -> Result<()> {
    out.write_all(&value.to_le_bytes())?;
    Ok(())
}
// ZIP32 stored entries: fixed ASCII names, no compression/encryption/data descriptors.
pub fn zip(payload: &Path, destination: &Path) -> Result<()> {
    let _pins = pin_payload(payload)?;
    let roots = Workspaces::new(payload, &[])?;
    let dir = roots.directory("default", payload)?;
    let mut file = output(destination)?;
    let mut entries = Vec::new();
    let mut buffer = [0u8; 65536];
    for name in BASE_FILES.into_iter().chain([MANIFEST_FILE]) {
        let mut input = dir.open_pinned_read(name)?;
        let size = u32::try_from(input.metadata()?.len())?;
        let mut crc = crc32fast::Hasher::new();
        loop {
            let n = input.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            crc.update(&buffer[..n]);
        }
        let crc = crc.finalize();
        input.seek(SeekFrom::Start(0))?;
        let offset = u32::try_from(file.stream_position()?)?;
        u32le(&mut file, 0x04034b50)?;
        u16le(&mut file, 20)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 33)?;
        u32le(&mut file, crc)?;
        u32le(&mut file, size)?;
        u32le(&mut file, size)?;
        u16le(&mut file, name.len() as u16)?;
        u16le(&mut file, 0)?;
        file.write_all(name.as_bytes())?;
        if std::io::copy(&mut input, &mut file)? != size as u64 {
            return Err("ZIP source changed".into());
        }
        entries.push((name, size, crc, offset));
    }
    let start = u32::try_from(file.stream_position()?)?;
    for (name, size, crc, offset) in &entries {
        u32le(&mut file, 0x02014b50)?;
        u16le(&mut file, 20)?;
        u16le(&mut file, 20)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 33)?;
        u32le(&mut file, *crc)?;
        u32le(&mut file, *size)?;
        u32le(&mut file, *size)?;
        u16le(&mut file, name.len() as u16)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 0)?;
        u16le(&mut file, 0)?;
        u32le(&mut file, 0)?;
        u32le(&mut file, *offset)?;
        file.write_all(name.as_bytes())?;
    }
    let end = u32::try_from(file.stream_position()?)?;
    u32le(&mut file, 0x06054b50)?;
    u16le(&mut file, 0)?;
    u16le(&mut file, 0)?;
    u16le(&mut file, entries.len() as u16)?;
    u16le(&mut file, entries.len() as u16)?;
    u32le(&mut file, end - start)?;
    u32le(&mut file, start)?;
    u16le(&mut file, 0)?;
    file.sync_all()?;
    Ok(())
}
pub fn hash(path: &Path) -> Result<serde_json::Value> {
    let mut file = racp_core::secure_read_file(path)?;
    let (size, digest) = hash_file(&mut file, MAX_PAYLOAD_BYTES + 128 * 1024 * 1024)?;
    Ok(
        serde_json::json!({"file":path.file_name().ok_or("artifact file missing")?.to_string_lossy(),"size_bytes":size,"sha256":digest}),
    )
}
