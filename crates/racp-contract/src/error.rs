use serde::{Serialize, Serializer};
/// Codes are static program constants; sensitive diagnostic strings cannot become codes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ErrorCode(pub &'static str);
impl Serialize for ErrorCode {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(self.0)
    }
}
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct RacpError {
    pub code: ErrorCode,
}
impl RacpError {
    pub const fn new(code: &'static str) -> Self {
        Self {
            code: ErrorCode(code),
        }
    }
}
impl std::fmt::Display for RacpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.code.0)
    }
}
impl std::error::Error for RacpError {}
impl From<std::io::Error> for RacpError {
    fn from(_: std::io::Error) -> Self {
        Self::new("LOCAL_STATE_FAILED")
    }
}
impl From<serde_json::Error> for RacpError {
    fn from(_: serde_json::Error) -> Self {
        Self::new("REQUEST_INVALID")
    }
}
