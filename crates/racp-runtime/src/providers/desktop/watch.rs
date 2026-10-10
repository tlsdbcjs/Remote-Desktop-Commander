//! Hooks retain interruption counters and owned-down release authority, never foreign input contents.
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};
pub struct InterruptionCounter {
    tag: usize,
    foreign: u64,
    foreground: u64,
    own: u64,
    serial: u64,
    windows: BTreeMap<usize, u64>,
    healthy: bool,
}
impl InterruptionCounter {
    pub fn new(tag: usize) -> Self {
        Self {
            tag,
            foreign: 0,
            foreground: 0,
            own: 0,
            serial: 0,
            windows: BTreeMap::new(),
            healthy: true,
        }
    }
    fn increment(value: &mut u64, healthy: &mut bool) {
        match value.checked_add(1) {
            Some(next) => *value = next,
            None => *healthy = false,
        }
    }
    pub fn input(&mut self, flags: u32, extra: usize, injected: u32) {
        if flags & injected != 0 && extra == self.tag {
            Self::increment(&mut self.own, &mut self.healthy)
        } else {
            Self::increment(&mut self.foreign, &mut self.healthy)
        }
    }
    pub fn foreign(&self) -> u64 {
        self.foreign
    }
    pub fn foreground(&self) -> u64 {
        self.foreground
    }
    pub fn own(&self) -> u64 {
        self.own
    }
    pub fn healthy(&self) -> bool {
        self.healthy
    }
    pub fn window_generation(&mut self, handle: usize) -> u64 {
        if let Some(generation) = self.windows.get(&handle) {
            return *generation;
        }
        if self.windows.len() >= 4096 {
            self.windows.clear();
        }
        Self::increment(&mut self.serial, &mut self.healthy);
        self.windows.insert(handle, self.serial);
        self.serial
    }
    pub fn window_event(&mut self, event: u32, handle: usize, object: i32, child: i32) {
        if event == 3 {
            Self::increment(&mut self.foreground, &mut self.healthy)
        } else if matches!(event, 0x8000 | 0x8001)
            && object == 0
            && child == 0
            && self.windows.contains_key(&handle)
        {
            Self::increment(&mut self.serial, &mut self.healthy);
            self.windows.insert(handle, self.serial);
        }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ReleaseInput {
    Key { code: u16, scan: u16, flags: u32 },
    Mouse { flags: u32 },
}
#[derive(Default)]
pub struct HeldInputs {
    pending: BTreeMap<ReleaseInput, Instant>,
    failure: bool,
}
impl HeldInputs {
    fn update(&mut self, input: ReleaseInput, up: bool, now: Instant) {
        if up {
            self.pending.remove(&input);
        } else if !self.pending.contains_key(&input) {
            if self.pending.len() >= 16 {
                self.failure = true;
            } else {
                self.pending.insert(input, now);
            }
        }
    }
    /// The caller must already have checked the injected flag and the exact paired marker.
    pub fn keyboard(&mut self, vk: u32, scan: u32, flags: u32, now: Instant) {
        if vk > u16::MAX as u32 || scan > u16::MAX as u32 {
            self.failure = true;
            return;
        }
        let input = if vk == 0xe7 {
            ReleaseInput::Key {
                code: 0,
                scan: scan as u16,
                flags: 6,
            }
        } else {
            ReleaseInput::Key {
                code: vk as u16,
                scan: 0,
                flags: 2 | (flags & 1),
            }
        };
        self.update(input, flags & 0x80 != 0, now);
    }
    pub fn mouse(&mut self, message: u32, now: Instant) {
        let (flags, up) = match message {
            0x201 => (4, false),
            0x202 => (4, true),
            0x204 => (16, false),
            0x205 => (16, true),
            0x207 => (64, false),
            0x208 => (64, true),
            _ => return,
        };
        self.update(ReleaseInput::Mouse { flags }, up, now);
    }
    pub fn snapshot(&self) -> Vec<ReleaseInput> {
        self.pending.keys().copied().collect()
    }
    pub fn overdue(&self, now: Instant, limit: Duration) -> bool {
        self.failure
            || self
                .pending
                .values()
                .any(|since| now.saturating_duration_since(*since) >= limit)
    }
}
