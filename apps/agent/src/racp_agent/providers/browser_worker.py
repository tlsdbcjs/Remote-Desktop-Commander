"""Contained, disposable Playwright worker. Imports begin only after the parent gate.

This file is launched by the base interpreter with -I, not the Windows venv redirector.
The parent assigns its Job Object before writing the first configuration line.
"""

import hashlib
import json
import os
import queue
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class WorkerError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def bounded_text(value: str, limit: int) -> str:
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


class BrowserWorker:
    def __init__(self, playwright: Any, config: dict[str, Any]) -> None:
        self.config = config
        self.pages: dict[str, Any] = {}
        self.revisions: dict[str, int] = {}
        self.observations: dict[str, tuple[str, str]] = {}
        self.frames: dict[str, Any] = {}
        self.page_frames: dict[str, list[str]] = {}
        self.refs: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.armed_page: Any = None
        self.armed_download: Any = None
        self.side_effect_started = False
        self.retained_uploads = 0
        self.events_active = False
        self.pending_events: dict[tuple[str, str | None], dict[str, Any]] = {}
        self.event_sequence, self.last_event_at = 0, 0.0
        self.borrowed = config.get("context_mode") == "existing"
        self.cdp_scope: dict[str, Any] | None = None
        self.targets: dict[str, str] = {}
        if config.get("cdp_endpoint"):
            self.browser = playwright.chromium.connect_over_cdp(
                config["cdp_endpoint"],
                timeout=config["timeout_ms"],
                no_defaults=True,
                is_local=True,
                artifacts_dir=config["downloads_path"],
            )
            self.cdp_scope = {
                "endpoint": config["cdp_endpoint"],
                "target_ids": [],
                "created_urls": [],
            }
        else:
            self.browser = playwright.chromium.launch(
                headless=config["headless"],
                chromium_sandbox=True,
                timeout=config["timeout_ms"],
                env=config["browser_env"],
                downloads_path=config["downloads_path"],
            )
        if self.borrowed:
            self.context = self.browser.contexts[0]
            chosen = set(config["page_target_ids"])
            selected = []
            for page in self.context.pages:
                target = self.target_info(page)["targetId"]
                if target in chosen:
                    selected.append((page, target))
            if len(selected) != len(chosen):
                raise WorkerError("HANDLE_EXPIRED")
            assert self.cdp_scope is not None
            if config["allow_page_termination"]:
                self.cdp_scope["target_ids"] = list(chosen)
            self.persist_cdp()
            for page, _target in selected:
                self.add_page(page)
                page.route("**/*", self.route)
                page.route_web_socket("**/*", self.websocket)
        else:
            self.context = self.browser.new_context(
                viewport={"width": config["width"], "height": config["height"]},
                accept_downloads=True,
                service_workers="block",
            )
            self.context.route("**/*", self.route)
            self.context.route_web_socket("**/*", self.websocket)
            self.context.on("page", self.add_page)
            self.context.on("download", self.download_event)
            page = self.context.new_page()
            if self.cdp_scope is not None:
                self.cdp_scope["context_id"] = self.target_info(page)["browserContextId"]
                self.persist_cdp()

    def target_info(self, page: Any) -> dict[str, Any]:
        session = page.context.new_cdp_session(page)
        try:
            return dict(session.send("Target.getTargetInfo")["targetInfo"])
        finally:
            session.detach()

    def persist_cdp(self) -> None:
        if self.cdp_scope is None:
            return
        path = Path(self.config["ownership_file"])
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("worker_pid") != os.getpid():
            raise WorkerError("PERMISSION_DENIED")
        record["remote_cdp"] = self.cdp_scope
        temporary = path.with_name("ownership.pending")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(record, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)

    def allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        host = parsed.hostname.encode("idna").decode().lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parsed.port
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            host += f":{port}"
        origin = f"{parsed.scheme}://{host}"
        return not self.config["allowed_origins"] or origin in self.config["allowed_origins"]

    def route(self, route: Any) -> None:
        blob_download = self.armed_page is not None and route.request.url.startswith("blob:")
        if self.allowed(route.request.url) or blob_download and self.allowed(route.request.url[5:]):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    def websocket(self, route: Any) -> None:
        url = route.url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
        if self.allowed(url):
            route.connect_to_server()
        else:
            route.close(code=1008, reason="network policy")

    def add_page(self, page: Any) -> str:
        for id, existing in self.pages.items():
            if page == existing:
                return id
        if len(self.pages) >= 8:
            page.close()
            self.event("page_limit", "rejected")
            raise WorkerError("RESOURCE_EXHAUSTED")
        id = "page_" + uuid.uuid4().hex
        self.pages[id], self.revisions[id], self.refs[id] = page, 0, {}
        self.page_frames[id] = []
        self.add_frame(id, page.main_frame)
        page.on("frameattached", lambda frame: self.add_frame(id, frame))
        page.on("framedetached", lambda frame: self.frame_changed(id))
        page.on("framenavigated", lambda frame: self.navigation(id, frame))
        page.on("dialog", self.dialog)
        if self.borrowed:
            page.on("download", self.download_event)
        if self.cdp_scope is not None:
            self.targets[id] = self.target_info(page)["targetId"]
        page.on("close", lambda: self.mark_event("page_closed", id, "closed"))
        self.mark_event("page_created", id, "active")
        return id

    def event(self, kind: str, state: str) -> None:
        self.events.append({"type": kind, "state": state})
        self.events = self.events[-32:]
        if kind.startswith("dialog:"):
            self.mark_event("dialog", None, state)
        elif kind == "download":
            self.mark_event("download", None, state)

    def mark_event(self, kind: str, page: str | None, state: str) -> None:
        if not self.events_active:
            return
        self.pending_events[(kind, page)] = {"kind": kind, "state": state}
        self.flush_events()

    def flush_events(self) -> None:
        if not self.pending_events or time.monotonic() - self.last_event_at < 0.05:
            return
        self.last_event_at = time.monotonic()
        events, self.pending_events = list(self.pending_events.values()), {}
        for event in events:
            self.event_sequence += 1
            reply(
                {
                    "type": "event",
                    "sequence": self.event_sequence,
                    **event,
                    "pages": self.inventory(),
                }
            )

    def dialog(self, dialog: Any) -> None:
        dialog.dismiss()
        self.event("dialog:" + dialog.type, "dismissed")

    def download_event(self, download: Any) -> None:
        if self.armed_page != download.page or self.armed_download is not None:
            download.cancel()
            try:
                download.delete()
            except Exception:
                pass
            self.event("download", "unsolicited_cancelled")
        else:
            self.armed_download = download

    def download(self, page: Any, data: dict[str, Any], timeout: int) -> dict[str, Any]:
        element = self.element(page, data)
        self.armed_page, self.armed_download = page, None
        try:
            with page.expect_download(timeout=timeout) as waiting:
                self.side_effect_started = True
                element.click(timeout=timeout)
            download = waiting.value
            path = download.path()
            if (
                path.resolve().parent != Path(self.config["downloads_path"]).resolve()
                or path.is_symlink()
            ):
                raise WorkerError("PERMISSION_DENIED")
            if path.stat().st_size > data["max_bytes"]:
                download.delete()
                raise WorkerError("RESOURCE_EXHAUSTED")
            total, checksum = 0, hashlib.sha256()
            # Only the generated spool filename is a destination; server filenames are data.
            with path.open("rb") as source, open(data["_output_path"], "xb") as output:
                while block := source.read(4 * 1024**2):
                    total += len(block)
                    if total > data["max_bytes"]:
                        raise WorkerError("RESOURCE_EXHAUSTED")
                    checksum.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            cleanup = "complete"
            try:
                download.delete()
            except Exception:
                cleanup = "pending"
            self.event("download", "completed")
            return {
                "artifact_id": None,
                "spool_path": data["_output_path"],
                "artifact_media_type": "application/octet-stream",
                "size_bytes": total,
                "sha256": checksum.hexdigest(),
                "suggested_filename": bounded_text(download.suggested_filename, 256),
                "native_cleanup": cleanup,
                "trust": "untrusted_page_data",
            }
        finally:
            self.armed_page, self.armed_download = None, None

    def upload(self, page: Any, data: dict[str, Any], timeout: int) -> dict[str, Any]:
        element = self.element(page, data)
        if self.retained_uploads >= 16:
            raise WorkerError("RESOURCE_EXHAUSTED")
        directory = Path(data["_upload_directory"])
        directory.mkdir(mode=0o700)
        path = directory / data["filename"]
        size, checksum = 0, hashlib.sha256()
        retained = False
        try:
            with open(data["_artifact_path"], "rb") as source, path.open("xb") as target:
                while block := source.read(4 * 1024**2):
                    size += len(block)
                    if size > data["_artifact_size"] or size > 1024**3:
                        raise WorkerError("INTEGRITY_ERROR")
                    checksum.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if size != data["_artifact_size"] or checksum.hexdigest() != data["_artifact_sha256"]:
                raise WorkerError("INTEGRITY_ERROR")
            self.page(data)
            self.side_effect_started = True
            element.set_input_files(str(path), timeout=timeout)
            # Chromium's File object may read its backing file later (fetch/FormData).
            # Keep the private immutable materialization until this context is closed.
            self.retained_uploads += 1
            retained = True
            self.event("upload", "completed")
            return {
                "input_artifact_id": data["artifact_id"],
                "filename": data["filename"],
                "size_bytes": size,
                "sha256": checksum.hexdigest(),
                "staging_retention": "browser_lifetime",
            }
        finally:
            # Generated staging directory is wholly inside the owned uploads sandbox.
            if directory.resolve().parent != Path(self.config["downloads_path"]).parent / "uploads":
                raise WorkerError("PERMISSION_DENIED")
            if not retained:
                shutil.rmtree(directory)

    def navigation(self, id: str, frame: Any) -> None:
        self.add_frame(id, frame)
        self.frame_changed(id)
        self.mark_event("navigation", id, "observed")

    def frame_changed(self, id: str) -> None:
        self.revisions[id] += 1
        self.observations.pop(id, None)
        self.mark_event("frame", id, "changed")

    def add_frame(self, page_id: str, frame: Any) -> str:
        for id in self.page_frames[page_id]:
            if self.frames[id] == frame:
                return id
        if len(self.frames) >= 512 or sum(not f.is_detached() for f in self.frames.values()) >= 128:
            self.pages[page_id].close()
            self.event("frame_limit", "closed_page")
            raise WorkerError("RESOURCE_EXHAUSTED")
        id = "frame_" + uuid.uuid4().hex
        self.frames[id] = frame
        self.page_frames[page_id].append(id)
        self.frame_changed(page_id)
        return id

    def frame(self, data: dict[str, Any], page: Any) -> tuple[str, Any]:
        id = data.get("frame_id") or self.add_frame(data["page_id"], page.main_frame)
        if id not in self.page_frames[data["page_id"]] or self.frames[id].is_detached():
            raise WorkerError("STALE_OBSERVATION")
        return id, self.frames[id]

    def frame_inventory(self, page_id: str) -> list[dict[str, Any]]:
        output = []
        for id in self.page_frames[page_id]:
            frame = self.frames[id]
            if frame.is_detached():
                continue
            parent = next(
                (
                    other
                    for other in self.page_frames[page_id]
                    if self.frames[other] == frame.parent_frame
                ),
                None,
            )
            output.append(
                {
                    "frame_id": id,
                    "parent_frame_id": parent,
                    "main": frame == self.pages[page_id].main_frame,
                    "url": bounded_text(frame.url, 256),
                    "name": bounded_text(frame.name, 64),
                }
            )
        return output

    def release_refs(self, id: str) -> None:
        for handle in self.refs[id].values():
            try:
                handle.dispose()
            except Exception:
                pass
        self.refs[id] = {}

    def page(self, data: dict[str, Any]) -> Any:
        id = data["page_id"]
        page = self.pages.get(id)
        if page is None or page.is_closed():
            raise WorkerError("HANDLE_EXPIRED")
        frame_id, _ = self.frame(data, page)
        if "observation_id" in data and (
            self.observations.get(id) != (data["observation_id"], frame_id)
            or str(self.revisions[id]) != data["navigation_revision"]
        ):
            raise WorkerError("STALE_OBSERVATION")
        return page

    def element(self, page: Any, data: dict[str, Any]) -> Any:
        _, scope = self.frame(data, page)
        if data.get("ref"):
            ref = self.refs[data["page_id"]].get(data["ref"])
            if ref is None or not ref.evaluate("e => e.isConnected"):
                raise WorkerError("STALE_OBSERVATION")
            self.page(data)
            return ref
        selector = data["selector"]
        if selector["by"] == "test_id":
            locator = scope.get_by_test_id(selector["value"])
        else:
            locator = scope.get_by_role(selector["value"], name=selector.get("name"), exact=True)
        count = locator.count()
        if count != 1:
            raise WorkerError("AMBIGUOUS_TARGET" if count > 1 else "ELEMENT_NOT_FOUND")
        self.page(data)
        return locator

    def snapshot(self, id: str, page: Any, data: dict[str, Any]) -> dict[str, Any]:
        frame_id, scope = self.frame(data, page)
        self.release_refs(id)
        observed_revision = self.revisions[id]
        elements = []
        matches = scope.locator("a,button,input,textarea,select,[role],[contenteditable=true]")
        count = matches.count()
        for index in range(min(count, 64)):
            element = matches.nth(index).element_handle(timeout=1000)
            if element is None:
                continue
            ref = "ref_" + uuid.uuid4().hex
            item = element.evaluate("""e => ({tag:e.tagName.toLowerCase(),
                role:(e.getAttribute('role') || '').slice(0,128),
                type:(e.getAttribute('type') || '').slice(0,128),
                name:(e.getAttribute('aria-label') || e.labels?.[0]?.textContent ||
                    e.innerText || '').slice(0,80),
                test_id:(e.getAttribute('data-testid') || '').slice(0,80),
                focused:document.activeElement === e})""")
            item = {
                key: bounded_text(value, 256) if isinstance(value, str) else value
                for key, value in item.items()
            }
            self.refs[id][ref] = element
            elements.append({"ref": ref, **item})
        tree = scope.locator("body").aria_snapshot(timeout=1000)
        title = bounded_text(scope.title(), 512)
        frames = self.frame_inventory(id)
        if observed_revision != self.revisions[id]:
            raise WorkerError("STALE_OBSERVATION")
        observation = "obs_" + uuid.uuid4().hex
        self.observations[id] = (observation, frame_id)
        result = {
            "observation_id": observation,
            "navigation_revision": str(observed_revision),
            "url": bounded_text(scope.url, 8192),
            "page_url": bounded_text(page.url, 8192),
            "frame_id": frame_id,
            "title": title,
            "frames": frames[:16],
            "semantic_tree": bounded_text(tree, 8192),
            "elements": elements,
            "focused_ref": next((e["ref"] for e in elements if e["focused"]), None),
            "viewport": page.viewport_size,
            "truncated": count > 64 or len(tree.encode("utf-8")) > 8192 or len(page.frames) > 16,
            "trust": "untrusted_page_data",
        }
        while elements and len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 48 * 1024:
            removed = elements.pop()
            self.refs[id].pop(removed["ref"]).dispose()
            if result["focused_ref"] == removed["ref"]:
                result["focused_ref"] = None
            result["truncated"] = True
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 48 * 1024:
            result.update(
                semantic_tree=bounded_text(tree, 4096),
                frames=frames[:8],
                url=bounded_text(scope.url, 4096),
                page_url=bounded_text(page.url, 2048),
                truncated=True,
            )
        return result

    def execute(self, action: str, data: dict[str, Any], timeout: int) -> dict[str, Any]:
        self.side_effect_started = False
        self.context.set_default_timeout(max(1, timeout))
        if action == "pages":
            return {"pages": self.inventory()}
        if action == "new_page":
            if len(self.pages) >= 8:
                raise WorkerError("RESOURCE_EXHAUSTED")
            if self.borrowed:
                assert self.cdp_scope is not None
                url = "about:blank#racp-" + uuid.uuid4().hex
                self.cdp_scope["created_urls"].append(url)
                self.persist_cdp()
                cdp = self.browser.new_browser_cdp_session()
                try:
                    target = cdp.send("Target.createTarget", {"url": url})["targetId"]
                finally:
                    cdp.detach()
                self.cdp_scope["target_ids"].append(target)
                self.persist_cdp()
                page = None
                deadline = time.monotonic() + min(timeout / 1000, 5)
                while page is None:
                    page = next(
                        (
                            p
                            for p in self.context.pages
                            if self.target_info(p)["targetId"] == target
                        ),
                        None,
                    )
                    if page is not None:
                        break
                    if time.monotonic() >= deadline:
                        raise WorkerError("BROWSER_ERROR")
                    self.pump()
                id = self.add_page(page)
                page.route("**/*", self.route)
                page.route_web_socket("**/*", self.websocket)
                return {"page_id": id}
            return {"page_id": self.add_page(self.context.new_page())}
        page = self.page(data)
        id = data["page_id"]
        frame_id, scope = self.frame(data, page)
        borrowed_target = self.borrowed and self.targets.get(id) in self.config["page_target_ids"]
        if self.borrowed and (
            action == "download"
            or action in {"upload", "evaluate"}
            and borrowed_target
            and not self.config["allow_page_termination"]
        ):
            raise WorkerError("OPERATION_NOT_SUPPORTED")
        if action == "navigate":
            if not self.allowed(data["url"]):
                raise WorkerError("PERMISSION_DENIED")
            self.side_effect_started = True
            scope.goto(data["url"], wait_until="domcontentloaded", timeout=timeout)
            result: dict[str, Any] = {"url": scope.url[:8192], "frame_id": frame_id}
        elif action == "snapshot":
            result = self.snapshot(id, page, data)
        elif action == "frames":
            entries = self.frame_inventory(id)
            cursor = data.get("cursor")
            if cursor is not None:
                if cursor not in self.page_frames[id]:
                    raise WorkerError("STALE_OBSERVATION")
                start = self.page_frames[id].index(cursor)
                entries = [f for f in entries if self.page_frames[id].index(f["frame_id"]) > start]
            selected = entries[: data["limit"]]
            result = {
                "frames": selected,
                "next_cursor": selected[-1]["frame_id"] if len(entries) > len(selected) else None,
                "consistency": "best_effort",
            }
        elif action == "screenshot":
            image = page.screenshot(type="png", full_page=False, timeout=timeout)
            if len(image) > 32 * 1024**2:
                raise WorkerError("RESOURCE_EXHAUSTED")
            Path(data["_output_path"]).write_bytes(image)
            result = {
                "spool_path": data["_output_path"],
                "artifact_id": None,
                "artifact_media_type": "image/png",
                "size_bytes": len(image),
            }
        elif action == "click":
            element = self.element(page, data)
            self.side_effect_started = True
            element.click(timeout=timeout)
            result = {}
        elif action == "type":
            element = self.element(page, data)
            self.side_effect_started = True
            element.fill(data["text"], timeout=timeout)
            result = {}
        elif action == "key":
            focused = scope.locator(":focus")
            if data.get("frame_id") and focused.count() != 1:
                raise WorkerError("FOCUS_MISMATCH")
            if not data.get("frame_id") and scope.evaluate(
                "() => document.activeElement?.tagName.toLowerCase() === 'iframe'"
            ):
                raise WorkerError("FOCUS_MISMATCH")
            self.side_effect_started = True
            if data.get("frame_id"):
                focused.press(data["key"], timeout=timeout)
            else:
                page.keyboard.press(data["key"])
            result = {}
        elif action == "evaluate":
            self.side_effect_started = True
            wrapper = """async () => {
                const expression = (EXPRESSION);
                const v = typeof expression === 'function' ? await expression() : await expression;
                const s = JSON.stringify(v);
                if (s === undefined) return {value:null};
                if (new TextEncoder().encode(s).byteLength > 65536) return {too_large:true};
                return {value:JSON.parse(s)};
            }""".replace("EXPRESSION", data["expression"])
            result = scope.evaluate(wrapper)
            if result.get("too_large"):
                raise WorkerError("RESOURCE_EXHAUSTED")
        elif action == "close_page":
            self.release_refs(id)
            page.close()
            result = {"closed": True}
        elif action == "download":
            result = self.download(page, data, timeout)
        elif action == "upload":
            result = self.upload(page, data, timeout)
        else:
            raise WorkerError("OPERATION_NOT_SUPPORTED")
        return {**result, "navigation_revision": str(self.revisions[id]), "events": self.events}

    def inventory(self) -> list[dict[str, Any]]:
        return [
            {
                "page_id": id,
                "navigation_revision": str(self.revisions[id]),
                "closed": p.is_closed(),
                "cdp_target_id": self.targets.get(id),
                "ownership": "borrowed"
                if self.borrowed and self.targets.get(id) in self.config["page_target_ids"]
                else "racp_owned",
            }
            for id, p in self.pages.items()
        ]

    def pump(self) -> None:
        # The sync API dispatches dialog/download/routes while processing API calls.
        # Keep that dispatcher running while stdin waits on a separate bounded reader.
        page = next((p for p in self.pages.values() if not p.is_closed()), None)
        if page is not None:
            page.wait_for_timeout(25)


