//! OS-free inventory generations and lease/IO bookkeeping. Reserving a lease
//! does not open hardware. Only a policy-authorized Provider may perform effects.
use racp_contract::{hardware::*, new_id, RacpError};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::{Duration, Instant},
};

/// Private backend locators; deliberately not serializable to remote callers.
#[derive(Clone)]
pub struct ObservedInterface {
    pub device_key: String,
    pub interface_key: String,
    pub vendor_id: Option<u16>,
    pub product_id: Option<u16>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HardwareInterface {
    pub target: HardwareTarget,
    pub vendor_id: Option<u16>,
    pub product_id: Option<u16>,
}
pub struct HardwareInventory {
    agent: String,
    boot: String,
    interfaces: BTreeMap<(String, String), HardwareInterface>,
}
impl HardwareInventory {
    pub fn new(agent: &str, boot: &str) -> Result<Self, RacpError> {
        if !identifier(agent) || !identifier(boot) {
            return Err(RacpError::new("HARDWARE_TARGET_INVALID"));
        }
        Ok(Self {
            agent: agent.into(),
            boot: boot.into(),
            interfaces: BTreeMap::new(),
        })
    }
    /// Publish only a complete, bounded backend snapshot. Partial/failed scans
    /// must not call this. Lost hotplug continuity requires invalidate().
    pub fn refresh(
        &mut self,
        observations: Vec<ObservedInterface>,
    ) -> Result<Vec<HardwareInterface>, RacpError> {
        let mut seen = BTreeSet::new();
        if observations.len() > MAX_HARDWARE_INTERFACES {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        for row in &observations {
            if [&row.device_key, &row.interface_key]
                .into_iter()
                .any(|s| s.is_empty() || s.len() > 8192 || s.chars().any(char::is_control))
                || !seen.insert((&row.device_key, &row.interface_key))
            {
                return Err(RacpError::new("HARDWARE_SNAPSHOT_INVALID"));
            }
        }
        let changed: BTreeSet<_> = observations
            .iter()
            .filter(|row| {
                self.interfaces
                    .get(&(row.device_key.clone(), row.interface_key.clone()))
                    .is_some_and(|old| {
                        old.vendor_id != row.vendor_id || old.product_id != row.product_id
                    })
            })
            .map(|row| row.device_key.clone())
            .collect();
        let mut devices = BTreeMap::new();
        for ((device, _), row) in &self.interfaces {
            if changed.contains(device) {
                continue;
            }
            devices.insert(
                device.clone(),
                (
                    row.target.hardware_id.clone(),
                    row.target.connection_generation.clone(),
                ),
            );
        }
        let mut next = BTreeMap::new();
        for row in observations {
            let key = (row.device_key.clone(), row.interface_key.clone());
            let (hardware_id, generation) = devices
                .entry(row.device_key)
                .or_insert_with(|| (new_id("hardware"), new_id("connection")));
            let target = self
                .interfaces
                .get(&key)
                .filter(|_| !changed.contains(&key.0))
                .map(|row| row.target.clone())
                .unwrap_or_else(|| HardwareTarget {
                    agent_device_id: self.agent.clone(),
                    agent_boot_id: self.boot.clone(),
                    hardware_id: hardware_id.clone(),
                    interface_id: new_id("hwif"),
                    connection_generation: generation.clone(),
                });
            next.insert(
                key,
                HardwareInterface {
                    target,
                    vendor_id: row.vendor_id,
                    product_id: row.product_id,
                },
            );
        }
        self.interfaces = next;
        Ok(self.interfaces.values().cloned().collect())
    }
    pub fn invalidate(&mut self) {
        self.interfaces.clear();
    }
    pub fn is_current(&self, target: &HardwareTarget) -> bool {
        target.validate().is_ok() && self.interfaces.values().any(|row| row.target == *target)
    }
}
struct PendingIo {
    budget: HardwareIoBudget,
    plan: HardwareReadPlan,
    operation_id: String,
    deadline: Instant,
    cancelled: bool,
}
/// Only a timely, uncancelled, scoped adapter completion can create this proof.
/// No Deserialize/constructor: remote input cannot manufacture a collection.
#[derive(Debug)]
pub struct HardwareIoReceipt {
    caller: HardwareCaller,
    lease: HardwareLease,
    io_id: String,
    bytes: u64,
    plan: HardwareReadPlan,
    operation_id: String,
}
impl HardwareIoReceipt {
    pub fn target(&self) -> &HardwareTarget {
        &self.lease.target
    }
    pub fn io_id(&self) -> &str {
        &self.io_id
    }
    pub fn byte_count(&self) -> u64 {
        self.bytes
    }
    pub fn caller(&self) -> &HardwareCaller {
        &self.caller
    }
    pub fn read_plan(&self) -> &HardwareReadPlan {
        &self.plan
    }
    pub fn operation_id(&self) -> &str {
        &self.operation_id
    }
}
struct Session {
    caller: HardwareCaller,
    lease: HardwareLease,
    phase: HardwarePhase,
    expires: Instant,
    pending: BTreeMap<String, PendingIo>,
    consumed: u64,
}
pub struct HardwareSessions {
    agent: String,
    boot: String,
    accepting: bool,
    sessions: BTreeMap<String, Session>,
}
impl HardwareSessions {
    pub fn new(agent: &str, boot: &str) -> Result<Self, RacpError> {
        HardwareInventory::new(agent, boot)?;
        Ok(Self {
            agent: agent.into(),
            boot: boot.into(),
            accepting: true,
            sessions: BTreeMap::new(),
        })
    }
    pub fn reserve(
        &mut self,
        caller: HardwareCaller,
        target: HardwareTarget,
        inventory: &HardwareInventory,
        lease_ms: u64,
        now: Instant,
    ) -> Result<HardwareLease, RacpError> {
        caller.validate()?;
        target.validate()?;
        if !self.accepting {
            return Err(RacpError::new("HARDWARE_STOPPING"));
        }
        if caller.agent_device_id != self.agent
            || caller.agent_boot_id != self.boot
            || target.agent_device_id != self.agent
            || target.agent_boot_id != self.boot
        {
            return Err(RacpError::new("HARDWARE_SCOPE_MISMATCH"));
        }
        if !inventory.is_current(&target) {
            return Err(RacpError::new("HARDWARE_CONNECTION_STALE"));
        }
        if !(1..=MAX_HARDWARE_LEASE_MS).contains(&lease_ms) {
            return Err(RacpError::new("HARDWARE_LEASE_INVALID"));
        }
        self.sessions
            .retain(|_, s| s.phase != HardwarePhase::Closed);
        if self.sessions.len() >= MAX_HARDWARE_SESSIONS
            || self
                .sessions
                .values()
                .filter(|s| s.caller.principal_id == caller.principal_id)
                .count()
                >= 4
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let lease = HardwareLease {
            session_id: new_id("hwsession"),
            lease_id: new_id("hwlease"),
            target,
        };
        self.sessions.insert(
            lease.session_id.clone(),
            Session {
                caller,
                lease: lease.clone(),
                phase: HardwarePhase::Reserved,
                expires: now + Duration::from_millis(lease_ms),
                pending: BTreeMap::new(),
                consumed: 0,
            },
        );
        Ok(lease)
    }
    fn checked(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
    ) -> Result<&mut Session, RacpError> {
        caller.validate()?;
        lease.validate()?;
        let session = self
            .sessions
            .get_mut(&lease.session_id)
            .ok_or_else(|| RacpError::new("HARDWARE_LEASE_STALE"))?;
        if session.caller != *caller || session.lease != *lease {
            return Err(RacpError::new("HARDWARE_SCOPE_MISMATCH"));
        }
        Ok(session)
    }
    /// Adapter calls this only after native open has succeeded. It must close
    /// any just-opened native handle if activation is rejected.
    pub fn activate(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
        inventory: &HardwareInventory,
        now: Instant,
    ) -> Result<(), RacpError> {
        let s = self.checked(caller, lease)?;
        if s.phase != HardwarePhase::Reserved {
            return Err(RacpError::new("HARDWARE_PHASE_INVALID"));
        }
        if now >= s.expires || !inventory.is_current(&lease.target) {
            s.phase = HardwarePhase::Closing;
            return Err(RacpError::new("HARDWARE_CONNECTION_STALE"));
        }
        s.phase = HardwarePhase::Active;
        Ok(())
    }
    pub fn begin_io(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
        inventory: &HardwareInventory,
        request: HardwareIoRequest,
        now: Instant,
    ) -> Result<String, RacpError> {
        request.validate()?;
        let HardwareIoRequest {
            budget,
            plan,
            operation_id,
        } = request;
        let s = self.checked(caller, lease)?;
        if s.phase != HardwarePhase::Active {
            return Err(RacpError::new("HARDWARE_PHASE_INVALID"));
        }
        if now >= s.expires {
            s.phase = HardwarePhase::Closing;
            return Err(RacpError::new("HARDWARE_LEASE_EXPIRED"));
        }
        if !inventory.is_current(&lease.target) {
            s.phase = HardwarePhase::Closing;
            return Err(RacpError::new("HARDWARE_CONNECTION_STALE"));
        }
        if Duration::from_millis(budget.timeout_ms) > s.expires.duration_since(now) {
            return Err(RacpError::new("HARDWARE_IO_BUDGET_INVALID"));
        }
        let reserved: u64 = s.pending.values().map(|v| v.budget.max_bytes).sum();
        if s.pending.len() >= 8 || s.consumed + reserved + budget.max_bytes > 64 * 1024 * 1024 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let id = new_id("hwio");
        let deadline = now + Duration::from_millis(budget.timeout_ms);
        s.pending.insert(
            id.clone(),
            PendingIo {
                budget,
                plan,
                operation_id,
                deadline,
                cancelled: false,
            },
        );
        Ok(id)
    }
    /// Completion is an internal adapter callback, never caller-supplied proof.
    pub fn finish_io(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
        inventory: &HardwareInventory,
        io_id: &str,
        bytes: u64,
        now: Instant,
    ) -> Result<HardwareIoReceipt, RacpError> {
        let s = self.checked(caller, lease)?;
        let budget = s
            .pending
            .get(io_id)
            .ok_or_else(|| RacpError::new("HARDWARE_IO_STALE"))?;
        if bytes > budget.budget.max_bytes {
            s.phase = HardwarePhase::Unknown;
            return Err(RacpError::new("HARDWARE_IO_OUTCOME_UNKNOWN"));
        }
        let cancelled = budget.cancelled;
        let plan = budget.plan.clone();
        let operation_id = budget.operation_id.clone();
        let late = now >= budget.deadline;
        s.pending.remove(io_id);
        s.consumed += bytes;
        if cancelled {
            return Err(RacpError::new("CANCELLED"));
        }
        if late {
            s.phase = HardwarePhase::Unknown;
            return Err(RacpError::new("HARDWARE_IO_OUTCOME_UNKNOWN"));
        }
        if !inventory.is_current(&lease.target) {
            s.phase = HardwarePhase::Unknown;
            return Err(RacpError::new("HARDWARE_CONNECTION_STALE"));
        }
        if s.phase != HardwarePhase::Active {
            return Err(RacpError::new("HARDWARE_IO_OUTCOME_UNKNOWN"));
        }
        Ok(HardwareIoReceipt {
            caller: caller.clone(),
            lease: lease.clone(),
            io_id: io_id.into(),
            bytes,
            plan,
            operation_id,
        })
    }
    pub fn cancel_io(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
        io_id: &str,
    ) -> Result<(), RacpError> {
        let s = self.checked(caller, lease)?;
        s.pending
            .get_mut(io_id)
            .ok_or_else(|| RacpError::new("HARDWARE_IO_STALE"))?
            .cancelled = true;
        Ok(())
    }
    pub fn begin_close(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
    ) -> Result<(), RacpError> {
        let s = self.checked(caller, lease)?;
        if s.phase != HardwarePhase::Closed {
            s.phase = HardwarePhase::Closing;
            for io in s.pending.values_mut() {
                io.cancelled = true;
            }
        }
        Ok(())
    }
    /// Stop/revoke prevents new IO immediately; native cancellation/close and
    /// pending completions must still be confirmed by the Provider.
    pub fn quiesce(&mut self) {
        self.accepting = false;
        for s in self
            .sessions
            .values_mut()
            .filter(|s| s.phase != HardwarePhase::Closed)
        {
            s.phase = HardwarePhase::Closing;
            for io in s.pending.values_mut() {
                io.cancelled = true;
            }
        }
    }
    pub fn complete_close(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
        native_closed: bool,
    ) -> Result<(), RacpError> {
        let s = self.checked(caller, lease)?;
        if s.phase == HardwarePhase::Closed {
            return Ok(());
        }
        if !matches!(s.phase, HardwarePhase::Closing | HardwarePhase::Unknown) {
            return Err(RacpError::new("HARDWARE_PHASE_INVALID"));
        }
        if !native_closed || !s.pending.is_empty() {
            s.phase = HardwarePhase::Unknown;
            return Err(RacpError::new("HARDWARE_CLEANUP_UNCONFIRMED"));
        }
        s.phase = HardwarePhase::Closed;
        Ok(())
    }
    pub fn phase(
        &mut self,
        caller: &HardwareCaller,
        lease: &HardwareLease,
    ) -> Result<HardwarePhase, RacpError> {
        Ok(self.checked(caller, lease)?.phase)
    }
    pub fn cleanup_confirmed(&self) -> bool {
        self.sessions
            .values()
            .all(|s| s.phase == HardwarePhase::Closed)
    }
}
