//! Editable text remains a draft; shared Agent validation decides admissibility.
use racp_contract::{local_ipc::PolicyConstraints, RacpError};

#[derive(Clone, Debug)]
pub enum Field {
    Workspaces,
    Timeout,
    Output,
    AllowExecutables,
    DenyExecutables,
}
#[derive(Debug, Default)]
pub struct Draft {
    pub workspaces: iced::widget::text_editor::Content,
    pub timeout: String,
    pub output: String,
    pub allow: iced::widget::text_editor::Content,
    pub deny: iced::widget::text_editor::Content,
    pub strict: bool,
    pub revision: Option<u64>,
    pub dirty: bool,
}
impl Draft {
    pub fn new() -> Self {
        let mut draft = Self::default();
        draft.replace(
            &racp_core::state_schema::NativePolicy::deny_all().constraints,
            0,
        );
        draft
    }
    pub fn replace(&mut self, value: &PolicyConstraints, revision: u64) {
        *self = Self {
            workspaces: iced::widget::text_editor::Content::with_text(
                &value.workspace_ids.join("\n"),
            ),
            timeout: value.max_timeout_ms.to_string(),
            output: value.max_output_bytes.to_string(),
            allow: iced::widget::text_editor::Content::with_text(
                &value.executable_allowlist.join("\n"),
            ),
            deny: iced::widget::text_editor::Content::with_text(
                &value.executable_denylist.join("\n"),
            ),
            strict: value.strict_os_isolation,
            revision: Some(revision),
            dirty: false,
        };
    }
    pub fn edit(&mut self, field: Field, value: String) {
        match field {
            Field::Timeout => self.timeout = value,
            Field::Output => self.output = value,
            Field::Workspaces => {
                self.workspaces = iced::widget::text_editor::Content::with_text(&value)
            }
            Field::AllowExecutables => {
                self.allow = iced::widget::text_editor::Content::with_text(&value)
            }
            Field::DenyExecutables => {
                self.deny = iced::widget::text_editor::Content::with_text(&value)
            }
        }
        self.dirty = true;
    }
    pub fn edit_list(&mut self, field: Field, action: iced::widget::text_editor::Action) {
        let content = match field {
            Field::Workspaces => &mut self.workspaces,
            Field::AllowExecutables => &mut self.allow,
            Field::DenyExecutables => &mut self.deny,
            _ => return,
        };
        if action.is_edit() {
            self.dirty = true;
        }
        content.perform(action);
    }
    pub fn parse(&self) -> Result<PolicyConstraints, RacpError> {
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
            workspace_ids: lines(&self.workspaces.text())?,
            max_timeout_ms: number(&self.timeout)?,
            max_output_bytes: number(&self.output)?,
            executable_allowlist: lines(&self.allow.text())?,
            executable_denylist: lines(&self.deny.text())?,
            strict_os_isolation: self.strict,
        };
        let mut policy = racp_core::state_schema::NativePolicy::deny_all();
        policy.constraints = constraints.clone();
        policy.permissions()?;
        Ok(constraints)
    }
}
