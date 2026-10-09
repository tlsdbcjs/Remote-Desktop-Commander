use crate::RacpError;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::{Arc, LazyLock, Mutex};
static BRIDGE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../schemas/bridge.json")).expect("checked bridge schema")
});
static WIRE: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../docs/protocol/agent-protocol-v1.schema.json"
    ))
    .expect("checked wire schema")
});
static BRIDGE_VALIDATOR: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| jsonschema::validator_for(&BRIDGE).expect("checked bridge schema"));
static WIRE_VALIDATOR: LazyLock<jsonschema::Validator> =
    LazyLock::new(|| jsonschema::validator_for(&WIRE).expect("checked wire schema"));
pub fn validate_schema(schema: &Value, value: &Value) -> Result<(), RacpError> {
    let validator = jsonschema::options()
        .should_validate_formats(true)
        .build(schema)
        .map_err(|_| RacpError::new("REQUEST_INVALID"))?;
    if validator.is_valid(value) && strict_types(schema, schema, value) {
        Ok(())
    } else {
        Err(RacpError::new("REQUEST_INVALID"))
    }
}
fn resolve<'a>(schema: &'a Value, root: &'a Value) -> &'a Value {
    schema
        .get("$ref")
        .and_then(Value::as_str)
        .and_then(|r| root.pointer(r.strip_prefix('#')?))
        .unwrap_or(schema)
}
// Union schemas share many $defs. Compile each candidate once; stream frames must
// not rebuild the complete protocol validator on every nested nullable field.
static CANDIDATES: LazyLock<Mutex<HashMap<String, Arc<jsonschema::Validator>>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
fn choice_valid(choice: &Value, root: &Value, value: &Value) -> bool {
    let resolved = resolve(choice, root);
    // Most protocol/bridge unions are discriminated by a constant string.
    if let Some(props) = resolved["properties"].as_object() {
        for (key, field) in props {
            if let Some(expected) = field.get("const") {
                if &value[key] != expected {
                    return false;
                }
            }
        }
    }
    if let Some(kind) = resolved["type"].as_str() {
        let matches = match kind {
            "null" => value.is_null(),
            "object" => value.is_object(),
            "array" => value.is_array(),
            "string" => value.is_string(),
            "boolean" => value.is_boolean(),
            "integer" | "number" => value.is_number(),
            _ => true,
        };
        if !matches {
            return false;
        }
    }
    let mut candidate = resolved.clone();
    let raw = serde_json::to_string(resolved).expect("schema JSON");
    if raw.contains("\"$ref\"") {
        if let Some(defs) = root.get("$defs") {
            candidate["$defs"] = defs.clone();
        }
    }
    let key = crate::canonical_digest(&candidate);
    let cached = {
        CANDIDATES
            .lock()
            .expect("validator cache lock")
            .get(&key)
            .cloned()
    };
    let validator = if let Some(v) = cached {
        v
    } else {
        let Ok(v) = jsonschema::validator_for(&candidate) else {
            return false;
        };
        let v = Arc::new(v);
        let mut cache = CANDIDATES.lock().expect("validator cache lock");
        if cache.len() < 512 {
            cache.insert(key, v.clone());
        }
        v
    };
    validator.is_valid(value)
}
/// Apply the same field defaults as the reference models after strict validation.
pub fn normalize(schema: &Value, value: &mut Value) {
    defaults(schema, schema, value)
}
fn defaults(schema: &Value, root: &Value, value: &mut Value) {
    let schema = resolve(schema, root);
    if schema["type"] == "number" {
        if let Some(number) = value.as_f64() {
            *value = serde_json::json!(number);
        }
    }
    for key in ["oneOf", "anyOf"] {
        if let Some(choices) = schema[key].as_array() {
            for choice in choices {
                if choice_valid(choice, root, value) {
                    defaults(choice, root, value);
                    return;
                }
            }
            return;
        }
    }
    if let (Some(props), Some(object)) = (schema["properties"].as_object(), value.as_object_mut()) {
        for (name, field) in props {
            if !object.contains_key(name) {
                let required = schema["required"]
                    .as_array()
                    .is_some_and(|r| r.iter().any(|x| x == name));
                if let Some(default) = field.get("default") {
                    object.insert(name.clone(), default.clone());
                } else if !required {
                    match field["type"].as_str() {
                        Some("array") => {
                            object.insert(name.clone(), serde_json::json!([]));
                        }
                        Some("object") => {
                            object.insert(name.clone(), serde_json::json!({}));
                        }
                        _ => (),
                    }
                }
            }
            if let Some(item) = object.get_mut(name) {
                defaults(field, root, item);
            }
        }
    }
    if let Some(items) = value.as_array_mut() {
        for item in items {
            defaults(&schema["items"], root, item);
        }
    }
}
fn tree(value: &Value, depth: usize) -> Result<(), RacpError> {
    if depth > 32 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    match value {
        Value::String(s) if s.len() > 256 * 1024 => return Err(RacpError::new("REQUEST_INVALID")),
        Value::Object(o) => {
            for (k, v) in o {
                tree(&Value::String(k.clone()), depth + 1)?;
                tree(v, depth + 1)?;
            }
        }
        Value::Array(a) => {
            for v in a {
                tree(v, depth + 1)?;
            }
        }
        _ => (),
    };
    Ok(())
}
pub fn decode_bridge(raw: &[u8]) -> Result<Value, RacpError> {
    if raw.len() > 16384 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let mut value: Value = serde_json::from_slice(raw)?;
    if !BRIDGE_VALIDATOR.is_valid(&value) || !strict_types(&BRIDGE, &BRIDGE, &value) {
        return Err(RacpError::new(if value.get("token").is_some() {
            "TOKEN_INVALID"
        } else {
            "REQUEST_INVALID"
        }));
    }
    normalize(&BRIDGE, &mut value);
    Ok(value)
}
pub fn decode_message(raw: &[u8]) -> Result<Value, RacpError> {
    if raw.len() > 1024 * 1024 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    let mut value: Value = serde_json::from_slice(raw)?;
    tree(&value, 0)?;
    if !WIRE_VALIDATOR.is_valid(&value) || !strict_types(&WIRE, &WIRE, &value) {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    normalize(&WIRE, &mut value);
    match value["type"].as_str() {
        Some("browser_state") => {
            let health = value["kind"] == "health";
            if (health && value["capability"]["name"] != "browser")
                || (!health && value["kind"] != "gap" && !value["capability"].is_null())
                || value["event_sequence"]
                    .as_str()
                    .and_then(|x| x.parse::<i64>().ok())
                    .is_none()
            {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
        }
        Some("event") => {
            if (value["state"] == "WAITING") == value["waiting_reason"].is_null() {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
        }
        Some("stream_data") => {
            let a = value["byte_offset"]
                .as_str()
                .and_then(|x| x.parse::<u64>().ok());
            let b = value["next_cursor"]
                .as_str()
                .and_then(|x| x.parse::<u64>().ok());
            if !a.zip(b).is_some_and(|(a, b)| b > a && b - a <= 65536) {
                return Err(RacpError::new("REQUEST_INVALID"));
            }
        }
        _ => (),
    };
    Ok(value)
}

// JSON Schema considers 1.0 an integer; the existing strict models do not.
pub(crate) fn strict_types(schema: &Value, root: &Value, value: &Value) -> bool {
    let schema = resolve(schema, root);
    for k in ["oneOf", "anyOf"] {
        if let Some(choices) = schema[k].as_array() {
            return choices
                .iter()
                .any(|c| choice_valid(c, root, value) && strict_types(c, root, value));
        }
    }
    if schema["type"] == "integer" && !value.as_number().is_some_and(|n| n.is_i64() || n.is_u64()) {
        return false;
    }
    if let Some(obj) = value.as_object() {
        for (key, item) in obj {
            if let Some(field) = schema["properties"].get(key) {
                if !strict_types(field, root, item) {
                    return false;
                }
            } else if schema["additionalProperties"].is_object()
                && !strict_types(&schema["additionalProperties"], root, item)
            {
                return false;
            }
        }
    }
    if let Some(array) = value.as_array() {
        return array
            .iter()
            .all(|x| strict_types(&schema["items"], root, x));
    }
    true
}
