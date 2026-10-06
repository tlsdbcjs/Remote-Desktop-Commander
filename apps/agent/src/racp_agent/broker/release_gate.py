"""Keep new session leases out until an armed operation's independent cleanup finishes."""

from typing import Any


class ReleaseGate:
    def __init__(self, session_id: int, sid: str) -> None:
        import pywintypes
        import win32event
        import win32security

        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = (
            win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f"D:P(A;;GA;;;{sid})(A;;GA;;;SY)", 1
            )
        )
        self.handle: Any = win32event.CreateEvent(
            attributes, True, True, f"Local\\RACP-Input-Released-{session_id}"
        )
        self.owned = False

    def ready(self) -> bool:
        import win32event

        return bool(win32event.WaitForSingleObject(self.handle, 0) == 0)

    def arm(self) -> None:
        import win32event

        if not self.owned:
            if not self.ready():
                raise PermissionError("another input guardian has pending cleanup")
            win32event.ResetEvent(self.handle)
            self.owned = True

    def release(self) -> None:
        import win32event

        if self.owned:
            win32event.SetEvent(self.handle)
            self.owned = False

    def close(self) -> None:
        # Never falsely signal a failed cleanup while disposing a handle.
        self.handle.Close()
