use racp_contract::{new_id, RacpError};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Scope {
    pub owner: String,
    pub device: String,
}
#[derive(Clone, Debug)]
pub struct Snapshot {
    pub available: bool,
    pub unavailable_code: &'static str,
    pub foreground: Option<String>,
    pub input_tick: u64,
    pub foreground_tick: u64,
    pub layout_revision: String,
    pub windows: BTreeMap<String, Value>,
}
struct Lease {
    id: String,
    scope: Scope,
    expires: Instant,
    foreground: Option<String>,
    input_tick: u64,
    foreground_tick: u64,
}
struct Observation {
    scope: Scope,
    expires: Instant,
    layout: String,
    windows: BTreeMap<String, Value>,
}
pub struct DesktopState {
    session: u32,
    lease: Option<Lease>,
    observations: BTreeMap<String, Observation>,
    release_pending: bool,
}
impl DesktopState {
    pub fn new(session: u32) -> Self {
        Self {
            session,
            lease: None,
            observations: BTreeMap::new(),
            release_pending: false,
        }
    }
    pub fn needs_release(&self) -> bool {
        self.release_pending
    }
    /// Called only after the native release path and independent Guardian confirm cleanup.
    pub fn cleanup_confirmed(&mut self) {
        self.release_pending = false;
    }
    pub fn abort(&mut self) {
        self.release_pending |= self.lease.take().is_some();
        self.observations.clear();
    }
    fn available(&mut self, env: &Snapshot) -> Result<(), RacpError> {
        if !env.available {
            self.abort();
            return Err(RacpError::new(env.unavailable_code));
        }
        Ok(())
    }
    fn ttl(ttl: u64) -> Result<Duration, RacpError> {
        if !(1000..=15000).contains(&ttl) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        Ok(Duration::from_millis(ttl))
    }
    pub fn acquire(
        &mut self,
        scope: &Scope,
        ttl: u64,
        env: &Snapshot,
        now: Instant,
    ) -> Result<String, RacpError> {
        let duration = Self::ttl(ttl)?;
        self.available(env)?;
        self.tick(env, now);
        if self.lease.is_some() || self.release_pending {
            return Err(RacpError::new("RESOURCE_BUSY"));
        }
        let id = new_id("lease");
        self.lease = Some(Lease {
            id: id.clone(),
            scope: scope.clone(),
            expires: now + duration,
            foreground: env.foreground.clone(),
            input_tick: env.input_tick,
            foreground_tick: env.foreground_tick,
        });
        Ok(id)
    }
    fn check_lease(&mut self, env: &Snapshot, now: Instant) -> Result<(), RacpError> {
        if self.lease.as_ref().is_none_or(|lease| lease.expires <= now) {
            self.abort();
            return Err(RacpError::new("LEASE_EXPIRED"));
        }
        self.available(env)?;
        let lease = self
            .lease
            .as_ref()
            .ok_or_else(|| RacpError::new("LEASE_EXPIRED"))?;
        if lease.foreground != env.foreground
            || lease.input_tick != env.input_tick
            || lease.foreground_tick != env.foreground_tick
        {
            self.abort();
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        Ok(())
    }
    fn scoped(
        &mut self,
        scope: &Scope,
        payload: &Value,
        env: &Snapshot,
        now: Instant,
    ) -> Result<(), RacpError> {
        if payload["session_id"] != self.session
            || self
                .lease
                .as_ref()
                .is_none_or(|lease| lease.scope != *scope || payload["lease_id"] != lease.id)
        {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        self.check_lease(env, now)
    }
    pub fn renew(
        &mut self,
        scope: &Scope,
        payload: &Value,
        env: &Snapshot,
        now: Instant,
    ) -> Result<String, RacpError> {
        let ttl = Self::ttl(payload["ttl_ms"].as_u64().unwrap_or(0))?;
        self.scoped(scope, payload, env, now)?;
        let lease = self
            .lease
            .as_mut()
            .ok_or_else(|| RacpError::new("LEASE_EXPIRED"))?;
        lease.expires = now + ttl;
        Ok(lease.id.clone())
    }
    pub fn release(
        &mut self,
        scope: &Scope,
        payload: &Value,
        env: &Snapshot,
        now: Instant,
    ) -> Result<(), RacpError> {
        self.scoped(scope, payload, env, now)?;
        self.abort();
        Ok(())
    }
    pub fn tick(&mut self, env: &Snapshot, now: Instant) {
        self.observations
            .retain(|_, observation| observation.expires > now);
        if self.lease.is_some() {
            let _ = self.check_lease(env, now);
        }
    }
    pub fn observe(
        &mut self,
        scope: &Scope,
        env: &Snapshot,
        now: Instant,
    ) -> Result<String, RacpError> {
        self.available(env)?;
        self.tick(env, now);
        if self.observations.len() >= 32 || env.windows.len() > 128 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let id = new_id("observation");
        self.observations.insert(
            id.clone(),
            Observation {
                scope: scope.clone(),
                expires: now + Duration::from_secs(5),
                layout: env.layout_revision.clone(),
                windows: env.windows.clone(),
            },
        );
        Ok(id)
    }
    pub fn guard(
        &mut self,
        scope: &Scope,
        payload: &Value,
        env: &Snapshot,
        now: Instant,
        activate: bool,
    ) -> Result<(), RacpError> {
        self.scoped(scope, payload, env, now)?;
        let window = payload["expected_window_id"].as_str().unwrap_or("");
        let valid = self
            .observations
            .get(payload["observation_id"].as_str().unwrap_or(""))
            .is_some_and(|observation| {
                observation.scope == *scope
                    && observation.expires > now
                    && observation.layout == env.layout_revision
                    && payload["layout_revision"] == observation.layout
                    && observation
                        .windows
                        .get(window)
                        .is_some_and(|observed| env.windows.get(window) == Some(observed))
                    && (activate || env.foreground.as_deref() == Some(window))
            });
        if !valid {
            self.abort();
            return Err(RacpError::new("STALE_OBSERVATION"));
        }
        Ok(())
    }
    /// Preserve OS-dispatch semantics: a successful dispatch always requires a fresh observation.
    pub fn dispatched(&mut self, env: &Snapshot, activated: bool) -> Result<(), RacpError> {
        self.available(env)?;
        if activated {
            let lease = self
                .lease
                .as_mut()
                .ok_or_else(|| RacpError::new("LEASE_EXPIRED"))?;
            if env.input_tick != lease.input_tick {
                self.abort();
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            lease.foreground = env.foreground.clone();
            lease.foreground_tick = env.foreground_tick;
        }
        if !activated {
            self.check_lease(env, Instant::now())?;
        }
        self.observations.clear();
        Ok(())
    }
}
