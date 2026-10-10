//! Fixed OS observations: no shells, arbitrary registry paths, or secret environment keys.
use super::Provider;
use futures_util::future::BoxFuture;
use racp_contract::{timestamp, RacpError, VERSION};
use serde_json::{json, Value};
use std::time::{Duration, Instant};
use tokio_util::sync::CancellationToken;
#[cfg(windows)]
mod windows;
pub struct OSObservation {
    boot: String,
    device: String,
}
const COMMON: &[&str] = &[
    "system.info",
    "system.resources",
    "system.locale",
    "system.environment",
    "network.interfaces",
    "storage.volumes",
];
const WINDOWS: &[&str] = &[
    "network.connections",
    "services.list",
    "services.get",
    "software.inventory",
];
impl OSObservation {
    pub fn new(settings: &racp_core::AgentSettings, boot: &str) -> Self {
        Self {
            boot: boot.into(),
            device: settings.device_id.clone(),
        }
    }
}
fn observe(op: &str, p: &Value) -> Result<Value, RacpError> {
    match op {
        "system.info" => Ok(
            json!({"hostname":sysinfo::System::host_name(),"platform":sysinfo::System::name(),"architecture":std::env::consts::ARCH,"os_version":sysinfo::System::long_os_version(),"agent_version":VERSION,"execution_identity":whoami::username(),"scope":"os_account"}),
        ),
        "system.resources" => {
            let mut system = sysinfo::System::new();
            system.refresh_memory();
            system.refresh_cpu_all();
            let total = system.total_memory();
            let available = system.available_memory();
            #[cfg(windows)]
            let times = windows::cpu_times()?;
            #[cfg(not(windows))]
            let times = linux_cpu_times()?;
            Ok(
                json!({"cpu_count":system.cpus().len(),"cpu_times_seconds":times,"memory":{"total_bytes":total,"available_bytes":available,"used_bytes":total.saturating_sub(available),"percent":if total>0{100.0*(total-available)as f64/total as f64}else{0.0}},"uptime_seconds":sysinfo::System::uptime(),"cpu_measurement":"cumulative_cpu_times"}),
            )
        }
        "system.locale" => {
            #[cfg(windows)]
            return windows::locale();
            #[cfg(not(windows))]
            {
                let now = chrono::Local::now();
                Ok(
                    json!({"timezone":now.format("%Z").to_string(),"utc_offset_seconds":now.offset().local_minus_utc(),"process_locale":[std::env::var("LC_ALL").or_else(|_|std::env::var("LANG")).ok(),null],"filesystem_encoding":"utf-8","preferred_encoding":"utf-8"}),
                )
            }
        }
        "system.environment" => {
            let allowed = [
                "systemroot",
                "windir",
                "systemdrive",
                "programdata",
                "allusersprofile",
                "programfiles",
                "programfiles(x86)",
                "userprofile",
                "home",
                "lang",
                "lc_all",
                "tz",
            ];
            let mut values = serde_json::Map::new();
            for key in p["keys"].as_array().unwrap() {
                let key = key.as_str().unwrap();
                if key.len() > 256 || !allowed.contains(&key.to_lowercase().as_str()) {
                    return Err(RacpError::new("PERMISSION_DENIED"));
                }
                values.insert(
                    key.into(),
                    json!(std::env::var_os(key).map(|s| s.to_string_lossy().into_owned())),
                );
            }
            Ok(json!({"values":values,"scope":"safe_process_environment_keys"}))
        }
        "network.interfaces" => {
            let networks = sysinfo::Networks::new_with_refreshed_list();
            let mut rows = vec![];
            for (name, network) in &networks {
                let addresses:Vec<_>=network.ip_networks().iter().map(|ip|json!({"family":if ip.addr.is_ipv4(){2}else{23},"address":ip.addr.to_string(),"prefix":ip.prefix,"netmask":mask(ip.addr.is_ipv4(),ip.prefix),"broadcast":null})).collect();
                rows.push(json!({"name":name,"addresses":addresses,"mac_address":network.mac_address().to_string(),"mtu":network.mtu(),"is_up":null,"speed_mbps":null,"counters":{"bytes_sent":network.total_transmitted(),"bytes_recv":network.total_received(),"packets_sent":network.total_packets_transmitted(),"packets_recv":network.total_packets_received(),"errin":network.total_errors_on_received(),"errout":network.total_errors_on_transmitted()}}));
            }
            rows.sort_by_key(|r| r["name"].as_str().unwrap_or("").to_owned());
            let total = rows.len();
            rows.truncate(p["limit"].as_u64().unwrap_or(100) as usize);
            Ok(
                json!({"items":rows,"truncated":rows.len()<total,"consistency":"live_observation","route_query_supported":false,"dns_server_query_supported":false}),
            )
        }
        "storage.volumes" => {
            let disks = sysinfo::Disks::new_with_refreshed_list();
            let limit = p["limit"].as_u64().unwrap_or(100) as usize;
            let items:Vec<_>=disks.list().iter().take(limit).map(|d|json!({"device":d.name().to_string_lossy(),"mountpoint":d.mount_point(),"filesystem":d.file_system().to_string_lossy(),"options":if d.is_read_only(){"ro"}else{"rw"},"total_bytes":d.total_space(),"free_bytes":d.available_space(),"used_bytes":d.total_space().saturating_sub(d.available_space()),"availability":"available"})).collect();
            Ok(
                json!({"items":items,"truncated":disks.list().len()>limit,"consistency":"live_observation"}),
            )
        }
        #[cfg(windows)]
        "network.connections" | "services.list" | "services.get" | "software.inventory" => {
            windows::observe(op, p)
        }
        _ => Err(RacpError::new("CAPABILITY_UNAVAILABLE")),
    }
}
fn mask(v4: bool, prefix: u8) -> String {
    if v4 {
        std::net::Ipv4Addr::from(
            u32::MAX
                .checked_shl(32u32.saturating_sub(prefix as u32))
                .unwrap_or(0),
        )
        .to_string()
    } else {
        std::net::Ipv6Addr::from(
            u128::MAX
                .checked_shl(128u32.saturating_sub(prefix as u32))
                .unwrap_or(0),
        )
        .to_string()
    }
}
#[cfg(not(windows))]
fn linux_cpu_times() -> Result<Value, RacpError> {
    let raw = std::fs::read_to_string("/proc/stat")?;
    let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if ticks <= 0 {
        return Err(RacpError::new("CAPABILITY_UNAVAILABLE"));
    }
    let fields: Vec<f64> = raw
        .lines()
        .next()
        .unwrap_or("")
        .split_whitespace()
        .skip(1)
        .take(10)
        .map(|s| s.parse::<u64>().unwrap_or(0) as f64 / ticks as f64)
        .collect();
    let mut result = serde_json::Map::new();
    for (key, value) in [
        "user",
        "nice",
        "system",
        "idle",
        "iowait",
        "irq",
        "softirq",
        "steal",
        "guest",
        "guest_nice",
    ]
    .iter()
    .zip(fields)
    {
        result.insert((*key).into(), json!(value));
    }
    Ok(Value::Object(result))
}
impl Provider for OSObservation {
    fn capabilities(&self) -> Vec<Value> {
        let mut ops = COMMON.to_vec();
        if cfg!(windows) {
            ops.extend_from_slice(WINDOWS);
        }
        vec![
            json!({"name":"os_observation","version":"1.0.0","operations":ops,"installed":true,"supported":true,"enabled":true,"healthy":true,"unavailable_reason":null,"attributes":{"scope":"current_os_account","shell_queries":false}}),
        ]
    }
    fn execute(
        &self,
        r: Value,
        cancel: CancellationToken,
    ) -> BoxFuture<'_, Result<Value, RacpError>> {
        let boot = self.boot.clone();
        let device = self.device.clone();
        let deadline =
            Instant::now() + Duration::from_millis(r["remaining_timeout_ms"].as_u64().unwrap_or(1));
        Box::pin(async move {
            let result = tokio::task::spawn_blocking(move || {
                if cancel.is_cancelled() {
                    return Err(RacpError::new("CANCELLED"));
                }
                let op = r["operation"].as_str().unwrap_or("");
                let p = racp_contract::validate_operation(op, r["payload"].clone())?;
                let mut value = observe(op, &p)?;
                if cancel.is_cancelled() {
                    return Err(RacpError::new("CANCELLED"));
                }
                if Instant::now() >= deadline {
                    return Err(RacpError::new("TIMEOUT"));
                }
                value["device_id"] = json!(device);
                value["agent_boot_id"] = json!(boot);
                value["observed_at"] = json!(timestamp());
                Ok(value)
            })
            .await
            .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))??;
            Ok(json!({"state":"SUCCEEDED","result":result,"error":null}))
        })
    }
}
