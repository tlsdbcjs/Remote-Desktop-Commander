//! Bound PE import inspection. Base artifacts admit system DLLs only, never a dynamic CRT.
use std::{
    collections::BTreeSet,
    fs::File,
    io::{Read, Seek, SeekFrom},
};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
fn read(file: &mut File, offset: u64, length: usize) -> Result<Vec<u8>> {
    if offset
        .checked_add(length as u64)
        .is_none_or(|end| end > file.metadata().map(|m| m.len()).unwrap_or(0))
    {
        return Err("PE offset out of bounds".into());
    }
    file.seek(SeekFrom::Start(offset))?;
    let mut bytes = vec![0; length];
    file.read_exact(&mut bytes)?;
    Ok(bytes)
}
fn word(bytes: &[u8], offset: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(
        bytes
            .get(offset..offset + 2)
            .ok_or("PE word truncated")?
            .try_into()?,
    ))
}
fn dword(bytes: &[u8], offset: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(
        bytes
            .get(offset..offset + 4)
            .ok_or("PE dword truncated")?
            .try_into()?,
    ))
}
pub fn inspect(file: &mut File) -> Result<Vec<String>> {
    let dos = read(file, 0, 64)?;
    if &dos[..2] != b"MZ" {
        return Err("PE DOS header invalid".into());
    }
    let start = dword(&dos, 60)? as u64;
    if start > 1024 * 1024 {
        return Err("PE header too large".into());
    }
    let header = read(file, start, 24)?;
    if &header[..4] != b"PE\0\0" || word(&header, 4)? != 0x8664 {
        return Err("native x64 PE required".into());
    }
    let count = word(&header, 6)? as usize;
    let opt_size = word(&header, 20)? as usize;
    if count == 0 || count > 96 || opt_size < 240 || opt_size > 4096 {
        return Err("PE section/optional-header bound exceeded".into());
    }
    let optional = read(file, start + 24, opt_size)?;
    if word(&optional, 0)? != 0x20b || dword(&optional, 108)? < 14 {
        return Err("PE64 directories missing".into());
    }
    // Delay-load imports need a separate resolver; base native builds must not use them.
    if dword(&optional, 112 + 13 * 8)? != 0 || dword(&optional, 112 + 13 * 8 + 4)? != 0 {
        return Err("delay-loaded base dependencies are not admitted".into());
    }
    let sections = read(file, start + 24 + opt_size as u64, count * 40)?;
    let rva = |address: u32, length: u32| -> Result<u64> {
        let mut matches = Vec::new();
        for section in sections.chunks_exact(40) {
            let base = dword(section, 12)?;
            let size = dword(section, 16)?;
            let raw = dword(section, 20)?;
            if let Some(relative) = address.checked_sub(base) {
                if relative.checked_add(length).is_some_and(|end| end <= size) {
                    matches.push(
                        (raw as u64)
                            .checked_add(relative as u64)
                            .ok_or("PE address overflow")?,
                    );
                }
            }
        }
        if matches.len() != 1 {
            return Err("PE address has no unique file mapping".into());
        }
        Ok(matches[0])
    };
    let address = dword(&optional, 120)?;
    let size = dword(&optional, 124)?;
    if address == 0 || size < 20 || size > 64 * 1024 {
        return Err("PE import directory invalid".into());
    }
    let directory = read(file, rva(address, size)?, size as usize)?;
    let mut names = BTreeSet::new();
    let mut ended = false;
    for descriptor in directory.chunks_exact(20) {
        if descriptor.iter().all(|v| *v == 0) {
            ended = true;
            break;
        }
        if names.len() > 128 {
            return Err("too many PE imports".into());
        }
        let name_rva = dword(descriptor, 12)?;
        let mut name = Vec::new();
        let mut complete = false;
        for offset in 0..128u32 {
            let address = name_rva.checked_add(offset).ok_or("PE name overflow")?;
            let byte = read(file, rva(address, 1)?, 1)?[0];
            if byte == 0 {
                complete = true;
                break;
            }
            if !(byte.is_ascii_alphanumeric() || b"-_.".contains(&byte)) {
                return Err("PE DLL name invalid".into());
            }
            name.push(byte);
        }
        if !complete || name.is_empty() {
            return Err("PE DLL name unterminated".into());
        }
        let name = String::from_utf8(name)?.to_ascii_lowercase();
        let system = [
            "kernel32.dll",
            "ntdll.dll",
            "advapi32.dll",
            "user32.dll",
            "gdi32.dll",
            "shell32.dll",
            "ole32.dll",
            "combase.dll",
            // Native AccessKit/UIA provider: UiaReturnRawElementProvider is
            // exported by this Windows system DLL (uiautomationcoreapi.h).
            "uiautomationcore.dll",
            "oleaut32.dll",
            "ws2_32.dll",
            "iphlpapi.dll",
            "version.dll",
            "wtsapi32.dll",
            "pdh.dll",
            "powrprof.dll",
            "netapi32.dll",
            "secur32.dll",
            "psapi.dll",
            "wevtapi.dll",
            "bcryptprimitives.dll",
            "crypt32.dll",
            "bcrypt.dll",
            "imm32.dll",
            "dwmapi.dll",
            "uxtheme.dll",
            "comctl32.dll",
            "shcore.dll",
            "winmm.dll",
            "dbghelp.dll",
            "d3d11.dll",
            "dxgi.dll",
            "d3d12.dll",
            "d3dcompiler_47.dll",
        ];
        if !system.contains(&name.as_str())
            && !name.starts_with("api-ms-win-core-")
            && !name.starts_with("api-ms-win-shcore-")
        {
            return Err(format!("base dependency is not admitted: {name}").into());
        }
        names.insert(name);
    }
    if !ended {
        return Err("PE imports lack termination".into());
    }
    Ok(names.into_iter().collect())
}
