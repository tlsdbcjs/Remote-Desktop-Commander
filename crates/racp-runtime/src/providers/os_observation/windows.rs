use racp_contract::RacpError;
use serde_json::{json, Value};
use std::{
    mem::size_of,
    net::{Ipv4Addr, Ipv6Addr},
};
use windows_sys::Win32::{
    Foundation::*,
    Globalization::*,
    NetworkManagement::IpHelper::*,
    System::{Registry::*, Services::*, Threading::GetSystemTimes, Time::*},
};
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
fn text(s: &[u16]) -> String {
    String::from_utf16_lossy(&s[..s.iter().position(|v| *v == 0).unwrap_or(s.len())])
}
fn denied() -> RacpError {
    RacpError::new("PERMISSION_DENIED")
}
pub(super) fn cpu_times() -> Result<Value, RacpError> {
    let mut idle = FILETIME::default();
    let mut kernel = idle;
    let mut user = idle;
    if unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } == 0 {
        return Err(denied());
    }
    let seconds = |t: FILETIME| {
        (((t.dwHighDateTime as u64) << 32) | t.dwLowDateTime as u64) as f64 / 10000000.0
    };
    Ok(
        json!({"user":seconds(user),"system":(seconds(kernel)-seconds(idle)).max(0.0),"idle":seconds(idle)}),
    )
}
pub(super) fn locale() -> Result<Value, RacpError> {
    let mut name = [0u16; 85];
    if unsafe { GetUserDefaultLocaleName(name.as_mut_ptr(), 85) } == 0 {
        return Err(denied());
    }
    let mut zone = TIME_ZONE_INFORMATION::default();
    let kind = unsafe { GetTimeZoneInformation(&mut zone) };
    if kind == u32::MAX {
        return Err(denied());
    }
    let bias = zone.Bias
        + if kind == 2 {
            zone.DaylightBias
        } else {
            zone.StandardBias
        };
    let timezone = if kind == 2 {
        text(&zone.DaylightName)
    } else {
        text(&zone.StandardName)
    };
    Ok(
        json!({"timezone":timezone,"utc_offset_seconds":-60*bias,"process_locale":[text(&name),"UTF-8"],"filesystem_encoding":"utf-8","preferred_encoding":"utf-8"}),
    )
}
struct Service(SC_HANDLE);
impl Drop for Service {
    fn drop(&mut self) {
        unsafe {
            CloseServiceHandle(self.0);
        }
    }
}
fn manager(access: u32) -> Result<Service, RacpError> {
    let raw = unsafe { OpenSCManagerW(std::ptr::null(), std::ptr::null(), access) };
    if raw.is_null() {
        Err(denied())
    } else {
        Ok(Service(raw))
    }
}
fn service_record(manager: &Service, name: &str, display: &str, status: u32) -> Value {
    let raw = unsafe { OpenServiceW(manager.0, wide(name).as_ptr(), SERVICE_QUERY_CONFIG) };
    let mut start = Value::Null;
    if !raw.is_null() {
        let service = Service(raw);
        let mut needed = 0;
        unsafe {
            QueryServiceConfigW(service.0, std::ptr::null_mut(), 0, &mut needed);
        }
        if needed > 0 && needed <= 65536 {
            let mut buffer =
                vec![0usize; (needed as usize + size_of::<usize>() - 1) / size_of::<usize>()];
            let config = buffer.as_mut_ptr().cast::<QUERY_SERVICE_CONFIGW>();
            if unsafe { QueryServiceConfigW(service.0, config, needed, &mut needed) } != 0 {
                start = json!(match unsafe { (*config).dwStartType } {
                    SERVICE_AUTO_START => "automatic",
                    SERVICE_DEMAND_START => "manual",
                    SERVICE_DISABLED => "disabled",
                    SERVICE_BOOT_START => "boot",
                    SERVICE_SYSTEM_START => "system",
                    _ => "unknown",
                });
            }
        }
    }
    json!({"name":name,"display_name":display,"status":match status{SERVICE_RUNNING=>"running",SERVICE_STOPPED=>"stopped",SERVICE_PAUSED=>"paused",SERVICE_START_PENDING=>"start_pending",SERVICE_STOP_PENDING=>"stop_pending",SERVICE_CONTINUE_PENDING=>"continue_pending",SERVICE_PAUSE_PENDING=>"pause_pending",_=>"unknown"},"start_type":start,"availability":"available"})
}
fn bounded_pointer(raw: *const u16, buffer: &[usize]) -> Result<String, RacpError> {
    let low = buffer.as_ptr() as usize;
    let high = low + std::mem::size_of_val(buffer);
    let address = raw as usize;
    if address < low || address >= high || address % 2 != 0 {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    let words = unsafe { std::slice::from_raw_parts(raw, ((high - address) / 2).min(1024)) };
    let end = words
        .iter()
        .position(|w| *w == 0)
        .ok_or_else(|| RacpError::new("RESOURCE_EXHAUSTED"))?;
    String::from_utf16(&words[..end]).map_err(|_| RacpError::new("INVALID_ARGUMENT"))
}
fn services(p: &Value) -> Result<Value, RacpError> {
    let manager = manager(SC_MANAGER_ENUMERATE_SERVICE | SC_MANAGER_CONNECT)?;
    let mut resume = 0;
    let mut skipped = 0usize;
    let mut items = vec![];
    let offset = p["offset"].as_u64().unwrap_or(0) as usize;
    let limit = p["limit"].as_u64().unwrap_or(100) as usize;
    loop {
        let mut buffer = vec![0usize; 256 * 1024 / size_of::<usize>()];
        let mut needed = 0;
        let mut count = 0;
        let okay = unsafe {
            EnumServicesStatusExW(
                manager.0,
                SC_ENUM_PROCESS_INFO,
                SERVICE_WIN32,
                SERVICE_STATE_ALL,
                buffer.as_mut_ptr().cast(),
                (buffer.len() * size_of::<usize>()) as u32,
                &mut needed,
                &mut count,
                &mut resume,
                std::ptr::null(),
            )
        };
        if okay == 0 && unsafe { GetLastError() } != ERROR_MORE_DATA {
            return Err(denied());
        }
        if count as usize
            > std::mem::size_of_val(buffer.as_slice()) / size_of::<ENUM_SERVICE_STATUS_PROCESSW>()
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let rows = unsafe {
            std::slice::from_raw_parts(
                buffer.as_ptr().cast::<ENUM_SERVICE_STATUS_PROCESSW>(),
                count as usize,
            )
        };
        for row in rows {
            if skipped < offset {
                skipped += 1;
                continue;
            }
            let name = bounded_pointer(row.lpServiceName, &buffer)?;
            let display = bounded_pointer(row.lpDisplayName, &buffer)?;
            items.push(service_record(
                &manager,
                &name,
                &display,
                row.ServiceStatusProcess.dwCurrentState,
            ));
            if items.len() > limit {
                break;
            }
        }
        if items.len() > limit || okay != 0 {
            break;
        }
        if count == 0 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
    }
    let truncated = items.len() > limit;
    items.truncate(limit);
    Ok(
        json!({"items":items,"truncated":truncated,"next_offset":if truncated{Some(offset+limit)}else{None},"scope":"current_os_account","consistency":"live_observation","arguments_included":false}),
    )
}
fn get_service(p: &Value) -> Result<Value, RacpError> {
    let manager = manager(SC_MANAGER_CONNECT)?;
    let name = p["name"].as_str().unwrap();
    let raw = unsafe {
        OpenServiceW(
            manager.0,
            wide(name).as_ptr(),
            SERVICE_QUERY_STATUS | SERVICE_QUERY_CONFIG,
        )
    };
    if raw.is_null() {
        return Err(RacpError::new(
            if unsafe { GetLastError() } == ERROR_SERVICE_DOES_NOT_EXIST {
                "PROCESS_NOT_FOUND"
            } else {
                "PERMISSION_DENIED"
            },
        ));
    }
    let service = Service(raw);
    let mut status = SERVICE_STATUS_PROCESS::default();
    let mut needed = 0;
    if unsafe {
        QueryServiceStatusEx(
            service.0,
            SC_STATUS_PROCESS_INFO,
            (&mut status as *mut SERVICE_STATUS_PROCESS).cast(),
            size_of::<SERVICE_STATUS_PROCESS>() as u32,
            &mut needed,
        )
    } == 0
    {
        return Err(denied());
    }
    let mut display = [0u16; 1024];
    let mut length = 1024;
    let display = if unsafe {
        GetServiceDisplayNameW(
            manager.0,
            wide(name).as_ptr(),
            display.as_mut_ptr(),
            &mut length,
        )
    } != 0
    {
        text(&display)
    } else {
        name.into()
    };
    Ok(
        json!({"service":service_record(&manager,name,&display,status.dwCurrentState),"scope":"current_os_account","arguments_included":false}),
    )
}
struct Key(HKEY);
impl Drop for Key {
    fn drop(&mut self) {
        unsafe {
            RegCloseKey(self.0);
        }
    }
}
fn open(root: HKEY, path: &str, view: u32) -> Option<Key> {
    let mut raw = std::ptr::null_mut();
    if unsafe { RegOpenKeyExW(root, wide(path).as_ptr(), 0, KEY_READ | view, &mut raw) }
        == ERROR_SUCCESS
    {
        Some(Key(raw))
    } else {
        None
    }
}
fn registry_text(key: &Key, name: &str) -> Option<String> {
    let mut buffer = [0u16; 2048];
    let mut bytes = 4096;
    let mut kind = 0;
    if unsafe {
        RegQueryValueExW(
            key.0,
            wide(name).as_ptr(),
            std::ptr::null(),
            &mut kind,
            buffer.as_mut_ptr().cast(),
            &mut bytes,
        )
    } != ERROR_SUCCESS
        || !matches!(kind, REG_SZ | REG_EXPAND_SZ)
        || bytes > 4096
        || bytes % 2 != 0
    {
        return None;
    }
    let value = text(&buffer[..bytes as usize / 2]);
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}
fn software(p: &Value) -> Result<Value, RacpError> {
    let offset = p["offset"].as_u64().unwrap_or(0) as usize;
    let limit = p["limit"].as_u64().unwrap_or(100) as usize;
    let mut position = 0;
    let mut items = vec![];
    let mut unavailable = vec![];
    'roots: for (label, root) in [("machine", HKEY_LOCAL_MACHINE), ("user", HKEY_CURRENT_USER)] {
        for (view, access) in [("64", KEY_WOW64_64KEY), ("32", KEY_WOW64_32KEY)] {
            let Some(container) = open(
                root,
                "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
                access,
            ) else {
                unavailable.push(json!({"source":label,"registry_view":view}));
                continue;
            };
            for index in 0..20000u32 {
                let mut buffer = [0u16; 512];
                let mut length = 512;
                let code = unsafe {
                    RegEnumKeyExW(
                        container.0,
                        index,
                        buffer.as_mut_ptr(),
                        &mut length,
                        std::ptr::null(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                        std::ptr::null_mut(),
                    )
                };
                if code == ERROR_NO_MORE_ITEMS {
                    break;
                }
                if code != ERROR_SUCCESS {
                    return Err(RacpError::new("RESOURCE_EXHAUSTED"));
                }
                if position < offset {
                    position += 1;
                    continue;
                }
                let name = text(&buffer[..length as usize]);
                let record = if let Some(key) = open(container.0, &name, access) {
                    json!({"id":format!("{label}:{view}:{name}"),"name":registry_text(&key,"DisplayName"),"version":registry_text(&key,"DisplayVersion"),"publisher":registry_text(&key,"Publisher"),"source":label,"registry_view":view})
                } else {
                    json!({"id":format!("{label}:{view}:{name}"),"availability":"unavailable"})
                };
                items.push(record);
                if items.len() > limit {
                    break 'roots;
                }
            }
        }
    }
    let truncated = items.len() > limit;
    items.truncate(limit);
    Ok(
        json!({"items":items,"truncated":truncated,"next_offset":if truncated{Some(offset+limit)}else{None},"unavailable_sources":unavailable,"scope":"windows_uninstall_registration","consistency":"live_observation","msix_inventory_included":false,"uninstall_commands_included":false}),
    )
}
fn dword(row: &[u8], offset: usize) -> u32 {
    u32::from_ne_bytes(row[offset..offset + 4].try_into().unwrap())
}
fn port(row: &[u8], offset: usize) -> u16 {
    u16::from_be_bytes(row[offset..offset + 2].try_into().unwrap())
}
fn connections(p: &Value) -> Result<Value, RacpError> {
    let limit = p["limit"].as_u64().unwrap_or(100) as usize;
    let pid = p["pid"].as_u64().map(|n| n as u32);
    let mut items = vec![];
    let mut total = 0;
    for (family, width, tcp) in [
        (2, 24, true),
        (23, 56, true),
        (2, 12, false),
        (23, 28, false),
    ] {
        let mut size = 0;
        let invoke = |buffer: *mut core::ffi::c_void, size: &mut u32| unsafe {
            if tcp {
                GetExtendedTcpTable(buffer, size, 1, family, TCP_TABLE_OWNER_PID_ALL, 0)
            } else {
                GetExtendedUdpTable(buffer, size, 1, family, UDP_TABLE_OWNER_PID, 0)
            }
        };
        let code = invoke(std::ptr::null_mut(), &mut size);
        if code != ERROR_INSUFFICIENT_BUFFER && code != ERROR_SUCCESS {
            return Err(denied());
        }
        if size < 4 || size > 16 * 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let mut buffer = vec![0u32; (size as usize + 3) / 4];
        if invoke(buffer.as_mut_ptr().cast(), &mut size) != ERROR_SUCCESS {
            return Err(denied());
        }
        let bytes =
            unsafe { std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), size as usize) };
        let count = dword(bytes, 0) as usize;
        if count > (bytes.len() - 4) / width {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        for row in bytes[4..].chunks_exact(width).take(count) {
            let process = dword(row, width - 4);
            if pid.is_some_and(|p| p != process) {
                continue;
            }
            total += 1;
            if items.len() >= limit {
                continue;
            }
            let (state, local, remote) = if family == 2 {
                let start = if tcp { 4 } else { 0 };
                let local = json!({"ip":Ipv4Addr::from(dword(row,start).to_ne_bytes()).to_string(),"port":port(row,start+4)});
                let remote = if tcp && dword(row, 0) != MIB_TCP_STATE_LISTEN as u32 {
                    json!({"ip":Ipv4Addr::from(dword(row,12).to_ne_bytes()).to_string(),"port":port(row,16)})
                } else {
                    Value::Null
                };
                (if tcp { dword(row, 0) } else { 0 }, local, remote)
            } else {
                let address: [u8; 16] = row[..16].try_into().unwrap();
                let local = json!({"ip":Ipv6Addr::from(address).to_string(),"port":port(row,20),"scope_id":dword(row,16)});
                let state = if tcp { dword(row, 48) } else { 0 };
                let remote = if tcp && state != MIB_TCP_STATE_LISTEN as u32 {
                    let address: [u8; 16] = row[24..40].try_into().unwrap();
                    json!({"ip":Ipv6Addr::from(address).to_string(),"port":port(row,44),"scope_id":dword(row,40)})
                } else {
                    Value::Null
                };
                (state, local, remote)
            };
            items.push(json!({"pid":process,"family":family,"type":if tcp{1}else{2},"local":local,"remote":remote,"status":if tcp{match state{1=>"CLOSED",2=>"LISTEN",3=>"SYN_SENT",4=>"SYN_RECV",5=>"ESTABLISHED",6=>"FIN_WAIT1",7=>"FIN_WAIT2",8=>"CLOSE_WAIT",9=>"CLOSING",10=>"LAST_ACK",11=>"TIME_WAIT",12=>"DELETE_TCB",_=>"UNKNOWN"}}else{"NONE"}}));
        }
    }
    items.sort_by_key(|v| {
        (
            v["pid"].as_u64(),
            v["local"].to_string(),
            v["remote"].to_string(),
        )
    });
    Ok(
        json!({"items":items,"truncated":total>limit,"consistency":"live_observation","visibility":"current_os_account"}),
    )
}
pub(super) fn observe(op: &str, p: &Value) -> Result<Value, RacpError> {
    match op {
        "services.list" => services(p),
        "services.get" => get_service(p),
        "software.inventory" => software(p),
        "network.connections" => connections(p),
        _ => Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
    }
}
