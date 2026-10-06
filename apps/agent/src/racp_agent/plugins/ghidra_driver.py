import asyncio
import json
import os
from pathlib import Path
from typing import Any

from racp_domain.models import RACPError
from racp_protocol.models import new_id
from racp_protocol.plugins import PluginManifest

from racp_agent.plugins.manifest import decode_json
from racp_agent.plugins.process import OwnedPluginProcess


class GhidraDriver:
    def __init__(self, installation: Path, java: Path, manifest: PluginManifest) -> None:
        self.installation, self.java, self.manifest = installation, java, manifest
        self.current: OwnedPluginProcess | None = None
        self.log = bytearray()
        self.log_bytes = 0

    async def drain(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(8192):
            self.log_bytes += len(chunk)
            self.log.extend(chunk)
            del self.log[:-65536]
            if self.log_bytes > 8 * 1024**2:
                raise RACPError(
                    "RESOURCE_EXHAUSTED",
                    "Ghidra log limit exceeded",
                    layer="plugin",
                    execution_state="unknown",
                )

    async def process(self, command: list[str], directory: Path) -> int:
        owned = await OwnedPluginProcess.start(
            self.manifest.model_copy(
                update={"command": command, "working_directory": str(directory)}
            )
        )
        self.current = owned
        self.log, self.log_bytes = bytearray(), 0
        assert owned.process.stdout is not None and owned.process.stderr is not None
        drains = [
            asyncio.create_task(self.drain(owned.process.stdout)),
            asyncio.create_task(self.drain(owned.process.stderr)),
        ]
        finished = asyncio.create_task(owned.process.wait())
        try:
            done, _ = await asyncio.wait([finished, *drains], return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if not task.cancelled():
                    error = task.exception()
                    if error is not None:
                        raise error
            return await finished
        finally:
            for task in [finished, *drains]:
                task.cancel()
            await asyncio.gather(finished, *drains, return_exceptions=True)
            await owned.stop()
            self.current = None
            if owned.cleanup_status != "complete":
                # Exit the generation; the parent supervisor must reclaim its Job.
                raise ValueError("Ghidra child process-tree cleanup is unverified")

    def vm_arguments(self, directory: Path) -> list[str]:
        platform = "WINDOWS" if os.name == "nt" else "LINUX"
        props = (self.installation / "support/launch.properties").read_text(encoding="utf-8")
        result = [
            line.split("=", 1)[1]
            for line in props.splitlines()
            if line.startswith(("VMARGS=", "VMARGS_" + platform + "="))
        ]
        for name in ["home", "settings", "cache", "temp"]:
            (directory / name).mkdir(exist_ok=True)
        return [
            *result,
            "-Xmx512m",
            "-XX:ParallelGCThreads=2",
            "-XX:CICompilerCount=2",
            "-Djava.awt.headless=true",
            "-Dcpu.core.limit=2",
            "-Duser.home=" + str(directory / "home"),
            "-Dapplication.settingsdir=" + str(directory / "settings"),
            "-Dapplication.cachedir=" + str(directory / "cache"),
            "-Dapplication.tempdir=" + str(directory / "temp"),
            "-Djava.io.tmpdir=" + str(directory / "temp"),
        ]

    async def call(
        self, target: Path, payload: dict[str, Any], seconds: float, *, opening: bool = False
    ) -> dict[str, Any]:
        directory = target.parent / "ghidra"
        directory.mkdir(exist_ok=True)
        project = target.parent / "database"
        project.mkdir(exist_ok=True)
        identifier = new_id("bridge")
        request, response = directory / (identifier + ".json"), directory / (identifier + ".out")
        request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        arguments = [str(project), "analysis"]
        if opening:
            arguments.extend(
                ["-import", str(target), "-analysisTimeoutPerFile", str(max(1, int(seconds)))]
            )
        else:
            arguments.extend(["-process", target.name, "-noanalysis"])
            if payload["action"] not in {"rename", "comment"}:
                arguments.append("-readOnly")
        arguments.extend(
            [
                "-max-cpu",
                "2",
                "-scriptPath",
                str(Path(__file__).parent),
                "-postScript",
                "RACPStaticBridge.java",
                str(request),
                str(response),
            ]
        )
        command = [
            str(self.java),
            *self.vm_arguments(directory),
            "-cp",
            str(self.installation / "Ghidra/Framework/Utility/lib/Utility.jar"),
            "ghidra.Ghidra",
            "ghidra.app.util.headless.AnalyzeHeadless",
            *arguments,
        ]
        try:
            mutation = payload["action"] in {"rename", "comment"}
            async with asyncio.timeout(seconds):
                code = await self.process(command, directory)
            (directory / "last-operation.log").write_bytes(self.log)
            if code != 0 or not response.is_file():
                if opening or mutation:
                    raise ValueError("Ghidra mutation/open outcome is uncertain")
                raise RACPError(
                    "GHIDRA_OPERATION_FAILED",
                    "Ghidra headless operation did not produce a result",
                    layer="plugin",
                    execution_state="not_started",
                )
            with response.open("rb") as stream:
                raw = stream.read(512 * 1024 + 1)
            result = decode_json(raw, 512 * 1024)
            if "error" in result:
                if opening or result.get("execution_state") != "not_started":
                    raise ValueError("Ghidra mutation/open outcome is uncertain")
                raise RACPError(
                    result["error"],
                    str(result.get("message", "Ghidra rejected operation"))[:4096],
                    layer="plugin",
                    execution_state="not_started",
                )
            if set(result) != {"result"} or not isinstance(result["result"], dict):
                raise ValueError("Ghidra response framing differs")
            if mutation and b"REPORT: Save succeeded for processed file:" not in self.log:
                raise ValueError("Ghidra mutation persistence acknowledgement is absent")
            return result["result"]
        finally:
            request.unlink(missing_ok=True)
            response.unlink(missing_ok=True)
