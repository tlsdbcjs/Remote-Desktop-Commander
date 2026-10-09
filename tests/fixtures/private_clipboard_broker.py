"""Native clipboard + real authenticated Broker pipe in an owned private station.

Test-only backend: no public WinSta0 clipboard, GUI input or UIA/COM.
"""

import argparse
import ctypes
import os
import uuid
from ctypes import wintypes

from racp_agent.broker.clipboard import ClipboardChanged, WindowsClipboard
from racp_agent.broker.config import BrokerConfig
from racp_agent.broker.core import BrokerCore
from racp_agent.broker.identity import process_identity
from racp_agent.broker.pipe import PipeServer
from racp_domain.models import RACPError


class PrivateBackend:
    def __init__(self, session_id):
        self.session_id = session_id
        self.identity = process_identity(os.getpid())

    def status(self):
        return {
            "available": True,
            "session_id": self.session_id,
            "broker_pid": self.identity.pid,
            "broker_created": self.identity.created,
            "user_sid": self.identity.sid,
            "input_guardian_available": False,
            "clipboard_scope": "owned_private_test_window_station",
        }

    def clipboard(self, operation, payload):
        try:
            with WindowsClipboard() as clipboard:
                if operation == "clipboard.read":
                    return clipboard.read(payload["max_bytes"])
                if operation == "clipboard.state":
                    return clipboard.state()
                return clipboard.write(payload["text"], payload["expected_sequence"])
        except ClipboardChanged:
            raise RACPError(
                "PRECONDITION_FAILED", "Private clipboard changed", layer="broker",
                reason="CLIPBOARD_CHANGED",
            ) from None

    def release_inputs(self):
        pass

    def release_lease(self):
        pass

    def clear_observations(self):
        pass

    def guard_info(self):
        return {"guardian": None}

    def attach_guard(self, _raw):
        raise RACPError("CAPABILITY_UNAVAILABLE", "No GUI input in private fixture", layer="broker")


def run(config):
    assert ctypes.WinDLL("shell32").IsUserAnAdmin()
    config.require_agent(process_identity(config.agent_pid))
    config.require_broker(process_identity(os.getpid()))
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32")
    user.GetProcessWindowStation.restype = wintypes.HANDLE
    user.CreateWindowStationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    user.CreateWindowStationW.restype = wintypes.HANDLE
    user.SetProcessWindowStation.argtypes = [wintypes.HANDLE]
    user.CloseWindowStation.argtypes = [wintypes.HANDLE]
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.CreateDesktopW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    user.CreateDesktopW.restype = wintypes.HANDLE
    user.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    old_station = user.GetProcessWindowStation()
    old_desktop = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    station = user.CreateWindowStationW("RACP-Private-Clip-" + uuid.uuid4().hex, 0, 0xF037F, None)
    assert station and user.SetProcessWindowStation(station)
    desktop = user.CreateDesktopW("Owned", None, None, 0, 0xF01FF, None)
    assert desktop and user.SetThreadDesktop(desktop)
    core = BrokerCore(config.session_id, PrivateBackend(config.session_id))
    server = None
    try:
        with WindowsClipboard() as clipboard:
            clipboard.write("RACP_OWNED_PRIVATE_CLIP_한글🙂", clipboard.state()["sequence_number"])
        server = PipeServer(config)
        while True:
            config.require_agent(process_identity(config.agent_pid))
            core.tick()
            server.accept(core.handle)
    finally:
        if server:
            server.close()
        assert user.SetThreadDesktop(old_desktop)
        assert user.SetProcessWindowStation(old_station)
        assert user.CloseDesktop(desktop)
        assert user.CloseWindowStation(station)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairing", required=True)
    run(BrokerConfig.load(__import__("pathlib").Path(parser.parse_args().pairing)))
