use super::{
    operations::{bounded, click},
    session::Session,
    BrowserConfig,
};
use racp_contract::RacpError;
use serde_json::{json, Value};
use tokio_util::sync::CancellationToken;
pub(super) struct Download {
    page: String,
    max: u64,
    guid: Option<String>,
    filename: String,
    completed: bool,
    error: Option<RacpError>,
}
impl Download {
    pub fn page(&self) -> &str {
        &self.page
    }
}
fn guid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(n, c)| {
            if [8, 13, 18, 23].contains(&n) {
                c == b'-'
            } else {
                c.is_ascii_hexdigit()
            }
        })
}
impl Session {
    pub(super) async fn watch_downloads(
        &self,
        cancel: &CancellationToken,
    ) -> Result<(), RacpError> {
        let directory = self.profile.join("downloads");
        racp_core::private_dir(&directory)?;
        self.cdp.call("Browser.setDownloadBehavior",json!({"behavior":"allowAndName","downloadPath":directory,"browserContextId":self.context,"eventsEnabled":true}),None,cancel).await?;
        Ok(())
    }
    pub(super) async fn upload(
        &self,
        request: &Value,
        object: &str,
        page_session: &str,
        spool: &std::path::Path,
        cancel: &CancellationToken,
        effect: &std::sync::atomic::AtomicBool,
    ) -> Result<Value, RacpError> {
        let p = &request["payload"];
        if self
            .state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .uploads
            >= 16
        {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        let input = p["_artifact_path"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let expected = spool.join(format!(
            "{}.input",
            request["operation_id"].as_str().unwrap_or("op_invalid")
        ));
        if std::path::Path::new(input) != expected {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let size = p["_artifact_size"]
            .as_u64()
            .filter(|size| *size <= 1024 * 1024 * 1024)
            .ok_or_else(|| RacpError::new("INTEGRITY_ERROR"))?;
        let checksum = p["_artifact_sha256"]
            .as_str()
            .ok_or_else(|| RacpError::new("INTEGRITY_ERROR"))?;
        let filename = p["filename"]
            .as_str()
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let directory = self
            .profile
            .join("uploads")
            .join(racp_contract::new_id("upload"));
        racp_core::private_dir(&directory)?;
        let path = directory.join(filename);
        let work=async {
            let (size,checksum)=copy(std::path::Path::new(input),&path,1024*1024*1024,Some((size,checksum)),cancel)?;
            let valid=self.cdp.call("Runtime.callFunctionOn",json!({"objectId":object,"functionDeclaration":"function(){return this.isConnected && this.tagName==='INPUT' && this.type==='file';}","returnByValue":true}),Some(page_session),cancel).await?;
            if valid["result"]["value"]!=true {return Err(RacpError::new("ELEMENT_NOT_FOUND"));}
            effect.store(true, std::sync::atomic::Ordering::SeqCst);
            self.cdp.call("DOM.setFileInputFiles",json!({"objectId":object,"files":[path]}),Some(page_session),cancel).await?;
            self.state.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?.uploads+=1;
            self.emit("download","files_verified");
            Ok(json!({"input_artifact_id":p["artifact_id"],"filename":filename,"size_bytes":size,"sha256":checksum,"staging_retention":"browser_lifetime"}))
        }.await;
        if work.is_err() {
            std::fs::remove_dir_all(&directory)?;
        }
        work
    }
    pub(super) async fn download_event(
        &self,
        method: &str,
        p: &Value,
        config: &BrowserConfig,
    ) -> Result<(), RacpError> {
        let id = p["guid"]
            .as_str()
            .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?;
        if !guid(id) {
            return Err(RacpError::new("BROWSER_ERROR"));
        }
        let mut cancel = false;
        let mut unsolicited = false;
        let mut cancel_context = self.context.clone();
        {
            let mut state = self
                .state
                .lock()
                .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?;
            if method == "Browser.downloadWillBegin" {
                let page = state
                    .pages
                    .iter()
                    .find(|(_, page)| {
                        page.frames
                            .values()
                            .any(|frame| frame.active && frame.native == p["frameId"])
                    })
                    .map(|(id, _)| id.clone());
                let Some(page_id) = page.as_ref() else {
                    return Ok(());
                };
                cancel_context = state.pages[page_id].browser_context.clone();

                if let Some(download) = state
                    .download
                    .as_mut()
                    .filter(|d| Some(&d.page) == page.as_ref() && d.guid.is_none())
                {
                    let url = p["url"].as_str().unwrap_or("");
                    let allowed = config.allowed(url)
                        || url
                            .strip_prefix("blob:")
                            .is_some_and(|url| config.allowed(url));
                    download.guid = Some(id.into());
                    download.filename = bounded(p["suggestedFilename"].as_str().unwrap_or(""), 256);
                    if !allowed {
                        cancel = true;
                        download.error = Some(RacpError::new("PERMISSION_DENIED"));
                    }
                } else {
                    cancel = true;
                    unsolicited = true;
                }
            } else if let Some(download) = state
                .download
                .as_mut()
                .filter(|d| d.guid.as_deref() == Some(id))
            {
                let received = p["receivedBytes"]
                    .as_f64()
                    .filter(|n| n.is_finite() && *n >= 0.0)
                    .ok_or_else(|| RacpError::new("BROWSER_ERROR"))?;
                let total = p["totalBytes"]
                    .as_f64()
                    .filter(|n| n.is_finite() && *n >= 0.0)
                    .unwrap_or(0.0);
                if (received > download.max as f64 || total > download.max as f64)
                    && download.error.is_none()
                {
                    download.error = Some(RacpError::new("RESOURCE_EXHAUSTED"));
                    cancel = true;
                }
                if matches!(p["state"].as_str(), Some("completed" | "canceled")) {
                    download.completed = true;
                }
                if p["state"] == "canceled" && download.error.is_none() {
                    download.error = Some(RacpError::new("BROWSER_ERROR"));
                }
            }
        }
        if cancel {
            let mut arguments = json!({"guid":id});
            if !cancel_context.is_empty() {
                arguments["browserContextId"] = json!(cancel_context);
            }
            self.cdp
                .call("Browser.cancelDownload", arguments, None, &self.stop)
                .await?;
            if unsolicited {
                self.emit("download", "unsolicited_cancelled");
            }
        }
        self.notify.notify_waiters();
        Ok(())
    }
    pub(super) async fn download(
        &self,
        request: &Value,
        object: &str,
        page_session: &str,
        spool: &std::path::Path,
        cancel: &CancellationToken,
        effect: &std::sync::atomic::AtomicBool,
    ) -> Result<Value, RacpError> {
        let p = &request["payload"];
        let page = p["page_id"].as_str().unwrap_or("");
        let max = p["max_bytes"].as_u64().unwrap_or(1024 * 1024 * 1024);
        let directory = self.profile.join("downloads");
        racp_core::private_dir(&directory)?;
        self.state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .download = Some(Download {
            page: page.into(),
            max,
            guid: None,
            filename: String::new(),
            completed: false,
            error: None,
        });
        let work=async {
            self.cdp.call("Browser.setDownloadBehavior",json!({"behavior":"allowAndName","downloadPath":directory,"browserContextId":self.context,"eventsEnabled":true}),None,cancel).await?;
            click(self,object,page_session,cancel,effect).await?;
            loop {
                let notified=self.notify.notified();
                let complete=self.state.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?.download.as_ref().is_some_and(|d|d.completed);
                if complete {break;}
                tokio::select!{_=cancel.cancelled()=>return Err(RacpError::new("CANCELLED")),_=self.stop.cancelled()=>return Err(RacpError::new("BROWSER_ERROR")),_=notified=>{}}
            }
            let (id,filename,error)={let state=self.state.lock().map_err(|_|RacpError::new("LOCAL_STATE_FAILED"))?;let download=state.download.as_ref().ok_or_else(||RacpError::new("BROWSER_ERROR"))?;(download.guid.clone().ok_or_else(||RacpError::new("BROWSER_ERROR"))?,download.filename.clone(),download.error)};
            if let Some(error)=error {return Err(error);}
            let source=directory.join(&id);
            let output=spool.join(format!("{}.download",request["operation_id"].as_str().unwrap_or("op_invalid")));
            let copied=copy(&source,&output,max,None,cancel);
            if copied.is_err(){let _=std::fs::remove_file(&output);}
            let (size,sha256)=copied?;
            std::fs::remove_file(&source)?;
            self.emit("download","completed");
            Ok(json!({"artifact_id":null,"spool_path":output,"artifact_media_type":"application/octet-stream","size_bytes":size,"sha256":sha256,"suggested_filename":filename,"native_cleanup":"complete","trust":"untrusted_page_data"}))
        }.await;
        let reset = self.watch_downloads(&self.stop).await;
        self.state
            .lock()
            .map_err(|_| RacpError::new("LOCAL_STATE_FAILED"))?
            .download = None;
        if work.is_err() {
            self.close().await?;
        }
        reset?;
        work
    }
}
fn copy(
    source: &std::path::Path,
    output: &std::path::Path,
    max: u64,
    expected: Option<(u64, &str)>,
    cancel: &CancellationToken,
) -> Result<(u64, String), RacpError> {
    use sha2::{Digest, Sha256};
    use std::io::{Read, Write};
    let mut source = racp_core::secure_read_file(source)?;
    let mut output = racp_core::secure_create_file(output)?;
    let mut buffer = vec![0; 4 * 1024 * 1024];
    let mut size = 0u64;
    let mut hash = Sha256::new();
    loop {
        if cancel.is_cancelled() {
            return Err(RacpError::new("CANCELLED"));
        }
        let n = source.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        size += n as u64;
        if size > max {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        hash.update(&buffer[..n]);
        output.write_all(&buffer[..n])?;
    }
    let sha256 = format!("{:x}", hash.finalize());
    if expected.is_some_and(|(length, checksum)| length != size || checksum != sha256) {
        return Err(RacpError::new("INTEGRITY_ERROR"));
    }
    output.sync_all()?;
    Ok((size, sha256))
}
