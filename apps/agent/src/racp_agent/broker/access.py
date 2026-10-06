"""Minimum peer-query grants on our own process/token; no token duplication or impersonation."""

from collections.abc import Callable
from typing import Any


def grant_own_access(principals: tuple[str, ...], *, managed_broker: bool) -> Callable[[], None]:
    import win32api
    import win32security

    process = win32api.GetCurrentProcess()
    token = win32security.OpenProcessToken(
        process, 0x60008
    )  # READ_CONTROL | WRITE_DAC | TOKEN_QUERY
    saved: list[tuple[Any, Any]] = []
    try:
        for handle, rights in ((process, 0x101101 if managed_broker else 0x101000), (token, 8)):
            original = win32security.GetKernelObjectSecurity(
                handle, win32security.DACL_SECURITY_INFORMATION
            )
            # Clone the descriptor; leave existing owner/system permissions untouched.
            descriptor = win32security.SECURITY_DESCRIPTOR(bytes(original))
            acl = descriptor.GetSecurityDescriptorDacl()
            if acl is None:
                raise PermissionError("a null process/token DACL cannot authenticate a Broker peer")
            for value in set(principals):
                principal = win32security.ConvertStringSidToSid(value)
                acl.AddAccessAllowedAce(win32security.ACL_REVISION, rights, principal)
            descriptor.SetSecurityDescriptorDacl(1, acl, 0)
            win32security.SetKernelObjectSecurity(
                handle, win32security.DACL_SECURITY_INFORMATION, descriptor
            )
            saved.append((handle, original))
    except BaseException:
        for handle, descriptor in reversed(saved):
            win32security.SetKernelObjectSecurity(
                handle, win32security.DACL_SECURITY_INFORMATION, descriptor
            )
        token.Close()
        raise

    def restore() -> None:
        try:
            for handle, descriptor in reversed(saved):
                win32security.SetKernelObjectSecurity(
                    handle, win32security.DACL_SECURITY_INFORMATION, descriptor
                )
        finally:
            token.Close()

    return restore
