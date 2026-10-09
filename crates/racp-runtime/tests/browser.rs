use futures_util::{SinkExt, StreamExt};
use racp_runtime::providers::browser::{cdp::Cdp, BrowserConfig};
use racp_runtime::providers::{browser::Browser, Provider};
use serde_json::json;
use tokio_util::sync::CancellationToken;

fn setup() -> (tempfile::TempDir, Browser) {
    let root = tempfile::tempdir().unwrap();
    let settings: racp_core::AgentSettings = serde_json::from_value(json!({"version":1,"gateway":"http://localhost:1234","device_id":"dev_browser","workspace":root.path(),"data_dir":root.path().join("data"),"profile":"trusted_personal","allowed_workspaces":[],"ca_file":null,"desktop_enabled":false})).unwrap();
    let executable = std::env::var_os("RACP_TEST_CHROMIUM_PATH")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../.tools/playwright/chromium-1243/chrome-linux64/chrome")
        });
    assert!(
        executable.is_file(),
        "native test Chromium must be staged before running browser tests"
    );
    let config = BrowserConfig {
        executable: Some(executable),
        ..Default::default()
    };
    (
        root,
        Browser::new(&settings, "boot_browser", config).unwrap(),
    )
}
async fn execute(
    browser: &Browser,
    action: &str,
    payload: serde_json::Value,
    owner: &str,
) -> serde_json::Value {
    let operation = format!("browser.{action}");
    browser.execute(json!({"operation":operation,"payload":racp_contract::validate_operation(&operation,payload).unwrap(),"operation_id":racp_contract::new_id("op"),"remaining_timeout_ms":15000,"context":{"workspace_id":"default","principal_id":owner}}),CancellationToken::new()).await.unwrap()
}
fn succeeded(result: serde_json::Value) -> serde_json::Value {
    assert_eq!(result["state"], "SUCCEEDED", "{result}");
    result["result"].clone()
}
fn observed(opened: &serde_json::Value, snapshot: &serde_json::Value) -> serde_json::Value {
    json!({"browser_id":opened["browser_id"],"page_id":opened["pages"][0]["page_id"],"observation_id":snapshot["observation_id"],"navigation_revision":snapshot["navigation_revision"]})
}
#[tokio::test]
async fn isolated_contexts_owner_boot_snapshot_and_complete_cleanup() {
    let (_root, browser) = setup();
    let first = succeeded(execute(&browser, "open", json!({}), "owner_one").await);
    let second = succeeded(execute(&browser, "open", json!({}), "owner_one").await);
    let page = |opened: &serde_json::Value| json!({"browser_id":opened["browser_id"],"page_id":opened["pages"][0]["page_id"]});
    let denied = execute(
        &browser,
        "pages",
        json!({"browser_id":first["browser_id"]}),
        "foreign",
    )
    .await;
    assert_eq!(denied["error"]["code"], "PERMISSION_DENIED");
    let expired = execute(
        &browser,
        "pages",
        json!({"browser_id":first["browser_id"],"agent_boot_id":"old"}),
        "owner_one",
    )
    .await;
    assert_eq!(expired["error"]["code"], "HANDLE_EXPIRED");
    let one = succeeded(execute(&browser, "snapshot", page(&first), "owner_one").await);
    let two = succeeded(execute(&browser, "snapshot", page(&second), "owner_one").await);
    assert_ne!(one["observation_id"], two["observation_id"]);
    let mut target = observed(&first, &one);
    target["expression"] = json!("() => {window.privateValue='one'; return window.privateValue;}");
    assert_eq!(
        succeeded(execute(&browser, "evaluate", target, "owner_one").await)["value"],
        "one"
    );
    let mut target = observed(&second, &two);
    target["expression"] = json!("() => window.privateValue || null");
    assert!(succeeded(execute(&browser, "evaluate", target, "owner_one").await)["value"].is_null());
    for opened in [first, second] {
        let closed = succeeded(
            execute(
                &browser,
                "close",
                json!({"browser_id":opened["browser_id"]}),
                "owner_one",
            )
            .await,
        );
        assert_eq!(closed["cleanup_status"], "complete");
    }
    assert!(browser.inventory().iter().all(|h| h["state"] == "CLOSED"));
    browser.cleanup().await.unwrap();
}

