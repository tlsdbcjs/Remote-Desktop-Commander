//! Physical-pixel, read-only inspection of this Broker's interactive desktop.
use super::{native_identity::PinnedPeer, PeerIdentity, Snapshot};
use racp_contract::{digest, new_id, RacpError};
use serde_json::{json, Value};
use std::{collections::BTreeMap, mem::size_of};
use windows_sys::Win32::{
    Foundation::*,
    Graphics::Gdi::*,
    Security::*,
    System::{RemoteDesktop::*, StationsAndDesktops::*},
    UI::{HiDpi::*, WindowsAndMessaging::*},
};

pub(super) struct NativeDesktop {
    identity: PeerIdentity,
    salt: String,
    pub watch: super::hooks::NativeWatch,
    pub(super) marker: usize,
    guardian: PinnedPeer,
}
fn text(raw: &[u16]) -> String {
    String::from_utf16_lossy(&raw[..raw.iter().position(|v| *v == 0).unwrap_or(raw.len())])
}
fn object_name(handle: HANDLE) -> Result<String, RacpError> {
    let mut name = [0u16; 256];
    let mut length = 0;
    if handle.is_null()
        || unsafe {
            GetUserObjectInformationW(
                handle,
                UOI_NAME,
                name.as_mut_ptr().cast(),
                size_of_val(&name) as u32,
                &mut length,
            )
        } == 0
        || length as usize > size_of_val(&name)
    {
        return Err(RacpError::new("SESSION_UNAVAILABLE"));
    }
    Ok(text(&name))
}
use std::mem::size_of_val;
impl NativeDesktop {
    pub fn new(session: u32, marker: usize, guardian: PinnedPeer) -> Result<Self, RacpError> {
        let identity = PinnedPeer::open(std::process::id())?.identity().clone();
        if session == 0 || identity.session != session {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        if unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) }
            .is_null()
        {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        Ok(Self {
            identity,
            salt: new_id("desktop"),
            watch: super::hooks::NativeWatch::new(marker, false)?,
            marker,
            guardian,
        })
    }
    pub fn availability(&self) -> Result<(), RacpError> {
        self.guardian.alive()?;
        self.watch.counters()?;
        if object_name(unsafe { GetProcessWindowStation() })? != "WinSta0" {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        let mut buffer = std::ptr::null_mut();
        let mut length = 0;
        if unsafe {
            WTSQuerySessionInformationW(
                std::ptr::null_mut(),
                self.identity.session,
                WTSSessionInfoEx,
                &mut buffer,
                &mut length,
            )
        } == 0
        {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        let result = (|| {
            if buffer.is_null() || (length as usize) < size_of::<WTSINFOEXW>() {
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            let info = unsafe { std::ptr::read_unaligned(buffer.cast::<WTSINFOEXW>()) };
            let level = unsafe { info.Data.WTSInfoExLevel1 };
            if info.Level != 1
                || level.SessionId != self.identity.session
                || level.SessionState != WTSActive
            {
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            if level.SessionFlags as u32 == WTS_SESSIONSTATE_LOCK {
                return Err(RacpError::new("SESSION_LOCKED"));
            }
            if level.SessionFlags as u32 != WTS_SESSIONSTATE_UNLOCK || level.UserName[0] == 0 {
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            // Resolve the current logon's SID rather than treating a reused session ID as authority.
            let account = format!("{}\\{}", text(&level.DomainName), text(&level.UserName));
            let account: Vec<u16> = account.encode_utf16().chain(Some(0)).collect();
            let mut sid_size = 0;
            let mut domain_size = 0;
            let mut kind = 0;
            unsafe {
                LookupAccountNameW(
                    std::ptr::null(),
                    account.as_ptr(),
                    std::ptr::null_mut(),
                    &mut sid_size,
                    std::ptr::null_mut(),
                    &mut domain_size,
                    &mut kind,
                );
            }
            if !(8..=128).contains(&sid_size) || domain_size > 1024 {
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            let mut sid = vec![0usize; (sid_size as usize).div_ceil(size_of::<usize>())];
            let mut domain = vec![0u16; domain_size as usize];
            if unsafe {
                LookupAccountNameW(
                    std::ptr::null(),
                    account.as_ptr(),
                    sid.as_mut_ptr().cast(),
                    &mut sid_size,
                    domain.as_mut_ptr(),
                    &mut domain_size,
                    &mut kind,
                )
            } == 0
            {
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            let sid_bytes =
                unsafe { std::slice::from_raw_parts(sid.as_ptr().cast::<u8>(), sid_size as usize) };
            let count = sid_bytes[1] as usize;
            if sid_bytes[0] != 1 || count > 15 || 8 + count * 4 > sid_bytes.len() {
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            let authority = sid_bytes[2..8]
                .iter()
                .fold(0u64, |v, b| v * 256 + *b as u64);
            let mut current = format!("S-1-{authority}");
            for part in sid_bytes[8..8 + count * 4].chunks_exact(4) {
                current.push_str(&format!(
                    "-{}",
                    u32::from_le_bytes(part.try_into().unwrap())
                ));
            }
            if current != self.identity.sid {
                return Err(RacpError::new("SESSION_UNAVAILABLE"));
            }
            Ok(())
        })();
        unsafe {
            WTSFreeMemory(buffer.cast());
        }
        result?;
        let desktop = unsafe { OpenInputDesktop(0, 0, DESKTOP_READOBJECTS) };
        if desktop.is_null() {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        let name = object_name(desktop);
        unsafe {
            CloseDesktop(desktop);
        }
        if name? != "Default"
            || object_name(unsafe {
                GetThreadDesktop(windows_sys::Win32::System::Threading::GetCurrentThreadId())
            })? != "Default"
        {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        Ok(())
    }
    pub fn status(&self) -> Value {
        let code = self.availability().err().map(|e| e.code.0);
        json!({"session_id":self.identity.session,"broker_pid":self.identity.pid,"broker_created":self.identity.created,"user_sid":self.identity.sid,"user_name":whoami::username(),"integrity":self.identity.integrity,"available":code.is_none(),"error_code":code,"input_guardian_available":true,"input_hook_healthy":self.watch.counters().is_ok(),"ui_automation_available":false,"user_input_detection_complete":self.watch.counters().is_ok()})
    }
    pub fn layout(&self) -> Result<Value, RacpError> {
        self.availability()?;
        struct Collect {
            monitors: Vec<Value>,
            failed: bool,
        }
        unsafe extern "system" fn collect(
            monitor: HMONITOR,
            _: HDC,
            _: *mut RECT,
            ctx: LPARAM,
        ) -> i32 {
            let state = &mut *(ctx as *mut Collect);
            if state.monitors.len() >= 32 {
                state.failed = true;
                return 0;
            }
            let mut info = MONITORINFOEXW::default();
            info.monitorInfo.cbSize = size_of::<MONITORINFOEXW>() as u32;
            let mut x = 0;
            let mut y = 0;
            if GetMonitorInfoW(monitor, &mut info.monitorInfo) == 0
                || GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut x, &mut y) < 0
                || x == 0
                || y == 0
            {
                state.failed = true;
                return 0;
            }
            let r = info.monitorInfo.rcMonitor;
            state.monitors.push(json!({"device":text(&info.szDevice),"origin":{"x":r.left,"y":r.top},"width":r.right-r.left,"height":r.bottom-r.top,"scale_x":x as f64/96.0,"scale_y":y as f64/96.0}));
            1
        }
        let mut state = Collect {
            monitors: vec![],
            failed: false,
        };
        if unsafe {
            EnumDisplayMonitors(
                std::ptr::null_mut(),
                std::ptr::null(),
                Some(collect),
                &mut state as *mut _ as isize,
            )
        } == 0
            || state.failed
            || state.monitors.is_empty()
        {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        state
            .monitors
            .sort_by(|a, b| a["device"].as_str().cmp(&b["device"].as_str()));
        let revision = digest(format!(
            "{}:{}",
            self.salt,
            serde_json::to_string(&state.monitors)?
        ));
        for (index, m) in state.monitors.iter_mut().enumerate() {
            m["monitor_id"] = json!(format!("monitor_{}_{index}", &revision[..16]));
        }
        Ok(
            json!({"session_id":self.identity.session,"layout_revision":revision,"monitors":state.monitors,"virtual_origin":{"x":unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) },"y":unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) }},"width":unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) },"height":unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) }}),
        )
    }
    pub(super) fn window(&self, window: HWND) -> Result<Value, RacpError> {
        if unsafe { IsWindow(window) } == 0 || unsafe { IsWindowVisible(window) } == 0 {
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        let mut pid = 0;
        unsafe {
            GetWindowThreadProcessId(window, &mut pid);
        }
        let peer = PinnedPeer::open(pid)?;
        if peer.identity().session != self.identity.session
            || peer.identity().integrity > self.identity.integrity
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let mut bounds = RECT::default();
        if unsafe { GetWindowRect(window, &mut bounds) } == 0 {
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        let mut title = [0u16; 257];
        let mut class = [0u16; 65];
        unsafe {
            GetWindowTextW(window, title.as_mut_ptr(), title.len() as i32);
            GetClassNameW(window, class.as_mut_ptr(), class.len() as i32);
        }
        peer.alive()?;
        let id = digest(format!(
            "{}:{}:{pid}:{}:{}",
            self.salt,
            window as usize,
            peer.identity().created,
            self.watch.generation(window as usize)
        ));
        Ok(
            json!({"window_id":format!("window_{}",&id[..32]),"pid":pid,"create_time":peer.identity().created,"bounds":[bounds.left,bounds.top,bounds.right,bounds.bottom],"integrity":peer.identity().integrity,"title":text(&title),"class_name":text(&class),"dpi":unsafe { GetDpiForWindow(window) }}),
        )
    }
    pub fn snapshot(&self) -> Result<(Snapshot, Value), RacpError> {
        let layout = self.layout()?;
        struct Collect<'a> {
            desktop: &'a NativeDesktop,
            values: BTreeMap<String, Value>,
        }
        unsafe extern "system" fn collect(window: HWND, context: LPARAM) -> i32 {
            let state = &mut *(context as *mut Collect<'_>);
            if state.values.len() >= 128 {
                return 0;
            }
            if let Ok(value) = state.desktop.window(window) {
                state
                    .values
                    .insert(value["window_id"].as_str().unwrap().into(), value);
            }
            1
        }
        let mut state = Collect {
            desktop: self,
            values: BTreeMap::new(),
        };
        unsafe {
            EnumWindows(Some(collect), &mut state as *mut _ as isize);
        }
        let foreground = self
            .window(unsafe { GetForegroundWindow() })
            .ok()
            .and_then(|v| v["window_id"].as_str().map(str::to_owned));
        self.availability()?;
        Ok((
            Snapshot {
                available: true,
                unavailable_code: "SESSION_UNAVAILABLE",
                foreground,
                input_tick: self.watch.counters()?.0,
                foreground_tick: self.watch.counters()?.1,
                layout_revision: layout["layout_revision"].as_str().unwrap().into(),
                windows: state.values,
            },
            layout,
        ))
    }
    pub fn capture(
        &self,
        payload: &Value,
        snapshot: &Snapshot,
        layout: &Value,
    ) -> Result<(Vec<u8>, Option<Vec<u8>>, Value), RacpError> {
        self.availability()?;
        let mut left = layout["virtual_origin"]["x"].as_i64().unwrap_or(0) as i32;
        let mut top = layout["virtual_origin"]["y"].as_i64().unwrap_or(0) as i32;
        let mut width = layout["width"].as_i64().unwrap_or(0) as i32;
        let mut height = layout["height"].as_i64().unwrap_or(0) as i32;
        if let Some(id) = payload["window_id"].as_str() {
            let window = snapshot
                .windows
                .get(id)
                .ok_or_else(|| RacpError::new("STALE_OBSERVATION"))?;
            let bounds = &window["bounds"];
            left = bounds[0].as_i64().unwrap_or(0) as i32;
            top = bounds[1].as_i64().unwrap_or(0) as i32;
            width = bounds[2].as_i64().unwrap_or(0) as i32 - left;
            height = bounds[3].as_i64().unwrap_or(0) as i32 - top;
        } else if let Some(id) = payload["monitor_id"].as_str() {
            let monitor = layout["monitors"]
                .as_array()
                .and_then(|ms| ms.iter().find(|m| m["monitor_id"] == id))
                .ok_or_else(|| RacpError::new("STALE_OBSERVATION"))?;
            left = monitor["origin"]["x"].as_i64().unwrap_or(0) as i32;
            top = monitor["origin"]["y"].as_i64().unwrap_or(0) as i32;
            width = monitor["width"].as_i64().unwrap_or(0) as i32;
            height = monitor["height"].as_i64().unwrap_or(0) as i32;
        }
        if width <= 0
            || height <= 0
            || width > 16384
            || height > 16384
            || (width as u64 * height as u64) > 16 * 1024 * 1024
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        struct Surface {
            screen: HDC,
            memory: HDC,
            bitmap: HBITMAP,
            previous: HGDIOBJ,
        }
        impl Drop for Surface {
            fn drop(&mut self) {
                unsafe {
                    if !self.previous.is_null() && self.previous as isize != -1 {
                        SelectObject(self.memory, self.previous);
                    }
                    if !self.bitmap.is_null() {
                        DeleteObject(self.bitmap);
                    }
                    if !self.memory.is_null() {
                        DeleteDC(self.memory);
                    }
                    if !self.screen.is_null() {
                        ReleaseDC(std::ptr::null_mut(), self.screen);
                    }
                }
            }
        }
        let mut surface = Surface {
            screen: unsafe { GetDC(std::ptr::null_mut()) },
            memory: std::ptr::null_mut(),
            bitmap: std::ptr::null_mut(),
            previous: std::ptr::null_mut(),
        };
        if surface.screen.is_null() {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        surface.memory = unsafe { CreateCompatibleDC(surface.screen) };
        if surface.memory.is_null() {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let mut info = BITMAPINFO::default();
        info.bmiHeader = BITMAPINFOHEADER {
            biSize: size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width,
            biHeight: -height,
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB,
            ..Default::default()
        };
        let mut pixels = std::ptr::null_mut();
        surface.bitmap = unsafe {
            CreateDIBSection(
                surface.screen,
                &info,
                DIB_RGB_COLORS,
                &mut pixels,
                std::ptr::null_mut(),
                0,
            )
        };
        if surface.bitmap.is_null() || pixels.is_null() {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        surface.previous = unsafe { SelectObject(surface.memory, surface.bitmap) };
        if surface.previous.is_null() || surface.previous as isize == -1 {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        if unsafe {
            BitBlt(
                surface.memory,
                0,
                0,
                width,
                height,
                surface.screen,
                left,
                top,
                SRCCOPY | CAPTUREBLT,
            )
        } == 0
            || unsafe { GdiFlush() } == 0
        {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        let bytes = unsafe {
            std::slice::from_raw_parts(pixels.cast::<u8>(), width as usize * height as usize * 4)
        };
        let mut rgb = Vec::with_capacity(width as usize * height as usize * 3);
        for pixel in bytes.chunks_exact(4) {
            rgb.extend_from_slice(&[pixel[2], pixel[1], pixel[0]]);
        }
        drop(surface);
        let png = encode_png(width as u32, height as u32, &rgb, 32 * 1024 * 1024)?;
        let mut preview_bytes = None;
        let mut result = json!({"width":width,"height":height,"crop_origin":{"x":left,"y":top},"format":"png","window_id":payload["window_id"],"monitor_id":payload["monitor_id"],"capture_scope":"visible_rectangle"});
        if payload["preview"].as_bool().unwrap_or(true) {
            let scale = (800.0 / width as f64).min(600.0 / height as f64).min(1.0);
            let pw = (width as f64 * scale).floor().max(1.0) as usize;
            let ph = (height as f64 * scale).floor().max(1.0) as usize;
            let mut preview = vec![0u8; pw * ph * 3];
            for y in 0..ph {
                for x in 0..pw {
                    let from =
                        ((y * height as usize / ph) * width as usize + x * width as usize / pw) * 3;
                    let to = (y * pw + x) * 3;
                    preview[to..to + 3].copy_from_slice(&rgb[from..from + 3]);
                }
            }
            preview_bytes = Some(encode_png(pw as u32, ph as u32, &preview, 2 * 1024 * 1024)?);
            result["preview"] = json!({"width":pw,"height":ph,"physical_pixels_per_preview_pixel_x":width as f64/pw as f64,"physical_pixels_per_preview_pixel_y":height as f64/ph as f64});
        }
        let (after, _) = self.snapshot()?;
        if after.layout_revision != snapshot.layout_revision
            || payload["window_id"]
                .as_str()
                .is_some_and(|id| after.windows.get(id) != snapshot.windows.get(id))
        {
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        Ok((png, preview_bytes, result))
    }
}
fn encode_png(width: u32, height: u32, rgb: &[u8], limit: usize) -> Result<Vec<u8>, RacpError> {
    struct Bounded {
        bytes: Vec<u8>,
        limit: usize,
    }
    impl std::io::Write for Bounded {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            if bytes.len() > self.limit.saturating_sub(self.bytes.len()) {
                return Err(std::io::Error::other("PNG size limit"));
            }
            self.bytes.extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut output = Bounded {
        bytes: vec![],
        limit,
    };
    {
        let mut encoder = png::Encoder::new(&mut output, width, height);
        encoder.set_color(png::ColorType::Rgb);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder
            .write_header()
            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
        writer
            .write_image_data(rgb)
            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
        writer
            .finish()
            .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
    }
    Ok(output.bytes)
}
