//! Headless consumer of the authenticated IPC contract; no alternate control transport.
use racp_contract::{local_ipc::*, RacpError};
use std::path::Path;

pub async fn run(root: &Path, service: bool, raw: &[u8]) -> Result<(), RacpError> {
    reject_duplicate_keys(raw)?;
    let request: ControlRequest = serde_json::from_slice(raw)?;
    request.validate()?;
    // Desktop seed/registration is owned by the existing session bridge.
    if !matches!(
        request.command,
        ControlCommand::Snapshot
            | ControlCommand::Subscribe { .. }
            | ControlCommand::UpdatePolicy { .. }
            | ControlCommand::DecideApproval { .. }
            | ControlCommand::CancelOperation { .. }
            | ControlCommand::Stop
    ) {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    #[cfg(windows)]
    {
        use racp_runtime::local_ipc::{connect, LocalEndpoint};
        use std::{io::Write, time::Duration};
        let endpoint = if service {
            LocalEndpoint::load_service(root)?
        } else {
            LocalEndpoint::load(root)?
        };
        let mut session = connect(&endpoint).await?;
        let reply = session.request(&request).await?;
        fn emit(value: &serde_json::Value) -> Result<(), RacpError> {
            let stdout = std::io::stdout();
            let mut output = stdout.lock();
            serde_json::to_writer(&mut output, value)?;
            output.write_all(b"\n")?;
            output.flush()?;
            Ok(())
        }
        emit(&serde_json::to_value(&reply)?)?;
        if matches!(reply.result, ControlResult::Subscribed { .. }) {
            let until = tokio::time::Instant::now() + Duration::from_secs(5);
            for _ in 0..EVENT_QUEUE_CAPACITY {
                match tokio::time::timeout_at(until, session.next_event()).await {
                    Ok(event) => emit(&serde_json::to_value(event?)?)?,
                    Err(_) => break,
                }
            }
        }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = (root, service);
        Err(RacpError::new("LOCAL_IPC_NOT_IMPLEMENTED"))
    }
}
