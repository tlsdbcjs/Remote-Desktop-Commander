//! Installer guard: authenticated shutdown, pinned backup, no forced GUI termination.
use racp_contract::{digest,new_id,RacpError};
use racp_core::{FileInfo,InstanceLock,Workspaces};
use serde_json::{json,Value};
use sha2::{Digest,Sha256};
use std::{path::{Path,PathBuf},io::{Read,Write}};
fn option(args:&[String],name:&str)->Result<String,RacpError>{
    args.windows(2).find(|p|p[0]==name).map(|p|p[1].clone()).ok_or_else(||RacpError::new("REQUEST_INVALID"))
}
fn scan(root:&Path, gui_only:bool)->Result<(),RacpError>{
    let system=sysinfo::System::new_all();
    for (pid,process) in system.processes() {
        if pid.as_u32()==std::process::id(){continue;}
        let name=process.name().to_string_lossy().to_lowercase();
        let candidate=!gui_only || name=="racp-client.exe" || name=="racp client.exe";
        if !candidate {continue;}
        if let Some(exe)=process.exe() {
            if racp_core::path_within(exe,root) {return Err(RacpError::new("RESOURCE_BUSY"));}
        } else if name=="racp-client.exe" || name=="racp client.exe" || name=="racp-agent.exe" {
            return Err(RacpError::new("PRECONDITION_FAILED"));
        }
    }
    Ok(())
}
fn backup(state:&Path)->Result<Value,RacpError>{
    if !state.try_exists()? {return Ok(Value::Null);}
    let roots=Workspaces::new(state,&[])?;
    let output=state.join("backups").join(new_id("before-maintenance"));
    racp_core::private_dir(&output)?;
    let destination=Workspaces::new(&output,&[])?;
    let mut pending=vec![(state.to_path_buf(),output.clone(),0u8)];
    let mut files=vec![];let mut total=0u64;let mut count=0u32;
    while let Some((source,target,depth))=pending.pop(){
        if depth>64{return Err(RacpError::new("RESOURCE_EXHAUSTED"));}
        let dir=roots.directory("default",&source)?;
        let copied=destination.directory("default",&target)?;
        for name in dir.names()? {
            count+=1;if count>20000{return Err(RacpError::new("RESOURCE_EXHAUSTED"));}
            if source==state && name.eq_ignore_ascii_case("backups") || name.ends_with(".lock"){continue;}
            let info=dir.info(&name)?.ok_or_else(||RacpError::new("PRECONDITION_FAILED"))?;
            if info.link{return Err(RacpError::new("PATH_ACCESS_DENIED"));}
            if info.directory {
                copied.mkdir(&name)?;
                pending.push((source.join(&name),target.join(&name),depth+1));continue;
            }
            if info.directory || info.link{return Err(RacpError::new("PATH_ACCESS_DENIED"));}
            let mut input=dir.open_read(&name)?;
            let before=FileInfo::from_file(&input)?;
            total=total.checked_add(before.size).filter(|v|*v<=10*1024*1024*1024).ok_or_else(||RacpError::new("RESOURCE_EXHAUSTED"))?;
            let mut file=copied.create(&name)?;let mut hasher=Sha256::new();let mut size=0u64;let mut buffer=[0u8;65536];
            loop{let n=input.read(&mut buffer)?;if n==0{break;}size+=n as u64;if size>before.size{return Err(RacpError::new("PRECONDITION_FAILED"));}hasher.update(&buffer[..n]);file.write_all(&buffer[..n])?;}
            file.sync_all()?;drop(file);
            if size!=before.size || FileInfo::from_file(&input)?.revision()!=before.revision() || dir.require_file(&name)?.revision()!=before.revision(){return Err(RacpError::new("PRECONDITION_FAILED"));}
            let hash=format!("{:x}",hasher.finalize());
            let mut verifier=copied.open_read(&name)?;let mut checked=Sha256::new();loop{let n=verifier.read(&mut buffer)?;if n==0{break;}checked.update(&buffer[..n]);}
            if format!("{:x}",checked.finalize())!=hash{return Err(RacpError::new("PRECONDITION_FAILED"));}
            files.push(json!({"file":source.join(&name).strip_prefix(state).map_err(|_|RacpError::new("PATH_ACCESS_DENIED"))?.to_string_lossy().replace('\\',"/"),"size":size,"sha256":hash}));
        }
    }
    racp_core::atomic_write(&output.join("backup-manifest.json"),&serde_json::to_vec(&json!({"version":1,"kind":"same-user-agent-state","files":files}))?,false)?;
    Ok(json!(output))
}
#[cfg(windows)]
fn startup(install:&Path,name:&str,mode:&str)->Result<bool,RacpError>{
    use windows_sys::Win32::{Foundation::*,System::Registry::*};
    if name!="app.racp.client"{return Err(RacpError::new("REQUEST_INVALID"));}
    fn wide(s:&str)->Vec<u16>{s.encode_utf16().chain(Some(0)).collect()}
    struct Key(HKEY);impl Drop for Key{fn drop(&mut self){unsafe{RegCloseKey(self.0);}}}
    let mut handle=std::ptr::null_mut();
    let code=unsafe{RegOpenKeyExW(HKEY_CURRENT_USER,wide(r"Software\Microsoft\Windows\CurrentVersion\Run").as_ptr(),0,KEY_QUERY_VALUE|KEY_SET_VALUE,&mut handle)};
    if code==ERROR_FILE_NOT_FOUND{return Ok(false);}if code!=ERROR_SUCCESS{return Err(RacpError::new("LOCAL_STATE_FAILED"));}
    let key=Key(handle);let mut kind=0;let mut size=0;let name=wide(name);
    let code=unsafe{RegQueryValueExW(key.0,name.as_ptr(),std::ptr::null(),&mut kind,std::ptr::null_mut(),&mut size)};
    if code==ERROR_FILE_NOT_FOUND{return Ok(false);}if code!=ERROR_SUCCESS || kind!=REG_SZ || size>32768 || size%2!=0{return Err(RacpError::new("PRECONDITION_FAILED"));}
    let mut raw=vec![0u16;size as usize/2];
    if unsafe{RegQueryValueExW(key.0,name.as_ptr(),std::ptr::null(),&mut kind,raw.as_mut_ptr().cast(),&mut size)}!=ERROR_SUCCESS{return Err(RacpError::new("LOCAL_STATE_FAILED"));}
    let end=raw.iter().position(|v|*v==0).unwrap_or(raw.len());let current=String::from_utf16(&raw[..end]).map_err(|_|RacpError::new("PRECONDITION_FAILED"))?;
    let owned=["racp-client.exe","RACP Client.exe"].iter().any(|file|current.eq_ignore_ascii_case(&format!("\"{}\" racp-background-agent",install.join(file).display())));
    if !owned{return Err(RacpError::new("PERMISSION_DENIED"));}
    if mode=="uninstall" {
        if unsafe{RegDeleteValueW(key.0,name.as_ptr())}!=ERROR_SUCCESS{return Err(RacpError::new("LOCAL_STATE_FAILED"));}
        let mut approved=std::ptr::null_mut();let code=unsafe{RegOpenKeyExW(HKEY_CURRENT_USER,wide(r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run").as_ptr(),0,KEY_SET_VALUE,&mut approved)};
        if code==ERROR_SUCCESS{let approved=Key(approved);let code=unsafe{RegDeleteValueW(approved.0,name.as_ptr())};if code!=ERROR_SUCCESS&&code!=ERROR_FILE_NOT_FOUND{return Err(RacpError::new("LOCAL_STATE_FAILED"));}}
        else if code!=ERROR_FILE_NOT_FOUND{return Err(RacpError::new("LOCAL_STATE_FAILED"));}
        return Ok(true);
    }
    if mode=="finalize" {
        let value=wide(&format!("\"{}\" racp-background-agent",install.join("racp-client.exe").display()));
        if unsafe{RegSetValueExW(key.0,name.as_ptr(),0,REG_SZ,value.as_ptr().cast(),(value.len()*2) as u32)}!=ERROR_SUCCESS{return Err(RacpError::new("LOCAL_STATE_FAILED"));}
    }
    Ok(false)
}
pub async fn prepare(args:&[String])->Result<Value,RacpError>{
    if !cfg!(windows){return Err(RacpError::new("OPERATION_NOT_SUPPORTED"));}
    let install=racp_core::validate_local_path(&PathBuf::from(option(args,"--install-dir")?))?;
    let state=racp_core::validate_local_path(&PathBuf::from(option(args,"--state-dir")?))?;
    let executable=option(args,"--executable-name")?;
    if executable!="racp-client.exe"{return Err(RacpError::new("REQUEST_INVALID"));}
    let mode=option(args,"--mode")?;
    if !matches!(mode.as_str(),"upgrade"|"uninstall"|"finalize"){return Err(RacpError::new("REQUEST_INVALID"));}
    let login=option(args,"--login-name")?;
    #[cfg(windows)] startup(&install,&login,"inspect")?;
    if mode=="finalize" {
        #[cfg(windows)] startup(&install,&login,"finalize")?;
        return Ok(json!({"startup_migrated":true,"data":"preserved"}));
    }
    scan(&install,true)?;
    let control=crate::ControlClient::new(state.clone(),install.join("agent/racp-agent.exe"));
    let result=control.maintenance_stop(&install).await?;
    if result["state"]!="STOPPED" || matches!(result["cleanup_status"].as_str(),Some("unknown"|"partial"|"pending")){return Err(RacpError::new("CLEANUP_FAILED"));}
    scan(&install,false)?;
    let _lock=if state.join("credential.bin").try_exists()?{
        let (settings,_)=racp_core::load_settings(&state,true)?;
        Some(InstanceLock::acquire(&state.join(format!("agent-{}.lock",digest(&settings.device_id))))?)
    }else{None};
    let saved=backup(&state)?;
    if control.status().await?["state"]!="STOPPED"{return Err(RacpError::new("RESOURCE_BUSY"));}
    #[cfg(windows)] let removed=startup(&install,&login,&mode)?;
    #[cfg(not(windows))] let removed=false;
    Ok(json!({"agent":"STOPPED","data":"preserved","backup":saved,"startup_removed":removed}))
}
