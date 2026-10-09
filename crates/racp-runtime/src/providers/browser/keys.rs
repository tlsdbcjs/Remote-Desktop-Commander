use racp_contract::RacpError;
use serde_json::{json, Value};
#[derive(Debug)]
pub(super) struct Key {
    pub name: String,
    pub code: String,
    pub virtual_key: u32,
    pub text: String,
}
pub(super) fn parse(value: &str) -> Result<(Vec<Key>, Key, u32), RacpError> {
    let mut parts = value.split('+').collect::<Vec<_>>();
    let name = parts.pop().unwrap_or("");
    let mut modifiers = vec![];
    let mut mask = 0;
    for part in parts {
        let (name, code, key, bit) = match part {
            "Alt" => ("Alt", "AltLeft", 18, 1),
            "Control" | "Ctrl" => ("Control", "ControlLeft", 17, 2),
            "Meta" => ("Meta", "MetaLeft", 91, 4),
            "Shift" => ("Shift", "ShiftLeft", 16, 8),
            _ => return Err(RacpError::new("INVALID_ARGUMENT")),
        };
        if mask & bit != 0 {
            return Err(RacpError::new("INVALID_ARGUMENT"));
        }
        mask |= bit;
        modifiers.push(Key {
            name: name.into(),
            code: code.into(),
            virtual_key: key,
            text: String::new(),
        });
    }
    let (name, code, key, text) = match name {
        "Tab" => ("Tab".into(), "Tab".into(), 9, "\t".into()),
        "Enter" => ("Enter".into(), "Enter".into(), 13, "\r".into()),
        "Escape" | "Esc" => ("Escape".into(), "Escape".into(), 27, String::new()),
        "Backspace" => ("Backspace".into(), "Backspace".into(), 8, String::new()),
        "Delete" => ("Delete".into(), "Delete".into(), 46, String::new()),
        "Insert" => ("Insert".into(), "Insert".into(), 45, String::new()),
        "End" => ("End".into(), "End".into(), 35, String::new()),
        "Home" => ("Home".into(), "Home".into(), 36, String::new()),
        "PageUp" => ("PageUp".into(), "PageUp".into(), 33, String::new()),
        "PageDown" => ("PageDown".into(), "PageDown".into(), 34, String::new()),
        "ArrowLeft" => ("ArrowLeft".into(), "ArrowLeft".into(), 37, String::new()),
        "ArrowUp" => ("ArrowUp".into(), "ArrowUp".into(), 38, String::new()),
        "ArrowRight" => ("ArrowRight".into(), "ArrowRight".into(), 39, String::new()),
        "ArrowDown" => ("ArrowDown".into(), "ArrowDown".into(), 40, String::new()),
        "Space" => (" ".into(), "Space".into(), 32, " ".into()),
        function
            if function.starts_with('F')
                && function[1..]
                    .parse::<u32>()
                    .is_ok_and(|n| (1..=24).contains(&n)) =>
        {
            (
                function.into(),
                function.into(),
                111 + function[1..].parse::<u32>().unwrap(),
                String::new(),
            )
        }
        character if character.chars().count() == 1 => {
            let c = character.chars().next().unwrap();
            let code = if c.is_ascii_alphabetic() {
                format!("Key{}", c.to_ascii_uppercase())
            } else if c.is_ascii_digit() {
                format!("Digit{c}")
            } else {
                String::new()
            };
            (
                character.into(),
                code,
                if c.is_ascii() {
                    c.to_ascii_uppercase() as u32
                } else {
                    0
                },
                character.into(),
            )
        }
        _ => return Err(RacpError::new("INVALID_ARGUMENT")),
    };
    Ok((
        modifiers,
        Key {
            name,
            code,
            virtual_key: key,
            text,
        },
        mask,
    ))
}
pub(super) fn event(key: &Key, kind: &str, modifiers: u32) -> Value {
    json!({"type":kind,"key":key.name,"code":key.code,"windowsVirtualKeyCode":key.virtual_key,"nativeVirtualKeyCode":key.virtual_key,"modifiers":modifiers,"text":if kind=="keyDown" && modifiers & 6 ==0 {&key.text}else{""}})
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unicode_modifiers_and_function_keys() {
        let (m, k, mask) = parse("Control+Shift+a").unwrap();
        assert_eq!(mask, 10);
        assert_eq!(m.len(), 2);
        assert_eq!(k.code, "KeyA");
        assert_eq!(event(&k, "keyDown", mask)["text"], "");
        assert_eq!(parse("한").unwrap().1.text, "한");
        assert_eq!(parse("F24").unwrap().1.virtual_key, 135);
        assert!(parse("Control+Control+a").is_err());
        assert!(parse("F25").is_err());
    }
}
