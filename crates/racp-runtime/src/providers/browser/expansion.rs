//! Page/origin/revision-scoped diagnostics and storage. No browser-wide cookie or storage command.
use super::{
    events::DiagnosticRecord,
    session::{Browser, Session},
    BrowserConfig,
};
use racp_contract::{digest, RacpError};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use tokio_util::sync::CancellationToken;

pub(super) fn redacted_url(text: &str) -> Option<String> {
    let mut url = url::Url::parse(text).ok()?;
    if !matches!(url.scheme(), "http" | "https") {
        return None;
    }
    url.set_query(None);
    url.set_fragment(None);
    let _ = url.set_username("");
    let _ = url.set_password(None);
    Some(super::operations::bounded(url.as_str(), 2048))
}
fn origin(url: &str) -> Result<String, RacpError> {
    let url = url::Url::parse(url).map_err(|_| RacpError::new("INVALID_ARGUMENT"))?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err(RacpError::new("PERMISSION_DENIED"));
    }
    Ok(url.origin().ascii_serialization())
}
impl Session {
    pub(super) fn capture_diagnostic(
        &self,
        event: &Value,
        config: &BrowserConfig,
    ) -> Result<bool, RacpError> {
        let method = event["method"].as_str().unwrap_or("");
        let (kind, permission) = match method {
            "Runtime.consoleAPICalled" => ("console", "browser.console.read"),
            "Network.requestWillBeSent" | "Network.responseReceived" => {
                ("network", "browser.network.read")
            }
            _ => return Ok(false),
        };
        let permissions = self
            .capture_policy
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        // Approval on a read authorizes that read, not unsolicited background collection.
        if !racp_core::permission_allows_field(&permissions, permission) {
            return Ok(true);
        }
        let owner = event["sessionId"].as_str().unwrap_or("");
        let p = &event["params"];
        let mut state = self
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let bound = if kind == "console" {
            state
                .contexts
                .get(&(owner.into(), p["executionContextId"].as_u64().unwrap_or(0)))
                .cloned()
        } else {
            None
        };
        let mut record = None;
        for (page_id, page) in &state.pages {
            if page.handle["state"] != "ACTIVE" {
                continue;
            }
            let frame = page.frames.values().find(|frame| {
                frame.active
                    && frame.session == owner
                    && if kind == "console" {
                        bound.as_ref().is_some_and(|(native, unique)| {
                            frame.native == *native && frame.context.as_ref() == Some(unique)
                        })
                    } else {
                        frame.native == p["frameId"].as_str().unwrap_or("")
                    }
            });
            if let Some(frame) = frame {
                if !config.allowed(&frame.url) {
                    return Ok(true);
                }
                let origin = origin(&frame.url)?;
                if kind == "network" {
                    let url = p["request"]["url"]
                        .as_str()
                        .or(p["response"]["url"].as_str())
                        .unwrap_or("");
                    if !config.allowed(url)
                        || self.borrowed
                            && super::expansion::origin(url).ok().as_ref() != Some(&origin)
                    {
                        return Ok(true);
                    }
                }
                record = Some(DiagnosticRecord {
                    page: page_id.clone(),
                    frame: frame.id.clone(),
                    revision: page.revision,
                    origin,
                    kind: kind.into(),
                    sequence: 0,
                    data: json!({"method":method,"data":p}),
                });
                break;
            }
        }
        if let Some(record) = record {
            state.diagnostic.push(record);
        }
        Ok(true)
    }
}
impl Browser {
    pub(super) async fn expanded(
        &self,
        session: &Session,
        r: &Value,
        cancel: &CancellationToken,
        effect: &AtomicBool,
    ) -> Result<Value, RacpError> {
        let p = &r["payload"];
        let action = r["operation"].as_str().unwrap_or("");
        let page_id = p["page_id"].as_str().unwrap_or("");
        let expected = p["expected_origin"].as_str().unwrap_or("");
        if origin(expected)? != expected || !self.config.allowed(expected) {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let (frame_id, frame_session, context, revision, url) = {
            let state = session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            let page = state
                .pages
                .get(page_id)
                .filter(|page| page.handle["state"] == "ACTIVE")
                .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
            if p["navigation_revision"].as_str() != Some(page.revision.to_string().as_str()) {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            let frame = page
                .frames
                .get(p["frame_id"].as_str().unwrap_or(&page.main))
                .filter(|frame| frame.active)
                .ok_or_else(|| RacpError::new("STALE_OBSERVATION"))?;
            if origin(&frame.url)? != expected || !self.config.allowed(&frame.url) {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            (
                frame.id.clone(),
                frame.session.clone(),
                frame
                    .context
                    .clone()
                    .ok_or_else(|| RacpError::new("RESOURCE_BUSY"))?,
                page.revision,
                frame.url.clone(),
            )
        };
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        let result = match action {
            "browser.console_read" | "browser.network_read" => {
                let kind = if action == "browser.console_read" {
                    "console"
                } else {
                    "network"
                };
                let after = p["after_sequence"].as_u64().unwrap_or(0);
                let limit = p["limit"].as_u64().unwrap_or(100).min(256) as usize;
                let state = session
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                if after > state.diagnostic.sequence {
                    return Err(RacpError::new("STALE_OBSERVATION"));
                }
                let matches = state
                    .diagnostic
                    .records
                    .iter()
                    .filter(|record| {
                        record.page == page_id
                            && record.frame == frame_id
                            && record.revision == revision
                            && record.origin == expected
                            && record.kind == kind
                            && record.sequence > after
                    })
                    .collect::<Vec<_>>();
                let items = matches
                    .iter()
                    .take(limit)
                    .map(|record| json!({"sequence":record.sequence,"event":record.data}))
                    .collect::<Vec<_>>();
                let next = if matches.len() > limit {
                    items
                        .last()
                        .and_then(|v| v["sequence"].as_u64())
                        .unwrap_or(after)
                } else {
                    state.diagnostic.sequence
                };
                json!({"items":items,"next_sequence":next,"gap":after<state.diagnostic.gap,"truncated":matches.len()>limit,"collection_requires_allow":true,"origin":expected,"navigation_revision":revision.to_string()})
            }
            "browser.cookies_read" => {
                if session.borrowed {
                    return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
                }
                let reply = session
                    .cdp
                    .call(
                        "Network.getCookies",
                        json!({"urls":[url]}),
                        Some(&frame_session),
                        cancel,
                    )
                    .await?;
                let cookies = reply["cookies"]
                    .as_array()
                    .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
                if cookies.len() > 256 || reply.to_string().len() > 1048576 {
                    return Err(RacpError::new("OUTPUT_LIMIT_EXCEEDED"));
                }
                let host = url::Url::parse(expected)
                    .map_err(|_| RacpError::new("INVALID_ARGUMENT"))?
                    .host_str()
                    .unwrap_or("")
                    .to_owned();
                let cookies=cookies.iter().filter(|cookie|{
                    let domain=cookie["domain"].as_str().unwrap_or("").trim_start_matches('.');
                    !domain.is_empty() && (domain==host || host.ends_with(&format!(".{domain}"))) && cookie.get("partitionKey").is_none()
                }).map(|cookie|json!({"name":cookie["name"],"value":cookie["value"],"domain":cookie["domain"],"path":cookie["path"],"expires":cookie["expires"],"secure":cookie["secure"],"http_only":cookie["httpOnly"],"same_site":cookie["sameSite"]})).collect::<Vec<_>>();
                json!({"cookies":cookies,"origin":expected,"partitioned_cookies_omitted":true})
            }
            "browser.storage_modify" => {
                if session.borrowed {
                    return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
                }
                let read=session.cdp.call("Runtime.callFunctionOn",json!({"functionDeclaration":"function(origin,kind,key){if(location.origin!==origin)return {conflict:true};const s=kind==='local'?localStorage:sessionStorage;const value=s.getItem(key);if(value!==null && value.length>32768)return {too_large:true};return {value};}","uniqueContextId":context,"arguments":[{"value":expected},{"value":p["storage"]},{"value":p["key"]}],"returnByValue":true}),Some(&frame_session),cancel).await?;
                if read.get("exceptionDetails").is_some()
                    || read["result"]["value"]["conflict"] == true
                {
                    return Err(RacpError::new("STALE_OBSERVATION"));
                }
                if read["result"]["value"]["too_large"] == true {
                    return Err(RacpError::new("OUTPUT_LIMIT_EXCEEDED"));
                }
                let observed = read["result"]["value"]
                    .get("value")
                    .ok_or_else(|| RacpError::new("RUNTIME_RESPONSE_INVALID"))?;
                if !observed.is_null() && !observed.is_string() {
                    return Err(RacpError::new("RUNTIME_RESPONSE_INVALID"));
                }
                // Hash the canonical JSON scalar; null and an empty string have distinct revisions.
                if digest(serde_json::to_vec(observed)?) != p["expected_value_sha256"] {
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
                effect.store(true, Ordering::SeqCst);
                let committed=session.cdp.call("Runtime.callFunctionOn",json!({"functionDeclaration":"function(origin,kind,key,oldValue,newValue){if(location.origin!==origin)return {conflict:true};const s=kind==='local'?localStorage:sessionStorage;if(s.getItem(key)!==oldValue)return {conflict:true};if(newValue===null)s.removeItem(key);else s.setItem(key,newValue);return {committed:s.getItem(key)===newValue};}","uniqueContextId":context,"arguments":[{"value":expected},{"value":p["storage"]},{"value":p["key"]},{"value":observed},{"value":p["value"]}],"returnByValue":true}),Some(&frame_session),cancel).await?;
                if committed["result"]["value"]["conflict"] == true {
                    return Err(RacpError::new("PRECONDITION_FAILED"));
                }
                if committed.get("exceptionDetails").is_some()
                    || committed["result"]["value"]["committed"] != true
                {
                    return Err(RacpError::new("EXECUTION_UNKNOWN"));
                }
                json!({"origin":expected,"storage":p["storage"],"key":p["key"],"committed":true,"value_sha256":digest(serde_json::to_vec(&p["value"])?)})
            }
            _ => return Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
        };
        let state = session
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
        let page = state
            .pages
            .get(page_id)
            .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
        if page.revision != revision
            || page.handle["state"] != "ACTIVE"
            || page.frames.get(&frame_id).is_none_or(|frame| {
                !frame.active
                    || frame.context.as_ref() != Some(&context)
                    || origin(&frame.url).ok().as_deref() != Some(expected)
            })
        {
            return Err(RacpError::new(if effect.load(Ordering::SeqCst) {
                "EXECUTION_UNKNOWN"
            } else {
                "STALE_OBSERVATION"
            }));
        }
        Ok(result)
    }
}
