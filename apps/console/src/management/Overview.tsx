import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api, type Session } from "../api";

const statusSchema = z.object({
  lifecycle: z.string(),
  ready: z.boolean(),
  version: z.string(),
  instance_id: z.string().nullable(),
  mode: z.string(),
  setup_mode: z.string(),
  database: z.string(),
  mcp: z.string(),
  settings_revision: z.number(),
  connected_devices: z.number(),
  total_devices: z.number(),
  event_streams: z.number(),
  tls_configured: z.boolean(),
  tls_expires_at: z.string().nullable(),
  disk_free_bytes: z.number().nullable(),
  warnings: z.array(z.string()),
});

export function ManagementOverview({
  session,
  failure,
}: {
  session: Session;
  failure: (error: unknown) => void;
}) {
  const status = useQuery({
    queryKey: ["management", "status"],
    queryFn: () => api("/management/status", statusSchema, session),
  });
  if (status.isError) {
    failure(status.error);
    return null;
  }
  return (
    <section className="panel" aria-label="Gateway 관리 상태">
      <div className="section-title">
        <h2>Gateway 관리 상태</h2>
        <button className="quiet" onClick={() => void status.refetch()}>
          새로고침
        </button>
      </div>
      {status.isPending || !status.data ? (
        <p role="status">관리 상태 조회 중…</p>
      ) : (
        <>
          <dl>
            <dt>버전</dt>
            <dd>{status.data.version}</dd>
            <dt>인스턴스</dt>
            <dd>{status.data.instance_id ?? "legacy"}</dd>
            <dt>수명주기</dt>
            <dd>{status.data.lifecycle}</dd>
            <dt>DB / MCP</dt>
            <dd>
              {status.data.database} / {status.data.mcp}
            </dd>
            <dt>장비</dt>
            <dd>
              {status.data.connected_devices} online /{" "}
              {status.data.total_devices} total
            </dd>
            <dt>설정 revision</dt>
            <dd>{status.data.settings_revision}</dd>
          </dl>
          {status.data.warnings.length > 0 && (
            <div className="notice">{status.data.warnings.join(" · ")}</div>
          )}
        </>
      )}
    </section>
  );
}
