import { readFile,writeFile,mkdir,access,readdir,lstat,cp,rename } from "node:fs/promises";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"..");
const sha=async(file)=>{const hash=createHash("sha256");for await(const bytes of createReadStream(file))hash.update(bytes);return hash.digest("hex");};
function run(exe,args,env={}){const result=spawnSync(exe,args,{cwd:root,encoding:"utf8",env:{...process.env,...env}});if(result.status!==0)throw new Error("Runtime staging command failed: "+exe);return result.stdout;}
async function files(root){const result=[];async function walk(folder,depth=0){if(depth>32)throw new Error("Runtime nesting limit");for(const row of await readdir(folder,{withFileTypes:true})){const file=path.join(folder,row.name);const info=await lstat(file);if(info.isSymbolicLink())throw new Error("Linked runtime component");if(info.isDirectory())await walk(file,depth+1);else if(info.isFile()){if(result.length>=20000)throw new Error("Runtime inventory limit");result.push({path:path.relative(root,file).replaceAll("\\","/"),size:info.size,sha256:await sha(file)});}else throw new Error("Non-file runtime component");}}await walk(root);return result.sort((a,b)=>a.path.localeCompare(b.path));}
export async function stageWebView({admit=false}={}){
 if(process.platform!=="win32"||process.arch!=="x64")throw new Error("Native Windows x64 build required");
 const pin=JSON.parse(await readFile(path.join(root,"scripts/webview2-runtime-lock.json"),"utf8"));
 if(!/^\d+\.\d+\.\d+\.\d+$/.test(pin.version)||pin.architecture!=="x64"||!/^https:\/\/msedge\.sf\.dl\.delivery\.mp\.microsoft\.com\//.test(pin.url))throw new Error("Invalid pinned runtime source");
 if(!pin.sha256&&!admit)throw new Error("Runtime dependency has not been admitted");
 const cache=path.join(root,".tools","webview2",pin.version);await mkdir(cache,{recursive:true});const cab=path.join(cache,"runtime.cab");
 try{await access(cab);}catch{const response=await fetch(pin.url,{signal:AbortSignal.timeout(120000)});if(!response.ok||!response.body)throw new Error("Runtime download failed");const temporary=cab+".partial";await pipeline(Readable.fromWeb(response.body),(await import("node:fs")).createWriteStream(temporary,{flags:"wx"}));await rename(temporary,cab);}
 const hash=await sha(cab);if(pin.sha256&&pin.sha256!==hash)throw new Error("Runtime cabinet hash differs");
 console.log("WEBVIEW2_ADMISSION "+JSON.stringify({...pin,sha256:hash}));
 const expanded=path.join(cache,"expanded");try{await access(expanded);}catch{await mkdir(expanded);run("expand.exe",[cab,"-F:*",expanded]);}
 const candidates=[];for(const entry of await readdir(expanded,{withFileTypes:true})){if(entry.isDirectory())candidates.push(path.join(expanded,entry.name));}candidates.push(expanded);
 let runtime;for(const folder of candidates){try{await access(path.join(folder,"msedgewebview2.exe"));runtime=folder;break;}catch{}}
 if(!runtime)throw new Error("Fixed runtime executable missing");
 const evidence=JSON.parse(run("pwsh",["-NoProfile","-NonInteractive","-Command","$p=$env:RACP_WEBVIEW_BINARY; $s=Get-AuthenticodeSignature -LiteralPath $p; $v=(Get-Item -LiteralPath $p).VersionInfo; @{status=$s.Status.ToString();subject=$s.SignerCertificate.Subject;version=$v.FileVersion}|ConvertTo-Json -Compress"],{RACP_WEBVIEW_BINARY:path.join(runtime,"msedgewebview2.exe")}));
 if(evidence.status!=="Valid"||!evidence.subject?.includes("Microsoft Corporation")||!evidence.version?.startsWith(pin.version))throw new Error("Runtime signature/version differs");
 const binary=await readFile(path.join(runtime,"msedgewebview2.exe"));const pe=binary.readUInt32LE(60);if(binary.subarray(0,2).toString()!=="MZ"||binary.readUInt16LE(pe+4)!==0x8664)throw new Error("Runtime architecture differs");
 const staged=path.join(root,"apps/client/src-tauri/staged/webview2");try{await access(staged);throw new Error("Staged runtime already exists; preserve or quarantine this batch");}catch(e){if(e.code!=="ENOENT")throw e;}
 await mkdir(path.dirname(staged),{recursive:true});await cp(runtime,staged,{recursive:true,errorOnExist:true,force:false});
 const inventory=await files(staged);await writeFile(path.join(path.dirname(staged),"webview2-manifest.json"),JSON.stringify({version:pin.version,architecture:"x64",cab_sha256:hash,files:inventory},null,2));
 await mkdir(path.join(root,"apps/client/src-tauri/staged/agent"),{recursive:true});return{...pin,sha256:hash,files:inventory};
}
if(process.argv[1]===fileURLToPath(import.meta.url)){await stageWebView({admit:process.argv.includes("--admit-runtime")});}
