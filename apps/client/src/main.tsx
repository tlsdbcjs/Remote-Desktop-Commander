import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import messages from "../messages.json";

function failureMessage(error: unknown): string {
  if (error instanceof Error) {
    // Electron adds an IPC prefix. Only retain an exact known diagnostic suffix.
    for (const message of Object.values(messages)) {
      if (
        error.message === message ||
        error.message.endsWith("Error: " + message)
      )
        return message;
    }
  }
  return messages.REQUEST_FAILED;
}
type Profile = "read_only" | "standard" | "trusted_personal";
type Workspace = { id: string; path: string };
type Info = {
  configured: boolean;
  execution_identity: string;
  gateway?: string;
  device_id?: string;
  workspace?: string;
  profile?: Profile;
  allowed_workspaces?: Workspace[];
  desktop_enabled?: boolean;
};
type Status = {
  state: "RUNNING" | "STOPPING" | "STOPPED";
  connected: boolean;
  execution_identity?: string;
  pid?: number;
  connection_epoch?: number;
  connection_phase?: string;
  active_operations?: number;
  desktop?: { enabled: boolean; healthy: boolean; unavailable_reason?: string | null;
    sessions: { session_id: number; available: boolean; input_ready: boolean }[] };
  operations?: { operation_id: string; operation: string; state: string }[];
};
type Activity = {
  event: string;
  timestamp?: string;
  operation?: string;
  state?: string;
  operation_id?: string;
};
type Overview = {
  status: Status | null;
  activity: { events: Activity[]; tail_limited?: boolean };
  updatedAt: string | null;
  error: string;
  busy: boolean;
  tray_available: boolean;
};
type Enrollment = {
  gateway: string;
  workspace: string;
  ca_file: string | null;
  profile: Profile;
  token: string;
  allowed_workspaces: Workspace[];
};
type EditableSettings = Omit<Enrollment, "token"> & {
  device_id: string;
  revision: string;
  desktop_enabled: boolean;
};
type ConnectionPreview = {
  gateway: string;
  expires_at: string;
  ca_sha256: string | null;
};
type API = {
  overview(): Promise<Overview>;
  refresh(): Promise<Overview>;
  exit(): Promise<void>;
  loginSettings(): Promise<LoginSettings>;
  setLogin(enabled: boolean): Promise<LoginSettings>;
  info(): Promise<Info>;
  settings(): Promise<EditableSettings>;
  updateSettings(
    value: Omit<EditableSettings, "device_id">,
  ): Promise<Info>;
  status(): Promise<Status>;
  start(): Promise<Status>;
  stop(): Promise<Status>;
  folder(): Promise<string>;
  ca(): Promise<string>;
  connection(): Promise<ConnectionPreview | null>;
  enrollConnection(value: {
    workspace: string;
    profile: Profile;
    allowed_workspaces: Workspace[];
  }): Promise<Info>;
  enroll(value: Enrollment): Promise<Info>;
};
type LoginSettings = {
  available: boolean;
  registered: boolean;
  enabled: boolean;
};
declare global {
  interface Window {
    racpClient: API;
  }
}
function Client() {
  const [editing, setEditing] = useState(false);
  const [info, setInfo] = useState<Info | null>(null),
    [status, setStatus] = useState<Status | null>(null);
  const [gateway, setGateway] = useState(""),
    [folder, setFolder] = useState("");
  const [ca, setCA] = useState(""),
    [token, setToken] = useState(""),
    [profile, setProfile] = useState<Profile>("read_only");
  const [folders, setFolders] = useState<Workspace[]>([]);
  const [login, setLogin] = useState<LoginSettings | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [tab, setTab] = useState<"activity" | "settings">("activity");
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [infoFailed, setInfoFailed] = useState(false);
  const [manualSetup, setManualSetup] = useState(false);
  const [connection, setConnection] = useState<ConnectionPreview | null>(null);
  async function refresh() {
    try {
      setInfo(await window.racpClient.info());
      setInfoFailed(false);
    } catch (error) {
      setInfoFailed(true);
      throw error;
    }
    const snapshot = await window.racpClient.refresh();
    setOverview(snapshot);
    setStatus(snapshot.status);
    setLogin(await window.racpClient.loginSettings());
  }
  useEffect(() => {
    refresh().catch((error) => setMessage(failureMessage(error)));
    const timer = setInterval(
      () =>
        window.racpClient
          .overview()
          .then((value) => {
            setOverview(value);
            setStatus(value.status);
          })
          .catch(() => {
            setStatus(null);
            setMessage(
              "Agent 상태를 확인할 수 없습니다. 상태 확인을 다시 실행해 주세요.",
            );
          }),
      5000,
    );
    return () => clearInterval(timer);
  }, []);
  async function perform(action: () => Promise<unknown>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await refresh();
    } catch (error) {
      setMessage(failureMessage(error));
    } finally {
      setBusy(false);
      setToken("");
    }
  }
  return (
    <main>
      <header>
        <span className="eyebrow">REMOTE PC CLIENT</span>
        <h1>RACP Client</h1>
        <p>이 PC를 연결하고 AI가 사용할 폴더와 실행 권한을 선택합니다.</p>
      </header>
      <section className="state">
        <span className={status?.connected ? "dot online" : "dot"} />
        <strong>
          {!status
            ? "상태 확인 중"
            : status.connected
              ? "Gateway 연결됨"
              : status.state === "RUNNING"
                ? "연결 중"
                : status.state === "STOPPING"
                  ? "종료 중"
                  : "Agent 종료됨"}
        </strong>
        <small>
          {status?.execution_identity ?? info?.execution_identity ?? ""}
        </small>
      </section>
      {!editing && !infoFailed && (message || overview?.error) && (
        <p role="alert">{message || overview?.error}</p>
      )}
      {editing || infoFailed ? (
        <SettingsEditor
          initialError={message || overview?.error}
          onCancel={infoFailed ? undefined : () => setEditing(false)}
          onSaved={async () => {
            setEditing(false);
            setMessage("");
            await refresh();
          }}
        />
      ) : info === null ? (
        <section>
          <h2>PC 등록 상태 확인 중</h2>
          <p>이 PC의 저장된 연결 설정을 확인하고 있습니다.</p>
        </section>
      ) : info.configured ? (
        <>
          <div className="metrics">
            <article>
              <small>진행 중 작업</small>
              <strong>{status ? (status.active_operations ?? 0) : "—"}</strong>
            </article>
            <article>
              <small>Agent PID</small>
              <strong>{status?.pid ?? "—"}</strong>
            </article>
            <article>
              <small>최근 상태 확인</small>
              <strong className="timestamp">
                {overview?.updatedAt
                  ? new Date(overview.updatedAt).toLocaleTimeString()
                  : "—"}
              </strong>
            </article>
          </div>
          <div className="actions dashboard-actions">
            <button
              disabled={busy || overview?.busy || status?.state !== "STOPPED"}
              onClick={() => perform(window.racpClient.start)}
            >
              Agent 시작
            </button>
            <button
              className="secondary"
              disabled={busy || overview?.busy || status?.state !== "RUNNING"}
              onClick={() => perform(window.racpClient.stop)}
            >
              Agent 종료
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => perform(refresh)}
            >
              상태 확인
            </button>
            <button
              className="danger"
              disabled={busy || overview?.busy}
              onClick={() => perform(window.racpClient.exit)}
            >
              완전 종료
            </button>
          </div>
          <p className="hint">
            {overview?.tray_available
              ? "창을 닫으면 트레이에 남습니다. 트레이 아이콘을 클릭하면 다시 열 수 있습니다."
              : "트레이를 사용할 수 없습니다. 창을 열어 두고 완전 종료 버튼을 사용하세요."}
          </p>
          <nav className="tabs" aria-label="클라이언트 화면">
            <button
              aria-pressed={tab === "activity"}
              onClick={() => setTab("activity")}
            >
              현황 · 최근 활동
            </button>
            <button
              aria-pressed={tab === "settings"}
              onClick={() => setTab("settings")}
            >
              PC 설정
            </button>
          </nav>
          {tab === "activity" ? (
            <>
              <section>
                <h2>진행 중 작업</h2>
                {status?.operations?.length ? (
                  <ul className="operation-list">
                    {status.operations.map((item) => (
                      <li key={item.operation_id}>
                        <strong>{item.operation}</strong>
                        <span className="badge">{stateLabel(item.state)}</span>
                        <small>{item.operation_id}</small>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>
                    {status
                      ? "현재 진행 중인 작업이 없습니다."
                      : "Agent 상태를 확인할 수 없습니다."}
                  </p>
                )}
              </section>
              <section>
                <div className="section-heading">
                  <h2>최근 활동</h2>
                  <small>최근 40개 · 3초마다 확인</small>
                </div>
                <div
                  className="activity"
                  role="log"
                  aria-label="Agent 최근 활동"
                >
                  {overview?.activity.events.length ? (
                    [...overview.activity.events].reverse().map((item, i) => (
                      <div className="activity-row" key={i}>
                        <time>
                          {item.timestamp
                            ? new Date(item.timestamp).toLocaleTimeString()
                            : "—"}
                        </time>
                        <div>
                          <strong>{eventLabel(item.event)}</strong>
                          {item.operation && <span>{item.operation}</span>}
                        </div>
                        {item.state && (
                          <span
                            className={`badge ${item.state === "SUCCEEDED" ? "success" : ""}`}
                          >
                            {stateLabel(item.state)}
                          </span>
                        )}
                      </div>
                    ))
                  ) : (
                    <p>
                      활동 기록이 없습니다. 원격 작업을 실행하면 여기에
                      표시됩니다.
                    </p>
                  )}
                </div>
                <p className="hint">
                  작업 이름과 결과를 표시합니다. 명령 인자·파일 내용·인증 정보는
                  표시하지 않습니다.
                  {overview?.activity.tail_limited
                    ? " 이전 기록은 표시 범위를 벗어났습니다."
                    : ""}
                </p>
              </section>
            </>
          ) : (
            <section>
              <h2>등록한 PC</h2>
              <dl>
                <dt>Gateway</dt>
                <dd>{info.gateway}</dd>
                <dt>Device</dt>
                <dd>{info.device_id}</dd>
                <dt>허용 폴더</dt>
                <dd>{info.workspace}</dd>
                <dt>실행 권한</dt>
                <dd>{info.profile}</dd>
                <dt>Windows 화면</dt>
                <dd>{info.desktop_enabled
                  ? status?.desktop?.healthy && !status.desktop.unavailable_reason
                    ? status.desktop.sessions.some((item) => item.available && item.input_ready)
                      ? "화면 제어 준비됨" : "화면 조회 가능 · 입력 준비 확인 필요"
                    : "허용됨 · 로그인 화면 확인 필요"
                  : "화면 제어 꺼짐"}</dd>
              </dl>
              {info.allowed_workspaces?.map((item) => (
                <p key={item.id}>
                  {item.id}: {item.path}
                </p>
              ))}
              <button disabled={busy} onClick={() => setEditing(true)}>
                등록 정보 편집
              </button>
              {login?.available && (
                <label className="login-option">
                  <input
                    type="checkbox"
                    checked={login.registered}
                    disabled={busy}
                    onChange={(event) =>
                      perform(() =>
                        window.racpClient.setLogin(event.target.checked),
                      )
                    }
                  />
                  Windows 로그인 후 자동으로 Agent 연결
                </label>
              )}
              {login?.registered && !login.enabled && (
                <p role="alert">
                  Windows 시작 앱에서 자동 연결이 꺼져 있습니다. 시작 앱 설정을
                  확인해 주세요.
                </p>
              )}
              {login?.enabled && (
                <p className="hint">
                  Agent 종료 후에도 다음 Windows 로그인에서는 다시 연결합니다.
                  자동 연결을 원하지 않으면 이 설정을 꺼 주세요.
                </p>
              )}
            </section>
          )}
        </>
      ) : (
        <section>
          <h2>새 PC 연결</h2>
          <p className="hint">
            최초 한 번만 등록하면 다음 실행부터 저장된 설정으로 Agent를
            시작합니다. 등록 토큰은 한 번만 사용할 수 있으며 10분 뒤 만료됩니다.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              perform(async () => {
                if (!manualSetup) {
                  try {
                    return await window.racpClient.enrollConnection({
                      workspace: folder,
                      profile,
                      allowed_workspaces: folders,
                    });
                  } finally {
                    setConnection(null);
                  }
                }
                return window.racpClient.enroll({
                  gateway,
                  workspace: folder,
                  ca_file: ca || null,
                  profile,
                  token,
                  allowed_workspaces: folders,
                });
              });
            }}
          >
            <div className="actions">
              <button
                type="button"
                className={!manualSetup ? "" : "secondary"}
                disabled={busy}
                onClick={() => setManualSetup(false)}
              >
                연결 파일로 등록
              </button>
              <button
                type="button"
                className={manualSetup ? "" : "secondary"}
                disabled={busy}
                onClick={() => {
                  setManualSetup(true);
                  setConnection(null);
                }}
              >
                직접 입력
              </button>
            </div>
            {!manualSetup && (
              <>
                <p>
                  Gateway에서 발급한 연결 파일을 선택하세요. 최초 등록 후에는 이
                  설정을 다시 입력하지 않습니다.
                </p>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() =>
                    perform(async () => {
                      setConnection(null);
                      setConnection(await window.racpClient.connection());
                    })
                  }
                >
                  연결 파일 선택
                </button>
                {connection && (
                  <dl>
                    <dt>연결할 Gateway</dt>
                    <dd>{connection.gateway}</dd>
                    <dt>유효 시간</dt>
                    <dd>{new Date(connection.expires_at).toLocaleString()}</dd>
                    <dt>인증서</dt>
                    <dd>
                      {connection.ca_sha256
                        ? `파일에 포함됨 · SHA-256 ${connection.ca_sha256}`
                        : "Windows의 신뢰 인증서 사용"}
                    </dd>
                  </dl>
                )}
                <p className="hint">
                  선택한 Gateway에 이 PC를 등록합니다. 신뢰하는 Gateway에서 받은
                  파일만 사용하세요. 등록 후 연결 파일은 삭제해도 됩니다.
                </p>
              </>
            )}
            {manualSetup && (
              <label>
                Gateway 주소
                <input
                  value={gateway}
                  onChange={(event) => setGateway(event.target.value)}
                  required
                  maxLength={2048}
                />
              </label>
            )}
            <label>
              허용 폴더
              <div className="pick">
                <input value={folder} readOnly required />
                <button
                  type="button"
                  className="secondary"
                  onClick={() => window.racpClient.folder().then(setFolder)}
                >
                  폴더 선택
                </button>
              </div>
            </label>
            {folders.map((item, index) => (
              <div className="pick" key={index}>
                <input
                  aria-label={`추가 폴더 ${index + 1} ID`}
                  value={item.id}
                  pattern="[A-Za-z][A-Za-z0-9_-]{0,31}"
                  required
                  onChange={(event) =>
                    setFolders(
                      folders.map((value, i) =>
                        i === index
                          ? { ...value, id: event.target.value }
                          : value,
                      ),
                    )
                  }
                />
                <input
                  aria-label={`추가 폴더 ${index + 1} 경로`}
                  value={item.path}
                  readOnly
                />
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    setFolders(folders.filter((_, i) => i !== index))
                  }
                >
                  제거
                </button>
              </div>
            ))}
            <button
              type="button"
              className="secondary"
              disabled={folders.length >= 15}
              onClick={() =>
                window.racpClient.folder().then((value) => {
                  if (value)
                    setFolders([
                      ...folders,
                      { id: `folder${folders.length + 1}`, path: value },
                    ]);
                })
              }
            >
              허용 폴더 추가
            </button>
            {manualSetup && (
              <label>
                추가 CA 인증서 · 필요할 때 선택
                <div className="pick">
                  <input value={ca} readOnly />
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => window.racpClient.ca().then(setCA)}
                  >
                    인증서 선택
                  </button>
                </div>
              </label>
            )}
            <label>
              실행 권한
              <select
                value={profile}
                onChange={(event) => setProfile(event.target.value as Profile)}
              >
                <option value="read_only">읽기 전용</option>
                <option value="standard">표준 · 실행은 owner 승인</option>
                <option value="trusted_personal">
                  개인용 · 파일 수정과 명령 실행 허용
                </option>
              </select>
            </label>
            {manualSetup && (
              <label>
                일회용 등록 토큰
                <input
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  required
                  minLength={20}
                  maxLength={128}
                />
              </label>
            )}
            <p className="hint">
              명령은 현재 OS 계정의 권한으로 실행됩니다. 폴더 선택은 shell의 OS
              권한을 격리하지 않습니다.
            </p>
            <button disabled={busy || !folder || (!manualSetup && !connection)}>
              {busy ? "연결 요청 중…" : "PC 등록"}
            </button>
          </form>
        </section>
      )}
      <footer>AI/MCP 클라이언트 → Gateway 서버 → 이 PC의 Agent</footer>
    </main>
  );
}
function eventLabel(value: string): string {
  return (
    (
      {
        agent_connected: "Gateway 연결",
        agent_disconnected: "연결 끊김 · 재연결 대기",
        agent_handshake_rejected: "연결 인증 거부",
        agent_execution_started: "작업 시작",
        agent_result: "작업 완료",
      } as Record<string, string>
    )[value] ?? value
  );
}
function stateLabel(value: string): string {
  return (
    (
      {
        RUNNING: "실행 중",
        SUCCEEDED: "완료",
        FAILED: "실패",
        CANCELLED: "취소됨",
        TIMED_OUT: "시간 초과",
        UNKNOWN: "결과 확인 필요",
        ACCEPTED: "접수됨",
        DISPATCHED: "전달됨",
        CANCEL_REQUESTED: "취소 중",
        RECONCILING: "상태 복구 중",
      } as Record<string, string>
    )[value] ?? value
  );
}
function SettingsEditor({
  onCancel,
  onSaved,
  initialError,
}: {
  onCancel?(): void;
  onSaved(): Promise<void>;
  initialError?: string;
}) {
  const [value, setValue] = useState<EditableSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    window.racpClient
      .settings()
      .then(setValue)
      .catch((error) => setError(failureMessage(error)));
  }, []);
  async function choose(kind: "folder" | "ca", index?: number) {
    setBusy(true);
    setError("");
    try {
      const selected = await window.racpClient[kind]();
      if (!selected) return;
      setValue((previous) => {
        if (!previous) return previous;
        if (kind === "ca") return { ...previous, ca_file: selected };
        if (index === undefined) return { ...previous, workspace: selected };
        return {
          ...previous,
          allowed_workspaces: previous.allowed_workspaces.map(
            (item, position) =>
              position === index ? { ...item, path: selected } : item,
          ),
        };
      });
    } catch (error) {
      setError(failureMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h2>등록 정보 편집</h2>
      <p>
        Agent를 중지한 뒤 저장하세요. 기존 PC 등록과 작업 기록은 보존되며, 변경
        내용은 다음 Agent 시작부터 적용됩니다.
      </p>
      {(error || initialError) && <p role="alert">{error || initialError}</p>}
      {!value ? (
        <p>
          저장된 설정을 확인하고 있습니다. 인증 정보를 읽지 못하면 수정할 수
          없습니다.
        </p>
      ) : (
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            try {
              await window.racpClient.updateSettings({
                revision: value.revision,
                gateway: value.gateway,
                workspace: value.workspace,
                profile: value.profile,
                ca_file: value.ca_file,
                allowed_workspaces: value.allowed_workspaces,
                desktop_enabled: value.desktop_enabled,
              });
              await onSaved();
            } catch (error) {
              setError(failureMessage(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Gateway 주소
            <input
              required
              disabled={busy}
              maxLength={2048}
              aria-label="Gateway 주소"
              value={value.gateway}
              onChange={(event) =>
                setValue({ ...value, gateway: event.target.value })
              }
            />
          </label>
          <dl>
            <dt>Device</dt>
            <dd>{value.device_id}</dd>
          </dl>
          <p className="hint">
            기존 Device 인증 정보는 유지합니다. 같은 Gateway의 주소나 포트가
            바뀐 경우 수정할 수 있으며, 다른 Gateway 서버로 이동하려면 새 등록이
            필요합니다.
          </p>
          <label>
            허용 폴더
            <div className="pick">
              <input
                required
                disabled={busy}
                aria-label="허용 폴더"
                value={value.workspace}
                onChange={(event) =>
                  setValue({ ...value, workspace: event.target.value })
                }
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => choose("folder")}
              >
                폴더 선택
              </button>
            </div>
          </label>
          {value.allowed_workspaces.map((item, index) => (
            <div key={index}>
              <label>
                추가 폴더 ID
                <input
                  required
                  disabled={busy}
                  value={item.id}
                  onChange={(event) =>
                    setValue({
                      ...value,
                      allowed_workspaces: value.allowed_workspaces.map(
                        (previous, position) =>
                          position === index
                            ? { ...previous, id: event.target.value }
                            : previous,
                      ),
                    })
                  }
                />
              </label>
              <label>
                추가 허용 폴더
                <div className="pick">
                  <input
                    required
                    disabled={busy}
                    value={item.path}
                    onChange={(event) =>
                      setValue({
                        ...value,
                        allowed_workspaces: value.allowed_workspaces.map(
                          (previous, position) =>
                            position === index
                              ? { ...previous, path: event.target.value }
                              : previous,
                        ),
                      })
                    }
                  />
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => choose("folder", index)}
                  >
                    폴더 선택
                  </button>
                </div>
              </label>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setValue({
                    ...value,
                    allowed_workspaces: value.allowed_workspaces.filter(
                      (_, position) => position !== index,
                    ),
                  })
                }
              >
                폴더 제거
              </button>
            </div>
          ))}
          <button
            type="button"
            disabled={busy || value.allowed_workspaces.length >= 15}
            onClick={() =>
              setValue({
                ...value,
                allowed_workspaces: [
                  ...value.allowed_workspaces,
                  { id: "", path: "" },
                ],
              })
            }
          >
            허용 폴더 추가
          </button>
          <label>
            추가 CA 인증서
            <div className="pick">
              <input
                disabled={busy}
                aria-label="추가 CA 인증서"
                value={value.ca_file ?? ""}
                onChange={(event) =>
                  setValue({ ...value, ca_file: event.target.value || null })
                }
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => choose("ca")}
              >
                인증서 선택
              </button>
            </div>
          </label>
          <p className="hint">
            공개 인증서를 사용하는 서버는 비워 둘 수 있습니다. 사설 인증서
            서버는 해당 CA 파일이 필요합니다.
          </p>
          <label>
            실행 권한
            <select
              disabled={busy}
              aria-label="실행 권한"
              value={value.profile}
              onChange={(event) =>
                setValue({ ...value, profile: event.target.value as Profile })
              }
            >
              <option value="read_only">읽기 전용</option>
              <option value="standard">표준 · 실행은 owner 승인</option>
              <option value="trusted_personal">
                개인용 · 허용된 작업 실행
              </option>
            </select>
          </label>
          <p className="hint">
            명령은 현재 OS 계정의 권한으로 실행됩니다. 폴더 선택은 shell의 OS
            권한을 격리하지 않습니다.
          </p>
          <label className="desktop-setting">
            <input type="checkbox" checked={value.desktop_enabled ?? false} disabled={busy}
              onChange={(event) => setValue({ ...value, desktop_enabled: event.target.checked })} />
            이 PC의 Windows 화면 캡처·마우스·키보드 조작 허용
          </label>
          <p className="hint">다음 Agent 시작부터 현재 Windows 로그인 화면에 적용됩니다. 입력 작업에는 선택한 실행 권한과 승인 정책이 적용됩니다.</p>
          <div className="actions">
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await window.racpClient.stop();
                } catch (error) {
                  setError(failureMessage(error));
                } finally {
                  setBusy(false);
                }
              }}
            >
              Agent 중지
            </button>
            <button disabled={busy}>설정 저장</button>
          </div>
        </form>
      )}
      {onCancel && (
        <button type="button" disabled={busy} onClick={onCancel}>
          취소
        </button>
      )}
    </section>
  );
}
createRoot(document.getElementById("root")!).render(<Client />);
