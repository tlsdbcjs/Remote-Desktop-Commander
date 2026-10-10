use racp_contract::RacpError;
use serde_json::Value;
pub const MAX_PIPE_MESSAGE: usize = 65536;
fn bounded(value: &Value, depth: usize, nodes: &mut usize) -> Result<(), RacpError> {
    *nodes += 1;
    if depth > 16 || *nodes > 10000 {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    match value {
        Value::Array(values) => {
            for value in values {
                bounded(value, depth + 1, nodes)?;
            }
        }
        Value::Object(values) => {
            for value in values.values() {
                bounded(value, depth + 1, nodes)?;
            }
        }
        _ => {}
    }
    Ok(())
}
pub fn decode_pipe_message(raw: &[u8]) -> Result<Value, RacpError> {
    if raw.is_empty() || raw.len() > MAX_PIPE_MESSAGE {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    let value: Value = serde_json::from_slice(raw)?;
    if !value.is_object() {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    bounded(&value, 0, &mut 0)?;
    Ok(value)
}
pub fn encode_pipe_message(value: &Value) -> Result<Vec<u8>, RacpError> {
    if !value.is_object() {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    bounded(value, 0, &mut 0)?;
    let raw = serde_json::to_vec(value)?;
    if raw.len() > MAX_PIPE_MESSAGE {
        return Err(RacpError::new("INVALID_ARGUMENT"));
    }
    Ok(raw)
}
