//! Broker-thread MTA owns every COM pointer; references never relocate by selector.
use super::{native::NativeDesktop, native_identity::PinnedPeer, Scope};
use racp_contract::{new_id, RacpError};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};
use windows::{
    core::BSTR,
    Win32::{Foundation::HWND, System::Com::*, UI::Accessibility::*},
};
struct Reference {
    element: IUIAutomationElement,
    root: IUIAutomationElement,
    scope: Scope,
    window: String,
    observation: String,
    fingerprint: Value,
    expires: Instant,
}
pub(super) struct Automation {
    client: Option<IUIAutomation2>,
    references: BTreeMap<String, Reference>,
}
fn unavailable(_: windows::core::Error) -> RacpError {
    RacpError::new("CAPABILITY_UNAVAILABLE")
}
impl Automation {
    pub fn new() -> Result<Self, RacpError> {
        unsafe {
            CoInitializeEx(None, COINIT_MULTITHREADED)
                .ok()
                .map_err(unavailable)?;
            let initialized = (|| {
                let client: IUIAutomation2 =
                    CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER)
                        .map_err(unavailable)?;
                client.SetConnectionTimeout(500).map_err(unavailable)?;
                client.SetTransactionTimeout(500).map_err(unavailable)?;
                client.SetAutoSetFocus(false).map_err(unavailable)?;
                Ok(Self {
                    client: Some(client),
                    references: BTreeMap::new(),
                })
            })();
            if initialized.is_err() {
                CoUninitialize();
            }
            initialized
        }
    }
    fn fingerprint(element: &IUIAutomationElement) -> Result<Value, RacpError> {
        unsafe {
            let pid = element.CurrentProcessId().map_err(unavailable)?;
            let birth = if pid > 0 {
                PinnedPeer::open(pid as u32)
                    .ok()
                    .map(|p| p.identity().created)
            } else {
                None
            };
            let bounds = element.CurrentBoundingRectangle().map_err(unavailable)?;
            let password = element.CurrentIsPassword().map_err(unavailable)?.as_bool();
            Ok(
                json!({"pid":pid,"process_created":birth,"bounds":[bounds.left,bounds.top,bounds.right,bounds.bottom],"control_type":element.CurrentControlType().map_err(unavailable)?.0,"automation_id":element.CurrentAutomationId().map_err(unavailable)?.to_string().chars().take(128).collect::<String>(),"name":if password {String::new()}else{element.CurrentName().map_err(unavailable)?.to_string().chars().take(256).collect()},"enabled":element.CurrentIsEnabled().map_err(unavailable)?.as_bool(),"offscreen":element.CurrentIsOffscreen().map_err(unavailable)?.as_bool(),"password":password}),
            )
        }
    }
    pub fn inspect(
        &mut self,
        native: &NativeDesktop,
        scope: &Scope,
        window: &str,
        observation: &str,
        limit: usize,
        deadline: Instant,
    ) -> Result<Value, RacpError> {
        unsafe {
            self.references.retain(|_, r| r.expires > Instant::now());
            if self.references.len() + limit > 256 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let client = self
                .client
                .as_ref()
                .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
            let root = client
                .ElementFromHandle(HWND(native.window_handle(window)?))
                .map_err(unavailable)?;
            let walker = client.ControlViewWalker().map_err(unavailable)?;
            let mut stack = vec![(root.clone(), None::<String>, 0usize)];
            let mut nodes = vec![];
            let until = deadline.min(Instant::now() + Duration::from_secs(1));
            let mut bytes = 0;
            while let Some((element, parent, depth)) = stack.pop() {
                if Instant::now() >= until || nodes.len() >= limit {
                    stack.push((element, parent, depth));
                    break;
                }
                let fingerprint = Self::fingerprint(&element)?;
                let id = new_id("element");
                let mut patterns = vec![];
                if fingerprint["enabled"] == true && fingerprint["password"] == false {
                    if element
                        .GetCurrentPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId)
                        .is_ok()
                    {
                        patterns.push("invoke");
                    }
                    if element
                        .GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
                        .is_ok()
                    {
                        patterns.push("value");
                    }
                }
                let mut node = fingerprint.clone();
                node["element_ref"] = json!(id);
                node["parent_ref"] = json!(parent);
                node["depth"] = json!(depth);
                node["patterns"] = json!(patterns);
                bytes += serde_json::to_vec(&node)?.len();
                if bytes > 32768 {
                    stack.push((element, parent, depth));
                    break;
                }
                self.references.insert(
                    id.clone(),
                    Reference {
                        element: element.clone(),
                        root: root.clone(),
                        scope: scope.clone(),
                        window: window.into(),
                        observation: observation.into(),
                        fingerprint,
                        expires: Instant::now() + Duration::from_secs(5),
                    },
                );
                nodes.push(node);
                if depth < 16 {
                    let mut child = walker.GetFirstChildElement(&element).ok();
                    let mut siblings = vec![];
                    while let Some(e) = child {
                        if siblings.len() >= limit {
                            break;
                        }
                        child = walker.GetNextSiblingElement(&e).ok();
                        siblings.push(e);
                    }
                    for child in siblings.into_iter().rev() {
                        stack.push((child, Some(id.clone()), depth + 1));
                    }
                }
            }
            native.availability()?;
            Ok(json!({"nodes":nodes,"truncated":!stack.is_empty(),"window_id":window}))
        }
    }
    pub fn mutate(
        &mut self,
        native: &NativeDesktop,
        scope: &Scope,
        operation: &str,
        p: &Value,
        deadline: Instant,
    ) -> Result<Value, RacpError> {
        unsafe {
            let reference = self
                .references
                .get(p["element_ref"].as_str().unwrap_or(""))
                .ok_or_else(|| RacpError::new("STALE_OBSERVATION"))?;
            if reference.scope != *scope
                || reference.window != p["expected_window_id"].as_str().unwrap_or("")
                || reference.observation != p["observation_id"].as_str().unwrap_or("")
                || reference.expires <= Instant::now()
                || Instant::now() >= deadline
                || reference.fingerprint != Self::fingerprint(&reference.element)?
                || reference.fingerprint["password"] != false
                || reference.fingerprint["enabled"] != true
                || reference.fingerprint["offscreen"] != false
            {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            let client = self
                .client
                .as_ref()
                .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
            let fresh_root = client
                .ElementFromHandle(HWND(native.window_handle(&reference.window)?))
                .map_err(unavailable)?;
            if !client
                .CompareElements(&fresh_root, &reference.root)
                .map_err(unavailable)?
                .as_bool()
            {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            let walker = client.ControlViewWalker().map_err(unavailable)?;
            let mut ancestor = Some(reference.element.clone());
            let mut inside = false;
            for _ in 0..64 {
                let Some(element) = ancestor else {
                    break;
                };
                if client
                    .CompareElements(&element, &fresh_root)
                    .map_err(unavailable)?
                    .as_bool()
                {
                    inside = true;
                    break;
                }
                ancestor = walker.GetParentElement(&element).ok();
            }
            if !inside {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            let peer = PinnedPeer::open(reference.fingerprint["pid"].as_u64().unwrap_or(0) as u32)?;
            if Some(peer.identity().created) != reference.fingerprint["process_created"].as_f64() {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            native.availability()?;
            peer.alive()?;
            if operation == "desktop.invoke" {
                reference
                    .element
                    .GetCurrentPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId)
                    .map_err(unavailable)?
                    .Invoke()
                    .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
            } else {
                let pattern = reference
                    .element
                    .GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
                    .map_err(unavailable)?;
                if pattern.CurrentIsReadOnly().map_err(unavailable)?.as_bool() {
                    return Err(RacpError::new("PERMISSION_DENIED"));
                }
                pattern
                    .SetValue(&BSTR::from(p["value"].as_str().unwrap_or("")))
                    .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
            }
            self.references.clear();
            Ok(json!({"dispatched":true,"application_verified":false}))
        }
    }
}
impl Drop for Automation {
    fn drop(&mut self) {
        self.references.clear();
        self.client.take();
        unsafe {
            CoUninitialize();
        }
    }
}
