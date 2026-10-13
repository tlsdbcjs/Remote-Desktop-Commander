//! Toolkit-neutral authenticated local IPC. Both GUI frontends adapt these futures.
use racp_contract::local_ipc::{
    ControlCommand, ControlReply, ControlRequest, ControlResult, ExecutionScope, StateEvent,
    StateSnapshot, PROTOCOL,
};
use racp_contract::service_scope::ControllerRole;
use std::future::Future;

/// One item of the Agent state feed.
#[derive(Clone, Debug)]
pub enum Feed {
    Snapshot(StateSnapshot, ControllerRole),
    Event(StateEvent),
    Disconnected,
}

/// Sends one control command over a fresh authenticated session.
pub async fn request(command: ControlCommand) -> Result<ControlReply, String> {
    // Filesystem/OS identity queries run away from the UI event loop.
    let endpoint = tokio::task::spawn_blocking(crate::control_target::endpoint)
        .await
        .map_err(|_| "LOCAL_STATE_FAILED".to_owned())?
        .map_err(|e| e.to_string())?;
    let mut session = racp_runtime::local_ipc::connect(&endpoint)
        .await
        .map_err(|e| e.to_string())?;
    session
        .request(&ControlRequest {
            protocol: PROTOCOL,
            request_id: racp_contract::new_id("ui"),
            deadline_ms: 5_000,
            command,
        })
        .await
        .map_err(|e| e.to_string())
}

/// Subscribes to the Agent state feed until `emit` returns false.
/// Only disconnected sessions retry. Healthy sessions wait for events without polling.
pub async fn subscribe<F, Fut>(mut emit: F)
where
    F: FnMut(Feed) -> Fut,
    Fut: Future<Output = bool>,
{
    let mut delay = std::time::Duration::from_millis(500);
    loop {
        let mut open = true;
        let result: Result<(), racp_contract::RacpError> = async {
            let endpoint = tokio::task::spawn_blocking(crate::control_target::endpoint)
                .await
                .map_err(|_| racp_contract::RacpError::new("LOCAL_STATE_FAILED"))??;
            let mut session = racp_runtime::local_ipc::connect(&endpoint).await?;
            let reply = session
                .request(&ControlRequest {
                    protocol: PROTOCOL,
                    request_id: racp_contract::new_id("ui"),
                    deadline_ms: 5_000,
                    command: ControlCommand::Subscribe {
                        boot_id: "resync".into(),
                        after_sequence: 0,
                    },
                })
                .await?;
            let ControlResult::Subscribed { snapshot, role } = reply.result else {
                return Err(racp_contract::RacpError::new("RUNTIME_RESPONSE_INVALID"));
            };
            if crate::control_target::service()
                && !snapshot.identity.as_ref().is_some_and(|identity| {
                    matches!(identity.execution_scope, ExecutionScope::Service)
                })
            {
                return Err(racp_contract::RacpError::new("SERVICE_SCOPE_INVALID"));
            }
            let mut boot_id = snapshot.boot_id.clone();
            let mut sequence = snapshot.sequence;
            if !emit(Feed::Snapshot(snapshot, role)).await {
                open = false;
                return Err(racp_contract::RacpError::new("CANCELLED"));
            }
            loop {
                let event = session.next_event().await?;
                if event.boot_id != boot_id || sequence.checked_add(1) != Some(event.sequence) {
                    return Err(racp_contract::RacpError::new("RESYNC_REQUIRED"));
                }
                boot_id.clone_from(&event.boot_id);
                sequence = event.sequence;
                if !emit(Feed::Event(event)).await {
                    open = false;
                    return Err(racp_contract::RacpError::new("CANCELLED"));
                }
            }
        }
        .await;
        if !open || result.is_err() && !emit(Feed::Disconnected).await {
            break;
        }
        tokio::time::sleep(delay).await;
        delay = (delay * 2).min(std::time::Duration::from_secs(10));
    }
}
