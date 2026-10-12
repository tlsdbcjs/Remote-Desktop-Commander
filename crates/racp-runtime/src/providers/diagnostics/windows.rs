use racp_contract::RacpError;
use serde_json::{json, Value};
use std::{mem::size_of, time::Instant};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{
    Foundation::{GetLastError, ERROR_NO_MORE_ITEMS, FILETIME},
    System::{EventLog::*, Threading::GetSystemTimes},
};
pub(super) fn cpu_times() -> Result<Value, RacpError> {
    let mut idle = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    if unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } == 0 {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    let ticks = |v: FILETIME| u64::from(v.dwLowDateTime) | (u64::from(v.dwHighDateTime) << 32);
    Ok(json!({"idle":ticks(idle),"kernel_including_idle":ticks(kernel),"user":ticks(user)}))
}
struct Handle(isize);
impl Drop for Handle {
    fn drop(&mut self) {
        if self.0 != 0 {
            unsafe {
                EvtClose(self.0);
            }
        }
    }
}
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
unsafe fn string(value: &EVT_VARIANT, buffer: &[usize]) -> Result<String, RacpError> {
    if value.Type != EvtVarTypeString as u32 {
        return Ok(String::new());
    }
    let ptr = unsafe { value.Anonymous.StringVal };
    if ptr.is_null() {
        return Ok(String::new());
    }
    let begin = buffer.as_ptr() as usize;
    let end = begin + std::mem::size_of_val(buffer);
    let address = ptr as usize;
    if address < begin || address >= end || address % 2 != 0 {
        return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
    }
    let available = ((end - address) / 2).min(4096);
    let mut len = 0;
    while len < available && unsafe { *ptr.add(len) } != 0 {
        len += 1;
    }
    if len >= available {
        return Err(RacpError::new("RESOURCE_EXHAUSTED"));
    }
    String::from_utf16(unsafe { std::slice::from_raw_parts(ptr, len) })
        .map_err(|_| RacpError::new("RUNTIME_RESPONSE_INVALID"))
}
pub(super) fn events(
    p: &Value,
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<Value, RacpError> {
    let channel = p["channel"].as_str().unwrap_or("System");
    if !["System", "Application", "Security"].contains(&channel) {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    let after = p["after_record_id"].as_u64().unwrap_or(0);
    let limit = p["limit"].as_u64().unwrap_or(100).min(1000) as usize;
    let channel = wide(channel);
    let query = wide(&format!("*[System[(EventRecordID > {after})]]"));
    let query = Handle(unsafe {
        EvtQuery(
            0,
            channel.as_ptr(),
            query.as_ptr(),
            EvtQueryChannelPath | EvtQueryForwardDirection,
        )
    });
    if query.0 == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let context =
        Handle(unsafe { EvtCreateRenderContext(0, std::ptr::null(), EvtRenderContextSystem) });
    if context.0 == 0 {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    let mut items = vec![];
    let mut cursor = after;
    while items.len() < limit {
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        let mut raw_handle = 0;
        let mut returned = 0;
        if unsafe { EvtNext(query.0, 1, &mut raw_handle, 50, 0, &mut returned) } == 0 {
            if unsafe { GetLastError() } == ERROR_NO_MORE_ITEMS {
                break;
            }
            return Err(RacpError::new("EVENT_QUERY_INTERRUPTED"));
        }
        let event = Handle(raw_handle);
        let mut used = 0;
        let mut count = 0;
        unsafe {
            EvtRender(
                context.0,
                event.0,
                EvtRenderEventValues,
                0,
                std::ptr::null_mut(),
                &mut used,
                &mut count,
            );
        }
        if used == 0 || used > 65536 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let mut words = vec![0usize; (used as usize).div_ceil(size_of::<usize>())];
        if unsafe {
            EvtRender(
                context.0,
                event.0,
                EvtRenderEventValues,
                used,
                words.as_mut_ptr().cast(),
                &mut used,
                &mut count,
            )
        } == 0
            || count < 18
            || count as usize * size_of::<EVT_VARIANT>() > words.len() * size_of::<usize>()
        {
            return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
        }
        let values = unsafe {
            std::slice::from_raw_parts(words.as_ptr().cast::<EVT_VARIANT>(), count as usize)
        };
        if values[9].Type != EvtVarTypeUInt64 as u32 {
            return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
        }
        cursor = unsafe { values[9].Anonymous.UInt64Val };
        for (index, kind) in [
            (2, EvtVarTypeUInt16),
            (4, EvtVarTypeByte),
            (8, EvtVarTypeFileTime),
            (12, EvtVarTypeUInt32),
            (13, EvtVarTypeUInt32),
        ] {
            if values[index].Type != kind as u32 && values[index].Type != EvtVarTypeNull as u32 {
                return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
            }
        }
        items.push(unsafe{json!({"record_id":cursor,"provider":string(&values[0],&words)?,"event_id":(values[2].Type!=EvtVarTypeNull as u32).then(||values[2].Anonymous.UInt16Val),"level":(values[4].Type!=EvtVarTypeNull as u32).then(||values[4].Anonymous.ByteVal),"time_created_filetime":(values[8].Type!=EvtVarTypeNull as u32).then(||values[8].Anonymous.FileTimeVal),"process_id":(values[12].Type!=EvtVarTypeNull as u32).then(||values[12].Anonymous.UInt32Val),"thread_id":(values[13].Type!=EvtVarTypeNull as u32).then(||values[13].Anonymous.UInt32Val),"event_data_omitted":true})});
    }
    Ok(
        json!({"items":items,"next_record_id":cursor,"truncated":items.len()==limit,"content":"system_metadata_only"}),
    )
}
