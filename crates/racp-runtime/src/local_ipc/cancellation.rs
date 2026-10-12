//! Same-user controller cancellation of this Agent's current-boot execution only.
use crate::Agent;
use racp_contract::{local_ipc::StateChange, RacpError};
use racp_core::{Journal, Record};
use std::{sync::atomic::Ordering, time::Duration};

fn bound(agent: &Agent, operation_id: &str, request_id: &str) -> Result<Record, RacpError> {
    let record = agent.journal.get(operation_id)?;
    if record.request["device_id"] != agent.settings.device_id
        || record.request["agent_boot_id"] != agent.boot_id
        || record.request["request_id"] != request_id
    {
        return Err(RacpError::new("STALE_CONNECTION"));
    }
    Ok(record)
}

/// Queued work rechecks authority after receipt. Keep policy/stop serialization only
/// through the cancellation signal; waiting for cleanup must not block Agent stop.
pub(super) async fn observe(
    agent: &Agent,
    boot_id: &str,
    operation_id: &str,
    request_id: &str,
    receipt_id: String,
) -> StateChange {
    let mut cancel_requested = false;
    let work = async {
        let serial = agent.policy_updates.lock().await;
        if agent.stopping.load(Ordering::Acquire) || boot_id != agent.boot_id {
            return Err(RacpError::new("STALE_CONNECTION"));
        }
        let record = bound(agent, operation_id, request_id)?;
        agent
            .journal
            .local_cancel_intent(operation_id, &receipt_id)?;
        if !Journal::terminal(&record.state) {
            let task = agent.tasks.lock().await.get(operation_id).cloned();
            if let Some(task) = task {
                let accepted = task
                    .request_cancel(&agent.journal, operation_id, "local_owner")
                    .await?;
                cancel_requested = !Journal::terminal(&accepted.state);
                agent.publish_native_activity(operation_id).await;
            } else if !Journal::terminal(&bound(agent, operation_id, request_id)?.state) {
                return Err(RacpError::new("EXECUTION_UNKNOWN"));
            }
        }
        drop(serial);
        // Durable terminal state can precede execution resource release. Observe both.
        let wait = async {
            loop {
                let changed = agent.completed.notified();
                let record = bound(agent, operation_id, request_id)?;
                let active = agent.tasks.lock().await.contains_key(operation_id);
                if !active && Journal::terminal(&record.state) {
                    return Ok::<_, RacpError>(record);
                }
                changed.await;
            }
        };
        tokio::time::timeout(Duration::from_secs(15), wait)
            .await
            .map_err(|_| RacpError::new("CANCEL_OBSERVATION_TIMEOUT"))?
    };
    let result = work.await;
    let error_code = result.as_ref().err().map(|error| error.code.0.to_owned());
    // Never include payloads, command arguments, results, credentials or OS diagnostics.
    let observed = result.ok().or_else(|| {
        (boot_id == agent.boot_id)
            .then(|| bound(agent, operation_id, request_id).ok())
            .flatten()
    });
    let state = observed
        .as_ref()
        .map(|record| record.state.as_str())
        .unwrap_or("UNKNOWN");
    let terminal = observed
        .as_ref()
        .is_some_and(|record| Journal::terminal(&record.state));
    let cleanup_status =
        if observed.is_some() && agent.tasks.lock().await.contains_key(operation_id) {
            // A durable result is not proof that the execution released its resources.
            "pending"
        } else {
            observed.as_ref().map(cleanup).unwrap_or("unknown")
        };
    StateChange::CancelFinished {
        receipt_id,
        operation_id: operation_id.to_owned(),
        cancel_requested,
        state: state.to_owned(),
        terminal,
        cleanup_status: cleanup_status.to_owned(),
        error_code,
    }
}

fn cleanup(record: &Record) -> &str {
    let reported = record
        .result
        .as_ref()
        .and_then(|value| value["cleanup_status"].as_str())
        .or_else(|| {
            record
                .error
                .as_ref()
                .and_then(|value| value["details"]["cleanup_status"].as_str())
        });
    match reported {
        Some("complete" | "partial" | "unknown" | "quarantined") => reported.unwrap(),
        _ if Journal::terminal(&record.state) => "not_reported",
        _ => "pending",
    }
}
