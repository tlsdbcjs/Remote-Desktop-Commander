"""Actual Win32 clipboard API in a private window station, never the user's clipboard."""

import ctypes
import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows clipboard")


def test_private_native_clipboard_roundtrip_cas_and_utf8_budget(tmp_path: Path) -> None:
    assert importlib.util.find_spec("racp_agent.broker.clipboard"), (
        "Native clipboard backend missing"
    )
    module = importlib.import_module("racp_agent.broker.clipboard")
    script = tmp_path / "private_clipboard.py"
    script.write_text(
        """import ctypes,importlib.util,json,sys,uuid
from ctypes import wintypes
assert ctypes.WinDLL('shell32').IsUserAnAdmin()
user=ctypes.WinDLL('user32',use_last_error=True)
user.GetProcessWindowStation.restype=wintypes.HANDLE
user.CreateWindowStationW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
user.CreateWindowStationW.restype=wintypes.HANDLE
user.SetProcessWindowStation.argtypes=[wintypes.HANDLE]
user.CloseWindowStation.argtypes=[wintypes.HANDLE]
user.GetThreadDesktop.argtypes=[wintypes.DWORD];user.GetThreadDesktop.restype=wintypes.HANDLE
user.CreateDesktopW.argtypes=[wintypes.LPCWSTR,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
user.CreateDesktopW.restype=wintypes.HANDLE
user.SetThreadDesktop.argtypes=[wintypes.HANDLE];user.CloseDesktop.argtypes=[wintypes.HANDLE]
kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentThreadId.restype=wintypes.DWORD
old_station=user.GetProcessWindowStation();old_desktop=user.GetThreadDesktop(kernel.GetCurrentThreadId())
station=user.CreateWindowStationW('RACP-Clipboard-Test-'+uuid.uuid4().hex,0,0x000f037f,None)
assert station and user.SetProcessWindowStation(station)
desktop=user.CreateDesktopW('Owned',None,None,0,0x000f01ff,None)
assert desktop and user.SetThreadDesktop(desktop)
try:
 spec=importlib.util.spec_from_file_location('owned_clipboard',sys.argv[1]);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 with module.WindowsClipboard() as clipboard:
  initial=clipboard.read(8192)
  def reject():raise PermissionError('Own test session lost')
  try:clipboard.write('must not clear',initial['sequence_number'],guard=reject)
  except PermissionError:pass
  else:raise AssertionError('Session guard bypassed')
  assert clipboard.read(8192)['sequence_number']==initial['sequence_number']
  text='RACP_OWN_CLIP_한글🙂'
  written=clipboard.write(text,initial['sequence_number'])
  observed=clipboard.read(8192)
  assert written['sequence_number']==observed['sequence_number']
  assert observed['text']==text and not observed['truncated']
  assert written['written_bytes']==len(text.encode())
  partial=clipboard.read(17)
  assert len(partial['text'].encode())<=17 and partial['truncated']
  try:clipboard.write('must not replace',initial['sequence_number'])
  except module.ClipboardChanged:pass
  else:raise AssertionError('Stale write accepted')
  assert clipboard.read(8192)['text']==text
  cleared=clipboard.write(None,observed['sequence_number'])
  assert clipboard.read(8192)['text'] is None
  print(json.dumps({'status':'PASS','private_station':True,'roundtrip':True,'stale_write_rejected':True,'clear':True,'utf8_budget':True}))
finally:
 assert user.SetThreadDesktop(old_desktop);assert user.SetProcessWindowStation(old_station)
 assert user.CloseDesktop(desktop);assert user.CloseWindowStation(station)
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-I", str(script), module.__file__],
        capture_output=True,
        timeout=15,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b'"status": "PASS"' in result.stdout
    assert ctypes.WinDLL("shell32").IsUserAnAdmin()
