//! Explicit-call USB PnP discovery only. Not installed in NativeProviders and
//! never called at Agent startup. No CreateFile/driver IO/install/reset/transfer.
use racp_contract::{hardware::MAX_HARDWARE_INTERFACES, RacpError};
use racp_core::hardware::ObservedInterface;
use std::time::Instant;
use tokio_util::sync::CancellationToken;
use windows_sys::Win32::{
    Devices::{DeviceAndDriverInstallation::*, Usb::GUID_DEVINTERFACE_USB_DEVICE},
    Foundation::*,
    System::Registry::REG_MULTI_SZ,
};
const MAX_DETAIL_BYTES: u32 = 65_536;
struct InfoSet(HDEVINFO);
impl Drop for InfoSet {
    fn drop(&mut self) {
        unsafe { SetupDiDestroyDeviceInfoList(self.0) };
    }
}
fn unknown() -> RacpError {
    RacpError::new("HARDWARE_DISCOVERY_UNCONFIRMED")
}
fn check(deadline: Instant, cancel: &CancellationToken) -> Result<(), RacpError> {
    if cancel.is_cancelled() {
        return Err(RacpError::new("CANCELLED"));
    }
    if Instant::now() >= deadline {
        return Err(RacpError::new("TIMEOUT"));
    }
    Ok(())
}
fn detail_size(bytes: u32, error: u32) -> Result<usize, RacpError> {
    if error != ERROR_INSUFFICIENT_BUFFER
        || bytes < std::mem::size_of::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>() as u32
        || bytes > MAX_DETAIL_BYTES
        || bytes % 2 != 0
    {
        return Err(unknown());
    }
    Ok(bytes as usize)
}
fn bounded_text(units: &[u16]) -> Result<String, RacpError> {
    let end = units.iter().position(|v| *v == 0).ok_or_else(unknown)?;
    if end == 0 || end > 4096 {
        return Err(unknown());
    }
    let text = String::from_utf16(&units[..end]).map_err(|_| unknown())?;
    if text.len() > 8192 || text.chars().any(char::is_control) {
        return Err(unknown());
    }
    Ok(text)
}
fn device_ids(units: &[u16]) -> Result<(Option<u16>, Option<u16>), RacpError> {
    // PnP hardware IDs are documented identifiers. Do not parse the opaque
    // symbolic device interface path returned by GetDeviceInterfaceDetail.
    let Some(end) = units.windows(2).position(|v| v == [0, 0]) else {
        return Err(unknown());
    };
    let mut found = None;
    for part in units[..=end]
        .split(|v| *v == 0)
        .filter(|part| !part.is_empty())
    {
        let id = String::from_utf16(part)
            .map_err(|_| unknown())?
            .to_ascii_uppercase();
        if id.len() > 4096 || id.chars().any(char::is_control) {
            return Err(unknown());
        }
        let Some(tail) = id.strip_prefix("USB\\") else {
            continue;
        };
        let fields: Vec<_> = tail.split('&').collect();
        let parse = |prefix: &str| -> Option<u16> {
            let values: Vec<_> = fields
                .iter()
                .filter_map(|v| v.strip_prefix(prefix))
                .collect();
            if values.len() != 1 || values[0].len() != 4 {
                return None;
            }
            u16::from_str_radix(values[0], 16).ok()
        };
        if let (Some(vid), Some(pid)) = (parse("VID_"), parse("PID_")) {
            if found.is_some_and(|old| old != (vid, pid)) {
                return Err(unknown());
            }
            found = Some((vid, pid));
        }
    }
    Ok(found.map_or((None, None), |(vid, pid)| (Some(vid), Some(pid))))
}
/// Caller must authorize metadata discovery and preserve unknown outcomes.
/// Synchronous SDK calls are not preemptible: the future Provider must contain
/// these in a bounded worker before claiming a hard execution deadline.
pub fn enumerate_usb_interfaces(
    deadline: Instant,
    cancel: &CancellationToken,
) -> Result<Vec<ObservedInterface>, RacpError> {
    check(deadline, cancel)?;
    let raw = unsafe {
        SetupDiGetClassDevsW(
            &GUID_DEVINTERFACE_USB_DEVICE,
            std::ptr::null(),
            std::ptr::null_mut(),
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
        )
    };
    if raw == -1 || raw == 0 {
        return Err(unknown());
    }
    let info = InfoSet(raw);
    let mut observations = vec![];
    for index in 0..=MAX_HARDWARE_INTERFACES as u32 {
        check(deadline, cancel)?;
        let mut interface = SP_DEVICE_INTERFACE_DATA {
            cbSize: std::mem::size_of::<SP_DEVICE_INTERFACE_DATA>() as u32,
            ..Default::default()
        };
        if unsafe {
            SetupDiEnumDeviceInterfaces(
                info.0,
                std::ptr::null(),
                &GUID_DEVINTERFACE_USB_DEVICE,
                index,
                &mut interface,
            )
        } == 0
        {
            let error = unsafe { GetLastError() };
            check(deadline, cancel)?;
            if error == ERROR_NO_MORE_ITEMS {
                return Ok(observations);
            }
            return Err(unknown());
        }
        if index == MAX_HARDWARE_INTERFACES as u32 {
            return Err(RacpError::new("RESOURCE_EXHAUSTED"));
        }
        if interface.Flags & SPINT_ACTIVE == 0 {
            continue;
        }
        let mut required = 0;
        let success = unsafe {
            SetupDiGetDeviceInterfaceDetailW(
                info.0,
                &interface,
                std::ptr::null_mut(),
                0,
                &mut required,
                std::ptr::null_mut(),
            )
        };
        let error = unsafe { GetLastError() };
        if success != 0 {
            return Err(unknown());
        }
        let bytes = detail_size(required, error)?;
        let mut storage = vec![usize::MAX; bytes.div_ceil(std::mem::size_of::<usize>())];
        let detail = storage
            .as_mut_ptr()
            .cast::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>();
        unsafe {
            (*detail).cbSize = std::mem::size_of::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>() as u32;
        }
        let mut device = SP_DEVINFO_DATA {
            cbSize: std::mem::size_of::<SP_DEVINFO_DATA>() as u32,
            ..Default::default()
        };
        check(deadline, cancel)?;
        if unsafe {
            SetupDiGetDeviceInterfaceDetailW(
                info.0,
                &interface,
                detail,
                bytes as u32,
                std::ptr::null_mut(),
                &mut device,
            )
        } == 0
        {
            return Err(unknown());
        }
        let offset = std::mem::offset_of!(SP_DEVICE_INTERFACE_DETAIL_DATA_W, DevicePath);
        let path = bounded_text(unsafe {
            std::slice::from_raw_parts(
                (detail.cast::<u8>().add(offset)).cast::<u16>(),
                (bytes - offset) / 2,
            )
        })?;
        required = 0;
        if unsafe {
            SetupDiGetDeviceInstanceIdW(info.0, &device, std::ptr::null_mut(), 0, &mut required)
        } != 0
            || unsafe { GetLastError() } != ERROR_INSUFFICIENT_BUFFER
            || !(2..=4097).contains(&required)
        {
            return Err(unknown());
        }
        let mut instance = vec![u16::MAX; required as usize];
        check(deadline, cancel)?;
        if unsafe {
            SetupDiGetDeviceInstanceIdW(
                info.0,
                &device,
                instance.as_mut_ptr(),
                required,
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(unknown());
        }
        let device_key = bounded_text(&instance)?.to_ascii_uppercase();
        let mut property_type = 0;
        required = 0;
        let ok = unsafe {
            SetupDiGetDeviceRegistryPropertyW(
                info.0,
                &device,
                SPDRP_HARDWAREID,
                &mut property_type,
                std::ptr::null_mut(),
                0,
                &mut required,
            )
        };
        let error = unsafe { GetLastError() };
        let (vendor_id, product_id) = if ok == 0 && error == ERROR_INVALID_DATA {
            (None, None)
        } else {
            if ok != 0
                || error != ERROR_INSUFFICIENT_BUFFER
                || required < 4
                || required > MAX_DETAIL_BYTES
                || required % 2 != 0
            {
                return Err(unknown());
            }
            let mut ids = vec![u16::MAX; required as usize / 2];
            check(deadline, cancel)?;
            if unsafe {
                SetupDiGetDeviceRegistryPropertyW(
                    info.0,
                    &device,
                    SPDRP_HARDWAREID,
                    &mut property_type,
                    ids.as_mut_ptr().cast(),
                    required,
                    std::ptr::null_mut(),
                )
            } == 0
                || property_type != REG_MULTI_SZ
            {
                return Err(unknown());
            }
            device_ids(&ids)?
        };
        check(deadline, cancel)?;
        observations.push(ObservedInterface {
            device_key,
            interface_key: path,
            vendor_id,
            product_id,
        });
    }
    Err(unknown())
}
#[cfg(test)]
mod tests {
    use super::*;
    fn ids(values: &[&str]) -> Vec<u16> {
        let mut v = vec![];
        for s in values {
            v.extend(s.encode_utf16());
            v.push(0);
        }
        v.push(0);
        v
    }
    #[test]
    fn decoder_reads_documented_hardware_ids_without_conflating_device_identity() {
        assert_eq!(
            device_ids(&ids(&[
                r"USB\VID_1234&PID_5678&REV_0100",
                r"USB\VID_1234&PID_5678"
            ]))
            .unwrap(),
            (Some(0x1234), Some(0x5678))
        );
        assert_eq!(
            device_ids(&ids(&[r"USB\Class_FF&SubClass_00"])).unwrap(),
            (None, None)
        );
        assert!(device_ids(&ids(&[r"USB\VID_1234&PID_5678", r"USB\VID_1234&PID_9999"])).is_err());
        assert!(device_ids(&[65, 0]).is_err());
    }
    #[test]
    fn sdk_buffers_reject_bad_probe_sizes_invalid_utf16_and_missing_terminators() {
        for size in [0, 4, 9, MAX_DETAIL_BYTES + 2] {
            assert!(detail_size(size, ERROR_INSUFFICIENT_BUFFER).is_err());
        }
        assert!(detail_size(128, ERROR_ACCESS_DENIED).is_err());
        assert!(detail_size(128, ERROR_INSUFFICIENT_BUFFER).is_ok());
        assert!(bounded_text(&[0xd800, 0]).is_err());
        assert!(bounded_text(&[65, 66]).is_err());
        assert!(bounded_text(&[65, 10, 0]).is_err());
        assert_eq!(bounded_text(&[0xd55c, 0xae00, 0]).unwrap(), "한글");
    }
}
