//! Constraint drafts stay text until saved; shared Agent validation decides admissibility.
use racp_contract::{local_ipc::PolicyConstraints, RacpError};

/// Parses constraint draft text into a policy value and runs the shared permission validation.
pub fn parse_fields(
    workspaces: &str,
    timeout: &str,
    output: &str,
    allow: &str,
    deny: &str,
    strict: bool,
) -> Result<PolicyConstraints, RacpError> {
    let lines = |value: &str| -> Result<Vec<String>, RacpError> {
        // Bound drafts before constructing the IPC frame. Preserve path spaces.
        if value.len() > 16 * 1024 {
            return Err(RacpError::new("PERMISSIONS_INVALID"));
        }
        let mut result = Vec::new();
        for line in value.lines().map(str::trim).filter(|line| !line.is_empty()) {
            if !result.iter().any(|item| item == line) {
                result.push(line.to_owned());
            }
        }
        Ok(result)
    };
    let number = |value: &str| {
        value
            .trim()
            .parse::<u64>()
            .map_err(|_| RacpError::new("PERMISSIONS_INVALID"))
    };
    let constraints = PolicyConstraints {
        workspace_ids: lines(workspaces)?,
        max_timeout_ms: number(timeout)?,
        max_output_bytes: number(output)?,
        executable_allowlist: lines(allow)?,
        executable_denylist: lines(deny)?,
        strict_os_isolation: strict,
    };
    let mut policy = racp_core::state_schema::NativePolicy::deny_all();
    policy.constraints = constraints.clone();
    policy.permissions()?;
    Ok(constraints)
}
