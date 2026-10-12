use super::*;
use std::io::BufRead;
use windows::core::PCWSTR;
use windows::Win32::{
    System::Com::{
        CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER,
        COINIT_APARTMENTTHREADED,
    },
    UI::Shell::*,
};

pub(super) fn execute(
    files: &Filesystem,
    workspace: &str,
    path: &Path,
    p: &Value,
    budget: &Budget,
) -> Result<Value> {
    budget.check()?;
    let parent = files.guards.parent(workspace, path)?;
    let filename = name(path)?;
    let current = info(&parent, filename)?;
    if p["expected_revision"] != current.revision() {
        return Err(RacpError::new("PRECONDITION_FAILED").into());
    }
    let stage_name = new_id("racp-trash");
    parent.mkdir(&stage_name)?;
    let stage_path = parent.path.join(&stage_name);
    let stage = files.guards.directory(workspace, &stage_path)?;
    if let Err(error) = super::acl::private_directory(&stage) {
        drop(stage);
        let _ = parent.unlink(&stage_name, true);
        return Err(error.into());
    }
    if let Err(error) =
        parent.move_to_checked(filename, &stage, filename, false, Some(&current.revision()))
    {
        drop(stage);
        let _ = parent.unlink(&stage_name, true);
        return Err(error.into());
    }
    let moved = stage_path.join(filename);
    let result = crate::providers::worker::run(
        "trash-worker",
        &files.spool,
        json!({"path":moved}),
        budget.deadline,
        &budget.cancel,
    );
    let absent = stage.info(filename).map_err(|_|Failure{error:RacpError::new("CLEANUP_UNKNOWN"),details:json!({"cleanup_status":"partial","recovery_path":moved,"recycle_state":"unknown"})})?.is_none();
    if let Err(error) = result {
        let restored = !absent && stage.move_to(filename, &parent, filename, false).is_ok();
        if restored || absent {
            drop(stage);
            let _ = parent.unlink(&stage_name, true);
        }
        return Err(Failure {
            error,
            details: json!({"cleanup_status":if restored{"complete"}else{"partial"},"rolled_back":restored,"recycled_or_unknown":absent,"recovery_path":if restored||absent{None}else{Some(moved)}}),
        });
    }
    if !absent {
        return Err(Failure {
            error: RacpError::new("CLEANUP_UNKNOWN"),
            details: json!({"cleanup_status":"partial","recovery_path":moved}),
        });
    }
    drop(stage);
    parent.unlink(&stage_name, true).map_err(|_|Failure{error:RacpError::new("CLEANUP_UNKNOWN"),details:json!({"cleanup_status":"partial","recycled":true,"empty_owned_staging":stage_path})})?;
    Ok(
        json!({"path":path,"recycled":true,"permanent_delete_fallback":false,"original_revision":current.revision(),"cleanup_status":"complete"}),
    )
}
pub fn run_worker() -> std::result::Result<(), RacpError> {
    let mut raw = vec![];
    std::io::stdin()
        .lock()
        .take(16385)
        .read_until(b'\n', &mut raw)?;
    if raw.len() > 16384 {
        return Err(RacpError::new("REQUEST_INVALID"));
    }
    racp_contract::local_ipc::reject_duplicate_keys(&raw)?;
    #[derive(serde::Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Input {
        path: PathBuf,
    }
    let input: Input = serde_json::from_slice(&raw)?;
    racp_core::validate_local_path(&input.path)?;
    let parent = input
        .path
        .parent()
        .ok_or_else(|| RacpError::new("PATH_ACCESS_DENIED"))?;
    if !parent
        .file_name()
        .is_some_and(|v| v.to_string_lossy().starts_with("racp-trash_"))
    {
        return Err(RacpError::new("PATH_ACCESS_DENIED"));
    }
    let path: Vec<u16> = input
        .path
        .as_os_str()
        .to_string_lossy()
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            .ok()
            .map_err(|_| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
    }
    struct Com;
    impl Drop for Com {
        fn drop(&mut self) {
            unsafe {
                CoUninitialize();
            }
        }
    }
    let _com = Com;
    unsafe {
        let operation: IFileOperation =
            CoCreateInstance(&FileOperation, None, CLSCTX_INPROC_SERVER)
                .map_err(|_| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
        operation
            .SetOperationFlags(
                FOF_NO_UI
                    | FOFX_RECYCLEONDELETE
                    | FOFX_ADDUNDORECORD
                    | FOFX_EARLYFAILURE
                    | FOFX_NOCOPYHOOKS,
            )
            .map_err(|_| RacpError::new("CAPABILITY_UNAVAILABLE"))?;
        let item: IShellItem = SHCreateItemFromParsingName(PCWSTR(path.as_ptr()), None)
            .map_err(|_| RacpError::new("PATH_ACCESS_DENIED"))?;
        operation
            .DeleteItem(&item, None)
            .map_err(|_| RacpError::new("PERMISSION_DENIED"))?;
        operation
            .PerformOperations()
            .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?;
        if operation
            .GetAnyOperationsAborted()
            .map_err(|_| RacpError::new("EXECUTION_UNKNOWN"))?
            .as_bool()
        {
            return Err(RacpError::new("EXECUTION_UNKNOWN"));
        }
    }
    println!("{}", json!({"ok":true,"result":{"recycled":true}}));
    Ok(())
}
