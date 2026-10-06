# RACP 2-PC 실증 랩 구성 및 검증 가이드 (Two-PC Lab Guide)

> **문서 ID**: `DOC-GDE-TWOPC`  
> **상태**: Active · **기준 버전**: v0.1.8  
> **최종 개정일**: 2026-10-07 · **분류**: Integration & Test Guide

---

## 개요 (Overview)

본 가이드는 실제 2대의 물리적 Windows PC 환경(Gateway 호스트 PC와 원격 Agent PC)에서 **RACP 시스템**의 네트워크 관통, 원격 파일 송수신, 프로세스 제어, PTY 터미널 스트리밍, 대용량 아티팩트 전송 및 멱등성 검증을 수행하는 테스트 랩 환경 구축 절차를 안내합니다.

---

## 목차 (Table of Contents)

- [1. 랩 토폴로지 및 테스트 환경 구성](#1-랩-토폴로지-및-테스트-환경-구성)
- [2. Gateway 호스트 PC (140) 설정 및 방화벽](#2-gateway-호스트-pc-140-설정-및-방화벽)
- [3. 원격 Agent PC (141) 포터블 클라이언트 구동](#3-원격-agent-pc-141-포터블-클라이언트-구동)
- [4. 실증 검증 시나리오 및 확인 항목](#4-실증-검증-시나리오-및-확인-항목)
- [5. 시험 종료 및 방화벽 규칙 롤백](#5-시험-종료-및-방화벽-규칙-롤백)
- [6. 관련 문서](#6-관련-문서)

---

## 1. 랩 토폴로지 및 테스트 환경 구성

```mermaid
flowchart LR
    subgraph HostPC ["호스트 PC (192.168.29.140)"]
        GW["RACP Gateway (:8765)<br/>HTTPS/WSS Control Plane"]
        FW["Windows 방화벽<br/>TCP 8765 허용 규칙"]
        FW --> GW
    end

    subgraph RemotePC ["원격 대상 PC (192.168.29.141)"]
        Agent["RACP Agent / Client<br/>(포터블 번들 런타임)"]
        Work["작업 공간<br/>(Allowed Workspaces)"]
        Agent <--> Work
    end

    Agent -->|"Outbound HTTPS/WSS<br/>(TLS + 1회용 Token)"| FW

    classDef host fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    classDef remote fill:#f1f8e9,stroke:#558b2f,stroke-width:2px;
    class HostPC host;
    class RemotePC remote;
```

- **Gateway 호스트**: `192.168.29.140` (포트 TCP `8765` 리스닝)
- **원격 Agent PC**: `192.168.29.141` (아웃바운드 WSS 연결 시작)
- **보안 원칙**: RDP, WMI, OS 계정 비밀번호는 사용하지 않으며, 오직 RACP 프로토콜만을 통해 통신합니다.

---

## 2. Gateway 호스트 PC (140) 설정 및 방화벽

Gateway PC에서 테스트 전용 랩 환경을 초기화하고 실행합니다:

```powershell
# 1. 테스트 포트 방화벽 허용 (원격 141 PC로부터의 8765 포트 인바운드만 제한적 허용)
.\.racp\two-pc-lab\Allow-Test-Port.ps1

# 2. Gateway 서버 실행
uv run racp-gateway `
    --host 0.0.0.0 `
    --port 8765 `
    --public-origin https://192.168.29.140:8765 `
    --tls-cert .racp\two-pc-lab\gateway.pem `
    --tls-key .racp\two-pc-lab\gateway.key
```

Console 접속 후 신규 PC 연결 토큰 또는 `.racp` 연결 파일을 발급합니다.

---

## 3. 원격 Agent PC (141) 포터블 클라이언트 구동

원격 PC에는 Python이나 Node.js를 설치할 필요가 없습니다.

1. 배포 패키지(`RACP Client-0.1.8-win.zip` 또는 포터블 번들)를 원격 PC에 복사하고 압축을 해제합니다.
2. `RACP Client.exe`를 실행합니다.
3. Gateway에서 발급한 `.racp` 파일을 선택하고 인가할 작업 폴더를 지정한 뒤 **[PC 등록]** → **[Agent 시작]**을 클릭합니다.
4. 클라이언트의 트레이 아이콘이 녹색(Online)으로 전환되고 Gateway Console에 해당 기기가 `ONLINE`으로 표시되는지 확인합니다.

---

## 4. 실증 검증 시나리오 및 확인 항목

Gateway 및 AI 클라이언트에서 다음 항목을 검증합니다:

1. **파일 시스템 원격 조작**: 원격 141 PC의 한글 경로 파일 읽기/쓰기 및 SHA-256 해시 검증.
2. **원격 셸 명령 실행**: `whoami`, `dir`, 환경 변수 조회 및 nonzero 종료 코드 수집.
3. **가상 터미널(ConPTY) 스트리밍**: 대화형 PowerShell 세션 개방, 입력 송신 및 실시간 출력 수신.
4. **동일 Key 멱등성 검증**: 동일한 `idempotency_key`로 중복 요청 전송 시 부작용 카운터가 1회로 유지되는지 확인.
5. **대용량 아티팩트 전송**: 100 MiB 이상 파일의 전송 중단 및 재개(Resume) 검증.

> [!NOTE]
> 상세 물리 실증 테스트 통과 결과(총 322개 이상 테스트 통과 및 물리 141 통신 증거)는 [`docs/quality/implementation-status.md`](../quality/implementation-status.md)에서 확인할 수 있습니다.

---

## 5. 시험 종료 및 방화벽 규칙 롤백

테스트가 완료되면 보안을 위해 열어두었던 방화벽 규칙을 즉시 제거합니다:

```powershell
.\.racp\two-pc-lab\Remove-Test-Port.ps1
```

---

## 6. 관련 문서

- [데스크톱 클라이언트 가이드](desktop-client-guide.md)
- [PC 등록 및 온보딩 가이드](pc-connect-guide.md)
- [구현 작업 및 검증 현황](../quality/implementation-status.md)
- [Windows 릴리스 인수 게이트](../quality/windows-release-gates.md)
