use racp_contract::RacpError;
fn label(name: &str) -> String {
    name.to_ascii_lowercase().replace('_', "-")
}
pub fn decode_lossy(raw: &[u8], name: &str) -> Result<String, RacpError> {
    match label(name).as_str() {
        "utf-8" | "utf8" => Ok(String::from_utf8_lossy(raw).into_owned()),
        "utf-8-sig" => Ok(String::from_utf8_lossy(
            raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(raw),
        )
        .into_owned()),
        "ascii" | "us-ascii" => Ok(raw
            .iter()
            .map(|b| {
                if b.is_ascii() {
                    char::from(*b)
                } else {
                    '\u{fffd}'
                }
            })
            .collect()),
        "latin1" | "latin-1" | "iso-8859-1" => Ok(raw.iter().map(|b| char::from(*b)).collect()),
        "utf-16" | "utf-16-le" | "utf-16-be" => {
            let big = label(name) == "utf-16-be" || raw.starts_with(b"\xfe\xff");
            let raw = if label(name) == "utf-16" {
                raw.strip_prefix(b"\xff\xfe")
                    .or_else(|| raw.strip_prefix(b"\xfe\xff"))
                    .unwrap_or(raw)
            } else {
                raw
            };
            let words: Vec<u16> = raw
                .chunks_exact(2)
                .map(|b| {
                    if big {
                        u16::from_be_bytes([b[0], b[1]])
                    } else {
                        u16::from_le_bytes([b[0], b[1]])
                    }
                })
                .collect();
            let mut text = String::from_utf16_lossy(&words);
            if raw.len() % 2 != 0 {
                text.push('\u{fffd}');
            }
            Ok(text)
        }
        _ => {
            let normalized = label(name);
            let encoding = encoding_rs::Encoding::for_label(normalized.as_bytes())
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            Ok(encoding.decode_without_bom_handling(raw).0.into_owned())
        }
    }
}
pub fn decode(raw: &[u8], name: &str) -> Result<String, RacpError> {
    let label = label(name);
    match label.as_str() {
        "utf-8" | "utf8" => {
            String::from_utf8(raw.to_vec()).map_err(|_| RacpError::new("INVALID_ARGUMENT"))
        }
        "utf-8-sig" => String::from_utf8(raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(raw).to_vec())
            .map_err(|_| RacpError::new("INVALID_ARGUMENT")),
        "ascii" | "us-ascii" => {
            if raw.is_ascii() {
                Ok(String::from_utf8(raw.to_vec()).unwrap())
            } else {
                Err(RacpError::new("INVALID_ARGUMENT"))
            }
        }
        "latin1" | "latin-1" | "iso-8859-1" => Ok(raw.iter().map(|b| char::from(*b)).collect()),
        "utf-16" | "utf-16-le" | "utf-16-be" => {
            let (raw, big) = if label == "utf-16" {
                if let Some(rest) = raw.strip_prefix(b"\xff\xfe") {
                    (rest, false)
                } else if let Some(rest) = raw.strip_prefix(b"\xfe\xff") {
                    (rest, true)
                } else {
                    return Err(RacpError::new("INVALID_ARGUMENT"));
                }
            } else {
                (raw, label == "utf-16-be")
            };
            if raw.len() % 2 != 0 {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            let words: Vec<u16> = raw
                .chunks_exact(2)
                .map(|b| {
                    if big {
                        u16::from_be_bytes([b[0], b[1]])
                    } else {
                        u16::from_le_bytes([b[0], b[1]])
                    }
                })
                .collect();
            String::from_utf16(&words).map_err(|_| RacpError::new("INVALID_ARGUMENT"))
        }
        _ => {
            let encoding = encoding_rs::Encoding::for_label(label.as_bytes())
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            encoding
                .decode_without_bom_handling_and_without_replacement(raw)
                .map(|text| text.into_owned())
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))
        }
    }
}
pub fn encode(text: &str, name: &str) -> Result<Vec<u8>, RacpError> {
    let label = label(name);
    match label.as_str() {
        "utf-8" | "utf8" => Ok(text.as_bytes().to_vec()),
        "utf-8-sig" => Ok([b"\xef\xbb\xbf".as_slice(), text.as_bytes()].concat()),
        "ascii" | "us-ascii" => {
            if text.is_ascii() {
                Ok(text.as_bytes().to_vec())
            } else {
                Err(RacpError::new("INVALID_ARGUMENT"))
            }
        }
        "latin1" | "latin-1" | "iso-8859-1" => text
            .chars()
            .map(|c| u8::try_from(c as u32).map_err(|_| RacpError::new("INVALID_ARGUMENT")))
            .collect(),
        "utf-16" | "utf-16-le" | "utf-16-be" => {
            let mut raw = if label == "utf-16" {
                vec![255, 254]
            } else {
                vec![]
            };
            for word in text.encode_utf16() {
                raw.extend(if label == "utf-16-be" {
                    word.to_be_bytes()
                } else {
                    word.to_le_bytes()
                });
            }
            Ok(raw)
        }
        _ => {
            let encoding = encoding_rs::Encoding::for_label(label.as_bytes())
                .ok_or_else(|| RacpError::new("INVALID_ARGUMENT"))?;
            let (raw, _, errors) = encoding.encode(text);
            if errors {
                return Err(RacpError::new("INVALID_ARGUMENT"));
            }
            Ok(raw.into_owned())
        }
    }
}
