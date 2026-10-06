"""OS session-wide exclusion across separate Agent/Broker processes."""

from typing import Any

from racp_domain.models import RACPError

from racp_agent.broker.release_gate import ReleaseGate


class SessionInputLease:
    def __init__(self, session_id: int, user_sid: str) -> None:
        import pywintypes
        import win32event
        import win32security

        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = (
            win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f"D:P(A;;GA;;;{user_sid})(A;;GA;;;SY)", 1
            )
        )
        self.handle: Any = win32event.CreateMutex(
            attributes, False, f"Local\\RACP-Desktop-Input-{session_id}"
        )
        self.owned = False
        self.gate = ReleaseGate(session_id, user_sid)

    def acquire(self) -> None:
        import win32event

        if not self.gate.ready():
            raise RACPError(
                "RESOURCE_BUSY", "previous desktop input cleanup is pending", layer="broker"
            )
        if self.owned or win32event.WaitForSingleObject(self.handle, 0) not in {0, 0x80}:
            raise RACPError(
                "RESOURCE_BUSY", "session input is leased by another Broker", layer="broker"
            )
        # WAIT_ABANDONED transfers ownership after the previous Broker exits.
        self.owned = True
        if not self.gate.ready():
            self.release()
            raise RACPError(
                "RESOURCE_BUSY",
                "desktop input cleanup changed during lease acquire",
                layer="broker",
            )

    def release(self) -> None:
        import win32event

        if self.owned:
            win32event.ReleaseMutex(self.handle)
            self.owned = False

    def close(self) -> None:
        self.release()
        self.handle.Close()
        self.gate.close()
