"""Private Agent files, using the actual execution identity and SYSTEM on Windows."""

import os
from pathlib import Path

from racp_agent.providers.paths import is_link


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(is_link(parent.lstat()) for parent in [path, *path.parents]):
        raise PermissionError("private Agent directory cannot follow links")
    path = path.resolve(strict=True)
    if os.name == "nt":
        import win32api
        import win32con
        import win32file
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        acl = win32security.ACL()
        for principal in (
            sid,
            win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
        ):
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION_DS, 3, win32file.FILE_ALL_ACCESS, principal
            )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )
    else:
        path.chmod(0o700)
    return path
