use super::session::{Browser, Page};
use racp_contract::{new_id, RacpError};
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
pub(super) fn bounded(text: &str, limit: usize) -> String {
    let mut n = text.len().min(limit);
    while !text.is_char_boundary(n) {
        n -= 1;
    }
    text[..n].into()
}
impl Browser {
    pub(super) async fn run(
        &self,
        request: &Value,
        cancel: &CancellationToken,
        effect: &std::sync::atomic::AtomicBool,
    ) -> Result<Value, RacpError> {
        let action = request["operation"].as_str().unwrap_or("");
        let p = &request["payload"];
        if action == "browser.cdp_targets" {
            return self.targets(request, cancel).await;
        }
        if action == "browser.attach" {
            return self.attach(request, cancel, effect).await;
        }
        if action == "browser.open" {
            return self.open(request, cancel, effect).await;
        }
        let session = self.get(request)?;
        let _serial = session.serial.lock().await;
        let borrowed_page = session.borrowed
            && session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .pages
                .get(p["page_id"].as_str().unwrap_or(""))
                .is_some_and(|page| page.handle["ownership"] == "borrowed");
        if session.borrowed
            && (action == "browser.download"
                || borrowed_page
                    && matches!(action, "browser.evaluate" | "browser.upload")
                    && !session.allow_termination)
        {
            return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));
        }
        if action == "browser.close" {
            effect.store(true, std::sync::atomic::Ordering::SeqCst);
            return session.close().await;
        }
        if session.handle()["state"] != "ACTIVE" {
            return Err(RacpError::new("HANDLE_EXPIRED"));
        }
        if action == "browser.pages" {
            return Ok(json!({"pages":session.pages()}));
        }
        if action == "browser.new_page" {
            effect.store(true, std::sync::atomic::Ordering::SeqCst);
            return session.new_page(cancel).await;
        }
        if action == "browser.keepalive" {
            let mut state = session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            state.expires = std::time::Instant::now() + std::time::Duration::from_secs(3600);
            state.handle["last_access_at"] = json!(racp_contract::timestamp());
            state.handle["expires_at"] = json!((chrono::Utc::now() + chrono::Duration::hours(1))
                .to_rfc3339_opts(chrono::SecondsFormat::Micros, true));
            return Ok(json!({"browser_id":state.handle["id"],"handle":state.handle}));
        }
        let page_id = p["page_id"].as_str().unwrap_or("");
        if action == "browser.frames" {
            let state = session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            let page = state
                .pages
                .get(page_id)
                .filter(|p| p.handle["state"] == "ACTIVE")
                .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
            let limit = p["limit"].as_u64().unwrap_or(16) as usize;
            let scope = format!(
                "{}:{}:{}:{}:{limit}",
                request["context"]["principal_id"],
                request["context"]["workspace_id"],
                p["browser_id"],
                p["page_id"]
            );
            let revision = page.revision.to_string();
            let offset = self
                .cursor
                .decode(p["cursor"].as_str(), &scope, &revision)
                .map_err(|_| RacpError::new("STALE_OBSERVATION"))?;
            let frames = frame_inventory(page);
            if offset > frames.len() {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            let end = (offset + limit).min(frames.len());
            let next = if end < frames.len() {
                Some(self.cursor.encode(end, &scope, &revision)?)
            } else {
                None
            };
            return Ok(json!({"frames":frames[offset..end],"next_cursor":next}));
        }
        // A frame load can finish before its Runtime execution-context event arrives.
        // Wait for the scoped context, preserving cancellation and the operation budget.
        loop {
            let notified = session.notify.notified();
            let ready = {
                let state = session
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                let page = state
                    .pages
                    .get(page_id)
                    .filter(|p| p.handle["state"] == "ACTIVE")
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
                let frame = page
                    .frames
                    .get(p["frame_id"].as_str().unwrap_or(&page.main))
                    .filter(|f| f.active)
                    .ok_or_else(|| RacpError::new("STALE_OBSERVATION"))?;
                frame.context.is_some()
            };
            if ready {
                break;
            }
            tokio::select! {_=cancel.cancelled()=>return Err(RacpError::new("CANCELLED")),_=session.stop.cancelled()=>return Err(RacpError::new("BROWSER_ERROR")),_=notified=>{},_=tokio::time::sleep(std::time::Duration::from_millis(20))=>{}}
        }
        let (page_session, frame_native, frame_id, context, revision) = {
            let state = session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            let page = state
                .pages
                .get(page_id)
                .filter(|p| p.handle["state"] == "ACTIVE")
                .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
            let frame_id = p["frame_id"].as_str().unwrap_or(&page.main);
            let frame = page
                .frames
                .get(frame_id)
                .filter(|f| f.active)
                .ok_or_else(|| RacpError::new("STALE_OBSERVATION"))?;
            if let Some(observation) = p["observation_id"].as_str() {
                if page.observation.as_ref()
                    != Some(&(observation.into(), frame_id.into(), page.revision))
                    || p["navigation_revision"].as_str() != Some(page.revision.to_string().as_str())
                {
                    return Err(RacpError::new("STALE_OBSERVATION"));
                }
            }
            (
                frame.session.clone(),
                frame.native.clone(),
                frame_id.to_owned(),
                frame
                    .context
                    .clone()
                    .ok_or_else(|| RacpError::new("RESOURCE_BUSY"))?,
                page.revision,
            )
        };
        if action == "browser.close_page" {
            let target = session
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
                .pages[page_id]
                .target
                .clone();
            effect.store(true, std::sync::atomic::Ordering::SeqCst);
            session
                .cdp
                .call(
                    "Target.closeTarget",
                    json!({"targetId":target}),
                    None,
                    cancel,
                )
                .await?;
            return Ok(json!({"page_id":page_id,"closed":true}));
        }
        if action == "browser.navigate" {
            let url = p["url"].as_str().unwrap_or("");
            if !self.config.allowed(url) {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            {
                let mut state = session
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                let page = state
                    .pages
                    .get_mut(page_id)
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
                page.loading = true;
                page.blocked = false;
                page.observation = None;
            }
            effect.store(true, std::sync::atomic::Ordering::SeqCst);
            let response = session
                .cdp
                .call(
                    "Page.navigate",
                    json!({"url":url,"frameId":frame_native}),
                    Some(&page_session),
                    cancel,
                )
                .await?;
            if response["errorText"].is_string() {
                return Err(RacpError::new("BROWSER_ERROR"));
            }
            loop {
                let notified = session.notify.notified();
                let (loaded, blocked) = {
                    let state = session
                        .state
                        .lock()
                        .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                    let page = &state.pages[page_id];
                    (
                        !page.loading
                            && page
                                .frames
                                .get(&frame_id)
                                .is_some_and(|f| f.context.is_some()),
                        page.blocked,
                    )
                };
                if blocked {
                    return Err(RacpError::new("PERMISSION_DENIED"));
                }
                if loaded {
                    break;
                }
                tokio::select! {_=cancel.cancelled()=>return Err(RacpError::new("CANCELLED")),_=session.stop.cancelled()=>return Err(RacpError::new("BROWSER_ERROR")),_=notified=>{}}
            }
            return Ok(json!({"browser_id":p["browser_id"],"page_id":page_id,"url":url}));
        }
        if action == "browser.screenshot" {
            use base64::Engine;
            use std::io::Write;
            let image = session
                .cdp
                .call(
                    "Page.captureScreenshot",
                    json!({"format":"png","captureBeyondViewport":false,"fromSurface":true}),
                    Some(&page_session),
                    cancel,
                )
                .await?;
            let raw = base64::engine::general_purpose::STANDARD
                .decode(image["data"].as_str().unwrap_or(""))
                .map_err(|_| RacpError::new("BROWSER_ERROR"))?;
            if raw.len() > 32 * 1024 * 1024 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let path = self.spool.join(format!("{}.png", new_id("browser")));
            let mut file = racp_core::secure_create_file(&path)?;
            file.write_all(&raw)?;
            file.sync_all()?;
            return Ok(
                json!({"browser_id":p["browser_id"],"page_id":page_id,"spool_path":path,"size_bytes":raw.len(),"sha256":racp_contract::digest(&raw),"artifact_id":null,"artifact_media_type":"image/png"}),
            );
        }
        if action == "browser.download" {
            let object = element(
                &session,
                p,
                page_id,
                &context,
                &frame_native,
                &page_session,
                cancel,
            )
            .await?;
            return session
                .download(request, &object, &page_session, &self.spool, cancel, effect)
                .await;
        }
        if action == "browser.upload" {
            let object = element(
                &session,
                p,
                page_id,
                &context,
                &frame_native,
                &page_session,
                cancel,
            )
            .await?;
            return session
                .upload(request, &object, &page_session, &self.spool, cancel, effect)
                .await;
        }
        if matches!(action, "browser.click" | "browser.type") {
            let object = element(
                &session,
                p,
                page_id,
                &context,
                &frame_native,
                &page_session,
                cancel,
            )
            .await?;
            let connected=session.cdp.call("Runtime.callFunctionOn",json!({"objectId":object,"functionDeclaration":"function(){return this.isConnected && !this.disabled && this.getClientRects().length>0;}","returnByValue":true}),Some(&page_session),cancel).await?;
            if connected["result"]["value"] != true {
                return Err(RacpError::new("STALE_OBSERVATION"));
            }
            if action == "browser.type" {
                effect.store(true, std::sync::atomic::Ordering::SeqCst);
                let prepared=session.cdp.call("Runtime.callFunctionOn",json!({"objectId":object,"functionDeclaration":"function(){this.focus();if(this.isContentEditable){this.textContent='';return true;}const proto=this.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;const setter=Object.getOwnPropertyDescriptor(proto,'value')?.set;if(!setter)return false;setter.call(this,'');this.dispatchEvent(new Event('input',{bubbles:true}));return true;}","returnByValue":true}),Some(&page_session),cancel).await?;
                if prepared["result"]["value"] != true {
                    return Err(RacpError::new("ELEMENT_NOT_EDITABLE"));
                }
                session
                    .cdp
                    .call(
                        "Input.insertText",
                        json!({"text":p["text"]}),
                        Some(&page_session),
                        cancel,
                    )
                    .await?;
            } else {
                click(&session, &object, &page_session, cancel, effect).await?;
            }
            return Ok(json!({"browser_id":p["browser_id"],"page_id":page_id}));
        }
        if action == "browser.evaluate" {
            effect.store(true, std::sync::atomic::Ordering::SeqCst);
            let expression = p["expression"].as_str().unwrap_or("");
            let expression=format!("(async()=>{{const fn=({expression});const value=await(typeof fn==='function'?fn():fn);const raw=JSON.stringify(value===undefined?null:value);if(new TextEncoder().encode(raw).length>65536)return {{limit:true}};return {{raw}};}})()");
            let result=session.cdp.call("Runtime.evaluate",json!({"expression":expression,"uniqueContextId":context,"returnByValue":true,"awaitPromise":true}),Some(&page_session),cancel).await?;
            if result.get("exceptionDetails").is_some() {
                return Err(RacpError::new("BROWSER_ERROR"));
            }
            if result["result"]["value"]["limit"] == true {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            let raw = result["result"]["value"]["raw"]
                .as_str()
                .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?;
            if raw.len() > 65536 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            return Ok(
                json!({"browser_id":p["browser_id"],"page_id":page_id,"value":serde_json::from_str::<Value>(raw)?}),
            );
        }
        if action == "browser.key" {
            let focus=session.cdp.call("Runtime.evaluate",json!({"expression":"document.activeElement?.tagName","uniqueContextId":context,"returnByValue":true}),Some(&page_session),cancel).await?;
            if matches!(focus["result"]["value"].as_str(), Some("IFRAME" | "FRAME")) {
                return Err(RacpError::new("FOCUS_MISMATCH"));
            }
            let (modifiers, key, mask) = super::keys::parse(p["key"].as_str().unwrap_or(""))?;
            effect.store(true, std::sync::atomic::Ordering::SeqCst);
            let pressed = async {
                for modifier in &modifiers {
                    session
                        .cdp
                        .call(
                            "Input.dispatchKeyEvent",
                            super::keys::event(modifier, "keyDown", mask),
                            Some(&page_session),
                            cancel,
                        )
                        .await?;
                }
                session
                    .cdp
                    .call(
                        "Input.dispatchKeyEvent",
                        super::keys::event(&key, "keyDown", mask),
                        Some(&page_session),
                        cancel,
                    )
                    .await?;
                Ok::<_, RacpError>(())
            }
            .await;
            // Release with an independent budget even when the operation was cancelled.
            let release = tokio::time::timeout(std::time::Duration::from_secs(2), async {
                let cleanup = CancellationToken::new();
                session
                    .cdp
                    .call(
                        "Input.dispatchKeyEvent",
                        super::keys::event(&key, "keyUp", mask),
                        Some(&page_session),
                        &cleanup,
                    )
                    .await?;
                for modifier in modifiers.iter().rev() {
                    session
                        .cdp
                        .call(
                            "Input.dispatchKeyEvent",
                            super::keys::event(modifier, "keyUp", 0),
                            Some(&page_session),
                            &cleanup,
                        )
                        .await?;
                }
                Ok::<_, RacpError>(())
            })
            .await;
            if !matches!(release, Ok(Ok(()))) {
                session.close().await?;
                return Err(RacpError::new("EXECUTION_UNKNOWN"));
            }
            pressed?;
            return Ok(json!({"browser_id":p["browser_id"],"page_id":page_id}));
        }
        if action == "browser.snapshot" {
            // Refs remain native remote objects, scoped to this observation.
            let observation = new_id("obs");
            let array=session.cdp.call("Runtime.evaluate",json!({"expression":"Array.from(document.querySelectorAll('a,button,input,textarea,select,[role],[contenteditable=true]')).slice(0,64)","uniqueContextId":context,"objectGroup":observation}),Some(&page_session),cancel).await?;
            let object = array["result"]["objectId"]
                .as_str()
                .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?;
            let properties = session
                .cdp
                .call(
                    "Runtime.getProperties",
                    json!({"objectId":object,"ownProperties":true}),
                    Some(&page_session),
                    cancel,
                )
                .await?;
            let mut elements = vec![];
            let mut refs = std::collections::BTreeMap::new();
            for property in properties["result"]
                .as_array()
                .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?
                .iter()
                .filter(|p| p["name"].as_str().is_some_and(|n| n.parse::<u8>().is_ok()))
            {
                let Some(object) = property["value"]["objectId"].as_str() else {
                    continue;
                };
                let metadata=session.cdp.call("Runtime.callFunctionOn",json!({"objectId":object,"functionDeclaration":"function(){return {tag:this.tagName.toLowerCase(),role:(this.getAttribute('role')||'').slice(0,128),type:(this.getAttribute('type')||'').slice(0,128),name:(this.getAttribute('aria-label')||this.labels?.[0]?.textContent||this.innerText||'').slice(0,80),test_id:(this.getAttribute('data-testid')||'').slice(0,80),focused:document.activeElement===this};}","returnByValue":true}),Some(&page_session),cancel).await?;
                let mut metadata = metadata["result"]["value"].clone();
                if !metadata.is_object() {
                    return Err(RacpError::new("BROWSER_ERROR"));
                }
                for value in metadata.as_object_mut().unwrap().values_mut() {
                    if let Some(s) = value.as_str() {
                        *value = json!(bounded(s, 256));
                    }
                }
                let id = new_id("ref");
                metadata["ref"] = json!(id);
                refs.insert(id, object.into());
                elements.push(metadata);
            }
            let metadata=session.cdp.call("Runtime.evaluate",json!({"expression":"({title:document.title.slice(0,512),url:location.href.slice(0,8192)})","uniqueContextId":context,"returnByValue":true}),Some(&page_session),cancel).await?;
            let tree = session
                .cdp
                .call(
                    "Accessibility.getFullAXTree",
                    json!({"frameId":frame_native,"depth":20}),
                    Some(&page_session),
                    cancel,
                )
                .await?;
            let mut semantic = String::new();
            for node in tree["nodes"].as_array().into_iter().flatten() {
                if node["ignored"] == true {
                    continue;
                }
                let name = node["name"]["value"].as_str().unwrap_or("");
                let role = node["role"]["value"].as_str().unwrap_or("");
                if semantic.len() + name.len() + role.len() + 4 > 8192 {
                    break;
                }
                semantic.push_str(&format!("- {role} {name}\n"));
            }
            let previous;
            let frames;
            let page_url;
            {
                let mut state = session
                    .state
                    .lock()
                    .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
                let page = state
                    .pages
                    .get_mut(page_id)
                    .ok_or_else(|| RacpError::new("HANDLE_EXPIRED"))?;
                if page.revision != revision {
                    return Err(RacpError::new("STALE_OBSERVATION"));
                }
                previous =
                    page.observation
                        .replace((observation.clone(), frame_id.clone(), revision));
                page.refs = refs;
                frames = frame_inventory(page);
                page_url = page
                    .frames
                    .get(&page.main)
                    .map(|f| bounded(&f.url, 8192))
                    .unwrap_or_default();
            }
            if let Some((previous, _, _)) = previous {
                let _ = session
                    .cdp
                    .call(
                        "Runtime.releaseObjectGroup",
                        json!({"objectGroup":previous}),
                        Some(&page_session),
                        cancel,
                    )
                    .await;
            }
            let focused = elements
                .iter()
                .find(|e| e["focused"] == true)
                .map(|e| e["ref"].clone());
            let result = json!({"browser_id":p["browser_id"],"page_id":page_id,"observation_id":observation,"navigation_revision":revision.to_string(),"frame_id":frame_id,"url":metadata["result"]["value"]["url"],"page_url":page_url,"title":metadata["result"]["value"]["title"],"frames":frames.into_iter().take(16).collect::<Vec<_>>(),"semantic_tree":semantic,"elements":elements,"focused_ref":focused,"viewport":{"width":session.width,"height":session.height},"truncated":false,"trust":"untrusted_page_data"});
            if result.to_string().len() > 48 * 1024 {
                return Err(RacpError::new("RESOURCE_EXHAUSTED"));
            }
            return Ok(result);
        }
        Err(RacpError::new("CAPABILITY_UNAVAILABLE"))
    }
}
pub(super) fn frame_inventory(page: &Page) -> Vec<Value> {
    page.frames.values().filter(|f|f.active).map(|f|json!({"frame_id":f.id,"parent_frame_id":f.parent.as_ref().and_then(|parent|page.frames.values().find(|f|&f.native==parent)).map(|f|&f.id),"main":f.id==page.main,"url":bounded(&f.url,256),"name":bounded(&f.name,64)})).collect()
}

pub(super) async fn element(
    session: &super::session::Session,
    p: &Value,
    page_id: &str,
    context: &str,
    frame_native: &str,
    page_session: &str,
    cancel: &CancellationToken,
) -> Result<String, RacpError> {
    let object = if let Some(id) = p["ref"].as_str() {
        session
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .pages[page_id]
            .refs
            .get(id)
            .cloned()
            .ok_or_else(|| RacpError::new("STALE_OBSERVATION"))?
    } else if p["selector"]["by"] == "test_id" {
        let value = serde_json::to_string(&p["selector"]["value"])?;
        let result=session.cdp.call("Runtime.evaluate",json!({"expression":format!("Array.from(document.querySelectorAll('[data-testid]')).filter(e=>e.getAttribute('data-testid')==={value})"),"uniqueContextId":context}),Some(page_session),cancel).await?;
        let props = session
            .cdp
            .call(
                "Runtime.getProperties",
                json!({"objectId":result["result"]["objectId"],"ownProperties":true}),
                Some(page_session),
                cancel,
            )
            .await?;
        let matches = props["result"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|p| p["name"].as_str().is_some_and(|v| v.parse::<u32>().is_ok()))
            .filter_map(|p| p["value"]["objectId"].as_str())
            .collect::<Vec<_>>();
        if matches.len() != 1 {
            return Err(RacpError::new(if matches.is_empty() {
                "ELEMENT_NOT_FOUND"
            } else {
                "AMBIGUOUS_TARGET"
            }));
        }
        matches[0].to_owned()
    } else {
        let tree = session
            .cdp
            .call(
                "Accessibility.getFullAXTree",
                json!({"frameId":frame_native}),
                Some(page_session),
                cancel,
            )
            .await?;
        let matches = tree["nodes"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|n| {
                n["ignored"] != true
                    && n["role"]["value"] == p["selector"]["value"]
                    && (p["selector"]["name"].is_null()
                        || n["name"]["value"] == p["selector"]["name"])
            })
            .filter_map(|n| n["backendDOMNodeId"].as_u64())
            .collect::<Vec<_>>();
        if matches.len() != 1 {
            return Err(RacpError::new(if matches.is_empty() {
                "ELEMENT_NOT_FOUND"
            } else {
                "AMBIGUOUS_TARGET"
            }));
        }
        let resolved = session
            .cdp
            .call(
                "DOM.resolveNode",
                json!({"backendNodeId":matches[0]}),
                Some(page_session),
                cancel,
            )
            .await?;
        resolved["object"]["objectId"]
            .as_str()
            .ok_or_else(|| RacpError::new("ELEMENT_NOT_FOUND"))?
            .into()
    };

    Ok(object)
}

pub(super) async fn click(
    session: &super::session::Session,
    object: &str,
    page_session: &str,
    cancel: &CancellationToken,
    effect: &std::sync::atomic::AtomicBool,
) -> Result<(), RacpError> {
    let described = session
        .cdp
        .call(
            "DOM.requestNode",
            json!({"objectId":object}),
            Some(page_session),
            cancel,
        )
        .await?;
    effect.store(true, std::sync::atomic::Ordering::SeqCst);
    session
        .cdp
        .call(
            "DOM.scrollIntoViewIfNeeded",
            json!({"objectId":object}),
            Some(page_session),
            cancel,
        )
        .await?;
    let _ = described;
    let box_model = session
        .cdp
        .call(
            "DOM.getBoxModel",
            json!({"objectId":object}),
            Some(page_session),
            cancel,
        )
        .await?;
    let quad = box_model["model"]["content"]
        .as_array()
        .ok_or_else(|| RacpError::new("ELEMENT_NOT_FOUND"))?;
    if quad.len() != 8 {
        return Err(RacpError::new("ELEMENT_NOT_FOUND"));
    }
    let x = (quad[0].as_f64().unwrap_or(0.0) + quad[4].as_f64().unwrap_or(0.0)) / 2.0;
    let y = (quad[1].as_f64().unwrap_or(0.0) + quad[5].as_f64().unwrap_or(0.0)) / 2.0;
    effect.store(true, std::sync::atomic::Ordering::SeqCst);
    for kind in ["mouseMoved", "mousePressed", "mouseReleased"] {
        session
            .cdp
            .call(
                "Input.dispatchMouseEvent",
                json!({"type":kind,"x":x,"y":y,"button":"left","clickCount":1}),
                Some(page_session),
                cancel,
            )
            .await?;
    }

    Ok(())
}
