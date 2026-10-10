use racp_runtime::providers::desktop::{decode_pipe_message, encode_pipe_message};
use serde_json::json;
#[test]
fn local_pipe_messages_preserve_utf8_and_reject_oversize_depth_and_non_objects() {
    let message =
        json!({"version":1,"request":{"operation":"desktop.type","payload":{"text":"한글"}}});
    assert_eq!(
        decode_pipe_message(&encode_pipe_message(&message).unwrap()).unwrap(),
        message
    );
    assert!(decode_pipe_message(&vec![b' '; 65537]).is_err());
    assert!(encode_pipe_message(&json!({"data":"a".repeat(65536)})).is_err());
    assert!(decode_pipe_message(b"[]").is_err());
    let mut nested = json!({});
    for _ in 0..17 {
        nested = json!({"nested":nested});
    }
    assert!(encode_pipe_message(&nested).is_err());
    assert!(decode_pipe_message(br#"{"value":NaN}"#).is_err());
}
