//! Service-created, per-user pairing buckets. No service enrollment credential is shared.
use super::{
    native_identity::PinnedPeer,
    provider::{BrokerProcess, Child, GuardianProcess},
    PairConfig, PeerIdentity,
};
use crate::identity::ProtectedProcess;
use racp_contract::{local_ipc::DesktopSeed, new_id, RacpError};
use racp_core::{AgentSettings, Directory, Workspaces};
use std::{
    path::PathBuf,
    time::{Duration, Instant},
};
pub async fn run(
    control: &std::path::Path,
    cancel: tokio_util::sync::CancellationToken,
) -> Result<(), RacpError> {
    use racp_contract::local_ipc::*;
    use racp_contract::service_scope::ControllerRole;
    let _payload = racp_core::distribution::pin_current_payload(true)?;
    let endpoint = crate::local_ipc::LocalEndpoint::load_service(control)?;
    let scope = endpoint
        .service_scope
        .as_ref()
        .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
    let actor = PinnedPeer::open(std::process::id())?;
    if scope.role(&actor.identity().sid, actor.identity().session)
        != Some(ControllerRole::Controller)
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let _queries = super::access::own(&[scope.service_sid.clone()], false)?;
    let service = PinnedPeer::open(endpoint.server_pid)?;
    let current =
        racp_core::validate_local_path(&std::env::current_exe()?.with_file_name("racp-agent.exe"))?;
    let server = service.executable()?;
    if !racp_core::path_within(&server, &current) || !racp_core::path_within(&current, &server) {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let request = |command| ControlRequest {
        protocol: PROTOCOL,
        request_id: new_id("desktop"),
        deadline_ms: MAX_RECEIPT_TIMEOUT_MS,
        command,
    };
    let mut session = crate::local_ipc::connect(&endpoint).await?;
    let snapshot = match session
        .request(&request(ControlCommand::Snapshot))
        .await?
        .result
    {
        ControlResult::Snapshot {
            snapshot,
            role: ControllerRole::Controller,
        } => snapshot,
        _ => return Err(RacpError::new("PERMISSION_DENIED")),
    };
    let seed = match session
        .request(&request(ControlCommand::PrepareDesktop {
            boot_id: snapshot.boot_id.clone(),
        }))
        .await?
        .result
    {
        ControlResult::DesktopPrepared { seed } => seed,
        _ => return Err(RacpError::new("SESSION_UNAVAILABLE")),
    };
    if seed.agent_pid != endpoint.server_pid
        || seed.agent_created != endpoint.server_created
        || seed.agent_sid != endpoint.user_sid
        || seed.user_sid != actor.identity().sid
        || seed.session_id != actor.identity().session
        || seed.device_id
            != snapshot
                .identity
                .as_ref()
                .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?
                .device_id
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let config = PairConfig {
        version: 1,
        pair_id: seed.pair_id.clone(),
        session_id: seed.session_id,
        user_sid: seed.user_sid,
        agent_sid: seed.agent_sid.clone(),
        agent_service_sid: Some(seed.agent_sid),
        agent_pid: seed.agent_pid,
        agent_created: seed.agent_created,
        agent_session: 0,
        secret: seed.secret,
        job_name: None,
    };
    config.validate()?;
    let root = PathBuf::from(seed.root);
    let expected = control.join("desktop-sessions").join(&seed.pair_id);
    if !racp_core::path_within(&root, &expected) || !racp_core::path_within(&expected, &root) {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let mut registered = false;
    let bucket = root.clone();
    let device = seed.device_id;
    let mut child = tokio::task::spawn_blocking(move || {
        super::provider::Desktop::launch_paired(config, root, &device)
    })
    .await
    .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))??;
    let result=async {
        let broker = PinnedPeer::open(child.peer.pid)?;
        let GuardianProcess::Local(guardian) = &child.guardian else {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        };
        let guardian = PinnedPeer::open(guardian.id())?;
        let reply=session.request(&request(ControlCommand::RegisterDesktop {boot_id:snapshot.boot_id.clone(),pair_id:seed.pair_id.clone(),
            broker_pid:broker.identity().pid,broker_created:broker.identity().created,guardian_pid:guardian.identity().pid,guardian_created:guardian.identity().created})).await?;
        if !matches!(reply.result,ControlResult::DesktopRegistered{ref pair_id,session_id} if pair_id==&seed.pair_id && session_id==actor.identity().session) {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        registered=true;
        loop {
            tokio::select! { _=cancel.cancelled()=>break, _=tokio::time::sleep(Duration::from_secs(1))=>() }
            if service.alive().is_err() || !child.process.alive() { break; }
            match session.request(&request(ControlCommand::Snapshot)).await?.result {
                ControlResult::Snapshot {snapshot:next,role:ControllerRole::Controller} if next.boot_id==snapshot.boot_id => (),
                _=>return Err(RacpError::new("STALE_CONNECTION")),
            }
        }
        Ok(())
    }.await;
    // Ask the service to stop its authenticated broker before the local owner closes the Job.
    let detached=session.request(&request(ControlCommand::RemoveDesktop {
        boot_id:snapshot.boot_id, pair_id:seed.pair_id.clone()
    })).await.is_ok_and(|reply|matches!(reply.result,ControlResult::DesktopRemoved{pair_id} if pair_id==seed.pair_id));
    tokio::task::spawn_blocking(move || child.stop())
        .await
        .map_err(|_| RacpError::new("CLEANUP_UNKNOWN"))??;
    if registered && !detached && service.alive().is_ok() && bucket.try_exists()? {
        return Err(RacpError::new("CLEANUP_UNKNOWN"));
    }
    result
}
use windows_sys::Win32::{
    Foundation::*,
    Security::{Authorization::*, SECURITY_ATTRIBUTES},
    Storage::FileSystem::*,
};

pub(super) struct Pending {
    pub config: PairConfig,
    pub path: PathBuf,
    pub pin: Directory,
    caller: PeerIdentity,
    deadline: Instant,
    pub device: String,
    record: super::session_ledger::Record,
}
impl Pending {
    pub fn discard(self) -> Result<(), RacpError> {
        if self.pin.names()?.is_empty() {
            let mut record = self.record;
            record.confirm_cleanup()?;
            cleanup_bucket(self.pin)?;
            record.complete()
        } else {
            // A launch may still be in flight. Signal release; retain its files until a proven stop.
            racp_core::atomic_write(&self.path.with_file_name("guardian-stop"), b"", true)?;
            Err(RacpError::new("CLEANUP_UNKNOWN"))
        }
    }
    pub fn accepts(&self, caller: &PeerIdentity) -> bool {
        caller.pid == self.caller.pid
            && caller.created == self.caller.created
            && caller.sid == self.caller.sid
            && caller.session == self.caller.session
    }
    pub fn expired(&self) -> bool {
        Instant::now() >= self.deadline
    }
    pub fn new(settings: &AgentSettings, caller: PeerIdentity) -> Result<Self, RacpError> {
        racp_core::service_directory::require_service_actor()?;
        if caller.session == 0 || !racp_contract::service_scope::interactive_user_sid(&caller.sid) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let actor = PinnedPeer::open(std::process::id())?;
        let config = racp_core::native_service::NativeServiceConfig::load(
            settings
                .data_dir
                .parent()
                .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
        )?;
        let parent = racp_core::service_directory::sessions(&config.endpoint_dir, &config.scope)?;
        let root = parent.path.clone();
        if parent.names()?.len() >= 64 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let pair_id = new_id("pair").trim_start_matches("pair_").to_owned();
        let bucket = root.join(&pair_id);
        let owner = &actor.identity().sid;
        let text = format!(
            "O:{owner}D:P(A;OICI;FA;;;{owner})(A;OICI;FA;;;SY)(A;OICI;0x{:08x};;;{})",
            racp_core::service_directory::PAIR_USER_ACCESS,
            caller.sid
        );
        let wide = |text: &str| text.encode_utf16().chain(Some(0)).collect::<Vec<_>>();
        let mut descriptor = std::ptr::null_mut();
        if unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                wide(&text).as_ptr(),
                SDDL_REVISION_1,
                &mut descriptor,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        struct Local(*mut core::ffi::c_void);
        impl Drop for Local {
            fn drop(&mut self) {
                unsafe {
                    LocalFree(self.0);
                }
            }
        }
        let _descriptor = Local(descriptor);
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        if unsafe { CreateDirectoryW(wide(&bucket.to_string_lossy()).as_ptr(), &attributes) } == 0 {
            return Err(RacpError::new("RESOURCE_BUSY"));
        }
        let pin = racp_core::service_directory::pin_pair(&bucket, owner, &caller.sid)?;
        use base64::Engine;
        let config = PairConfig {
            version: 1,
            pair_id,
            session_id: caller.session,
            user_sid: caller.sid.clone(),
            agent_sid: owner.clone(),
            agent_service_sid: Some(owner.clone()),
            agent_pid: actor.identity().pid,
            agent_created: actor.identity().created,
            agent_session: 0,
            secret: base64::engine::general_purpose::URL_SAFE_NO_PAD
                .encode(rand::random::<[u8; 32]>()),
            job_name: None,
        };
        config.validate()?;
        let record = super::session_ledger::Record::prepare(
            settings
                .data_dir
                .parent()
                .ok_or_else(|| RacpError::new("LOCAL_STATE_FAILED"))?,
            &config,
            &caller,
            &pin,
        )?;
        Ok(Self {
            path: bucket.join("pair.bin"),
            pin,
            config,
            caller,
            deadline: Instant::now() + Duration::from_secs(30),
            device: settings.device_id.clone(),
            record,
        })
    }
    pub fn seed(&self) -> DesktopSeed {
        DesktopSeed {
            pair_id: self.config.pair_id.clone(),
            root: self.path.parent().unwrap().to_string_lossy().into_owned(),
            secret: self.config.secret.clone(),
            device_id: self.device.clone(),
            agent_pid: self.config.agent_pid,
            agent_created: self.config.agent_created,
            agent_sid: self.config.agent_sid.clone(),
            user_sid: self.config.user_sid.clone(),
            session_id: self.config.session_id,
        }
    }
    pub fn register(
        mut self,
        caller: &PeerIdentity,
        broker: u32,
        birth: f64,
        guardian: u32,
        guardian_birth: f64,
    ) -> Result<Child, RacpError> {
        let mut failed = FailureSignal {
            path: self.path.clone(),
            armed: true,
        };
        if self.expired()
            || caller.pid != self.caller.pid
            || caller.created != self.caller.created
            || caller.sid != self.caller.sid
            || caller.session != self.caller.session
            || broker == guardian
            || broker == caller.pid
            || guardian == caller.pid
            || broker == std::process::id()
            || guardian == std::process::id()
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let peer = PinnedPeer::open(broker)?;
        let guardian = PinnedPeer::open(guardian)?;
        self.config.require_broker(peer.identity())?;
        self.config.require_broker(guardian.identity())?;
        let current = racp_core::validate_local_path(&std::env::current_exe()?)?;
        let same = |path: &std::path::Path| {
            racp_core::path_within(path, &current) && racp_core::path_within(&current, path)
        };
        if peer.identity().created != birth
            || guardian.identity().created != guardian_birth
            || !guardian.outside_jobs()?
            || !same(&peer.executable()?)
            || !same(&guardian.executable()?)
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let broker_identity = peer.identity().clone();
        // Mutual HMAC + pinned OS peer must complete before accepting this broker.
        let state = super::pipe::request(
            &self.config,
            &broker_identity,
            serde_json::json!({"operation":"broker.status"}),
            Duration::from_secs(3),
        )?;
        if state["session_id"] != self.config.session_id || state["available"] != true {
            return Err(RacpError::new("SESSION_UNAVAILABLE"));
        }
        let guardian_protection = ProtectedProcess::register(guardian.identity().pid)?;
        let protection = ProtectedProcess::register(peer.identity().pid)?;
        self.record
            .registered(peer.identity(), guardian.identity())?;
        failed.armed = false;
        Ok(Child {
            guardian: GuardianProcess::Joined(guardian),
            _guardian_protection: guardian_protection,
            process: BrokerProcess::Joined(peer),
            _protection: protection,
            peer: broker_identity,
            config: self.config,
            path: self.path,
            _pair_pin: Some(self.pin),
            owner: Some(caller.clone()),
            bucket_cleaned: false,
            ledger: Some(self.record),
        })
    }
}

struct FailureSignal {
    path: PathBuf,
    armed: bool,
}
impl Drop for FailureSignal {
    fn drop(&mut self) {
        if self.armed {
            let _ = racp_core::atomic_write(&self.path.with_file_name("guardian-stop"), b"", true);
        }
    }
}

/// Deletes only the fixed pairing files after both pinned processes have exited.
pub(super) fn cleanup_bucket(pin: Directory) -> Result<(), RacpError> {
    let names = pin.names()?;
    if names.len() > 3
        || names.iter().any(|name| {
            !["pair.bin", "guardian-status.json", "guardian-stop"].contains(&name.as_str())
        })
    {
        return Err(RacpError::new("CLEANUP_FAILED"));
    }
    for name in names {
        pin.unlink(&name, false)?;
    }
    let root = pin.path.clone();
    let parent = root
        .parent()
        .ok_or_else(|| RacpError::new("CLEANUP_FAILED"))?;
    let guards = Workspaces::new(parent, &[])?;
    let parent = guards.directory("default", parent)?;
    let name = root
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| RacpError::new("CLEANUP_FAILED"))?;
    drop(pin);
    parent.unlink(name, true)
}
