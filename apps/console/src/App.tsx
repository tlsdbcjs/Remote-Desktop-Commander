import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import type { components } from "./generated";
import { useListPage } from "./paging";
import { Artifacts, Audit } from "./Resources";
import { TerminalPush } from "./TerminalPush";
import { EnrollPC } from "./EnrollPC";
import {
  api,
  ApiError,
  approvalSchema,
  deviceSchema,
  eventSchema,
  jobSchema,
  listOf,
  objectSchema,
  safeText,
  sessionSchema,
  type Device,
  type Session,
} from "./api";

const views = [
  "Overview",
  "Devices",
  "Jobs",
  "Approvals",
  "Sessions",
  "Artifacts",
  "Audit",
  "Doctor",
  "Settings",
] as const;
type View = (typeof views)[number];
const labels: Record<View, string> = {
  Overview: "대시보드",
  Devices: "장비",
  Jobs: "작업",
  Approvals: "승인",
  Sessions: "세션",
  Artifacts: "파일",
  Audit: "감사 기록",
  Doctor: "진단",
  Settings: "설정",
};
const terminalStates = new Set([
  "COMPLETED",
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  "TIMED_OUT",
  "UNKNOWN",
]);

function Json({ value }: { value: unknown }) {
  return <pre>{safeText(JSON.stringify(value, null, 2) ?? "")}</pre>;
}
function DesktopScope({ value }: { value: unknown }) {
  const sessions = z
    .array(
      z.object({
        session_id: z.number().int(),
        user_name: z.string().optional(),
        user_sid: z.string().optional(),
        available: z.boolean(),
        reason: z.string().optional(),
      }),
    )
    .safeParse(value);
  return (
    <section aria-label="Desktop 실행 범위">
      <h3>Desktop 실행 범위</h3>
      <p>GUI 작업은 지정된 Windows 세션의 로그온 사용자 권한으로 수행됩니다.</p>
      <ul>
        {sessions.success ? (
          sessions.data.map((session) => (
            <li key={session.session_id}>
              세션 {session.session_id} ·{" "}
              {safeText(session.user_name ?? session.user_sid ?? "계정 미관측")}{" "}
              · {session.available ? "입력 가능" : "현재 입력 불가"}
              {!session.available && session.reason && (
                <p>{safeText(session.reason)}</p>
              )}
            </li>
          ))
        ) : (
          <li>세션 정보 미관측</li>
        )}
      </ul>
    </section>
  );
}
function Badge({ state }: { state: string }) {
  return <span className={`badge ${state.toLowerCase()}`}>{state}</span>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function App() {
  const cache = useQueryClient();
  const [session, setSession] = useState<Session | null>(null);
  const [loadingSession, setLoadingSession] = useState(true);
  const [secret, setSecret] = useState("");
  const [view, setView] = useState<View>("Overview");
  const [selected, setSelected] = useState("");
  const [workspaceChoice, setWorkspaceChoice] = useState({
    deviceId: "",
    id: "default",
  });
  const [connection, setConnection] = useState("connecting");
  const [networkEpoch, setNetworkEpoch] = useState(0);
  const [notice, setNotice] = useState("");
  const [operationId, setOperationId] = useState("");
  const [argv, setArgv] = useState('["python", "--version"]');
  const [profile, setProfile] =
    useState<components["schemas"]["OperationInput"]["execution_profile_id"]>(
      "read_only",
    );
  const [terminalId, setTerminalId] = useState("");
  const [cursor, setCursor] = useState("0");
  const [terminalText, setTerminalText] = useState("");

  function signedOut(message: string) {
    setSession(null);
    setSecret("");
    cache.clear();
    setNotice(message);
    setConnection("disconnected");
  }
  function failure(error: unknown) {
    if (error instanceof ApiError && error.status === 401)
      signedOut("세션이 만료되었습니다. 새 일회성 secret으로 로그인하세요.");
    else
      setNotice(
        error instanceof Error ? error.message : "요청을 완료하지 못했습니다.",
      );
  }
  useEffect(() => {
    let live = true;
    api("/console/session", sessionSchema, null)
      .then((value) => {
        if (live) setSession(value);
      })
      .catch(() => {})
      .finally(() => {
        if (live) setLoadingSession(false);
      });
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    if (!session) return;
    const stream = new EventSource("/events");
    let lastReceived = performance.now();
    const watchdog = window.setInterval(() => {
      if (performance.now() - lastReceived > 15000) {
        stream.close();
        setConnection("reconnecting");
        setNetworkEpoch((previous) => previous + 1);
      }
    }, 5000);
    const offline = () => {
      stream.close();
      setConnection("reconnecting");
    };
    const online = () => setNetworkEpoch((previous) => previous + 1);
    window.addEventListener("offline", offline);
    window.addEventListener("online", online);
    if (!navigator.onLine) offline();
    const receive = (message: MessageEvent<string>) => {
      try {
        const event = eventSchema.parse(JSON.parse(message.data));
        lastReceived = performance.now();
        if (event.type === "session_expired") {
          stream.close();
          signedOut("세션이 만료되었습니다. 다시 로그인하세요.");
          return;
        }
        setConnection("live");
        if (event.resource !== "keepalive") void cache.invalidateQueries();
        if (event.full_refresh) {
          window.dispatchEvent(new Event("racp-full-refresh"));
          setNotice("이벤트 기록 범위를 벗어나 최신 상태를 다시 조회했습니다.");
        }
      } catch {
        setConnection("reconnecting");
      }
    };
    stream.addEventListener("ready", receive as EventListener);
    stream.addEventListener("change", receive as EventListener);
    stream.addEventListener("refresh", receive as EventListener);
    stream.addEventListener("session_expired", receive as EventListener);
    stream.onerror = () => {
      setConnection("reconnecting");
      api("/console/session", sessionSchema, null).catch(failure);
    };
    let lastActivity = 0;
    const activity = () => {
      if (Date.now() - lastActivity > 60000) {
        lastActivity = Date.now();
        api("/console/session/activity", objectSchema, session, "POST").catch(
          failure,
        );
      }
    };
    window.addEventListener("pointerdown", activity);
    window.addEventListener("keydown", activity);
    return () => {
      window.clearInterval(watchdog);
      stream.close();
      window.removeEventListener("pointerdown", activity);
      window.removeEventListener("keydown", activity);
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", online);
    };
  }, [session, cache, networkEpoch]);

  const devices = useListPage("devices", deviceSchema, session, [
    "ONLINE",
    "OFFLINE",
    "DEGRADED",
    "CONNECTING",
    "REVOKED",
  ]);
  const jobs = useListPage("jobs", jobSchema, session, [
    "QUEUED",
    "RUNNING",
    "WAITING",
    "CANCEL_REQUESTED",
    "RECONCILING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
    "UNKNOWN",
  ]);
  const approvals = useListPage("approvals", approvalSchema, session, [
    "PENDING",
    "APPROVED",
    "DENIED",
    "EXPIRED",
    "CONSUMED",
  ]);
  const device =
    devices.data?.items.find((item) => item.id === selected) ??
    devices.data?.items[0];
  const filesystem = device?.info.capabilities.find(
    (cap) => cap.name === "filesystem",
  );
  const folders = z
    .array(z.object({ id: z.string(), path: z.string() }))
    .max(16)
    .safeParse(filesystem?.attributes?.workspaces);
  const workspaces =
    folders.success && folders.data.length
      ? folders.data
      : [
          {
            id: "default",
            path: String(filesystem?.attributes?.workspace ?? "미관측"),
          },
        ];
  const workspaceId =
    workspaceChoice.deviceId === device?.id &&
    workspaces.some((folder) => folder.id === workspaceChoice.id)
      ? workspaceChoice.id
      : "default";
  const handles = useQuery({
    queryKey: ["handles", device?.id],
    queryFn: () =>
      api(`/devices/${device!.id}/handles`, listOf(objectSchema), session),
    enabled: !!session && !!device,
  });
  const detail = useQuery({
    queryKey: ["operation", operationId],
    queryFn: () => api(`/operations/${operationId}`, objectSchema, session),
    enabled: !!session && !!operationId,
  });
  const extras = useQuery({
    queryKey: ["doctor"],
    queryFn: () => api("/doctor", objectSchema, session),
    enabled: !!session && view === "Doctor",
  });
  const mutation = useMutation({
    mutationFn: ({
      path,
      body,
      method = "POST",
    }: {
      path: string;
      body?: unknown;
      method?: string;
    }) => api(path, objectSchema, session, method, body),
    onSuccess: (value) => {
      setNotice("요청을 반영했습니다. 최신 상태를 확인하세요.");
      if (typeof value.operation_id === "string")
        setOperationId(value.operation_id);
      void cache.invalidateQueries();
    },
    onError: failure,
  });
  const run = (path: string, body?: unknown, method?: string) =>
    mutation.mutate({ path, body, method });

  async function login(event: FormEvent) {
    event.preventDefault();
    setNotice("");
    try {
      const value = await api("/console/session", sessionSchema, null, "POST", {
        setup_secret: secret,
      });
      setSecret("");
      setSession(value);
      setConnection("connecting");
    } catch (error) {
      setSecret("");
      failure(error);
    }
  }
  function execute(event: FormEvent) {
    event.preventDefault();
    if (!device) return;
    try {
      const parsed = z.array(z.string()).min(1).parse(JSON.parse(argv));
      const input: components["schemas"]["OperationInput"] = {
        device_id: device.id,
        workspace_id: workspaceId,
        operation: "shell.exec",
        payload: { argv: parsed },
        execution_profile_id: profile,
        idempotency_key: crypto.randomUUID(),
        execution_mode: "job",
        timeout_ms: null,
        approval_id: null,
      };
      run("/operations", input);
    } catch {
      setNotice("argv는 비어 있지 않은 JSON 문자열 배열이어야 합니다.");
    }
  }
  function confirmAction(path: string, context: string) {
    if (window.confirm(context)) run(path);
  }
  const context = (target: Device) =>
    `${target.name}\nDevice: ${target.id}\n실행 계정: ${target.info.execution_identity ?? "미관측"}\nSession: ${target.info.agent_boot_id ?? "미관측"}`;
  async function terminalRead() {
    if (!device || !terminalId) return;
    try {
      const result = await api("/operations", objectSchema, session, "POST", {
        device_id: device.id,
        operation: "terminal.read",
        payload: {
          handle_id: terminalId,
          cursor,
          wait_ms: 1000,
          max_bytes: 65536,
        },
      });
      if (result.error) {
        const error = objectSchema.parse(result.error);
        if (error.code === "CURSOR_EXPIRED") {
          const details = objectSchema.parse(error.details);
          setNotice(
            `출력 일부가 유실되었습니다. ${String(details.earliest_cursor)}부터 읽으려면 cursor를 직접 변경하세요.`,
          );
        } else setNotice(String(error.message));
        return;
      }
      const output = objectSchema.parse(result.result);
      setTerminalText((previous) =>
        (previous + safeText(String(output.data ?? ""))).slice(-65536),
      );
      setCursor(String(output.next_cursor));
    } catch (error) {
      failure(error);
    }
  }
  if (loadingSession)
    return (
      <main className="login">
        <p role="status">세션 확인 중…</p>
      </main>
    );
  if (!session)
    return (
      <main className="login">
        <section>
          <div className="brand">
            RACP<span>CONTROL CONSOLE</span>
          </div>
          <h1>작업 환경에 연결하세요</h1>
          <p>
            CLI에서 <code>racp console setup</code>을 실행한 뒤 5분 안에 일회성
            secret을 입력하세요.
          </p>
          <form onSubmit={login}>
            <label htmlFor="secret">일회성 setup secret</label>
            <input
              id="secret"
              type="password"
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              autoComplete="off"
              required
            />
            <button>로그인</button>
          </form>
          {notice && <p role="alert">{notice}</p>}
        </section>
      </main>
    );

  const errors = [
    devices.error,
    jobs.error,
    approvals.error,
    handles.error,
    detail.error,
    extras.error,
  ].filter(Boolean);
  return (
    <div className="layout">
      <aside>
        <a className="brand" href="/console/">
          RACP<span>CONTROL CONSOLE</span>
        </a>
        <nav aria-label="주요 화면">
          {views.map((item) => (
            <button
              key={item}
              aria-current={view === item ? "page" : undefined}
              onClick={() => setView(item)}
            >
              <span>{labels[item]}</span>
              <small>{item}</small>
            </button>
          ))}
        </nav>
        <footer>
          <span className="dot" />
          OWNER SESSION
          <br />
          <small>{session.owner_id}</small>
        </footer>
      </aside>
      <main className="content">
        <header>
          <div>
            <span className="eyebrow">REMOTE AUTOMATION CONTROL PLANE</span>
            <h1>{labels[view]}</h1>
          </div>
          <div className="connection" role="status">
            <span className={`dot ${connection === "live" ? "green" : ""}`} />
            {connection === "live"
              ? "이벤트 연결됨"
              : "재연결 중 · 마지막 관측 상태"}
            <button
              className="quiet"
              onClick={() => {
                api("/console/session", objectSchema, session, "DELETE")
                  .then(() => signedOut("로그아웃했습니다."))
                  .catch(failure);
              }}
            >
              로그아웃
            </button>
          </div>
        </header>
        {notice && (
          <div className="notice" role="status">
            {notice}
            <button
              className="quiet"
              aria-label="알림 닫기"
              onClick={() => setNotice("")}
            >
              ×
            </button>
          </div>
        )}
        {errors.length > 0 && (
          <div className="notice error" role="alert">
            {errors
              .map((error) =>
                error instanceof ApiError && error.status === 410
                  ? "결과가 만료되었습니다. 같은 key로 재실행하지 않습니다."
                  : error instanceof Error
                    ? error.message
                    : "조회 실패",
              )
              .join(" · ")}
            <button onClick={() => void cache.invalidateQueries()}>
              다시 조회
            </button>
          </div>
        )}
        {view === "Overview" && (
          <>
            <section className="metrics">
              <article>
                <span>관측 장비 · 현재 페이지</span>
                <strong>{devices.data?.items.length ?? "—"}</strong>
                <small>
                  {connection === "live"
                    ? "최신 API snapshot"
                    : "상태 확인 필요"}
                </small>
              </article>
              <article>
                <span>실행 중 작업 · 현재 페이지</span>
                <strong>
                  {jobs.data?.items.filter(
                    (item) => !terminalStates.has(item.state),
                  ).length ?? "—"}
                </strong>
                <small>대기 / 실행 / 취소 진행</small>
              </article>
              <article>
                <span>승인 대기 · 현재 페이지</span>
                <strong>
                  {approvals.data?.items.filter(
                    (item) => item.state === "PENDING",
                  ).length ?? "—"}
                </strong>
                <small>요청별 Approve once</small>
              </article>
            </section>
            <section className="panel">
              <div className="section-title">
                <h2>작업 환경</h2>
                <button className="quiet" onClick={() => setView("Devices")}>
                  모든 장비 →
                </button>
              </div>
              <DeviceCards
                items={devices.data?.items ?? []}
                stale={connection !== "live"}
                select={(item) => {
                  setSelected(item.id);
                  setView("Devices");
                }}
              />
            </section>
            <section className="panel">
              <h2>최근 작업</h2>
              {jobTable()}
            </section>
          </>
        )}
        {view === "Devices" && (
          <>
            <EnrollPC session={session} failure={failure} />
            <section className="panel">
              <h2>등록된 장비</h2>
              {devices.controls}
              {devices.isPending ? (
                <p role="status">불러오는 중…</p>
              ) : (
                <DeviceCards
                  items={devices.data?.items ?? []}
                  stale={connection !== "live"}
                  select={(item) => setSelected(item.id)}
                />
              )}
            </section>
            {device && (
              <section className="panel" aria-label="선택한 PC 정보">
                <div className="section-title">
                  <h2>{device.name}</h2>
                  <Badge state={device.info.status} />
                </div>
                <dl>
                  <dt>Stable Device ID</dt>
                  <dd>{device.id}</dd>
                  <dt>실행 계정</dt>
                  <dd>{device.info.execution_identity ?? "미관측"}</dd>
                  <dt>허용 폴더</dt>
                  <dd>
                    <ul>
                      {workspaces.map((folder) => (
                        <li key={folder.id}>
                          {safeText(folder.id)} · {safeText(folder.path)}
                        </li>
                      ))}
                    </ul>
                  </dd>
                  <dt>PC 실행 profile</dt>
                  <dd>
                    {safeText(
                      String(
                        device.info.capabilities.find(
                          (cap) => cap.name === "shell",
                        )?.attributes?.local_profile ?? "미관측",
                      ),
                    )}
                  </dd>
                  <dt>Session / boot</dt>
                  <dd>{device.info.agent_boot_id ?? "미관측"}</dd>
                  <dt>플랫폼 / Agent</dt>
                  <dd>
                    {device.info.platform} / {device.info.agent_version}
                  </dd>
                  <dt>마지막 관측</dt>
                  <dd>{device.info.last_seen_at ?? "미관측"}</dd>
                </dl>
                <h3>Capabilities</h3>
                <div className="chips">
                  {device.info.capabilities.map((cap) => (
                    <span key={cap.name}>
                      {cap.name} {cap.version} ·{" "}
                      {cap.unavailable_reason ??
                        (cap.installed === false
                          ? "not installed"
                          : cap.supported === false
                            ? "unsupported"
                            : cap.enabled === false
                              ? "disabled"
                              : cap.healthy === false
                                ? "unhealthy"
                                : "available")}
                    </span>
                  ))}
                </div>
                {device.info.capabilities
                  .filter((cap) => cap.name === "desktop" && cap.enabled)
                  .map((cap) => (
                    <DesktopScope
                      key={cap.name}
                      value={cap.attributes?.sessions ?? []}
                    />
                  ))}
                <h3>명령 실행</h3>
                <p>
                  이 장비의 OS 실행 계정 권한으로 실행됩니다. 기본 profile은
                  read_only입니다.
                </p>
                <form onSubmit={execute}>
                  <label htmlFor="workspace">작업 폴더</label>
                  <select
                    id="workspace"
                    value={workspaceId}
                    onChange={(event) =>
                      setWorkspaceChoice({
                        deviceId: device.id,
                        id: event.target.value,
                      })
                    }
                  >
                    {workspaces.map((folder) => (
                      <option key={folder.id} value={folder.id}>
                        {safeText(folder.id)} · {safeText(folder.path)}
                      </option>
                    ))}
                  </select>
                  <label htmlFor="argv">argv · JSON 배열</label>
                  <textarea
                    id="argv"
                    value={argv}
                    onChange={(event) => setArgv(event.target.value)}
                  />
                  <label htmlFor="profile">Execution profile</label>
                  <select
                    id="profile"
                    value={profile}
                    onChange={(event) =>
                      setProfile(
                        z
                          .enum(["read_only", "standard", "trusted_personal"])
                          .parse(event.target.value),
                      )
                    }
                  >
                    <option>read_only</option>
                    <option>standard</option>
                    <option>trusted_personal</option>
                  </select>
                  <button
                    disabled={
                      mutation.isPending ||
                      !["ONLINE", "DEGRADED"].includes(device.info.status) ||
                      !device.info.capabilities.some(
                        (cap) =>
                          cap.name === "shell" &&
                          cap.healthy !== false &&
                          cap.enabled !== false,
                      ) ||
                      connection !== "live"
                    }
                  >
                    Job 접수
                  </button>
                </form>
                <button
                  className="danger"
                  disabled={mutation.isPending || !!device.revoked}
                  onClick={() =>
                    confirmAction(
                      `/devices/${device.id}/revoke`,
                      `${context(device)}\n이 Device를 revoke하고 관리되는 실행을 정리합니다.`,
                    )
                  }
                >
                  장비 폐기
                </button>
              </section>
            )}
          </>
        )}
        {view === "Jobs" && (
          <section className="panel">
            <h2>작업 목록</h2>
            {jobs.controls}
            {jobTable()}
          </section>
        )}
        {view === "Approvals" && (
          <section className="panel">
            <h2>실행 승인</h2>
            {approvals.controls}
            {approvals.isPending ? (
              <p role="status">불러오는 중…</p>
            ) : !approvals.data?.items.length ? (
              <Empty>승인 요청이 없습니다.</Empty>
            ) : (
              approvals.data.items.map((item) => (
                <article className="approval" key={item.id}>
                  <div className="section-title">
                    <h3>{item.operation}</h3>
                    <Badge state={item.state} />
                  </div>
                  <p>
                    {item.device_name} · <code>{item.device_id}</code>
                  </p>
                  <p>
                    실행 계정 {item.execution_identity ?? "미관측"} · Session{" "}
                    {item.agent_boot_id ?? "미관측"} · {item.profile}
                  </p>
                  <p>
                    승인 만료 {new Date(item.expires * 1000).toLocaleString()}
                  </p>
                  <Json value={item.target} />
                  {item.state === "PENDING" && (
                    <>
                      <button
                        disabled={mutation.isPending}
                        onClick={() =>
                          confirmAction(
                            `/approvals/${item.id}/approve`,
                            `Approve once\nDevice: ${item.device_id}\n실행 계정: ${item.execution_identity}\nSession: ${item.agent_boot_id}\n${item.operation}\n${JSON.stringify(item.target)}`,
                          )
                        }
                      >
                        Approve once
                      </button>
                      <button
                        className="quiet"
                        disabled={mutation.isPending}
                        onClick={() => run(`/approvals/${item.id}/deny`)}
                      >
                        Deny
                      </button>
                    </>
                  )}
                  {item.state === "APPROVED" && (
                    <button
                      disabled={mutation.isPending}
                      onClick={() => run(`/approvals/${item.id}/execute`)}
                    >
                      승인한 동일 요청 실행
                    </button>
                  )}
                  {item.state === "EXPIRED" && (
                    <p>
                      승인이 만료되었습니다. 변경된 요청에는 새 승인이
                      필요합니다.
                    </p>
                  )}
                </article>
              ))
            )}
          </section>
        )}
        {view === "Sessions" && (
          <section className="panel">
            <h2>활성 세션</h2>
            <label htmlFor="session-device">장비</label>
            <select
              id="session-device"
              value={device?.id ?? ""}
              onChange={(event) => setSelected(event.target.value)}
            >
              {devices.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.id}
                </option>
              ))}
            </select>
            <Json value={handles.data?.items ?? []} />
            <h3>Terminal 진단 viewer</h3>
            <p>
              출력은 안전한 텍스트로 표시됩니다. 읽기는 세션 idle TTL을 연장하지
              않습니다.
            </p>
            <label htmlFor="terminal">Terminal Handle ID</label>
            <input
              id="terminal"
              value={terminalId}
              onChange={(event) => {
                setTerminalId(event.target.value);
                setCursor("0");
                setTerminalText("");
              }}
            />
            <label htmlFor="cursor">Byte cursor</label>
            <input
              id="cursor"
              value={cursor}
              onChange={(event) => setCursor(event.target.value)}
            />
            <button disabled={!terminalId} onClick={() => void terminalRead()}>
              출력 읽기
            </button>
            <pre aria-label="Terminal 출력">
              {terminalText || "아직 읽은 출력이 없습니다."}
            </pre>
            <TerminalPush
              key={`${device?.id}:${terminalId}`}
              device={device?.id ?? ""}
              handle={terminalId}
              cursor={cursor}
              chooseCursor={setCursor}
            />
          </section>
        )}
        {view === "Artifacts" && <Artifacts session={session} />}
        {view === "Audit" && <Audit session={session} />}
        {view === "Doctor" && (
          <section className="panel">
            <h2>진단</h2>
            {extras.isPending ? (
              <p role="status">불러오는 중…</p>
            ) : (
              <Json value={extras.data ?? {}} />
            )}
          </section>
        )}
        {view === "Settings" && (
          <section className="panel">
            <h2>Owner session</h2>
            <dl>
              <dt>Owner</dt>
              <dd>{session.owner_id}</dd>
              <dt>절대 만료</dt>
              <dd>{new Date(session.expires_at).toLocaleString()}</dd>
              <dt>유휴 만료</dt>
              <dd>마지막 사용자 활동 후 30분</dd>
            </dl>
            <p>owner secret은 브라우저 저장소에 보관하지 않습니다.</p>
            <h3>새 장비 등록</h3>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const name = new FormData(event.currentTarget).get("name");
                mutation.mutate(
                  { path: "/enrollment-tokens", body: { name } },
                  {
                    onSuccess: (value) =>
                      setNotice(
                        `1회 등록 token (10분 유효): ${String(value.token)}`,
                      ),
                  },
                );
              }}
            >
              <label htmlFor="device-name">장비 이름</label>
              <input id="device-name" name="name" required maxLength={128} />
              <button disabled={mutation.isPending}>등록 token 발급</button>
            </form>
          </section>
        )}
        {operationId && (
          <section className="panel" aria-labelledby="operation-title">
            <div className="section-title">
              <h2 id="operation-title">작업 조사</h2>
              <button className="quiet" onClick={() => setOperationId("")}>
                닫기
              </button>
            </div>
            <code>{operationId}</code>
            {detail.isPending ? <p>조회 중…</p> : <Json value={detail.data} />}
            {detail.data?.state === "UNKNOWN" && (
              <p>
                실행 여부가 불확정입니다. 자동 재실행하지 않고 늦은 결과와 출력
                첨부를 조사하세요.
              </p>
            )}
            <button
              className="quiet"
              onClick={() =>
                api(
                  `/operations/${operationId}/resolutions`,
                  objectSchema,
                  session,
                )
                  .then((value) => setNotice(safeText(JSON.stringify(value))))
                  .catch(failure)
              }
            >
              늦은 결과 조사
            </button>
            <button
              className="quiet"
              onClick={() =>
                api(`/operations/${operationId}/outputs`, objectSchema, session)
                  .then((value) => setNotice(safeText(JSON.stringify(value))))
                  .catch(failure)
              }
            >
              출력 첨부 조사
            </button>
          </section>
        )}
        <footer className="footnote">
          상태는 마지막 관측 기준입니다. 실행·취소 완료는 조회 API로 확인합니다.
        </footer>
      </main>
    </div>
  );

  function jobTable() {
    return jobs.isPending ? (
      <p role="status">불러오는 중…</p>
    ) : !jobs.data?.items.length ? (
      <Empty>아직 실행한 작업이 없습니다.</Empty>
    ) : (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>작업</th>
              <th>Device</th>
              <th>상태</th>
              <th>조회 / 취소</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data.items.map((item) => (
              <tr key={item.job_id}>
                <td>
                  {item.operation}
                  <small>{item.job_id}</small>
                </td>
                <td>{item.device_id}</td>
                <td>
                  <Badge state={item.state} />
                  {item.waiting_reason && <small>{item.waiting_reason}</small>}
                  {item.state === "CANCEL_REQUESTED" && (
                    <small>취소 접수 · 종료 확인 대기</small>
                  )}
                  {!item.outcome_available && <small>결과 만료</small>}
                </td>
                <td>
                  <button
                    className="quiet"
                    onClick={() => setOperationId(item.operation_id)}
                  >
                    조사
                  </button>
                  {!terminalStates.has(item.state) &&
                    item.state !== "CANCEL_REQUESTED" && (
                      <button
                        className="quiet"
                        disabled={mutation.isPending}
                        onClick={() => {
                          const target = devices.data?.items.find(
                            (value) => value.id === item.device_id,
                          );
                          confirmAction(
                            `/jobs/${item.job_id}/cancel`,
                            `${target ? context(target) : item.device_id}\n${item.operation}\n${item.job_id}\n취소를 요청합니다. 종료 완료는 별도로 확인합니다.`,
                          );
                        }}
                      >
                        취소 요청
                      </button>
                    )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
}

function DeviceCards({
  items,
  stale,
  select,
}: {
  items: Device[];
  stale: boolean;
  select: (item: Device) => void;
}) {
  return !items.length ? (
    <Empty>등록된 장비가 없습니다. 설정에서 등록 token을 발급하세요.</Empty>
  ) : (
    <div className="devices">
      {items.map((item) => (
        <button
          key={item.id}
          className="device-card"
          onClick={() => select(item)}
        >
          <div>
            <span className="device-icon">▣</span>
            <Badge state={stale ? "확인 필요" : item.info.status} />
          </div>
          <h3>{item.name}</h3>
          <code>{item.id}</code>
          <p>
            {item.info.platform ?? "플랫폼 미관측"} ·{" "}
            {item.info.execution_identity ?? "계정 미관측"}
          </p>
          <small>
            {stale ? `마지막 상태 ${item.info.status} · ` : ""}
            {item.info.last_seen_at
              ? new Date(item.info.last_seen_at).toLocaleString()
              : "관측 기록 없음"}
          </small>
        </button>
      ))}
    </div>
  );
}
