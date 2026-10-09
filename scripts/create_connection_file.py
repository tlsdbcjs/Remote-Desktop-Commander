"""One-command host enrollment-file creation; no tokens printed or duplicated."""

import argparse
import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

# Support both direct execution from the CMD launcher and module imports in tests.
if __package__:
    from .issue_desktop_lab_ticket import request_connection
else:
    from issue_desktop_lab_ticket import request_connection

ROOT = Path(__file__).resolve().parents[1]


def find_lab(root: Path) -> Path:
    state = root / ".racp"
    candidates = [state, *sorted(state.iterdir())] if state.is_dir() else []
    labs = [p for p in candidates if (p / "owner.bin").is_file() and (p / "ca.pem").is_file()]
    if len(labs) != 1:
        raise ValueError("A single configured host Gateway is required")
    return labs[0]


def create_file(lab: Path, output_root: Path, name: str) -> tuple[Path, datetime]:
    connection = request_connection(lab, name)
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    file = output_root / f"RACP-connection-{stamp}-{secrets.token_hex(3)}.racp"
    with file.open("x", encoding="utf-8") as stream:
        stream.write(connection.model_dump_json(indent=2) + "\n")
    return file, connection.expires_at.astimezone()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "connection-files")
    parser.add_argument("--name", default="Windows PC")
    parser.add_argument("--no-open", action="store_true", help="Do not open File Explorer")
    args = parser.parse_args(argv)
    try:
        lab = args.lab_dir or find_lab(ROOT)
        file, expires = create_file(lab, args.output_dir.resolve(), args.name)
    except httpx.HTTPStatusError:
        print("연결 파일을 발급하지 못했습니다. 호스트 Gateway의 인증·준비 상태를 확인하세요.")
        return 1
    except httpx.TransportError:
        print("호스트 Gateway에 연결할 수 없습니다. Gateway를 시작한 뒤 다시 실행하세요.")
        return 1
    except (OSError, ValueError, KeyError):
        print("호스트 설정 또는 저장 폴더를 확인하세요. Gateway 초기 설정이 하나 있어야 합니다.")
        return 1
    print(f"연결 파일 생성 완료: {file}")
    print(f"유효 시간: {expires:%Y-%m-%d %H:%M:%S %Z} (1회 사용)")
    print("이 파일을 원격 PC로 전달한 뒤 Client에서 [연결 파일 선택]으로 불러오세요.")
    if os.name == "nt" and not args.no_open:
        try:
            subprocess.Popen(
                ["explorer.exe", "/select," + str(file)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            print("탐색기를 열지 못했습니다. 위 경로에서 파일을 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
