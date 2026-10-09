use crate::{normalize, RacpError};
use base64::Engine;
use serde_json::Value;
use std::{collections::BTreeMap, sync::LazyLock};
static REGISTRY: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../../../docs/protocol/registry-v1.json"))
        .expect("checked registry")
});
static VALIDATORS: LazyLock<BTreeMap<String, jsonschema::Validator>> = LazyLock::new(|| {
    registry()
        .as_object()
        .expect("registry object")
        .iter()
        .map(|(k, v)| {
            (
                k.clone(),
                jsonschema::validator_for(&v["input_schema"]).expect("checked operation schema"),
            )
        })
        .collect()
});
pub fn registry() -> &'static Value {
    &REGISTRY
}
fn present(v: &Value, k: &str) -> bool {
    v.get(k).is_some_and(|x| !x.is_null())
}
fn invalid() -> RacpError {
    RacpError::new("INVALID_ARGUMENT")
}
pub fn validate_operation(name: &str, mut v: Value) -> Result<Value, RacpError> {
    let validator = VALIDATORS
        .get(name)
        .ok_or_else(|| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
    if !validator.is_valid(&v)
        || !crate::schema::strict_types(
            &registry()[name]["input_schema"],
            &registry()[name]["input_schema"],
            &v,
        )
    {
        return Err(invalid());
    }
    normalize(&registry()[name]["input_schema"], &mut v);
    if matches!(name, "shell.exec" | "process.spawn") {
        if v["mode"] == "argv" {
            let argv = v["argv"].as_array().ok_or_else(invalid)?;
            if argv.is_empty()
                || argv.len() > 256
                || argv[0].as_str() == Some("")
                || present(&v, "command")
                || present(&v, "shell")
            {
                return Err(invalid());
            }
        } else if present(&v, "argv")
            || v["command"].as_str().is_none_or(str::is_empty)
            || !present(&v, "shell")
            || name == "process.spawn"
        {
            return Err(invalid());
        }
        if !encoding(v["encoding"].as_str().unwrap_or("utf-8")) {
            return Err(invalid());
        }
    }
    if matches!(name, "shell.exec" | "process.spawn" | "terminal.open") {
        let mut strings = vec![];
        if let Some(a) = v["argv"].as_array() {
            strings.extend(a.iter().filter_map(Value::as_str));
        }
        for k in ["command", "cwd"] {
            if let Some(x) = v[k].as_str() {
                strings.push(x);
            }
        }
        if let Some(env) = v["env"].as_object() {
            if env.len() > if name == "terminal.open" { 256 } else { 128 } {
                return Err(invalid());
            }
            for (k, x) in env {
                if k.is_empty() || k.contains('=') || k.chars().count() > 256 {
                    return Err(invalid());
                }
                strings.push(k);
                if let Some(x) = x.as_str() {
                    if x.chars().count() > 32768 {
                        return Err(invalid());
                    }
                    strings.push(x);
                }
            }
        }
        if strings.iter().any(|s| s.contains('\0')) {
            return Err(invalid());
        }
        if name == "terminal.open"
            && (present(&v, "argv") && present(&v, "shell")
                || v["argv"]
                    .as_array()
                    .is_some_and(|a| a.first().and_then(Value::as_str) == Some(""))
                || strings.iter().any(|s| s.chars().count() > 32768))
        {
            return Err(invalid());
        }
    }
    if name == "terminal.write" {
        let data = v["data"].as_str().ok_or_else(invalid)?;
        let size = if v["encoding"] == "base64" {
            base64::engine::general_purpose::STANDARD
                .decode(data)
                .map_err(|_| invalid())?
                .len()
        } else {
            data.len()
        };
        if size > 65536 {
            return Err(invalid());
        }
    }
    if name == "filesystem.write"
        && (present(&v, "content") == present(&v, "artifact_id")
            || v["mode"] == "replace" && v["overwrite"] != true
            || v["mode"] == "append" && !present(&v, "expected_offset")
            || !encoding(v["encoding"].as_str().unwrap_or("utf-8"))
            || v["content"].as_str().is_some_and(|s| s.len() > 65536))
    {
        return Err(invalid());
    }
    if matches!(name, "filesystem.copy" | "filesystem.move")
        && v["destination_workspace_id"].is_null()
    {
        v.as_object_mut()
            .unwrap()
            .remove("destination_workspace_id");
    }
    if matches!(
        name,
        "browser.click" | "browser.type" | "browser.download" | "browser.upload"
    ) && present(&v, "ref") == present(&v, "selector")
    {
        return Err(invalid());
    }
    if name == "browser.navigate" {
        let text = v["url"].as_str().ok_or_else(invalid)?;
        let u = url::Url::parse(text).map_err(|_| invalid())?;
        if !matches!(u.scheme(), "http" | "https")
            || u.host_str().is_none()
            || !u.username().is_empty()
            || u.password().is_some()
            || text.chars().any(|c| c < ' ')
        {
            return Err(invalid());
        }
    }
    if matches!(name, "browser.attach" | "browser.cdp_targets") {
        let text = v["endpoint_url"].as_str().ok_or_else(invalid)?;
        let mut u = url::Url::parse(text).map_err(|_| invalid())?;
        let host = u.host_str().unwrap_or("").trim_matches(['[', ']']);
        let local = host == "localhost"
            || host
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback());
        // Url normalizes default ports; require an explicitly written authority port.
        let authority = text
            .split("://")
            .nth(1)
            .unwrap_or("")
            .split('/')
            .next()
            .unwrap_or("");
        if !local
            || !matches!(u.scheme(), "http" | "https" | "ws" | "wss")
            || authority
                .rsplit_once(':')
                .is_none_or(|(_, p)| p.parse::<u16>().is_err())
            || !u.username().is_empty()
            || u.password().is_some()
            || u.query().is_some()
            || u.fragment().is_some()
            || matches!(u.scheme(), "http" | "https") && !matches!(u.path(), "" | "/")
            || matches!(u.scheme(), "ws" | "wss") && !u.path().starts_with("/devtools/browser/")
        {
            return Err(invalid());
        }
        if host == "localhost" {
            u.set_host(Some("127.0.0.1")).map_err(|_| invalid())?;
        }
        // Preserve a trailing slash only when it was supplied in the reference endpoint.
        v["endpoint_url"] = Value::String(
            if text.ends_with('/') || matches!(u.scheme(), "ws" | "wss") {
                u.to_string()
            } else {
                u.to_string().trim_end_matches('/').to_string()
            },
        );
        if name == "browser.attach" {
            let ids = v["page_target_ids"].as_array().ok_or_else(invalid)?;
            let existing = v["context_mode"] == "existing";
            if existing && ids.is_empty()
                || !existing && (!ids.is_empty() || v["allow_page_termination"] == true)
                || ids.iter().enumerate().any(|(i, x)| ids[..i].contains(x))
            {
                return Err(invalid());
            }
        }
    }
    if name == "browser.upload" {
        let s = v["filename"].as_str().ok_or_else(invalid)?;
        let stem = s
            .split('.')
            .next()
            .unwrap_or("")
            .trim_end_matches([' ', '.'])
            .to_uppercase();
        let reserved = ["CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"]
            .contains(&stem.as_str())
            || ["COM", "LPT"].iter().any(|p| {
                stem.strip_prefix(p).is_some_and(|n| {
                    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"].contains(&n)
                })
            });
        if s == "."
            || s == ".."
            || s.ends_with([' ', '.'])
            || s.chars().any(|c| c < ' ' || "/\\:<>\"|?*".contains(c))
            || reserved
        {
            return Err(invalid());
        }
    }
    if name == "desktop.screenshot" && present(&v, "window_id") && present(&v, "monitor_id") {
        return Err(invalid());
    }
    if name == "desktop.key" {
        let a = v["keys"].as_array().ok_or_else(invalid)?;
        if a.iter().enumerate().any(|(i, x)| a[..i].contains(x)) {
            return Err(invalid());
        }
    }
    if name == "debugger.launch"
        && (v["executable"].as_str().is_some_and(|s| s.contains('\0'))
            || v["args"].as_array().is_some_and(|a| {
                a.iter()
                    .any(|s| s.as_str().is_some_and(|s| s.contains('\0')))
            }))
    {
        return Err(invalid());
    }
    if name == "debugger.command"
        && v["action"] == "breakpoint"
        && present(&v, "address") == present(&v, "symbol")
    {
        return Err(invalid());
    }
    if name == "re.query"
        && present(&v, "address_kind")
        && (v["address_kind"] == "module_rva") != present(&v, "module")
    {
        return Err(invalid());
    }
    if matches!(name, "process.memory_read" | "debugger.memory") {
        let address = u64::from_str_radix(
            v["address"]
                .as_str()
                .ok_or_else(invalid)?
                .trim_start_matches("0x"),
            16,
        )
        .map_err(|_| invalid())?;
        let size = v["size_bytes"].as_u64().ok_or_else(invalid)?;
        if (address as u128) + (size as u128) > (1u128 << 64) {
            return Err(invalid());
        }
    }
    Ok(v)
}
fn encoding(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().replace('_', "-").as_str(),
        "utf-8"
            | "utf8"
            | "ascii"
            | "latin-1"
            | "latin1"
            | "iso-8859-1"
            | "utf-16"
            | "utf-16-le"
            | "utf-16-be"
            | "cp1252"
            | "cp949"
            | "euc-kr"
    )
}
