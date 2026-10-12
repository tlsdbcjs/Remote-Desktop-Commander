use racp_contract::RacpError;
use serde_json::{json, Value};
use std::collections::VecDeque;

pub(super) struct DiagnosticRecord {
    pub page: String,
    pub frame: String,
    pub revision: u64,
    pub origin: String,
    pub kind: String,
    pub sequence: u64,
    pub data: Value,
}
#[derive(Default)]
pub(super) struct DiagnosticBuffer {
    pub records: VecDeque<DiagnosticRecord>,
    pub sequence: u64,
    pub gap: u64,
}
impl DiagnosticBuffer {
    pub fn push(&mut self, mut record: DiagnosticRecord) {
        if self.sequence >= i64::MAX as u64 {
            return;
        }
        self.sequence += 1;
        record.sequence = self.sequence;
        if record.data.to_string().len() > 8192 {
            self.gap = self.sequence;
            return;
        }
        if self.records.len() >= 256 {
            if let Some(old) = self.records.pop_front() {
                self.gap = self.gap.max(old.sequence);
            }
        }
        self.records.push_back(record);
    }
    pub fn clear(&mut self) {
        self.records.clear();
        self.gap = self.sequence;
    }
}
/// Connection-independent metadata retained until a Gateway ACK confirms receipt.
pub struct BrowserOutbox {
    instance: String,
    limit: usize,
    sequence: u64,
    sent: u64,
    acked: u64,
    pending: VecDeque<Value>,
    capability: Option<Value>,
}
impl BrowserOutbox {
    pub fn new(instance: &str, limit: usize) -> Self {
        Self {
            instance: instance.into(),
            limit: limit.clamp(1, 128),
            sequence: 0,
            sent: 0,
            acked: 0,
            pending: VecDeque::new(),
            capability: None,
        }
    }
    pub fn push(&mut self, mut event: Value, inventory: Vec<Value>) {
        // This bound is shared with the SQLite Gateway sequence representation.
        if self.sequence == i64::MAX as u64 {
            return;
        }
        self.sequence += 1;
        if let Some(capability) = event.get("capability") {
            self.capability = Some(capability.clone());
        }
        if self.pending.len() >= self.limit {
            self.pending.pop_front();
            event = json!({"kind":"gap","state":"refresh_required","handles":inventory});
            if let Some(capability) = &self.capability {
                event["capability"] = capability.clone();
            }
        }
        event["provider_instance_id"] = json!(self.instance);
        event["event_sequence"] = json!(self.sequence.to_string());
        self.pending.push_back(event);
    }
    pub fn cursor(&self) -> String {
        self.acked.to_string()
    }
    pub fn pending(&self, after: u64) -> Vec<Value> {
        self.pending
            .iter()
            .filter(|e| {
                e["event_sequence"]
                    .as_str()
                    .and_then(|s| s.parse::<u64>().ok())
                    .is_some_and(|n| n > after.max(self.acked))
            })
            .take(16)
            .cloned()
            .collect()
    }
    pub fn sent(&mut self, sequence: &str) -> Result<(), RacpError> {
        let sequence = sequence
            .parse::<u64>()
            .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
        if sequence > self.sequence {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.sent = self.sent.max(sequence);
        Ok(())
    }
    pub fn ack(&mut self, sequence: &str) -> Result<(), RacpError> {
        let sequence = sequence
            .parse::<u64>()
            .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
        if sequence > self.sent {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.acked = self.acked.max(sequence);
        while self.pending.front().is_some_and(|e| {
            e["event_sequence"]
                .as_str()
                .and_then(|n| n.parse::<u64>().ok())
                .is_some_and(|n| n <= self.acked)
        }) {
            self.pending.pop_front();
        }
        Ok(())
    }
}
