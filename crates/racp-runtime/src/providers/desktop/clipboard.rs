//! Session-local, bounded Unicode clipboard with a mandatory serial fence.
use super::native::NativeDesktop;
use racp_contract::{digest, RacpError};
use serde_json::{json, Value};
use windows_sys::Win32::{
    Foundation::*,
    System::{DataExchange::*, Memory::*},
    UI::WindowsAndMessaging::*,
};
struct Owner(HWND);
impl Drop for Owner {
    fn drop(&mut self) {
        unsafe {
            DestroyWindow(self.0);
        }
    }
}
struct Open;
impl Drop for Open {
    fn drop(&mut self) {
        unsafe {
            CloseClipboard();
        }
    }
}
struct Memory(HGLOBAL);
impl Drop for Memory {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                GlobalFree(self.0);
            }
        }
    }
}
fn serial() -> u32 {
    unsafe { GetClipboardSequenceNumber() }
}
fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(Some(0)).collect()
}
impl NativeDesktop {
    pub fn clipboard(&self, operation: &str, p: &Value) -> Result<Value, RacpError> {
        self.availability()?;
        let window = unsafe {
            CreateWindowExW(
                0,
                wide("STATIC").as_ptr(),
                wide("RACP clipboard owner").as_ptr(),
                0,
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        };
        if window.is_null() {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let owner = Owner(window);
        if unsafe { OpenClipboard(owner.0) } == 0 {
            return Err(RacpError::new("RESOURCE_BUSY"));
        }
        let opened = Open;
        if operation == "clipboard.state" {
            return Ok(json!({"session_id":p["session_id"],"sequence_number":serial()}));
        }
        if operation == "clipboard.read" {
            let max = p["max_bytes"].as_u64().unwrap_or(4096) as usize;
            if unsafe { IsClipboardFormatAvailable(13) } == 0 {
                return Ok(
                    json!({"session_id":p["session_id"],"sequence_number":serial(),"text":null,"returned_bytes":0,"truncated":false,"format":"CF_UNICODETEXT"}),
                );
            }
            let handle = unsafe { GetClipboardData(13) };
            if handle.is_null() {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            let size = unsafe { GlobalSize(handle) };
            let pointer = unsafe { GlobalLock(handle) };
            if pointer.is_null() || size % 2 != 0 {
                if !pointer.is_null() {
                    unsafe {
                        GlobalUnlock(handle);
                    }
                }
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            let cap = (size / 2).min(max + 1);
            let mut words =
                unsafe { std::slice::from_raw_parts(pointer.cast::<u16>(), cap) }.to_vec();
            unsafe {
                GlobalUnlock(handle);
            }
            let end = words.iter().position(|w| *w == 0);
            let capped = end.is_none() && cap < size / 2;
            if end.is_none() && !capped {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            if let Some(end) = end {
                words.truncate(end);
            } else if words.last().is_some_and(|w| (0xd800..=0xdbff).contains(w)) {
                words.pop();
            }
            let full =
                String::from_utf16(&words).map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
            let mut end = full.len().min(max);
            while !full.is_char_boundary(end) {
                end -= 1;
            }
            let text = &full[..end];
            self.availability()?;
            return Ok(
                json!({"session_id":p["session_id"],"sequence_number":serial(),"text":text,"returned_bytes":end,"storage_bytes":size,"truncated":capped||full.len()>max,"returned_sha256":digest(text),"format":"CF_UNICODETEXT"}),
            );
        }
        if operation != "clipboard.write" {
            return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
        }
        let text = p["text"].as_str();
        if text.is_none() != (p["clear"] == true)
            || text.is_some_and(|t| t.contains('\0') || t.len() > 8192)
        {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        let expected = p["expected_sequence"]
            .as_u64()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        if expected > u32::MAX as u64 || serial() != expected as u32 {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let mut memory = Memory(std::ptr::null_mut());
        if let Some(text) = text {
            let data = wide(text);
            memory.0 = unsafe { GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, data.len() * 2) };
            if memory.0.is_null() {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let pointer = unsafe { GlobalLock(memory.0) };
            if pointer.is_null() {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            unsafe {
                std::ptr::copy_nonoverlapping(data.as_ptr(), pointer.cast::<u16>(), data.len());
                GlobalUnlock(memory.0);
            }
        }
        self.availability()?;
        if self.cancelled.as_ref().is_some_and(|p| p.exists()) {
            return Err(RacpError::new("CANCELLED"));
        }
        if serial() != expected as u32 {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        if unsafe { EmptyClipboard() } == 0 {
            return Err(RacpError::new("EXECUTION_FAILED"));
        }
        if !memory.0.is_null() {
            if unsafe { SetClipboardData(13, memory.0) }.is_null() {
                return Err(RacpError::new("EXECUTION_UNKNOWN"));
            }
            memory.0 = std::ptr::null_mut();
        }
        drop(opened);
        Ok(
            json!({"session_id":p["session_id"],"sequence_number":serial(),"previous_sequence":expected,"written_bytes":text.unwrap_or("").len(),"written_sha256":digest(text.unwrap_or("")),"cleared":text.is_none(),"read_before_next_write":true}),
        )
    }
}
