"""Execution account display derived from Windows rather than caller environment."""

import getpass
import os


def execution_identity() -> str:
    if os.name == "nt":
        import win32api

        return str(win32api.GetUserName())
    return getpass.getuser()