#[tokio::test]
async fn cookies_form_refs_native_input_png_and_navigation_invalidation() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            tokio::spawn(async move {
                use tokio::io::{AsyncReadExt, AsyncWriteExt};
                let mut request = [0; 8192];
                let _ = socket.read(&mut request).await;
                let body="<!doctype html><title>Rust CDP</title><label>Name<input data-testid='name'></label><button onclick=\"document.querySelector('p').textContent='Hello '+document.querySelector('input').value\">Submit</button><button>Repeated</button><button>Repeated</button><p></p>";
                socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n{}",body.len(),body).as_bytes()).await.unwrap();
            });
        }
    });
    let (_root, browser) = setup();
    let first = succeeded(execute(&browser, "open", json!({}), "owner_one").await);
    let second = succeeded(execute(&browser, "open", json!({}), "owner_one").await);
    let page = |opened: &serde_json::Value| json!({"browser_id":opened["browser_id"],"page_id":opened["pages"][0]["page_id"]});
    for opened in [&first, &second] {
        let mut target = page(opened);
        target["url"] = json!(url);
        succeeded(execute(&browser, "navigate", target, "owner_one").await);
    }
    let snapshot = succeeded(execute(&browser, "snapshot", page(&first), "owner_one").await);
    assert_eq!(snapshot["title"], "Rust CDP");
    assert!(snapshot["semantic_tree"].as_str().unwrap().contains("Name"));
    let input = snapshot["elements"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["test_id"] == "name")
        .unwrap();
    let mut target = observed(&first, &snapshot);
    target["ref"] = input["ref"].clone();
    target["text"] = json!("한글 🙂");
    succeeded(execute(&browser, "type", target, "owner_one").await);
    let mut target = observed(&first, &snapshot);
    target["selector"] = json!({"by":"role","value":"button","name":"Submit"});
    succeeded(execute(&browser, "click", target, "owner_one").await);
    let after = succeeded(execute(&browser, "snapshot", page(&first), "owner_one").await);
    assert!(after["semantic_tree"]
        .as_str()
        .unwrap()
        .contains("Hello 한글 🙂"));
    let mut target = observed(&first, &snapshot);
    target["expression"] = json!("1");
    assert_eq!(
        execute(&browser, "evaluate", target, "owner_one").await["error"]["code"],
        "STALE_OBSERVATION"
    );
    let mut target = observed(&first, &after);
    target["selector"] = json!({"by":"role","value":"button","name":"Repeated"});
    assert_eq!(
        execute(&browser, "click", target, "owner_one").await["error"]["code"],
        "AMBIGUOUS_TARGET"
    );
    let mut target = observed(&first, &after);
    target["expression"] =
        json!("() => {document.cookie='private=one; path=/'; return document.cookie;}");
    assert_eq!(
        succeeded(execute(&browser, "evaluate", target, "owner_one").await)["value"],
        "private=one"
    );
    let other = succeeded(execute(&browser, "snapshot", page(&second), "owner_one").await);
    let mut target = observed(&second, &other);
    target["expression"] = json!("() => document.cookie");
    assert_eq!(
        succeeded(execute(&browser, "evaluate", target, "owner_one").await)["value"],
        ""
    );
    let png = succeeded(execute(&browser, "screenshot", page(&first), "owner_one").await);
    let raw = racp_core::read_bounded(
        std::path::Path::new(png["spool_path"].as_str().unwrap()),
        32 * 1024 * 1024,
        false,
    )
    .unwrap();
    assert_eq!(&raw[..8], b"\x89PNG\r\n\x1a\n");
    assert_eq!(png["sha256"], racp_contract::digest(&raw));
    browser.cleanup().await.unwrap();
    server.abort();
}

