//! Connection-scoped streams retain raw byte cursors and cumulative chunk credits.
use crate::{peer::Peer, Agent};
use racp_contract::RacpError;
use racp_core::error_value;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, VecDeque},
    sync::Arc,
    time::Duration,
};
use tokio::sync::Mutex;
use tokio_util::sync::CancellationToken;
struct Window {
    cursor: u64,
    consumed: u64,
    pending: VecDeque<u64>,
}
impl Window {
    fn ack(&mut self, offset: u64) -> Result<(), RacpError> {
        if offset <= self.consumed {
            return Ok(());
        }
        if !self.pending.contains(&offset) {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        self.pending.retain(|p| *p > offset);
        self.consumed = offset;
        Ok(())
    }
    fn available(&self, window: u64) -> u64 {
        if self.pending.len() >= 4 {
            0
        } else {
            window.saturating_sub(self.cursor - self.consumed)
        }
    }
}
struct Subscription {
    request: Value,
    window: Mutex<Window>,
    cancel: CancellationToken,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
}
#[derive(Clone)]
pub(crate) struct Streams {
    agent: Agent,
    peer: Peer,
    active: Arc<Mutex<BTreeMap<String, Arc<Subscription>>>>,
}
fn identity(request: &Value, kind: &str) -> Value {
    let mut message = json!({"protocol":1,"type":kind});
    for key in [
        "device_id",
        "agent_boot_id",
        "connection_epoch",
        "stream_id",
        "handle_id",
    ] {
        message[key] = request[key].clone();
    }
    message
}
fn probe(request: &Value, cursor: u64, max: u64) -> Value {
    json!({"context":request["context"],"payload":{"handle_id":request["handle_id"],"agent_boot_id":request["agent_boot_id"],"cursor":cursor.to_string(),"max_bytes":max}})
}
impl Streams {
    pub fn new(agent: Agent, peer: Peer) -> Self {
        Self {
            agent,
            peer,
            active: Arc::new(Mutex::new(BTreeMap::new())),
        }
    }
    async fn send(&self, subscription: &Subscription, message: Value) -> Result<(), RacpError> {
        tokio::select! {_=subscription.cancel.cancelled()=>Err(RacpError::new("DEVICE_OFFLINE")),sent=self.peer.send(message)=>sent}
    }
    pub async fn subscribe(&self, request: Value) -> Result<(), RacpError> {
        let id = request["stream_id"]
            .as_str()
            .ok_or_else(|| RacpError::new("REQUEST_INVALID"))?
            .to_owned();
        let cursor = request["cursor"]
            .as_str()
            .and_then(|s| s.parse::<u64>().ok())
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let mut active = self.active.lock().await;
        let invalid = if active.contains_key(&id) {
            Some(error_value(
                "CONFLICT",
                "stream already active",
                "not_started",
            ))
        } else if active.len() >= 16 {
            Some(error_value(
                "RESOURCE_EXHAUSTED",
                "stream limit reached",
                "not_started",
            ))
        } else if tokio::time::Instant::now() >= *self.agent.lease.lock().await {
            Some(error_value(
                "DEVICE_OFFLINE",
                "execution lease expired",
                "not_started",
            ))
        } else {
            self.agent
                .providers
                .stream_read(&probe(&request, cursor, 4))
                .err()
        };
        if let Some(error) = invalid {
            drop(active);
            let mut end = identity(&request, "stream_end");
            end["byte_offset"] = json!(cursor.to_string());
            end["reason"] = json!("error");
            end["error"] = error;
            end["process_exit"] = Value::Null;
            return self.peer.send(end).await;
        }
        let subscription = Arc::new(Subscription {
            request,
            window: Mutex::new(Window {
                cursor,
                consumed: cursor,
                pending: VecDeque::new(),
            }),
            cancel: CancellationToken::new(),
            task: Mutex::new(None),
        });
        active.insert(id.clone(), subscription.clone());
        drop(active);
        let this = self.clone();
        let target = subscription.clone();
        let task = tokio::spawn(async move {
            this.run(&target).await;
            if this
                .active
                .lock()
                .await
                .get(&id)
                .is_some_and(|s| Arc::ptr_eq(s, &target))
            {
                this.active.lock().await.remove(&id);
            }
        });
        *subscription.task.lock().await = Some(task);
        Ok(())
    }
    pub async fn ack(&self, message: &Value) -> Result<(), RacpError> {
        let active = self.active.lock().await;
        let Some(s) = active.get(message["stream_id"].as_str().unwrap_or("")) else {
            return Ok(());
        };
        if message["handle_id"] != s.request["handle_id"] {
            return Err(RacpError::new("PERMISSION_DENIED"));
        }
        let offset = message["byte_offset"]
            .as_str()
            .and_then(|s| s.parse::<u64>().ok())
            .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
        let result = s.window.lock().await.ack(offset);
        result
    }
    pub async fn unsubscribe(&self, message: &Value) -> Result<(), RacpError> {
        let mut active = self.active.lock().await;
        let id = message["stream_id"].as_str().unwrap_or("");
        if let Some(s) = active.get(id) {
            if message["handle_id"] != s.request["handle_id"] {
                return Err(RacpError::new("PERMISSION_DENIED"));
            }
            s.cancel.cancel();
        }
        let subscription = active.remove(id);
        drop(active);
        if let Some(s) = subscription {
            if let Some(task) = s.task.lock().await.take() {
                let _ = task.await;
            }
        }
        Ok(())
    }
    async fn run(&self, subscription: &Subscription) {
        let request = &subscription.request;
        let max = request["max_bytes"].as_u64().unwrap_or(65536);
        let size = request["window_bytes"].as_u64().unwrap_or(262144);
        let mut opened = identity(request, "stream_opened");
        opened["cursor"] = request["cursor"].clone();
        opened["max_bytes"] = json!(max.min(size));
        opened["window_bytes"] = json!(size);
        if self.send(subscription, opened).await.is_err() {
            return;
        }
        let work=async{
        loop{
            if tokio::time::Instant::now()>=*self.agent.lease.lock().await{return Err(error_value("DEVICE_OFFLINE","execution lease expired","not_started"));}
            let(cursor,remaining)={let window=subscription.window.lock().await;(window.cursor,window.available(size))};
            if remaining>=4{
                let output=match self.agent.providers.stream_read(&probe(request,cursor,max.min(remaining))){Ok(output)=>output,Err(e)=>{
                    if e["code"]=="CURSOR_EXPIRED"{let mut gap=identity(request,"stream_gap");gap["byte_offset"]=json!(cursor.to_string());gap["earliest_cursor"]=e["details"]["earliest_cursor"].clone();gap["lost_bytes"]=e["details"]["lost_bytes"].clone();let _=self.send(subscription,gap).await;}return Err(e);
                }};
                let next=output["next_cursor"].as_str().and_then(|s|s.parse::<u64>().ok()).ok_or_else(||error_value("RUNTIME_RESPONSE_INVALID","invalid terminal cursor","unknown"))?;
                if next>cursor{
                    {let mut window=subscription.window.lock().await;window.cursor=next;window.pending.push_back(next);}
                    let mut data=identity(request,"stream_data");data["byte_offset"]=json!(cursor.to_string());for key in ["next_cursor","data","invalid_byte_replacements","eof","process_exit"]{data[key]=output[key].clone();}
                    self.send(subscription,data).await.map_err(|e|error_value(e.code.0,"stream connection closed","unknown"))?;
                }
                if output["eof"]==true{let mut end=identity(request,"stream_end");end["byte_offset"]=json!(next.to_string());end["reason"]=json!("eof");end["error"]=Value::Null;end["process_exit"]=output["process_exit"].clone();let _=self.send(subscription,end).await;return Ok::<(),Value>(());}
                if next>cursor{continue;}
            }
            tokio::select!{_=subscription.cancel.cancelled()=>return Ok(()),_=tokio::time::sleep(Duration::from_millis(20))=>{}}
        }
    }.await;
        if let Err(error) = work {
            let mut end = identity(request, "stream_end");
            end["byte_offset"] = json!(subscription.window.lock().await.cursor.to_string());
            end["reason"] = json!("error");
            end["error"] = error;
            end["process_exit"] = Value::Null;
            let _ = self.send(subscription, end).await;
        }
    }
    pub async fn close(&self) {
        let active = std::mem::take(&mut *self.active.lock().await);
        for s in active.values() {
            s.cancel.cancel();
        }
        for s in active.values() {
            if let Some(task) = s.task.lock().await.take() {
                let _ = task.await;
            }
        }
    }
}
#[cfg(test)]
mod tests {
    #[test]
    fn cumulative_credit_requires_a_sent_boundary_and_caps_four_frames() {
        let mut window = super::Window {
            cursor: 256,
            consumed: 0,
            pending: std::collections::VecDeque::from([64, 128, 192, 256]),
        };
        assert_eq!(window.available(1024), 0);
        assert!(window.ack(63).is_err());
        assert_eq!(window.consumed, 0);
        window.ack(192).unwrap();
        assert_eq!(window.pending.len(), 1);
        assert_eq!(window.available(1024), 960);
        window.ack(128).unwrap();
        assert_eq!(window.consumed, 192);
        assert!(window.ack(300).is_err());
    }
}
