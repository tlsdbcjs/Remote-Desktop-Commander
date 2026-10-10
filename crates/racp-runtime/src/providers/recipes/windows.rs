use super::*;
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Seek, SeekFrom},
    mem::size_of,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::{
    Foundation::*,
    Networking::WinSock::*,
    System::{Diagnostics::Debug::*, LibraryLoader::*, Threading::*},
    UI::Shell::IsUserAnAdmin,
};
pub(super) fn administrator() -> bool {
    unsafe { IsUserAnAdmin() != 0 }
}
fn denied() -> RacpError {
    RacpError::new("PERMISSION_DENIED")
}
fn u32le(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
}
fn u16be(b: &[u8], at: usize) -> u16 {
    u16::from_be_bytes(b[at..at + 2].try_into().unwrap())
}
pub(super) fn validate_target(p: &Value) -> Result<(), RacpError> {
    let pid = p["pid"].as_u64().unwrap_or(0) as u32;
    let system = sysinfo::System::new_all();
    if super::super::processes::Processes::protected(&system, pid) {
        return Err(denied());
    }
    let pinned = super::super::desktop::native_identity::PinnedPeer::open(pid)?;
    let current = super::super::desktop::native_identity::PinnedPeer::open(std::process::id())?;
    if pinned.identity().sid != current.identity().sid
        || pinned.identity().integrity > current.identity().integrity
    {
        return Err(denied());
    }
    if (pinned.identity().created - p["create_time"].as_f64().unwrap_or(0.0)).abs() > 0.000001 {
        return Err(RacpError::new("PRECONDITION_FAILED"));
    }
    pinned.alive()
}
struct Dump {
    file: File,
    limit: u64,
    size: u64,
    started: bool,
    finished: bool,
    failure: Option<RacpError>,
}
unsafe extern "system" fn callback(
    context: *mut core::ffi::c_void,
    input: *const MINIDUMP_CALLBACK_INPUT,
    output: *mut MINIDUMP_CALLBACK_OUTPUT,
) -> i32 {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if context.is_null() || input.is_null() || output.is_null() {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let dump = &mut *context.cast::<Dump>();
        let input = std::ptr::read_unaligned(input);
        match input.CallbackType as i32 {
            IoStartCallback => {
                dump.started = true;
                (*output).Anonymous.Status = 1;
            }
            IoWriteAllCallback => {
                let io = input.Anonymous.Io;
                let end = io
                    .Offset
                    .checked_add(io.BufferBytes as u64)
                    .filter(|n| *n <= dump.limit)
                    .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
                if !dump.started || io.Buffer.is_null() {
                    return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
                }
                dump.file.seek(SeekFrom::Start(io.Offset))?;
                dump.file.write_all(std::slice::from_raw_parts(
                    io.Buffer.cast::<u8>(),
                    io.BufferBytes as usize,
                ))?;
                dump.size = dump.size.max(end);
                (*output).Anonymous.Status = 0;
            }
            IoFinishCallback => {
                dump.finished = true;
                (*output).Anonymous.Status = 0;
            }
            _ => (),
        }
        Ok::<_, RacpError>(())
    }));
    match result {
        Ok(Ok(())) => 1,
        error => {
            if !context.is_null() {
                (*context.cast::<Dump>()).failure = Some(match error {
                    Ok(Err(e)) => e,
                    _ => RacpError::new("CAPABILITY_UNAVAILABLE"),
                });
            }
            if !output.is_null() {
                (*output).Anonymous.Status = 0x80004005u32 as i32;
            }
            0
        }
    }
}
type DumpFn = unsafe extern "system" fn(
    HANDLE,
    u32,
    HANDLE,
    MINIDUMP_TYPE,
    *const MINIDUMP_EXCEPTION_INFORMATION,
    *const MINIDUMP_USER_STREAM_INFORMATION,
    *const MINIDUMP_CALLBACK_INFORMATION,
) -> i32;
struct Library(HMODULE);
impl Drop for Library {
    fn drop(&mut self) {
        unsafe {
            FreeLibrary(self.0);
        }
    }
}
pub(super) fn dump(p: &Value, path: &Path) -> Result<Value, RacpError> {
    validate_target(p)?;
    let pid = p["pid"].as_u64().unwrap() as u32;
    let raw = unsafe {
        OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_SYNCHRONIZE,
            0,
            pid,
        )
    };
    if raw.is_null() {
        return Err(denied());
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut created = FILETIME::default();
    let mut exited = created;
    let mut kernel = created;
    let mut user = created;
    if unsafe { GetProcessTimes(raw, &mut created, &mut exited, &mut kernel, &mut user) } == 0 {
        return Err(denied());
    }
    let ticks = ((created.dwHighDateTime as u64) << 32) | created.dwLowDateTime as u64;
    let birth = (ticks / 10000000) as f64 + (ticks % 10000000) as f64 / 10000000.0 - 11644473600.0;
    if (birth - p["create_time"].as_f64().unwrap()).abs() > 0.000001
        || unsafe { WaitForSingleObject(raw, 0) } != WAIT_TIMEOUT
    {
        return Err(RacpError::new("PRECONDITION_FAILED"));
    }
    let name: Vec<u16> = "dbghelp.dll".encode_utf16().chain(Some(0)).collect();
    let library = Library(unsafe {
        LoadLibraryExW(
            name.as_ptr(),
            std::ptr::null_mut(),
            LOAD_LIBRARY_SEARCH_SYSTEM32,
        )
    });
    if library.0.is_null() {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    let address = unsafe { GetProcAddress(library.0, c"MiniDumpWriteDump".as_ptr().cast()) }
        .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
    let write: DumpFn = unsafe { std::mem::transmute(address) };
    let file = racp_core::secure_create_file(path)?;
    let mut dump = Dump {
        file,
        limit: p["max_bytes"].as_u64().unwrap_or(67108864),
        size: 0,
        started: false,
        finished: false,
        failure: None,
    };
    let info = MINIDUMP_CALLBACK_INFORMATION {
        CallbackRoutine: Some(callback),
        CallbackParam: (&mut dump as *mut Dump).cast(),
    };
    let sink = std::fs::OpenOptions::new().write(true).open(r"\\.\NUL")?;
    let full = p["mode"] == "full";
    let okay = unsafe {
        write(
            raw,
            pid,
            sink.as_raw_handle(),
            if full {
                MiniDumpWithFullMemory
            } else {
                MiniDumpNormal
            },
            std::ptr::null(),
            std::ptr::null(),
            &info,
        )
    };
    if let Some(e) = dump.failure {
        return Err(e);
    }
    if okay == 0 || !dump.started || !dump.finished || dump.size < 32 {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    if unsafe { WaitForSingleObject(raw, 0) } != WAIT_TIMEOUT {
        return Err(RacpError::new("PRECONDITION_FAILED"));
    }
    dump.file.sync_all()?;
    let mut collected = racp_core::secure_read_file(path)?;
    let mut hash = Sha256::new();
    let mut block = [0u8; 65536];
    loop {
        let n = collected.read(&mut block)?;
        if n == 0 {
            break;
        }
        hash.update(&block[..n]);
    }
    drop(handle);
    Ok(
        json!({"backend":"Windows DbgHelp MiniDumpWriteDump","target_pid":pid,"target_create_time":birth,"mode":p["mode"],"bytes":dump.size,"sha256":format!("{:x}",hash.finalize()),"complete":true,"process_handle_closed":true,"bounded_io":true}),
    )
}
struct Winsock;
impl Drop for Winsock {
    fn drop(&mut self) {
        unsafe {
            WSACleanup();
        }
    }
}
struct Socket(SOCKET);
impl Drop for Socket {
    fn drop(&mut self) {
        unsafe {
            let off = 0u32;
            let mut returned = 0;
            WSAIoctl(
                self.0,
                0x98000001,
                (&off as *const u32).cast(),
                4,
                std::ptr::null_mut(),
                0,
                &mut returned,
                std::ptr::null_mut(),
                None,
            );
            closesocket(self.0);
        }
    }
}
fn matches(packet: &[u8], local: std::net::Ipv4Addr, peer: std::net::Ipv4Addr, port: u16) -> bool {
    if packet.len() < 24 || packet[0] >> 4 != 4 || !matches!(packet[9], 6 | 17) {
        return false;
    }
    let header = (packet[0] & 15) as usize * 4;
    let length = u16be(packet, 2) as usize;
    let minimum = if packet[9] == 6 { 20 } else { 8 };
    if header < 20
        || length > packet.len()
        || length < header + minimum
        || u16be(packet, 6) & 0x1fff != 0
    {
        return false;
    }
    let source: &[u8] = &packet[12..16];
    let destination: &[u8] = &packet[16..20];
    source == local.octets() && destination == peer.octets() && u16be(packet, header) == port
        || source == peer.octets()
            && destination == local.octets()
            && u16be(packet, header + 2) == port
}
pub(super) fn capture(p: &Value, path: &Path) -> Result<Value, RacpError> {
    let local = p["local_ip"]
        .as_str()
        .unwrap()
        .parse::<std::net::Ipv4Addr>()
        .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
    let peer = p["peer_ip"]
        .as_str()
        .unwrap()
        .parse::<std::net::Ipv4Addr>()
        .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
    if [local, peer]
        .iter()
        .any(|ip| ip.is_unspecified() || ip.is_multicast() || ip.is_broadcast())
    {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    let port = p["local_port"].as_u64().unwrap() as u16;
    let max = p["max_bytes"].as_u64().unwrap_or(16777216);
    let duration = p["duration_ms"].as_u64().unwrap_or(3000);
    let mut data = WSADATA::default();
    if unsafe { WSAStartup(0x202, &mut data) } != 0 {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    let _winsock = Winsock;
    let raw = unsafe { socket(AF_INET as i32, SOCK_RAW, IPPROTO_IP) };
    if raw == INVALID_SOCKET {
        return Err(denied());
    }
    let socket = Socket(raw);
    let address = SOCKADDR_IN {
        sin_family: AF_INET,
        sin_port: 0,
        sin_addr: IN_ADDR {
            S_un: IN_ADDR_0 {
                S_addr: u32::from_ne_bytes(local.octets()),
            },
        },
        sin_zero: [0; 8],
    };
    let timeout = 100u32;
    let on = 3u32;
    let mut returned = 0;
    if unsafe {
        bind(
            raw,
            (&address as *const SOCKADDR_IN).cast(),
            size_of::<SOCKADDR_IN>() as i32,
        )
    } == SOCKET_ERROR
        || unsafe {
            setsockopt(
                raw,
                SOL_SOCKET,
                SO_RCVTIMEO,
                (&timeout as *const u32).cast(),
                4,
            )
        } == SOCKET_ERROR
        || unsafe {
            WSAIoctl(
                raw,
                0x98000001,
                (&on as *const u32).cast(),
                4,
                std::ptr::null_mut(),
                0,
                &mut returned,
                std::ptr::null_mut(),
                None,
            )
        } == SOCKET_ERROR
    {
        return Err(denied());
    }
    let mut output = racp_core::secure_create_file(path)?;
    let header: [u8; 24] = [
        0xd4, 0xc3, 0xb2, 0xa1, 2, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff, 0, 0, 101, 0, 0, 0,
    ];
    output.write_all(&header)?;
    let mut hash = Sha256::new();
    hash.update(header);
    let mut size = 24u64;
    let mut count = 0;
    let started = Instant::now();
    let deadline = started + Duration::from_millis(duration);
    let mut reason = "duration";
    let mut buffer = [0u8; 65535];
    while Instant::now() < deadline {
        let n = unsafe { recv(raw, buffer.as_mut_ptr(), buffer.len() as i32, 0) };
        if n == SOCKET_ERROR {
            if unsafe { WSAGetLastError() } == WSAETIMEDOUT {
                continue;
            }
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let packet = &buffer[..n as usize];
        if !matches(packet, local, peer, port) {
            continue;
        }
        let packet = &packet[..u16be(packet, 2) as usize];
        if size + 16 + packet.len() as u64 > max {
            reason = "byte_limit";
            break;
        }
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let mut record = vec![];
        for value in [
            now.as_secs() as u32,
            now.subsec_micros(),
            packet.len() as u32,
            packet.len() as u32,
        ] {
            record.extend(value.to_le_bytes());
        }
        record.extend(packet);
        output.write_all(&record)?;
        hash.update(&record);
        size += record.len() as u64;
        count += 1;
    }
    drop(socket);
    output.sync_all()?;
    if count == 0 {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    Ok(
        json!({"backend":"Windows Winsock SIO_RCVALL","local_ip":local.to_string(),"peer_ip":peer.to_string(),"local_port":port,"packets":count,"bytes":size,"sha256":format!("{:x}",hash.finalize()),"duration_ms":started.elapsed().as_millis(),"complete":reason=="duration","termination_reason":reason,"raw_socket_closed":true,"link_type":"raw_ipv4","scope":"selected_ipv4_tcp_udp_flow","non_initial_fragments":"excluded","tls_decryption":false}),
    )
}
pub(super) fn verify(
    path: &Path,
    receipt: &Value,
    p: &Value,
    dump: bool,
    check: &impl Fn() -> Result<(), RacpError>,
) -> Result<(), RacpError> {
    let mut input = racp_core::secure_read_file(path)?;
    let size = input.metadata()?.len();
    let max = p["max_bytes"]
        .as_u64()
        .unwrap_or(if dump { 67108864 } else { 16777216 });
    if size > max || receipt["bytes"].as_u64() != Some(size) {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    let mut hash = Sha256::new();
    let mut block = [0u8; 65536];
    loop {
        check()?;
        let n = input.read(&mut block)?;
        if n == 0 {
            break;
        }
        hash.update(&block[..n]);
    }
    if receipt["sha256"] != format!("{:x}", hash.finalize()) {
        return Err(RacpError::new("CHECKSUM_MISMATCH"));
    }
    input.seek(SeekFrom::Start(0))?;
    if dump {
        let mut header = [0u8; 32];
        input.read_exact(&mut header)?;
        let count = u32le(&header, 8) as u64;
        let directory = u32le(&header, 12) as u64;
        let flags = u64::from_le_bytes(header[24..32].try_into().unwrap());
        if u32le(&header, 0) != 0x504d444d
            || u32le(&header, 4) & 0xffff != 0xa793
            || !(1..=1024).contains(&count)
            || directory < 32
            || directory + count * 12 > size
            || (flags & 2 != 0) != (p["mode"] == "full")
            || receipt["target_pid"] != p["pid"]
            || receipt["mode"] != p["mode"]
            || receipt["complete"] != true
            || receipt["bounded_io"] != true
            || receipt["process_handle_closed"] != true
        {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        input.seek(SeekFrom::Start(directory))?;
        let mut entry = [0u8; 12];
        for _ in 0..count {
            check()?;
            input.read_exact(&mut entry)?;
            let length = u32le(&entry, 4) as u64;
            let offset = u32le(&entry, 8) as u64;
            if length > 0 && (offset < 32 || offset + length > size) {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            }
        }
        if (receipt["target_create_time"].as_f64().unwrap_or(0.0)
            - p["create_time"].as_f64().unwrap())
        .abs()
            > 0.000001
        {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
    } else {
        let local = p["local_ip"]
            .as_str()
            .unwrap()
            .parse()
            .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
        let peer = p["peer_ip"]
            .as_str()
            .unwrap()
            .parse()
            .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
        let port = p["local_port"].as_u64().unwrap() as u16;
        let mut header = [0u8; 24];
        input.read_exact(&mut header)?;
        if u32le(&header, 0) != 0xa1b2c3d4 || u32le(&header, 20) != 101 {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let mut position = 24;
        let mut count = 0u64;
        while position < size {
            check()?;
            let mut record = [0u8; 16];
            input.read_exact(&mut record)?;
            let length = u32le(&record, 8) as usize;
            if u32le(&record, 4) >= 1000000
                || length > 65535
                || length != u32le(&record, 12) as usize
                || position + 16 + length as u64 > size
            {
                return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
            }
            let mut packet = vec![0u8; length];
            input.read_exact(&mut packet)?;
            if !matches(&packet, local, peer, port) {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            position += 16 + length as u64;
            count += 1;
        }
        if count == 0
            || receipt["packets"].as_u64() != Some(count)
            || receipt["local_ip"] != p["local_ip"]
            || receipt["peer_ip"] != p["peer_ip"]
            || receipt["local_port"] != p["local_port"]
            || receipt["raw_socket_closed"] != true
            || receipt["tls_decryption"] != false
        {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
    }
    Ok(())
}
