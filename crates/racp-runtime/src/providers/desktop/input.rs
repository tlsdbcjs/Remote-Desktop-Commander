use super::{native::NativeDesktop, Snapshot};
use racp_contract::RacpError;
use serde_json::{json, Value};
use std::time::{Duration, Instant};
use windows_sys::Win32::{
    Foundation::*,
    UI::{Input::KeyboardAndMouse::*, WindowsAndMessaging::*},
};
impl NativeDesktop {
    pub fn window_handle(&self, id: &str) -> Result<HWND, RacpError> {
        struct Find<'a> {
            desktop: &'a NativeDesktop,
            id: &'a str,
            handle: HWND,
        }
        unsafe extern "system" fn find(h: HWND, l: LPARAM) -> i32 {
            let s = &mut *(l as *mut Find<'_>);
            if s.desktop.window(h).is_ok_and(|v| v["window_id"] == s.id) {
                s.handle = h;
                return 0;
            }
            1
        }
        let mut state = Find {
            desktop: self,
            id,
            handle: std::ptr::null_mut(),
        };
        unsafe {
            EnumWindows(Some(find), &mut state as *mut _ as isize);
        }
        if state.handle.is_null() {
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        Ok(state.handle)
    }
    pub fn release_inputs(&self) -> Result<(), RacpError> {
        super::hooks::release(&self.watch.state)?;
        let until = Instant::now() + Duration::from_millis(200);
        while !self.watch.released() {
            if Instant::now() >= until {
                return Err(RacpError::new("CLEANUP_FAILED"));
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        Ok(())
    }
    fn send(
        &self,
        inputs: &[INPUT],
        snapshot: &Snapshot,
        deadline: Instant,
    ) -> Result<(), RacpError> {
        self.availability()?;
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        let (i, f) = self.watch.counters()?;
        if i != snapshot.input_tick || f != snapshot.foreground_tick {
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        if inputs.is_empty() || inputs.len() > 256 {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        if unsafe {
            SendInput(
                inputs.len() as u32,
                inputs.as_ptr(),
                std::mem::size_of::<INPUT>() as i32,
            )
        } != inputs.len() as u32
        {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
        Ok(())
    }
    pub fn dispatch_input(
        &self,
        operation: &str,
        p: &Value,
        snapshot: &Snapshot,
        deadline: Instant,
    ) -> Result<Value, RacpError> {
        let window = self.window_handle(p["expected_window_id"].as_str().unwrap_or(""))?;
        self.availability()?;
        if Instant::now() >= deadline {
            return Err(RacpError::new("TIMEOUT"));
        }
        if operation == "desktop.activate" {
            if self.watch.counters()?.0 != snapshot.input_tick
                || unsafe { SetForegroundWindow(window) } == 0
            {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            std::thread::sleep(Duration::from_millis(30));
            if unsafe { GetForegroundWindow() } != window {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            return Ok(json!({"dispatched":true,"application_verified":false}));
        }
        if unsafe { GetForegroundWindow() } != window {
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        let mouse = |flags, data, x, y| INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: x,
                    dy: y,
                    mouseData: data,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: self.marker,
                },
            },
        };
        let key = |vk, scan, flags| INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: vk,
                    wScan: scan,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: self.marker,
                },
            },
        };
        let position = |x: i64, y: i64| -> Result<INPUT, RacpError> {
            let l = unsafe { GetSystemMetrics(SM_XVIRTUALSCREEN) } as i64;
            let t = unsafe { GetSystemMetrics(SM_YVIRTUALSCREEN) } as i64;
            let w = unsafe { GetSystemMetrics(SM_CXVIRTUALSCREEN) } as i64;
            let h = unsafe { GetSystemMetrics(SM_CYVIRTUALSCREEN) } as i64;
            let bounds = &snapshot.windows[p["expected_window_id"].as_str().unwrap()]["bounds"];
            if w <= 1
                || h <= 1
                || x < l
                || x >= l + w
                || y < t
                || y >= t + h
                || x < bounds[0].as_i64().unwrap_or(i64::MAX)
                || x >= bounds[2].as_i64().unwrap_or(i64::MIN)
                || y < bounds[1].as_i64().unwrap_or(i64::MAX)
                || y >= bounds[3].as_i64().unwrap_or(i64::MIN)
            {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            Ok(mouse(
                MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                0,
                ((x - l) * 65535 / (w - 1)) as i32,
                ((y - t) * 65535 / (h - 1)) as i32,
            ))
        };
        let x = p["x"].as_i64().unwrap_or(0);
        let y = p["y"].as_i64().unwrap_or(0);
        let (down, up) = match p["button"].as_str().unwrap_or("left") {
            "right" => (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            "middle" => (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
            _ => (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        };
        let result = (|| {
            match operation {
                "desktop.move" => self.send(&[position(x, y)?], snapshot, deadline)?,
                "desktop.click" => {
                    let mut inputs = vec![position(x, y)?];
                    for _ in 0..p["click_count"].as_u64().unwrap_or(1) {
                        inputs.extend([mouse(down, 0, 0, 0), mouse(up, 0, 0, 0)]);
                    }
                    self.send(&inputs, snapshot, deadline)?;
                }
                "desktop.scroll" => self.send(
                    &[
                        position(x, y)?,
                        mouse(
                            MOUSEEVENTF_WHEEL,
                            p["delta"].as_i64().unwrap_or(0) as u32,
                            0,
                            0,
                        ),
                    ],
                    snapshot,
                    deadline,
                )?,
                "desktop.type" => {
                    let units: Vec<_> = p["text"].as_str().unwrap_or("").encode_utf16().collect();
                    for chunk in units.chunks(128) {
                        let mut inputs = vec![];
                        for &unit in chunk {
                            inputs.extend([
                                key(0, unit, KEYEVENTF_UNICODE),
                                key(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                            ]);
                        }
                        self.send(&inputs, snapshot, deadline)?;
                    }
                }
                "desktop.key" => {
                    let mut keys = vec![];
                    for name in p["keys"]
                        .as_array()
                        .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?
                    {
                        keys.push(virtual_key(name.as_str().unwrap_or(""))?);
                    }
                    let mut inputs = vec![];
                    for &vk in &keys {
                        inputs.push(key(vk, 0, 0));
                    }
                    for &vk in keys.iter().rev() {
                        inputs.push(key(vk, 0, KEYEVENTF_KEYUP));
                    }
                    self.send(&inputs, snapshot, deadline)?;
                }
                "desktop.drag" => {
                    self.send(&[position(x, y)?, mouse(down, 0, 0, 0)], snapshot, deadline)?;
                    let duration = p["duration_ms"].as_u64().unwrap_or(300);
                    let start = Instant::now();
                    let ex = p["end_x"].as_i64().unwrap_or(0);
                    let ey = p["end_y"].as_i64().unwrap_or(0);
                    position(ex, ey)?;
                    loop {
                        let elapsed = start.elapsed().as_millis() as u64;
                        let ratio = elapsed.min(duration) as f64 / duration.max(1) as f64;
                        self.send(
                            &[position(
                                x + ((ex - x) as f64 * ratio) as i64,
                                y + ((ey - y) as f64 * ratio) as i64,
                            )?],
                            snapshot,
                            deadline,
                        )?;
                        if elapsed >= duration {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    self.send(&[mouse(up, 0, 0, 0)], snapshot, deadline)?;
                }
                _ => return Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
            }
            Ok(json!({"dispatched":true,"application_verified":false}))
        })();
        let cleanup = self.release_inputs();
        if let Err(e) = cleanup {
            return Err(e);
        }
        result
    }
}
fn virtual_key(text: &str) -> Result<u16, RacpError> {
    let name = text.to_ascii_uppercase();
    let key = match name.as_str() {
        "CTRL" | "CONTROL" => VK_CONTROL,
        "ALT" => VK_MENU,
        "SHIFT" => VK_SHIFT,
        "WIN" | "META" => VK_LWIN,
        "ENTER" | "RETURN" => VK_RETURN,
        "TAB" => VK_TAB,
        "ESC" | "ESCAPE" => VK_ESCAPE,
        "SPACE" => VK_SPACE,
        "BACKSPACE" => VK_BACK,
        "DELETE" => VK_DELETE,
        "INSERT" => VK_INSERT,
        "HOME" => VK_HOME,
        "END" => VK_END,
        "PAGEUP" => VK_PRIOR,
        "PAGEDOWN" => VK_NEXT,
        "LEFT" => VK_LEFT,
        "RIGHT" => VK_RIGHT,
        "UP" => VK_UP,
        "DOWN" => VK_DOWN,
        _ => {
            if name.len() == 1 && name.as_bytes()[0].is_ascii_alphanumeric() {
                name.as_bytes()[0] as u16
            } else if let Some(f) = name
                .strip_prefix('F')
                .and_then(|s| s.parse::<u16>().ok())
                .filter(|f| (1..=24).contains(f))
            {
                VK_F1 + f - 1
            } else {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
        }
    };
    Ok(key)
}