#[tokio::test]
async fn frame_scope_cross_site_input_key_focus_and_detachment() {
    let listener = tokio::net::TcpListener::bind("0.0.0.0:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let url = format!("http://127.0.0.1:{port}/root");
    let server = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            tokio::spawn(async move {
                use tokio::io::{AsyncReadExt, AsyncWriteExt};
                let mut request = [0; 8192];
                let n = socket.read(&mut request).await.unwrap();
                let root = String::from_utf8_lossy(&request[..n]).starts_with("GET /root ");
                let body = if root {
                    format!("<!doctype html><title>Main</title><button>Main</button><iframe name='cross' src='http://127.0.0.2:{port}/child'></iframe>")
                } else {
                    "<!doctype html><title>Child</title><label>Field<input data-testid='field'></label><button onclick=\"document.querySelector('p').textContent='child:'+document.querySelector('input').value\">Submit</button><p></p>".into()
                };
                socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n{}",body.len(),body).as_bytes()).await.unwrap();
            });
        }
    });
    let (_root, browser) = setup();
    let opened = succeeded(execute(&browser, "open", json!({}), "owner_one").await);
    let page = json!({"browser_id":opened["browser_id"],"page_id":opened["pages"][0]["page_id"]});
    let mut nav = page.clone();
    nav["url"] = json!(url);
    succeeded(execute(&browser, "navigate", nav, "owner_one").await);
    let frames = succeeded(execute(&browser, "frames", page.clone(), "owner_one").await);
    assert_eq!(frames["frames"].as_array().unwrap().len(), 2);
    let child = frames["frames"]
        .as_array()
        .unwrap()
        .iter()
        .find(|f| f["main"] == false)
        .unwrap();
    let mut scope = page.clone();
    scope["frame_id"] = child["frame_id"].clone();
    let snapshot = succeeded(execute(&browser, "snapshot", scope.clone(), "owner_one").await);
    assert_eq!(snapshot["title"], "Child");
    let mut target = observed(&opened, &snapshot);
    target["frame_id"] = child["frame_id"].clone();
    target["selector"] = json!({"by":"test_id","value":"field"});
    target["text"] = json!("iframe 한글");
    succeeded(execute(&browser, "type", target, "owner_one").await);
    let mut target = observed(&opened, &snapshot);
    target["frame_id"] = child["frame_id"].clone();
    target["key"] = json!("End");
    succeeded(execute(&browser, "key", target, "owner_one").await);
    let main = succeeded(execute(&browser, "snapshot", page.clone(), "owner_one").await);
    let mut target = observed(&opened, &main);
    target["key"] = json!("Tab");
    assert_eq!(
        execute(&browser, "key", target, "owner_one").await["error"]["code"],
        "FOCUS_MISMATCH"
    );
    let mut target = observed(&opened, &main);
    target["expression"] = json!("() => {document.querySelector('iframe').remove(); return true;}");
    succeeded(execute(&browser, "evaluate", target, "owner_one").await);
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    assert_eq!(
        execute(&browser, "snapshot", scope, "owner_one").await["error"]["code"],
        "STALE_OBSERVATION"
    );
    browser.cleanup().await.unwrap();
    server.abort();
}

#[tokio::test]
async fn browser_cancel_cleanup_includes_startup_before_cdp_is_ready() {
    let (root, browser) = setup();
    let request = json!({"operation":"browser.open","payload":racp_contract::validate_operation("browser.open",json!({})).unwrap(),"operation_id":"op_early_cancel","remaining_timeout_ms":10,"context":{"workspace_id":"default","principal_id":"owner_one"}});
    let result = browser
        .execute(request, CancellationToken::new())
        .await
        .unwrap();
    assert_eq!(result["state"], "TIMED_OUT");
    assert!(browser.inventory().iter().all(|h| h["state"] == "CLOSED"));
    assert_eq!(
        std::fs::read_dir(root.path().join("data/browser"))
            .unwrap()
            .count(),
        0,
        "startup cancellation must remove its owned profile after process cleanup"
    );
    browser.cleanup().await.unwrap();
}

#[tokio::test]
async fn cdp_discovery_rejects_redirects_remote_targets_and_oversized_metadata() {
    for (status, body) in [
        ("302 Found", "".to_owned()),
        (
            "200 OK",
            json!({"webSocketDebuggerUrl":"ws://127.0.0.2:1/devtools/browser/foreign"}).to_string(),
        ),
        ("200 OK", "x".repeat(65537)),
    ] {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            use tokio::io::{AsyncReadExt, AsyncWriteExt};
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut request = [0; 8192];
            let _read = socket.read(&mut request).await.unwrap();
            socket.write_all(format!("HTTP/1.1 {status}\r\nLocation: http://127.0.0.2:1/json/version\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len()).as_bytes()).await.unwrap();
        });
        assert!(racp_runtime::providers::browser::cdp::discover(&endpoint)
            .await
            .is_err());
        server.await.unwrap();
    }
}

