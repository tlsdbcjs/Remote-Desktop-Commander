use futures_util::SinkExt;
use racp_contract::{decode_message, RacpError};
use serde_json::Value;
use tokio::{
    sync::{mpsc, oneshot},
    time::{timeout, Duration},
};
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;
struct Envelope {
    value: Value,
    done: oneshot::Sender<Result<(), RacpError>>,
}
#[derive(Clone)]
pub(crate) struct Peer {
    control: mpsc::Sender<Envelope>,
    data: mpsc::Sender<Envelope>,
    cancel: CancellationToken,
    task: std::sync::Arc<tokio::sync::Mutex<Option<tokio::task::JoinHandle<()>>>>,
}
impl Peer {
    pub fn spawn<S>(mut sink: S, cancel: CancellationToken) -> Self
    where
        S: futures_util::Sink<Message> + Send + Unpin + 'static,
        S::Error: Send + 'static,
    {
        let (control, mut controls) = mpsc::channel::<Envelope>(128);
        let (data, mut frames) = mpsc::channel::<Envelope>(8);
        let writer_cancel = cancel.clone();
        let task = tokio::spawn(async move {
            loop {
                let entry = tokio::select! {biased;_ =writer_cancel.cancelled()=>break,v=controls.recv()=>v,v=frames.recv()=>v};
                let Some(entry) = entry else { break };
                let result = timeout(
                    Duration::from_secs(5),
                    sink.send(Message::Text(entry.value.to_string().into())),
                )
                .await;
                let success = matches!(result, Ok(Ok(())));
                let _ = entry.done.send(if success {
                    Ok(())
                } else {
                    Err(RacpError::new("DEVICE_OFFLINE"))
                });
                if !success {
                    break;
                }
            }
            let _ = timeout(Duration::from_secs(1), sink.close()).await;
        });
        Self {
            control,
            data,
            cancel,
            task: std::sync::Arc::new(tokio::sync::Mutex::new(Some(task))),
        }
    }
    pub async fn close(&self) {
        self.cancel.cancel();
        if let Some(task) = self.task.lock().await.take() {
            let _ = task.await;
        }
    }
    pub async fn send(&self, value: Value) -> Result<(), RacpError> {
        let raw = serde_json::to_vec(&value)?;
        decode_message(&raw)?;
        let (done, reply) = oneshot::channel();
        let data = value["type"] == "stream_data";
        let entry = Envelope { value, done };
        if data {
            self.data
                .send(entry)
                .await
                .map_err(|_| RacpError::new("DEVICE_OFFLINE"))?;
        } else {
            self.control
                .try_send(entry)
                .map_err(|_| RacpError::new("RESOURCE_EXHAUSTED"))?;
        }
        reply.await.map_err(|_| RacpError::new("DEVICE_OFFLINE"))?
    }
}
