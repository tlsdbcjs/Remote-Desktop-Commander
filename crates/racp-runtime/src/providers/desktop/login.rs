//! User-logon registration. SCM never obtains user passwords or launches with a user token.
use super::{
    access,
    native_identity::{client_scope, PinnedPeer},
    pipe::{self, Pipe},
    provider::{BrokerProcess, Child, GuardianProcess},
    PairConfig,
};
use crate::identity::ProtectedProcess;
use racp_contract::{new_id, RacpError};
use racp_core::{AgentSettings, SecretStore};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*,
    System::{JobObjects::*, Pipes::*, Threading::*},
};
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Endpoint {
    version: u8,
    device_id: String,
    agent_sid: String,
    service_sid: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Configuration {
    version: u8,
    endpoint: PathBuf,
    users: Vec<String>,
}
impl Endpoint {
    fn validate(&self) -> Result<(), RacpError> {
        if self.version != 1
            || self.device_id.is_empty()
            || self.device_id.len() > 96
            || !self
                .device_id
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
            || !super::pairing::sid(&self.agent_sid)
            || self.agent_sid == "S-1-5-18"
            || !super::pairing::sid(&self.service_sid)
            || !self.service_sid.starts_with("S-1-5-80-")
        {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        Ok(())
    }
    fn agent(&self, peer: &super::PeerIdentity) -> Result<(), RacpError> {
        self.validate()?;
        if peer.sid != self.agent_sid
            || peer.session != 0
            || peer.administrator
            || !peer.service_sids.contains(&self.service_sid)
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(())
    }
    fn pipe(&self) -> String {
        format!(r"\\.\pipe\LOCAL\racp-login-{}", self.device_id)
    }
}
fn endpoint(path: &Path) -> Result<Endpoint, RacpError> {
    let e: Endpoint = serde_json::from_slice(&racp_core::read_bounded(path, 4096, false)?)?;
    e.validate()?;
    Ok(e)
}
pub fn configure_login(args: &[String], state: &Path) -> Result<Value, RacpError> {
    let option = |name: &str| -> Result<String, RacpError> {
        args.windows(2)
            .find(|a| a[0] == name)
            .map(|a| a[1].clone())
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))
    };
    let e = Endpoint {
        version: 1,
        device_id: option("--device-id")?,
        agent_sid: option("--agent-sid")?,
        service_sid: option("--service-sid")?,
    };
    e.validate()?;
    let users: Vec<String> = args
        .windows(2)
        .filter(|a| a[0] == "--login-user")
        .map(|a| a[1].clone())
        .collect();
    validate_users(&users)?;
    racp_core::private_dir(state)?;
    access::path_acl(
        state,
        &format!(
            "D:P(A;OICI;FA;;;{})(A;OICI;FA;;;{})(A;OICI;FA;;;BA){}",
            e.agent_sid,
            e.service_sid,
            users
                .iter()
                .map(|u| format!("(A;;FRFX;;;{u})"))
                .collect::<String>()
        ),
    )?;
    let path = PathBuf::from(option("--endpoint")?);
    racp_core::validate_local_path(&path)?;
    if path.try_exists()? || state.join("service-login.json").try_exists()? {
        return Err(RacpError::new("CONFLICT"));
    }
    prepare_buckets(&path, &e, &users)?;
    racp_core::atomic_write(&path, &serde_json::to_vec(&e)?, false)?;
    access::path_acl(
        &path,
        &format!(
            "D:P(A;;FA;;;{})(A;;FA;;;BA){}",
            e.service_sid,
            users
                .iter()
                .map(|u| format!("(A;;FR;;;{u})"))
                .collect::<String>()
        ),
    )?;
    racp_core::atomic_write(
        &state.join("service-login.json"),
        &serde_json::to_vec(&Configuration {
            version: 1,
            endpoint: path.clone(),
            users,
        })?,
        false,
    )?;
    access::path_acl(
        &state.join("service-login.json"),
        &format!(
            "D:P(A;;FA;;;{})(A;;FA;;;{})(A;;FA;;;BA)",
            e.agent_sid, e.service_sid
        ),
    )?;
    Ok(json!({"configured":true,"endpoint":path,"credential_material":false}))
}
fn validate_users(users: &[String]) -> Result<(), RacpError> {
    if !(1..=16).contains(&users.len())
        || users.iter().collect::<BTreeSet<_>>().len() != users.len()
        || users.iter().any(|u| {
            !super::pairing::sid(u)
                || u == "S-1-5-18"
                || u == "S-1-5-32-544"
                || u.starts_with("S-1-5-80-")
        })
    {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    Ok(())
}
pub fn verify_config_identity(state: &Path) -> Result<(), RacpError> {
    let path = state.join("service-login.json");
    if path.try_exists()? {
        let c: Configuration =
            serde_json::from_slice(&racp_core::read_bounded(&path, 16384, true)?)?;
        if c.version != 1 {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        validate_users(&c.users)?;
        endpoint(&c.endpoint)?.agent(PinnedPeer::open(std::process::id())?.identity())?;
    }
    Ok(())
}
pub(super) struct Registrar {
    _grants: access::Grants,
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}
impl Registrar {
    pub(super) fn start(
        settings: &AgentSettings,
        children: Arc<Mutex<BTreeMap<u32, Child>>>,
        connected: Arc<AtomicBool>,
        status: Arc<Mutex<Value>>,
    ) -> Result<Self, RacpError> {
        let c: Configuration = serde_json::from_slice(&racp_core::read_bounded(
            &settings.data_dir.join("service-login.json"),
            16384,
            true,
        )?)?;
        if c.version != 1 {
            return Err(RacpError::new("REQUEST_INVALID"));
        }
        validate_users(&c.users)?;
        let e = endpoint(&c.endpoint)?;
        let agent = PinnedPeer::open(std::process::id())?;
        e.agent(agent.identity())?;
        if e.device_id != settings.device_id {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let broker_root = prepare_buckets(&c.endpoint, &e, &c.users)?;
        let grants = access::own(&c.users, false)?;
        let mut pipe = Pipe::listen(
            &e.pipe(),
            &format!(
                "D:P(A;;GA;;;{}){}",
                e.service_sid,
                c.users
                    .iter()
                    .map(|u| format!("(A;;0x100103;;;{u})"))
                    .collect::<String>()
            ),
        )?;
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        let thread = std::thread::spawn(move || {
            while !stopping.load(Ordering::Acquire) {
                if pipe.accept().unwrap_or(false) {
                    if connected.load(Ordering::Acquire) {
                        let adopted = adopt(
                            &mut pipe,
                            &e,
                            &c.users,
                            agent.identity(),
                            &broker_root,
                            &children,
                        );
                        if let Ok(session) = adopted {
                            if let Ok(mut children) = children.lock() {
                                if let Some(child) = children.get_mut(&session) {
                                    if let Ok(mut state) = pipe::request(
                                        &child.config,
                                        &child.peer,
                                        json!({"operation":"broker.status"}),
                                        Duration::from_secs(3),
                                    ) {
                                        state["broker_running"] = json!(true);
                                        if let Ok(mut status) = status.lock() {
                                            *status = state;
                                        }
                                    }
                                }
                            }
                        }
                    }
                    pipe.disconnect();
                }
            }
        });
        Ok(Self {
            _grants: grants,
            stop,
            thread: Some(thread),
        })
    }
    pub(super) fn stop(&mut self) -> Result<(), RacpError> {
        self.stop.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            thread
                .join()
                .map_err(|_| RacpError::new("CLEANUP_FAILED"))?;
        }
        Ok(())
    }
}
impl Drop for Registrar {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}
pub(super) struct BrokerJob {
    job: OwnedHandle,
    peer: PinnedPeer,
}
impl BrokerJob {
    fn assign(peer: PinnedPeer, config: &PairConfig) -> Result<Self, RacpError> {
        let raw = unsafe { OpenProcess(0x101101, 0, peer.identity().pid) };
        if raw.is_null() {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let process = unsafe { OwnedHandle::from_raw_handle(raw) };
        peer.alive()?;
        let job = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
        if job.is_null() {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let job = unsafe { OwnedHandle::from_raw_handle(job) };
        let limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
            BasicLimitInformation: JOBOBJECT_BASIC_LIMIT_INFORMATION {
                LimitFlags: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
                ActiveProcessLimit: 8,
                ..Default::default()
            },
            ..Default::default()
        };
        if unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of_val(&limits) as u32,
            )
        } == 0
            || unsafe { AssignProcessToJobObject(job.as_raw_handle(), process.as_raw_handle()) }
                == 0
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        config.require_broker(peer.identity())?;
        peer.alive()?;
        Ok(Self { job, peer })
    }
    pub(super) fn alive(&self) -> bool {
        self.peer.alive().is_ok()
    }
    pub(super) fn kill(&self) -> Result<(), RacpError> {
        if unsafe { TerminateJobObject(self.job.as_raw_handle(), 1) } == 0 {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        Ok(())
    }
    pub(super) fn empty(&self) -> Result<bool, RacpError> {
        let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        if unsafe {
            QueryInformationJobObject(
                self.job.as_raw_handle(),
                JobObjectBasicAccountingInformation,
                (&mut info as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                std::mem::size_of_val(&info) as u32,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(RacpError::new("CLEANUP_FAILED"));
        }
        Ok(info.ActiveProcesses == 0)
    }
}
fn adopt(
    pipe: &mut Pipe,
    e: &Endpoint,
    users: &[String],
    agent: &super::PeerIdentity,
    broker_root: &Path,
    children: &Arc<Mutex<BTreeMap<u32, Child>>>,
) -> Result<u32, RacpError> {
    let hello = pipe.read()?;
    if hello.as_object().is_none_or(|v| v.len() != 5)
        || hello["version"] != 1
        || hello["device_id"] != e.device_id
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut pid = 0;
    if unsafe { GetNamedPipeClientProcessId(pipe.handle.as_raw_handle(), &mut pid) } == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let peer = PinnedPeer::open(pid)?;
    let (sid, session) = client_scope(pipe.handle.as_raw_handle())?;
    if !users.contains(&sid)
        || sid != peer.identity().sid
        || session != peer.identity().session
        || session == 0
        || hello["session_id"] != session
        || peer.identity().administrator
        || peer.identity().integrity > 0x2000
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let path = racp_core::validate_local_path(&PathBuf::from(
        hello["pair_path"]
            .as_str()
            .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?,
    ))?;
    let registration = path
        .parent()
        .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
    let name = registration
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("");
    if path.file_name().is_none_or(|n| n != "pair.bin")
        || registration.parent() != Some(broker_root.join(&sid).as_path())
        || name.len() != 45
        || !name.starts_with("registration_")
        || !name[13..]
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    access::require_owner(registration, &sid)?;
    if peer.executable()?.to_string_lossy().to_lowercase()
        != std::env::current_exe()?.to_string_lossy().to_lowercase()
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut children = children
        .lock()
        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
    if children.len() >= 16
        || children
            .get_mut(&session)
            .is_some_and(|c| c.process.alive())
    {
        return Err(RacpError::new("RESOURCE_BUSY"));
    }
    if let Some(mut old) = children.remove(&session) {
        old.stop()?;
    }
    use base64::Engine;
    let config = PairConfig {
        version: 1,
        pair_id: new_id("pair").trim_start_matches("pair_").into(),
        session_id: session,
        user_sid: sid,
        agent_sid: agent.sid.clone(),
        agent_service_sid: Some(e.service_sid.clone()),
        agent_pid: agent.pid,
        agent_created: agent.created,
        agent_session: agent.session,
        secret: base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(rand::random::<[u8; 32]>()),
        job_name: None,
    };
    config.validate()?;
    let identity = peer.identity().clone();
    let job = BrokerJob::assign(peer, &config)?;
    let client = hello["nonce"].as_str().unwrap_or("");
    let server = pipe::nonce();
    let expected = config.proof("login-broker", &server, client)?;
    pipe.write(&json!({"nonce":client,"server_nonce":server,"config":config}))?;
    let receipt = pipe.read()?;
    if receipt["received"] != true
        || receipt["pair_id"] != config.pair_id
        || receipt.as_object().is_none_or(|o| o.len() != 4)
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    config.verify(
        "login-broker",
        &server,
        client,
        receipt["proof"].as_str().unwrap_or(""),
    )?;
    if expected.is_empty() {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let guardian = receipt["guardian"]
        .as_object()
        .filter(|o| o.len() == 2)
        .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
    let guardian_pid = guardian["pid"]
        .as_u64()
        .filter(|n| *n > 0 && *n <= u32::MAX as u64)
        .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))? as u32;
    let guardian = PinnedPeer::open(guardian_pid)?;
    config.require_broker(guardian.identity())?;
    if !guardian.outside_jobs()?
        || guardian.executable()?.to_string_lossy().to_lowercase()
            != std::env::current_exe()?.to_string_lossy().to_lowercase()
    {
        return Err(RacpError::new("INPUT_GUARDIAN_UNAVAILABLE"));
    }
    if guardian.identity().created != receipt["guardian"]["created"].as_f64().unwrap_or(0.0) {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let child = Child {
        guardian: GuardianProcess::Registered(guardian),
        _guardian_protection: ProtectedProcess::register(guardian_pid)?,
        process: BrokerProcess::Registered(job),
        _protection: ProtectedProcess::register(pid)?,
        peer: identity,
        config,
        path,
    };
    // Service must be able to read guardian heartbeat and persist cancellation before adoption.
    racp_core::read_bounded(
        &child.path.with_file_name("guardian-status.json"),
        4096,
        false,
    )?;
    children.insert(session, child);
    pipe.write(&json!({"adopted":true}))?;
    Ok(session)
}
pub fn run_login_broker(endpoint_path: &Path) -> Result<(), RacpError> {
    let e = endpoint(endpoint_path)?;
    let actor = PinnedPeer::open(std::process::id())?;
    if actor.identity().session == 0
        || actor.identity().administrator
        || actor.identity().integrity > 0x2000
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let _single = racp_core::InstanceLock::acquire(
        &PathBuf::from(
            std::env::var_os("LOCALAPPDATA").ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
        )
        .join("RACP/login-brokers")
        .join(format!("{}-{}.lock", e.device_id, actor.identity().session)),
    )?;
    use std::os::windows::process::CommandExt;
    loop {
        let mut child = std::process::Command::new(std::env::current_exe()?)
            .arg("broker-register")
            .arg("--login-endpoint")
            .arg(endpoint_path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .creation_flags(0x08000000)
            .spawn()?;
        child.wait()?;
        actor.alive()?;
        std::thread::sleep(Duration::from_secs(3));
    }
}
pub fn register_login_broker(endpoint_path: &Path) -> Result<(), RacpError> {
    let e = endpoint(endpoint_path)?;
    let actor = PinnedPeer::open(std::process::id())?;
    if actor.identity().session == 0
        || actor.identity().administrator
        || actor.identity().integrity > 0x2000
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let _grants = access::own(&[e.service_sid.clone()], true)?;
    let pipe = Pipe::connect(&e.pipe())?;
    let mut server_pid = 0;
    if unsafe { GetNamedPipeServerProcessId(pipe.handle.as_raw_handle(), &mut server_pid) } == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let server = PinnedPeer::open(server_pid)?;
    e.agent(server.identity())?;
    let local = endpoint_path
        .parent()
        .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?
        .join("login-brokers")
        .join(&actor.identity().sid)
        .join(new_id("registration"));
    racp_core::private_dir(&local)?;
    access::path_acl(
        &local,
        &format!(
            "D:P(A;OICI;FA;;;{})(A;OICI;FA;;;{})",
            actor.identity().sid,
            e.service_sid
        ),
    )?;
    let path = local.join("pair.bin");
    let nonce = pipe::nonce();
    pipe.write(&json!({"version":1,"nonce":nonce,"device_id":e.device_id,"session_id":actor.identity().session,"pair_path":path}))?;
    let response = pipe.read()?;
    if response.as_object().is_none_or(|o| o.len() != 3) || response["nonce"] != nonce {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let config = PairConfig::decode(&serde_json::to_vec(&response["config"])?)?;
    config.require_agent(server.identity())?;
    config.require_broker(actor.identity())?;
    if config.agent_service_sid.as_deref() != Some(&e.service_sid) {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut document = BTreeMap::from([
        ("pair".into(), serde_json::to_string(&config)?),
        ("device_id".into(), e.device_id),
        ("broker_pid".into(), actor.identity().pid.to_string()),
        (
            "broker_created".into(),
            actor.identity().created.to_string(),
        ),
    ]);
    SecretStore::new(path.clone()).save(&document, false)?;
    let guardian = scheduled_guardian(&path, &config)?;
    document.insert("guardian_pid".into(), guardian.identity().pid.to_string());
    document.insert(
        "guardian_created".into(),
        guardian.identity().created.to_string(),
    );
    SecretStore::new(path.clone()).save(&document, true)?;
    pipe.write(&json!({"received":true,"pair_id":config.pair_id,"proof":config.proof("login-broker",response["server_nonce"].as_str().unwrap_or(""),&nonce)?,"guardian":{"pid":guardian.identity().pid,"created":guardian.identity().created}}))?;
    if pipe.read()? != json!({"adopted":true}) {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    server.alive()?;
    let result = super::broker::run_broker(&path);
    let _ = racp_core::atomic_write(&path.with_file_name("guardian-stop"), b"", false);
    let deadline = Instant::now() + Duration::from_secs(3);
    while guardian.alive().is_ok() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    if guardian.alive().is_ok() {
        return Err(RacpError::new("CLEANUP_FAILED"));
    }
    let _ = std::fs::remove_file(path);
    result
}
fn scheduled_guardian(path: &Path, config: &PairConfig) -> Result<PinnedPeer, RacpError> {
    let path = path.to_owned();
    let config = config.clone();
    std::thread::spawn(move || -> Result<PinnedPeer, RacpError> {
        use windows::{
            core::{Interface, BSTR},
            Win32::System::{Com::*, TaskScheduler::*, Variant::VARIANT},
        };
        unsafe {
            CoInitializeEx(None, COINIT_MULTITHREADED)
                .ok()
                .map_err(|_| RacpError::new("INPUT_GUARDIAN_UNAVAILABLE"))?;
        }
        struct Apartment;
        impl Drop for Apartment {
            fn drop(&mut self) {
                unsafe {
                    CoUninitialize();
                }
            }
        }
        let _apartment = Apartment;
        let result = (|| -> windows::core::Result<PinnedPeer> {
            unsafe {
                let scheduler: ITaskService =
                    CoCreateInstance(&TaskScheduler, None, CLSCTX_INPROC_SERVER)?;
                let empty = VARIANT::default();
                scheduler.Connect(&empty, &empty, &empty, &empty)?;
                let folder = scheduler.GetFolder(&BSTR::from("\\"))?;
                let task = scheduler.NewTask(0)?;
                let principal = task.Principal()?;
                principal.SetUserId(&BSTR::from(&config.user_sid))?;
                principal.SetLogonType(TASK_LOGON_INTERACTIVE_TOKEN)?;
                principal.SetRunLevel(TASK_RUNLEVEL_LUA)?;
                let settings = task.Settings()?;
                settings.SetExecutionTimeLimit(&BSTR::from("PT0S"))?;
                settings.SetHidden(windows::Win32::Foundation::VARIANT_BOOL(-1))?;
                let action: IExecAction = task.Actions()?.Create(TASK_ACTION_EXEC)?.cast()?;
                let exe = std::env::current_exe().map_err(|_| {
                    windows::core::Error::from_hresult(windows::core::HRESULT(0x80004005u32 as i32))
                })?;
                action.SetPath(&BSTR::from(exe.to_string_lossy().as_ref()))?;
                let text = path.to_string_lossy();
                if text.contains('"') {
                    return Err(windows::core::Error::from_hresult(windows::core::HRESULT(
                        0x80004005u32 as i32,
                    )));
                }
                action.SetArguments(&BSTR::from(format!("guardian --pair-config \"{text}\"")))?;
                let name = BSTR::from(format!("RACP-Guardian-{}", config.pair_id));
                let sid = VARIANT::from(BSTR::from(&config.user_sid));
                let security = VARIANT::from(BSTR::from(format!(
                    "D:P(A;;GA;;;{})(A;;GA;;;SY)",
                    config.user_sid
                )));
                let registered = folder.RegisterTaskDefinition(
                    &name,
                    &task,
                    TASK_CREATE.0,
                    &sid,
                    &empty,
                    TASK_LOGON_INTERACTIVE_TOKEN,
                    &security,
                )?;
                let running = registered.Run(&empty);
                let _ = folder.DeleteTask(&name, 0);
                running?;
                let deadline = Instant::now() + Duration::from_secs(5);
                loop {
                    if let Ok(raw) = racp_core::read_bounded(
                        &path.with_file_name("guardian-status.json"),
                        4096,
                        false,
                    ) {
                        if let Ok(status) = serde_json::from_slice::<Value>(&raw) {
                            if status["healthy"] == true && status["outside_all_jobs"] == true {
                                let pid = status["pid"].as_u64().unwrap_or(0) as u32;
                                if let Ok(peer) = PinnedPeer::open(pid) {
                                    if config.require_broker(peer.identity()).is_ok()
                                        && status["create_time"] == peer.identity().created
                                    {
                                        return Ok(peer);
                                    }
                                }
                            }
                        }
                    }
                    if Instant::now() >= deadline {
                        return Err(windows::core::Error::from_hresult(windows::core::HRESULT(
                            0x80004005u32 as i32,
                        )));
                    }
                    std::thread::sleep(Duration::from_millis(20));
                }
            }
        })();
        result.map_err(|_| RacpError::new("INPUT_GUARDIAN_UNAVAILABLE"))
    })
    .join()
    .map_err(|_| RacpError::new("INPUT_GUARDIAN_UNAVAILABLE"))?
}

pub fn login_startup(endpoint_path: &Path, remove: bool) -> Result<Value, RacpError> {
    use windows_sys::Win32::System::Registry::*;
    let e = endpoint(endpoint_path)?;
    let actor = PinnedPeer::open(std::process::id())?;
    if actor.identity().session == 0 || actor.identity().administrator {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let exe = std::env::current_exe()?;
    let path = endpoint_path.to_string_lossy();
    let exe = exe.to_string_lossy();
    if path.contains('"') || exe.contains('"') {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let expected = format!("\"{exe}\" broker-login --login-endpoint \"{path}\"");
    let wide = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
    let mut key = std::ptr::null_mut();
    if unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            wide(r"Software\Microsoft\Windows\CurrentVersion\Run").as_ptr(),
            0,
            std::ptr::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_QUERY_VALUE | KEY_SET_VALUE,
            std::ptr::null(),
            &mut key,
            std::ptr::null_mut(),
        )
    } != ERROR_SUCCESS
    {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    struct Key(HKEY);
    impl Drop for Key {
        fn drop(&mut self) {
            unsafe {
                RegCloseKey(self.0);
            }
        }
    }
    let key = Key(key);
    let name = wide(&format!("RACP-Broker-{}", e.device_id));
    let mut size = 0;
    let mut kind = 0;
    let found = unsafe {
        RegQueryValueExW(
            key.0,
            name.as_ptr(),
            std::ptr::null(),
            &mut kind,
            std::ptr::null_mut(),
            &mut size,
        )
    };
    if found != ERROR_SUCCESS && found != ERROR_FILE_NOT_FOUND {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    if found == ERROR_SUCCESS {
        if kind != REG_SZ || size > 32768 || size % 2 != 0 {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
        let mut raw = vec![0u16; size as usize / 2];
        if unsafe {
            RegQueryValueExW(
                key.0,
                name.as_ptr(),
                std::ptr::null(),
                &mut kind,
                raw.as_mut_ptr().cast(),
                &mut size,
            )
        } != ERROR_SUCCESS
        {
            return Err(RacpError::new("LOCAL_STATE_FAILED"));
        }
        let value =
            String::from_utf16(&raw[..raw.iter().position(|c| *c == 0).unwrap_or(raw.len())])
                .map_err(|_| RacpError::new("PRECONDITION_FAILED"))?;
        if value != expected {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
    }
    let status = if remove {
        if found == ERROR_FILE_NOT_FOUND {
            ERROR_SUCCESS
        } else {
            unsafe { RegDeleteValueW(key.0, name.as_ptr()) }
        }
    } else {
        let raw = wide(&expected);
        unsafe {
            RegSetValueExW(
                key.0,
                name.as_ptr(),
                0,
                REG_SZ,
                raw.as_ptr().cast(),
                (raw.len() * 2) as u32,
            )
        }
    };
    if status != ERROR_SUCCESS {
        return Err(RacpError::new("LOCAL_STATE_FAILED"));
    }
    Ok(
        json!({"login_enabled":!remove,"credential_material":false,"already_running_processes":"preserved"}),
    )
}

fn prepare_buckets(path: &Path, e: &Endpoint, users: &[String]) -> Result<PathBuf, RacpError> {
    let root = path
        .parent()
        .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?
        .join("login-brokers");
    racp_core::private_dir(&root)?;
    access::path_acl(
        &root,
        &format!(
            "D:P(A;OICI;FA;;;{})(A;OICI;FA;;;{})(A;OICI;FA;;;BA){}",
            e.agent_sid,
            e.service_sid,
            users
                .iter()
                .map(|u| format!("(A;;FRFX;;;{u})"))
                .collect::<String>()
        ),
    )?;
    for user in users {
        let bucket = root.join(user);
        racp_core::private_dir(&bucket)?;
        access::path_acl(
            &bucket,
            &format!(
                "D:P(A;OICI;FA;;;{})(A;OICI;FA;;;{})(A;OICI;FA;;;{})",
                e.agent_sid, e.service_sid, user
            ),
        )?;
    }
    Ok(root)
}