#[test]
fn browser_inventory_replay_ack_fence_and_overflow_refresh() {
    use racp_runtime::providers::browser::BrowserOutbox;
    let mut outbox = BrowserOutbox::new("provider_browser", 2);
    let handle = json!({"id":"browser_one","state":"ACTIVE"});
    outbox.push(json!({"browser_id":"browser_one","kind":"navigation","state":"changed","handles":[handle]}),vec![handle.clone()]);
    outbox.push(json!({"browser_id":"browser_one","kind":"page_created","state":"active","handles":[handle]}),vec![handle.clone()]);
    assert_eq!(outbox.pending(0).len(), 2);
    assert!(outbox.ack("3").is_err());
    assert!(
        outbox.ack("1").is_err(),
        "ACK cannot acknowledge an unsent event"
    );
    outbox.sent("1").unwrap();
    outbox.ack("1").unwrap();
    assert_eq!(outbox.cursor(), "1");
    assert_eq!(outbox.pending(0).len(), 1);
    outbox.push(
        json!({"kind":"frame","state":"changed"}),
        vec![handle.clone()],
    );
    outbox.push(
        json!({"kind":"frame","state":"changed"}),
        vec![handle.clone()],
    );
    let events = outbox.pending(1);
    assert_eq!(events.len(), 2);
    assert_eq!(events[1]["kind"], "gap");
    assert_eq!(events[1]["state"], "refresh_required");
    assert_eq!(events[1]["handles"], json!([handle]));
    outbox.sent("4").unwrap();
    outbox.ack("4").unwrap();
    assert!(outbox.pending(0).is_empty());
}

#[test]
fn origin_and_cdp_fences() {
    let mut config = BrowserConfig::default();
    assert!(config.allowed("https://example.org/path"));
    assert!(!config.allowed("file:///etc/passwd"));
    assert!(!config.allowed("https://user:secret@example.org/"));
    config.allow_origins = vec!["https://example.org".into()];
    assert!(config.allowed("https://example.org:443/path"));
    assert!(!config.allowed("https://example.org.evil.test/"));
    assert!(!config.allowed("https://example.org:444/"));
    assert!(!config.cdp_enabled);
}

#[tokio::test]
async fn cdp_correlates_out_of_order_replies_and_preserves_events() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "ws://{}/devtools/browser/test",
        listener.local_addr().unwrap()
    );
    let server = tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(tcp).await.unwrap();
        let mut requests = vec![];
        for _ in 0..2 {
            requests.push(
                serde_json::from_str::<serde_json::Value>(
                    ws.next().await.unwrap().unwrap().to_text().unwrap(),
                )
                .unwrap(),
            );
        }
        ws.send(tokio_tungstenite::tungstenite::Message::Text(json!({"method":"Page.frameNavigated","sessionId":"page_session","params":{"frame":{"id":"frame"}}}).to_string().into())).await.unwrap();
        for request in requests.iter().rev() {
            ws.send(tokio_tungstenite::tungstenite::Message::Text(
                json!({"id":request["id"],"result":{"method":request["method"]}})
                    .to_string()
                    .into(),
            ))
            .await
            .unwrap();
        }
        // Keep the connection alive until the client closes it.
        while let Some(Ok(message)) = ws.next().await {
            if message.is_close() {
                break;
            }
        }
    });
    let cdp = Cdp::connect(&endpoint).await.unwrap();
    let mut events = cdp.subscribe();
    let cancel = CancellationToken::new();
    let (first, second) = tokio::join!(
        cdp.call("First", json!({}), None, &cancel),
        cdp.call("Second", json!({}), Some("page_session"), &cancel)
    );
    assert_eq!(first.unwrap()["method"], "First");
    assert_eq!(second.unwrap()["method"], "Second");
    assert_eq!(
        events.recv().await.unwrap()["method"],
        "Page.frameNavigated"
    );
    cdp.close().await;
    server.await.unwrap();
}

#[tokio::test]
async fn cdp_cancellation_and_disconnect_release_pending_commands() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "ws://{}/devtools/browser/test",
        listener.local_addr().unwrap()
    );
    let (seen, ready) = tokio::sync::oneshot::channel();
    let server = tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(tcp).await.unwrap();
        ws.next().await.unwrap().unwrap();
        seen.send(()).unwrap();
        ws.next().await.unwrap().unwrap();
        ws.close(None).await.unwrap();
    });
    let cdp = Cdp::connect(&endpoint).await.unwrap();
    let cancel = CancellationToken::new();
    let cancelling = async {
        ready.await.unwrap();
        cancel.cancel();
    };
    let (result, _) = tokio::join!(
        cdp.call("NeverReplies", json!({}), None, &cancel),
        cancelling
    );
    assert_eq!(result.unwrap_err().code.0, "CANCELLED");
    let error = cdp
        .call("Disconnect", json!({}), None, &CancellationToken::new())
        .await
        .unwrap_err();
    assert_eq!(error.code.0, "BROWSER_CLOSED");
    cdp.close().await;
    server.await.unwrap();
}
