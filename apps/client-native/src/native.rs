//! Native Windows tray and per-user/session/state-root instance identity. Activation only shows UI.
use std::{
    os::windows::io::{FromRawHandle, OwnedHandle},
    sync::Arc,
    time::Duration,
};
use tokio::sync::mpsc::{Receiver, Sender};
use windows_sys::Win32::{
    Foundation::*,
    Security::Authorization::ConvertSidToStringSidW,
    Security::*,
    System::{LibraryLoader::GetModuleHandleW, RemoteDesktop::ProcessIdToSessionId, Threading::*},
    UI::{Shell::*, WindowsAndMessaging::*},
};
const EVENT: u32 = WM_APP + 17;
const ACTIVATE: u32 = WM_APP + 18;
const STOP: u32 = WM_APP + 19;
const STATUS: u32 = WM_APP + 20;
const AUTHORITY: u32 = WM_APP + 21;
// Shell SDK macro: NIN_KEYSELECT = NIN_SELECT | NINF_KEY, NINF_KEY = 1.
const KEYSELECT: u32 = NIN_SELECT | 1;
pub fn startup_error(code: &str) {
    let mut session = 0;
    unsafe {
        if ProcessIdToSessionId(GetCurrentProcessId(), &mut session) == 0 || session == 0 {
            return;
        }
    }
    let message = wide(&format!("RACP Client를 시작하지 못했습니다.\n{code}"));
    let title = wide("RACP Client 시작 오류");
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            message.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}
#[derive(Clone, Copy, Debug)]
pub enum ShellEvent {
    Show,
    StopAgent,
    Quit,
    Failed,
}
pub struct Shell {
    pub receiver: Arc<tokio::sync::Mutex<Option<Receiver<ShellEvent>>>>,
    hwnd: isize,
    _instance: OwnedHandle,
}
impl Shell {
    pub fn new() -> Result<Option<Arc<Self>>, String> {
        let mut session = 0;
        unsafe {
            if ProcessIdToSessionId(GetCurrentProcessId(), &mut session) == 0 || session == 0 {
                return Err("GUI_SESSION_UNSUPPORTED".into());
            }
        }
        let root = crate::control_target::root().map_err(|e| e.to_string())?;
        let identity = format!(
            "{}:{session}:{}",
            sid()?,
            root.to_string_lossy().to_ascii_lowercase()
        );
        let hash = racp_contract::digest(identity);
        let class = wide(&format!("RACP.Native.Tray.{hash}"));
        let name = wide(&format!("Local\\RACP.Native.GUI.{hash}"));
        let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if handle.is_null() {
            return Err("GUI_INSTANCE_FAILED".into());
        }
        let exists = unsafe { GetLastError() } == ERROR_ALREADY_EXISTS;
        let instance = unsafe { OwnedHandle::from_raw_handle(handle) };
        if exists {
            let hwnd = unsafe { FindWindowW(class.as_ptr(), std::ptr::null()) };
            if !hwnd.is_null() {
                unsafe {
                    PostMessageW(hwnd, ACTIVATE, 0, 0);
                }
            }
            return Ok(None);
        }
        let (sender, receiver) = tokio::sync::mpsc::channel(32);
        let (ready, started) = std::sync::mpsc::sync_channel(1);
        std::thread::Builder::new()
            .name("racp-native-tray".into())
            .spawn(move || {
                let result = run(class, sender, ready);
                if result.is_err() {
                    eprintln!("native tray failed");
                }
            })
            .map_err(|_| "GUI_TRAY_FAILED".to_owned())?;
        let hwnd = started
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| "GUI_TRAY_FAILED".to_owned())??;
        Ok(Some(Arc::new(Self {
            receiver: Arc::new(tokio::sync::Mutex::new(Some(receiver))),
            hwnd,
            _instance: instance,
        })))
    }
    pub fn status(&self, status: u32) {
        unsafe {
            PostMessageW(self.hwnd as HWND, STATUS, status.min(2) as usize, 0);
        }
    }
    pub fn control(&self, allowed: bool) {
        unsafe {
            PostMessageW(self.hwnd as HWND, AUTHORITY, usize::from(allowed), 0);
        }
    }
}
impl Drop for Shell {
    fn drop(&mut self) {
        unsafe {
            PostMessageW(self.hwnd as HWND, STOP, 0, 0);
        }
    }
}
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}
fn sid() -> Result<String, String> {
    let mut raw = std::ptr::null_mut();
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut raw) } == 0 {
        return Err("GUI_IDENTITY_FAILED".into());
    }
    let _token = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut size = 0;
    unsafe {
        GetTokenInformation(raw, TokenUser, std::ptr::null_mut(), 0, &mut size);
    }
    if size < std::mem::size_of::<TOKEN_USER>() as u32 || size > 65536 {
        return Err("GUI_IDENTITY_FAILED".into());
    }
    let mut words = vec![0usize; (size as usize).div_ceil(std::mem::size_of::<usize>())];
    if unsafe { GetTokenInformation(raw, TokenUser, words.as_mut_ptr().cast(), size, &mut size) }
        == 0
    {
        return Err("GUI_IDENTITY_FAILED".into());
    }
    let user = unsafe { &*words.as_ptr().cast::<TOKEN_USER>() };
    let mut text = std::ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(user.User.Sid, &mut text) } == 0 {
        return Err("GUI_IDENTITY_FAILED".into());
    }
    let mut length = 0;
    while length < 184 && unsafe { *text.add(length) } != 0 {
        length += 1;
    }
    let value = if length >= 184 {
        Err("GUI_IDENTITY_FAILED".into())
    } else {
        String::from_utf16(unsafe { std::slice::from_raw_parts(text, length) })
            .map_err(|_| "GUI_IDENTITY_FAILED".to_owned())
    };
    unsafe {
        LocalFree(text.cast());
    }
    value
}
struct State {
    sender: Sender<ShellEvent>,
    icons: [HICON; 3],
    status: usize,
    taskbar: u32,
    control: bool,
    service: bool,
}
fn icon(bytes: &[u8]) -> Result<HICON, String> {
    if bytes.len() < 22 || bytes[..4] != [0, 0, 1, 0] {
        return Err("GUI_ICON_INVALID".into());
    }
    let length = u32::from_le_bytes(bytes[14..18].try_into().unwrap()) as usize;
    let offset = u32::from_le_bytes(bytes[18..22].try_into().unwrap()) as usize;
    let end = offset
        .checked_add(length)
        .filter(|end| *end <= bytes.len())
        .ok_or_else(|| "GUI_ICON_INVALID".to_owned())?;
    let icon = unsafe {
        CreateIconFromResourceEx(
            bytes[offset..end].as_ptr(),
            length as u32,
            1,
            0x00030000,
            32,
            32,
            LR_DEFAULTCOLOR,
        )
    };
    if icon.is_null() {
        Err("GUI_ICON_INVALID".into())
    } else {
        Ok(icon)
    }
}
fn notify(hwnd: HWND, state: &State, command: u32) -> bool {
    let mut data = NOTIFYICONDATAW {
        cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: 1,
        uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP,
        uCallbackMessage: EVENT,
        hIcon: state.icons[state.status],
        ..Default::default()
    };
    let tip = wide("RACP Native Client");
    data.szTip[..tip.len()].copy_from_slice(&tip);
    let ok = unsafe { Shell_NotifyIconW(command, &data) } != 0;
    if command == NIM_ADD && ok {
        data.Anonymous.uVersion = NOTIFYICON_VERSION_4;
        unsafe {
            Shell_NotifyIconW(NIM_SETVERSION, &data);
        }
    }
    ok
}
unsafe extern "system" fn procedure(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    if message == WM_NCCREATE {
        let create = unsafe { &*(lparam as *const CREATESTRUCTW) };
        unsafe {
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, create.lpCreateParams as isize);
        }
    }
    let ptr = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as *mut State;
    if !ptr.is_null() {
        let state = unsafe { &mut *ptr };
        if message == state.taskbar {
            if !notify(hwnd, state, NIM_ADD) {
                let _ = state.sender.try_send(ShellEvent::Failed);
            }
            return 0;
        }
        match message {
            ACTIVATE => {
                let _ = state.sender.try_send(ShellEvent::Show);
                return 0;
            }
            STOP => {
                unsafe {
                    EndMenu();
                    PostQuitMessage(0);
                }
                return 0;
            }
            STATUS => {
                state.status = wparam.min(2);
                if !notify(hwnd, state, NIM_MODIFY) {
                    let _ = state.sender.try_send(ShellEvent::Failed);
                }
                return 0;
            }
            AUTHORITY => {
                state.control = wparam != 0;
                return 0;
            }
            EVENT => {
                match lparam as u32 & 0xffff {
                    WM_LBUTTONUP | WM_LBUTTONDBLCLK | NIN_SELECT | KEYSELECT => {
                        let _ = state.sender.try_send(ShellEvent::Show);
                    }
                    WM_CONTEXTMENU | WM_RBUTTONUP => {
                        let menu = unsafe { CreatePopupMenu() };
                        if !menu.is_null() {
                            for (id, label) in [
                                (1, "RACP 열기"),
                                (2, "Agent 종료"),
                                (
                                    3,
                                    if state.service {
                                        "앱 종료"
                                    } else {
                                        "Agent 정리 후 앱 종료"
                                    },
                                ),
                            ] {
                                let label = wide(label);
                                unsafe {
                                    AppendMenuW(menu, MF_STRING, id, label.as_ptr());
                                }
                            }
                            if !state.control {
                                unsafe {
                                    EnableMenuItem(menu, 2, MF_BYCOMMAND | MF_GRAYED);
                                }
                            }
                            let mut point = POINT::default();
                            unsafe {
                                GetCursorPos(&mut point);
                                SetForegroundWindow(hwnd);
                            }
                            let selected = unsafe {
                                TrackPopupMenu(
                                    menu,
                                    TPM_RETURNCMD | TPM_NONOTIFY,
                                    point.x,
                                    point.y,
                                    0,
                                    hwnd,
                                    std::ptr::null(),
                                )
                            };
                            let event = match selected {
                                1 => Some(ShellEvent::Show),
                                2 => Some(ShellEvent::StopAgent),
                                3 => Some(ShellEvent::Quit),
                                _ => None,
                            };
                            if let Some(event) = event {
                                let _ = state.sender.try_send(event);
                            }
                            unsafe {
                                DestroyMenu(menu);
                                PostMessageW(hwnd, WM_NULL, 0, 0);
                            }
                        }
                    }
                    _ => {}
                }
                return 0;
            }
            _ => {}
        }
    }
    unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
}
fn run(
    class: Vec<u16>,
    sender: Sender<ShellEvent>,
    ready: std::sync::mpsc::SyncSender<Result<isize, String>>,
) -> Result<(), String> {
    let module = unsafe { GetModuleHandleW(std::ptr::null()) };
    let definitions = WNDCLASSW {
        lpfnWndProc: Some(procedure),
        hInstance: module,
        lpszClassName: class.as_ptr(),
        ..Default::default()
    };
    if unsafe { RegisterClassW(&definitions) } == 0 {
        let err = unsafe { GetLastError() };
        eprintln!("tray RegisterClassW failed: {err}");
        let _ = ready.send(Err(format!("GUI_TRAY_FAILED: RegisterClassW {err}")));
        return Err(format!("GUI_TRAY_FAILED: RegisterClassW {err}"));
    }
    let icons = [
        icon(include_bytes!("../assets/offline.ico")).map_err(|e| {
            eprintln!("offline.ico failed: {e}");
            e
        })?,
        icon(include_bytes!("../assets/connected.ico")).map_err(|e| {
            eprintln!("connected.ico failed: {e}");
            e
        })?,
        icon(include_bytes!("../assets/busy.ico")).map_err(|e| {
            eprintln!("busy.ico failed: {e}");
            e
        })?,
    ];
    let mut state = Box::new(State {
        sender,
        icons,
        status: 0,
        taskbar: unsafe { RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()) },
        control: false,
        service: crate::control_target::service(),
    });
    let hwnd = unsafe {
        CreateWindowExW(
            WS_EX_TOOLWINDOW,
            class.as_ptr(),
            wide("RACP native tray").as_ptr(),
            0,
            0,
            0,
            0,
            0,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            module,
            (&mut *state as *mut State).cast(),
        )
    };
    if hwnd.is_null() {
        let err = unsafe { GetLastError() };
        eprintln!("tray CreateWindowExW failed: {err}");
        for icon in icons {
            unsafe {
                DestroyIcon(icon);
            }
        }
        let _ = ready.send(Err(format!("GUI_TRAY_FAILED: CreateWindowExW {err}")));
        return Err(format!("GUI_TRAY_FAILED: CreateWindowExW {err}"));
    }
    if !notify(hwnd, &state, NIM_ADD) {
        let err = unsafe { GetLastError() };
        eprintln!("tray notify NIM_ADD failed: {err} (tray icon deferred until taskbar available)");
    }
    let _ = ready.send(Ok(hwnd as isize));
    let mut message = MSG::default();
    loop {
        let result = unsafe { GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) };
        if result <= 0 {
            if result < 0 {
                let _ = state.sender.try_send(ShellEvent::Failed);
            }
            break;
        }
        unsafe {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    notify(hwnd, &state, NIM_DELETE);
    unsafe {
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        DestroyWindow(hwnd);
        UnregisterClassW(class.as_ptr(), module);
    }
    for icon in icons {
        unsafe {
            DestroyIcon(icon);
        }
    }
    Ok(())
}
