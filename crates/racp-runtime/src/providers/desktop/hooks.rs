//! Native hooks keep only counters and the exact paired injected-down ledger.
use super::{HeldInputs, InterruptionCounter, ReleaseInput};
use racp_contract::RacpError;
use std::{
    cell::RefCell,
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*,
    System::Threading::*,
    UI::{Accessibility::*, Input::KeyboardAndMouse::*, WindowsAndMessaging::*},
};

pub struct HookState {
    pub counter: InterruptionCounter,
    pub held: HeldInputs,
    pub healthy: bool,
    pub release_requested: bool,
    pub interrupted: bool,
    baseline: Option<(u64, u64)>,
    marker: usize,
}
thread_local! { static ACTIVE: RefCell<Option<Arc<Mutex<HookState>>>> = const { RefCell::new(None) }; }
unsafe extern "system" fn keyboard(code: i32, w: WPARAM, l: LPARAM) -> LRESULT {
    if code >= 0 {
        let input = &*(l as *const KBDLLHOOKSTRUCT);
        ACTIVE.with(|slot| {
            if let Some(state) = slot.borrow().as_ref() {
                if let Ok(mut s) = state.lock() {
                    let own = input.flags & LLKHF_INJECTED != 0 && input.dwExtraInfo == s.marker;
                    if own && input.flags & LLKHF_UP == 0 && s.held.snapshot().is_empty() {
                        s.baseline = Some((s.counter.foreign(), s.counter.foreground()));
                    }
                    s.counter
                        .input(input.flags, input.dwExtraInfo, LLKHF_INJECTED);
                    if own {
                        s.held
                            .keyboard(input.vkCode, input.scanCode, input.flags, Instant::now());
                    }
                }
            }
        });
    }
    CallNextHookEx(std::ptr::null_mut(), code, w, l)
}
unsafe extern "system" fn mouse(code: i32, w: WPARAM, l: LPARAM) -> LRESULT {
    if code >= 0 {
        let input = &*(l as *const MSLLHOOKSTRUCT);
        ACTIVE.with(|slot| {
            if let Some(state) = slot.borrow().as_ref() {
                if let Ok(mut s) = state.lock() {
                    let own = input.flags & LLMHF_INJECTED != 0 && input.dwExtraInfo == s.marker;
                    if own
                        && matches!(w as u32, WM_LBUTTONDOWN | WM_RBUTTONDOWN | WM_MBUTTONDOWN)
                        && s.held.snapshot().is_empty()
                    {
                        s.baseline = Some((s.counter.foreign(), s.counter.foreground()));
                    }
                    s.counter
                        .input(input.flags, input.dwExtraInfo, LLMHF_INJECTED);
                    if own {
                        s.held.mouse(w as u32, Instant::now());
                    }
                }
            }
        });
    }
    CallNextHookEx(std::ptr::null_mut(), code, w, l)
}
unsafe extern "system" fn event(_: HWINEVENTHOOK, e: u32, h: HWND, o: i32, c: i32, _: u32, _: u32) {
    ACTIVE.with(|slot| {
        if let Some(state) = slot.borrow().as_ref() {
            if let Ok(mut s) = state.lock() {
                s.counter.window_event(e, h as usize, o, c);
            }
        }
    });
}
pub fn release_input(input: ReleaseInput, marker: usize) -> INPUT {
    match input {
        ReleaseInput::Key { code, scan, flags } => INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: code,
                    wScan: scan,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: marker,
                },
            },
        },
        ReleaseInput::Mouse { flags } => INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: 0,
                    dy: 0,
                    mouseData: 0,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: marker,
                },
            },
        },
    }
}
/// Never holds the callback mutex across SendInput: hooks must be free to account receipts.
pub fn release(state: &Arc<Mutex<HookState>>) -> Result<(), RacpError> {
    let (values, marker) = {
        let s = state
            .lock()
            .map_err(|_| RacpError::new("SESSION_UNAVAILABLE"))?;
        (s.held.snapshot(), s.marker)
    };
    for value in values {
        let input = release_input(value, marker);
        if unsafe { SendInput(1, &input, std::mem::size_of::<INPUT>() as i32) } != 1 {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
    }
    Ok(())
}
pub struct NativeWatch {
    pub state: Arc<Mutex<HookState>>,
    thread_id: u32,
    join: Option<thread::JoinHandle<()>>,
}
impl NativeWatch {
    pub fn new(marker: usize, guardian: bool) -> Result<Self, RacpError> {
        let state = Arc::new(Mutex::new(HookState {
            counter: InterruptionCounter::new(marker),
            held: HeldInputs::default(),
            healthy: false,
            release_requested: false,
            interrupted: false,
            baseline: None,
            marker,
        }));
        let shared = state.clone();
        let (send, recv) = std::sync::mpsc::sync_channel(1);
        let join = thread::spawn(move || unsafe {
            ACTIVE.with(|a| *a.borrow_mut() = Some(shared.clone()));
            let keyboard =
                SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard), std::ptr::null_mut(), 0);
            let mouse = SetWindowsHookExW(WH_MOUSE_LL, Some(mouse), std::ptr::null_mut(), 0);
            let foreground = SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND,
                EVENT_SYSTEM_FOREGROUND,
                std::ptr::null_mut(),
                Some(event),
                0,
                0,
                WINEVENT_OUTOFCONTEXT,
            );
            let windows = SetWinEventHook(
                EVENT_OBJECT_CREATE,
                EVENT_OBJECT_DESTROY,
                std::ptr::null_mut(),
                Some(event),
                0,
                0,
                WINEVENT_OUTOFCONTEXT,
            );
            let mut message = MSG::default();
            PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_NOREMOVE);
            let healthy = !keyboard.is_null()
                && !mouse.is_null()
                && !foreground.is_null()
                && !windows.is_null();
            if let Ok(mut s) = shared.lock() {
                s.healthy = healthy;
            }
            let _ = send.send((GetCurrentThreadId(), healthy));
            if healthy {
                'pump: loop {
                    while PeekMessageW(&mut message, std::ptr::null_mut(), 0, 0, PM_REMOVE) != 0 {
                        if message.message == WM_QUIT {
                            break 'pump;
                        }
                        TranslateMessage(&message);
                        DispatchMessageW(&message);
                    }
                    let must_release = shared
                        .lock()
                        .map(|mut s| {
                            let interrupted = s.baseline.is_some_and(|(i, f)| {
                                i != s.counter.foreign() || f != s.counter.foreground()
                            });
                            if interrupted {
                                s.interrupted = true;
                            }
                            guardian
                                && (s.release_requested
                                    || interrupted
                                    || s.held.overdue(Instant::now(), Duration::from_secs(15)))
                        })
                        .unwrap_or(true);
                    if must_release {
                        let released = release(&shared).is_ok();
                        if !released {
                            if let Ok(mut s) = shared.lock() {
                                s.healthy = false;
                            }
                        } else if let Ok(mut s) = shared.lock() {
                            if s.held.snapshot().is_empty() && s.counter.healthy() { s.healthy = true; }
                        }
                    }
                    MsgWaitForMultipleObjectsEx(
                        0,
                        std::ptr::null(),
                        20,
                        QS_ALLINPUT,
                        MWMO_INPUTAVAILABLE,
                    );
                }
            }
            let _ = release(&shared);
            if !keyboard.is_null() {
                UnhookWindowsHookEx(keyboard);
            }
            if !mouse.is_null() {
                UnhookWindowsHookEx(mouse);
            }
            if !foreground.is_null() {
                UnhookWinEvent(foreground);
            }
            if !windows.is_null() {
                UnhookWinEvent(windows);
            }
            if let Ok(mut s) = shared.lock() {
                s.healthy = false;
            }
            ACTIVE.with(|a| *a.borrow_mut() = None);
        });
        let (thread_id, healthy) = recv
            .recv_timeout(Duration::from_secs(3))
            .map_err(|_| RacpError::new("SESSION_UNAVAILABLE"))?;
        if !healthy {
            let _ = join.join();
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        Ok(Self {
            state,
            thread_id,
            join: Some(join),
        })
    }
    pub fn counters(&self) -> Result<(u64, u64), RacpError> {
        let s = self
            .state
            .lock()
            .map_err(|_| RacpError::new("SESSION_UNAVAILABLE"))?;
        if !s.healthy || !s.counter.healthy() {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        Ok((s.counter.foreign(), s.counter.foreground()))
    }
    pub fn generation(&self, hwnd: usize) -> u64 {
        self.state
            .lock()
            .map(|mut s| s.counter.window_generation(hwnd))
            .unwrap_or(u64::MAX)
    }
    pub fn released(&self) -> bool {
        self.state
            .lock()
            .is_ok_and(|s| s.healthy && s.held.snapshot().is_empty())
    }
}
impl Drop for NativeWatch {
    fn drop(&mut self) {
        unsafe {
            PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0);
        }
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}
pub fn marker(secret: &str) -> usize {
    usize::from_str_radix(&racp_contract::digest(secret)[..16], 16).unwrap_or(1)
}
