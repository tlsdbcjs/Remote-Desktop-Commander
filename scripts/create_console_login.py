"""Issue a short-lived Console login code without exposing the host owner secret."""

import argparse
import os
import re
import secrets
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from racp_sdk.security import SecretStore, tls_context

if __package__:
    from .create_connection_file import ROOT, find_lab
else:
    from create_connection_file import ROOT, find_lab


def private_file(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        if os.name == "nt":
            import win32api
            import win32con
            import win32security

            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
            )
            try:
                sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            finally:
                token.Close()
            acl = win32security.ACL()
            acl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, sid)
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
            os.chmod(path, 0o600)
        stream.write(text)


def create_login(lab: Path) -> tuple[Path, str]:
    owner = SecretStore(lab / "owner.bin").load()
    with httpx.Client(
        base_url=owner["gateway"],
        verify=tls_context(lab / "ca.pem"),
        headers={"Authorization": "Bearer " + owner["token"]},
        trust_env=False,
        follow_redirects=False,
        timeout=15,
    ) as client:
        client.get("/console/").raise_for_status()
        result = client.post("/api/v1/console/setup-token")
        result.raise_for_status()
        data = result.json()
    secret = data["setup_secret"]
    if not isinstance(secret, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", secret):
        raise ValueError("Invalid Console login response")
    duration = data["expires_in_seconds"]
    if type(duration) is not int or not 0 < duration <= 300:
        raise ValueError("Invalid Console login lifetime")
    now = datetime.now().astimezone()
    expires = now + timedelta(seconds=duration)
    directory = lab / "console-login"
    directory.mkdir(exist_ok=True)
    file = directory / f"Console-login-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.txt"
    url = owner["gateway"].rstrip("/") + "/console/"
    private_file(
        file,
        f"로그인 코드: {secret}\n\n관리 Console: {url}\n"
        f"유효 시간: {expires:%Y-%m-%d %H:%M:%S %Z}\n"
        "위 코드를 Console의 [일회성 setup secret] 칸에 붙여넣으세요.\n"
        "5분 유효 / 1회 사용. PC 등록용 .racp 파일과 다릅니다.\n",
    )
    return file, url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-dir", type=Path)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    try:
        file, url = create_login(args.lab_dir or find_lab(ROOT))
    except (httpx.HTTPError, OSError, ValueError, KeyError):
        print(
            "관리 Console의 실행 상태와 호스트 설정을 확인하세요. "
            "로그인 코드를 발급하지 못했습니다."
        )
        return 1
    print(f"Console 주소: {url}")
    print(f"로그인 코드 파일 생성 완료: {file}")
    print("파일의 코드를 로그인 화면에 붙여넣으세요. 유효 시간은 5분입니다.")
    if os.name == "nt" and not args.no_open:
        subprocess.Popen(
            ["explorer.exe", "/select," + str(file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