def reply(value: dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > 1024**2 - 1:
        raw = b'{"error":{"code":"RESOURCE_EXHAUSTED"}}'
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


def main() -> None:
    config = json.loads(sys.stdin.buffer.readline(256 * 1024))
    sys.path.insert(0, config["site_packages"])
    from playwright.sync_api import Error, TimeoutError, sync_playwright

    try:
        with sync_playwright() as playwright:
            worker = BrowserWorker(playwright, config)
            reply(
                {
                    "result": {
                        "version": worker.browser.version,
                        "pages": worker.inventory(),
                        "cdp_cleanup": worker.cdp_scope,
                    }
                }
            )
            worker.events_active = True
            incoming: queue.Queue[bytes | None] = queue.Queue(maxsize=2)

            def read_input() -> None:
                while line := sys.stdin.buffer.readline(256 * 1024):
                    incoming.put(line)
                incoming.put(None)

            threading.Thread(target=read_input, daemon=True).start()
            while True:
                try:
                    line = incoming.get(timeout=0.025)
                except queue.Empty:
                    worker.pump()
                    worker.flush_events()
                    continue
                if line is None:
                    break
                try:
                    request = json.loads(line)
                    result = worker.execute(
                        request["action"], request["payload"], request["timeout"]
                    )
                    reply({"result": result, "pages": worker.inventory()})
                    worker.flush_events()
                except WorkerError as exc:
                    reply(
                        {
                            "error": {
                                "code": exc.code,
                                "execution_state": "unknown"
                                if worker.side_effect_started
                                else "not_started",
                            }
                        }
                    )
                except TimeoutError:
                    reply({"error": {"code": "TIMEOUT"}})
                except Error:
                    reply({"error": {"code": "BROWSER_ERROR"}})
                except OSError:
                    reply(
                        {
                            "error": {
                                "code": "RESOURCE_EXHAUSTED",
                                "execution_state": "unknown"
                                if worker.side_effect_started
                                else "not_started",
                            }
                        }
                    )
            worker.browser.close()
    except Exception:
        reply({"error": {"code": "CAPABILITY_UNAVAILABLE"}})


if __name__ == "__main__":
    main()
