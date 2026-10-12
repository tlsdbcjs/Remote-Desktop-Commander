use super::events::StateHub;
use crate::providers::desktop::native_identity::{client_scope, PinnedPeer};
use racp_contract::service_scope::{ControllerRole, ServiceScope};
use racp_contract::{digest, local_ipc::*, RacpError};
use serde::{Deserialize, Serialize};
use std::{
    ffi::c_void, mem::size_of, os::windows::io::AsRawHandle, path::Path, sync::Arc, time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::windows::named_pipe::{ClientOptions, NamedPipeClient, NamedPipeServer, ServerOptions},
    sync::Semaphore,
};
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::{
        Authorization::{ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1},
        SECURITY_ATTRIBUTES,
    },
    System::Pipes::{GetNamedPipeClientProcessId, GetNamedPipeServerProcessId},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalEndpoint {
    pub pipe_name: String,
    pub server_pid: u32,
    pub server_created: f64,
    pub user_sid: String,
    pub session_id: u32,
    #[serde(default)]
    pub service_scope: Option<ServiceScope>,
}
impl LocalEndpoint {
    pub fn for_root(root: &Path) -> Result<Self, RacpError> {
        let root = racp_core::validate_local_path(root)?;
        let peer = PinnedPeer::open(std::process::id())?;
        let identity = peer.identity();
        if identity.session == 0 {
            return Err(RacpError::new("LOCAL_IPC_SERVICE_SCOPE_REQUIRED"));
        }
        Ok(Self {
            pipe_name: format!(
                r"\\.\pipe\RACP.Native.{}",
                &digest(root.to_string_lossy().as_bytes())[..32]
            ),
            server_pid: identity.pid,
            server_created: identity.created,
            user_sid: identity.sid.clone(),
            session_id: identity.session,
            service_scope: None,
        })
    }
    pub fn load(root: &Path) -> Result<Self, RacpError> {
        let raw = racp_core::read_bounded(&root.join("native-endpoint.json"), 4096, false)?;
        reject_duplicate_keys(&raw)?;
        let endpoint: Self = serde_json::from_slice(&raw)?;
        let expected = Self::for_root(root)?;
        if endpoint.pipe_name != expected.pipe_name
            || endpoint.user_sid != expected.user_sid
            || endpoint.session_id != expected.session_id
            || endpoint.server_pid == 0
            || endpoint.service_scope.is_some()
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(endpoint)
    }
    pub fn for_service(root: &Path, scope: ServiceScope) -> Result<Self, RacpError> {
        scope.validate()?;
        let root = racp_core::validate_local_path(root)?;
        let peer = PinnedPeer::open(std::process::id())?;
        let identity = peer.identity();
        if scope.service_sid != racp_core::service_directory::expected_service_sid()?
            || identity.sid != scope.service_sid
            || identity.session != 0
            || identity.administrator
            || !identity.service_sids.contains(&scope.service_sid)
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        Ok(Self {
            pipe_name: format!(
                r"\\.\pipe\RACP.Native.Service.{}",
                &digest(root.to_string_lossy().as_bytes())[..32]
            ),
            server_pid: identity.pid,
            server_created: identity.created,
            user_sid: identity.sid.clone(),
            session_id: 0,
            service_scope: Some(scope),
        })
    }
    /// Explicit shared control directory, never the service credential/state root.
    pub fn load_service(root: &Path) -> Result<Self, RacpError> {
        let raw = racp_core::read_bounded(&root.join("native-endpoint.json"), 16384, false)?;
        reject_duplicate_keys(&raw)?;
        let endpoint: Self = serde_json::from_slice(&raw)?;
        let scope = endpoint
            .service_scope
            .as_ref()
            .ok_or_else(|| RacpError::new("SERVICE_SCOPE_INVALID"))?;
        let _pin = racp_core::service_directory::pin(root, scope)?;
        let suffix = endpoint
            .pipe_name
            .strip_prefix(r"\\.\pipe\RACP.Native.Service.")
            .ok_or_else(|| RacpError::new("SERVICE_SCOPE_INVALID"))?;
        if endpoint.user_sid != scope.service_sid
            || endpoint.session_id != 0
            || endpoint.server_pid == 0
            || suffix.len() != 32
            || !suffix.bytes().all(|v| v.is_ascii_hexdigit())
        {
            return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
        }
        Ok(endpoint)
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Challenge {
    protocol: u16,
    nonce: String,
    session_id: u32,
    server_pid: u32,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Proof {
    protocol: u16,
    nonce: String,
    client_nonce: String,
}

async fn read_raw<S: AsyncRead + Unpin>(socket: &mut S) -> Result<Vec<u8>, RacpError> {
    let size = socket.read_u32_le().await? as usize;
    if size == 0 || size > MAX_FRAME_BYTES {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let mut raw = vec![0; size];
    socket.read_exact(&mut raw).await?;
    reject_duplicate_keys(&raw)?;
    Ok(raw)
}
async fn write<S: AsyncWrite + Unpin, T: Serialize>(
    socket: &mut S,
    value: &T,
) -> Result<(), RacpError> {
    let raw = encode(value)?;
    socket.write_u32_le(raw.len() as u32).await?;
    socket.write_all(&raw).await?;
    Ok(())
}
async fn read<S: AsyncRead + Unpin, T: serde::de::DeserializeOwned>(
    socket: &mut S,
) -> Result<T, RacpError> {
    Ok(serde_json::from_slice(&read_raw(socket).await?)?)
}
fn nonce() -> String {
    let bytes: [u8; 32] = rand::random();
    bytes.iter().map(|v| format!("{v:02x}")).collect()
}
fn valid_nonce(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|v| v.is_ascii_hexdigit())
}
fn server(endpoint: &LocalEndpoint, first: bool) -> Result<NamedPipeServer, RacpError> {
    let mut sddl = format!("D:P(A;;GA;;;{})", endpoint.user_sid);
    if let Some(scope) = &endpoint.service_scope {
        scope.validate()?;
        // FILE_WRITE_DATA only: GENERIC_WRITE also grants FILE_CREATE_PIPE_INSTANCE.
        for user in &scope.controllers {
            sddl.push_str(&format!("(A;;0x0012008b;;;{})", user.user_sid));
        }
    }
    let descriptor: Vec<u16> = sddl.encode_utf16().chain(Some(0)).collect();
    let mut security = std::ptr::null_mut();
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            descriptor.as_ptr(),
            SDDL_REVISION_1,
            &mut security,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: security,
        bInheritHandle: 0,
    };
    let result = unsafe {
        ServerOptions::new()
            .first_pipe_instance(first)
            .reject_remote_clients(true)
            .max_instances(MAX_IN_FLIGHT + 1)
            .in_buffer_size(MAX_FRAME_BYTES as u32)
            .out_buffer_size(MAX_FRAME_BYTES as u32)
            .create_with_security_attributes_raw(
                &endpoint.pipe_name,
                &attributes as *const _ as *mut c_void,
            )
    };
    unsafe {
        LocalFree(security);
    }
    Ok(result?)
}
fn same_scope(peer: &PinnedPeer, endpoint: &LocalEndpoint) -> bool {
    peer.identity().sid == endpoint.user_sid && peer.identity().session == endpoint.session_id
}
fn client_role(peer: &PinnedPeer, endpoint: &LocalEndpoint) -> Option<ControllerRole> {
    match &endpoint.service_scope {
        Some(scope) => scope.role(&peer.identity().sid, peer.identity().session),
        None => same_scope(peer, endpoint).then_some(ControllerRole::Controller),
    }
}
fn server_scope(peer: &PinnedPeer, endpoint: &LocalEndpoint) -> bool {
    match &endpoint.service_scope {
        Some(scope) => {
            scope.validate().is_ok()
                && racp_core::service_directory::expected_service_sid()
                    .is_ok_and(|sid| sid == scope.service_sid)
                && peer.identity().sid == scope.service_sid
                && endpoint.user_sid == scope.service_sid
                && peer.identity().session == 0
                && endpoint.session_id == 0
                && !peer.identity().administrator
                && peer.identity().service_sids.contains(&scope.service_sid)
        }
        None => same_scope(peer, endpoint),
    }
}
async fn authenticate_server(
    socket: &mut NamedPipeServer,
    endpoint: &LocalEndpoint,
) -> Result<(PinnedPeer, ControllerRole), RacpError> {
    let mut pid = 0;
    if unsafe { GetNamedPipeClientProcessId(socket.as_raw_handle(), &mut pid) } == 0 {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    let peer = PinnedPeer::open(pid)?;
    let role = client_role(&peer, endpoint).ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?;
    let challenge = Challenge {
        protocol: PROTOCOL,
        nonce: nonce(),
        session_id: endpoint.session_id,
        server_pid: endpoint.server_pid,
    };
    write(socket, &challenge).await?;
    let proof: Proof = read(socket).await?;
    if proof.protocol != PROTOCOL
        || proof.nonce != challenge.nonce
        || !valid_nonce(&proof.client_nonce)
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    // Token impersonation is synchronous and reverted before the next await.
    let (sid, session) = client_scope(socket.as_raw_handle())?;
    if sid != peer.identity().sid || session != peer.identity().session {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    peer.alive()?;
    write(socket, &proof).await?;
    Ok((peer, role))
}
pub struct ControlSession {
    socket: NamedPipeClient,
    peer: PinnedPeer,
    valid: bool,
    role: ControllerRole,
}
fn open_client(endpoint: &LocalEndpoint) -> Result<NamedPipeClient, RacpError> {
    if endpoint.service_scope.is_none() {
        return Ok(ClientOptions::new().open(&endpoint.pipe_name)?);
    }
    use std::os::windows::{fs::OpenOptionsExt, io::IntoRawHandle};
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_FLAG_OVERLAPPED, FILE_GENERIC_READ, FILE_WRITE_DATA, SECURITY_IDENTIFICATION,
        SECURITY_SQOS_PRESENT,
    };
    let file = std::fs::OpenOptions::new()
        .access_mode(FILE_GENERIC_READ | FILE_WRITE_DATA)
        .share_mode(0)
        .custom_flags(FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION)
        .open(&endpoint.pipe_name)?;
    Ok(unsafe { NamedPipeClient::from_raw_handle(file.into_raw_handle()) }?)
}
pub async fn connect(endpoint: &LocalEndpoint) -> Result<ControlSession, RacpError> {
    tokio::time::timeout(
        Duration::from_millis(MAX_RECEIPT_TIMEOUT_MS.into()),
        async {
            let own = PinnedPeer::open(std::process::id())?;
            if client_role(&own, endpoint).is_none()
                || !endpoint.pipe_name.starts_with(r"\\.\pipe\RACP.Native.")
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            let mut socket = open_client(endpoint)?;
            let mut pid = 0;
            if unsafe { GetNamedPipeServerProcessId(socket.as_raw_handle(), &mut pid) } == 0
                || pid != endpoint.server_pid
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            let peer = PinnedPeer::open(pid)?;
            if !server_scope(&peer, endpoint) || peer.identity().created != endpoint.server_created
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            let challenge: Challenge = read(&mut socket).await?;
            if challenge.protocol != PROTOCOL
                || challenge.server_pid != pid
                || challenge.session_id != endpoint.session_id
                || !valid_nonce(&challenge.nonce)
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            let proof = Proof {
                protocol: PROTOCOL,
                nonce: challenge.nonce,
                client_nonce: nonce(),
            };
            write(&mut socket, &proof).await?;
            let receipt: Proof = read(&mut socket).await?;
            if receipt.protocol != proof.protocol
                || receipt.nonce != proof.nonce
                || receipt.client_nonce != proof.client_nonce
            {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            peer.alive()?;
            Ok(ControlSession {
                socket,
                peer,
                valid: true,
                role: client_role(&own, endpoint)
                    .ok_or_else(|| RacpError::new("PERMISSION_DENIED"))?,
            })
        },
    )
    .await
    .map_err(|_| RacpError::new("REQUEST_TIMEOUT"))?
}
impl ControlSession {
    pub fn role(&self) -> ControllerRole {
        self.role
    }
    pub async fn request(&mut self, request: &ControlRequest) -> Result<ControlReply, RacpError> {
        if !self.valid {
            return Err(RacpError::new("LOCAL_IPC_SESSION_CLOSED"));
        }
        request.validate()?;
        self.peer.alive()?;
        let result =
            tokio::time::timeout(Duration::from_millis(request.deadline_ms.into()), async {
                write(&mut self.socket, request).await?;
                let reply: ControlReply = read(&mut self.socket).await?;
                if reply.request_id != request.request_id {
                    return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
                }
                if let ControlResult::Snapshot { role, .. }
                | ControlResult::Subscribed { role, .. } = &reply.result
                {
                    if *role != self.role {
                        return Err(RacpError::new("SERVICE_SCOPE_INVALID"));
                    }
                }
                self.peer.alive()?;
                Ok(reply)
            })
            .await
            .map_err(|_| RacpError::new("REQUEST_TIMEOUT"))
            .and_then(|result| result);
        if result.is_err() {
            self.valid = false;
        }
        result
    }
    pub async fn next_event(&mut self) -> Result<StateEvent, RacpError> {
        if !self.valid {
            return Err(RacpError::new("LOCAL_IPC_SESSION_CLOSED"));
        }
        self.peer.alive()?;
        let event = read(&mut self.socket).await?;
        self.peer.alive()?;
        Ok(event)
    }
}
async fn accepted(
    mut socket: NamedPipeServer,
    endpoint: LocalEndpoint,
    hub: StateHub,
    shutdown: CancellationToken,
    controller: super::policy::ControlPlane,
) -> Result<(), RacpError> {
    let (peer, role) = tokio::time::timeout(
        Duration::from_secs(5),
        authenticate_server(&mut socket, &endpoint),
    )
    .await
    .map_err(|_| RacpError::new("REQUEST_TIMEOUT"))??;
    loop {
        let raw = tokio::select! { _ = shutdown.cancelled() => return Ok(()), value = tokio::time::timeout(Duration::from_secs(5), read_raw(&mut socket)) => value.map_err(|_| RacpError::new("REQUEST_TIMEOUT"))?? };
        peer.alive()?;
        let request = decode_request(&raw)?;
        let requested_stop = matches!(&request.command, ControlCommand::Stop);
        let mut subscription = None;
        let result =
            if role == ControllerRole::Observer
                && !matches!(
                    &request.command,
                    ControlCommand::Snapshot | ControlCommand::Subscribe { .. }
                )
            {
                ControlResult::Error {
                    code: ControlError::PermissionDenied,
                }
            } else {
                match request.command {
                    command @ (ControlCommand::PrepareDesktop { .. }
                    | ControlCommand::RegisterDesktop { .. }
                    | ControlCommand::RemoveDesktop { .. }) => controller
                        .desktop_session(
                            command,
                            peer.identity().clone(),
                            endpoint.service_scope.as_ref(),
                        )
                        .await
                        .unwrap_or(ControlResult::Error {
                            code: ControlError::PermissionDenied,
                        }),
                    ControlCommand::Snapshot => ControlResult::Snapshot {
                        snapshot: hub.snapshot().await,
                        role,
                    },
                    ControlCommand::Subscribe { .. } => {
                        let sub = hub
                            .subscribe_controller(role == ControllerRole::Controller)
                            .await;
                        let result = ControlResult::Subscribed {
                            snapshot: sub.snapshot.clone(),
                            role,
                        };
                        subscription = Some(sub);
                        result
                    }
                    command => controller.apply(command).await.unwrap_or_else(|error| {
                        ControlResult::Error {
                            code: match error.code.0 {
                                "RESOURCE_EXHAUSTED" => ControlError::ResourceExhausted,
                                "REVISION_CONFLICT" => ControlError::RevisionConflict,
                                "STALE_CONNECTION" => ControlError::StaleConnection,
                                "OPERATION_NOT_FOUND" => ControlError::OperationNotFound,
                                "DEVICE_OFFLINE" => ControlError::DeviceOffline,
                                "REQUEST_INVALID" => ControlError::InvalidRequest,
                                _ => ControlError::PermissionDenied,
                            },
                        }
                    }),
                }
            };
        let stop_receipt = if requested_stop {
            if let ControlResult::Accepted { receipt_id } = &result {
                Some(receipt_id.clone())
            } else {
                None
            }
        } else {
            None
        };
        tokio::time::timeout(
            Duration::from_millis(request.deadline_ms.into()),
            write(
                &mut socket,
                &ControlReply {
                    request_id: request.request_id,
                    result,
                },
            ),
        )
        .await
        .map_err(|_| RacpError::new("REQUEST_TIMEOUT"))??;
        if let Some(receipt) = stop_receipt {
            controller.stop_after_receipt(receipt).await;
        }
        if let Some(mut sub) = subscription {
            loop {
                let event = tokio::select! { _ = shutdown.cancelled() => return Ok(()), event = sub.next() => event? };
                peer.alive()?;
                // Closing on overflow/write deadline forces a fresh snapshot on reconnect.
                tokio::time::timeout(Duration::from_secs(5), write(&mut socket, &event))
                    .await
                    .map_err(|_| RacpError::new("REQUEST_TIMEOUT"))??;
            }
        }
    }
}
pub async fn serve(
    root: &Path,
    hub: StateHub,
    shutdown: CancellationToken,
    controller: super::policy::ControlPlane,
) -> Result<(), RacpError> {
    let endpoint = LocalEndpoint::for_root(root)?;
    serve_endpoint(root, endpoint, hub, shutdown, controller, None).await
}
pub async fn serve_endpoint(
    root: &Path,
    endpoint: LocalEndpoint,
    hub: StateHub,
    shutdown: CancellationToken,
    controller: super::policy::ControlPlane,
    ready: Option<tokio::sync::oneshot::Sender<()>>,
) -> Result<(), RacpError> {
    let _lock = racp_core::InstanceLock::acquire(&root.join("native-ipc.lock"))?;
    let mut listener = server(&endpoint, true)?;
    racp_core::atomic_write(
        &root.join("native-endpoint.json"),
        &serde_json::to_vec(&endpoint)?,
        true,
    )?;
    if let Some(ready) = ready {
        let _ = ready.send(());
    }
    let limit = Arc::new(Semaphore::new(MAX_IN_FLIGHT));
    let mut clients = tokio::task::JoinSet::new();
    let result = async {
        loop {
            tokio::select! { _ = shutdown.cancelled() => break, result = listener.connect() => result? }
            if let Ok(permit) = limit.clone().try_acquire_owned() {
                let next = server(&endpoint, false)?;
                let connected = std::mem::replace(&mut listener, next);
                let endpoint = endpoint.clone(); let hub = hub.clone(); let shutdown = shutdown.clone();
                let controller=controller.clone();
                clients.spawn(async move { let _permit = permit; let _ = accepted(connected, endpoint, hub, shutdown,controller).await; });
            } else {
                // Reuse the listener under overload instead of exceeding the pipe instance limit.
                listener.disconnect()?;
            }
            while clients.try_join_next().is_some() {}
        }
        Ok::<(), RacpError>(())
    }.await;
    // Dropping/aborting a pipe task cancels its owned overlapped I/O; no external process is stopped.
    clients.abort_all();
    while clients.join_next().await.is_some() {}
    let _ = std::fs::remove_file(root.join("native-endpoint.json"));
    result
}
