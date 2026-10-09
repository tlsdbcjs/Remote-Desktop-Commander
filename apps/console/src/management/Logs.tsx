import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api, safeText, type Session } from "../api";

const logEntry = z.object({
  id: z.string(),
  timestamp: z.string(),
  level: z.string(),
  event: z.string(),
  message: z.string(),
  device_id: z.string(),
  request_id: z.string(),
  actor_id: z.string(),
  fields: z.record(z.string(), z.unknown()),
});
const logPage = z.object({
  items: z.array(logEntry),
  next_cursor: z.string().nullable(),
});
const supportReceipt = z.object({
  id: z.string(),
  file_name: z.string(),
  sha256: z.string(),
  size_bytes: z.number(),
  created_at: z.string(),
  log_entries: z.number(),
});

export function ManagementLogs({ session }: { session: Session }) {
  const [device, setDevice] = useState("");
  const [support, setSupport] = useState<z.infer<typeof supportReceipt> | null>(
    null,
  );
  const logs = useQuery({
    queryKey: ["management", "logs", device],
    queryFn: () =>
      api(
        "/management/logs" +
          (device ? `?device_id=${encodeURIComponent(device)}` : ""),
        logPage,
        session,
      ),
  });
  const createSupport = useMutation({
    mutationFn: () =>
      api("/management/support-bundles", supportReceipt, session, "POST", {}),
    onSuccess: setSupport,
  });
  return (
    <section className="panel">
      <div className="section-title">
        <h2>운영 로그</h2>
        <div>
          <input
            aria-label="장비 ID 필터"
            value={device}
            onChange={(event) => setDevice(event.target.value)}
            placeholder="device_id"
          />
          <button
            type="button"
            disabled={createSupport.isPending}
            onClick={() => createSupport.mutate()}
          >
            {createSupport.isPending ? "지원 번들 생성 중…" : "지원 번들 생성"}
          </button>
        </div>
      </div>
      {support && (
        <p>
          민감정보를 제외한 지원 번들을 생성했습니다. {support.log_entries}개
          로그 포함 ·{" "}
          <a
            href={`/api/v1/management/support-bundles/${encodeURIComponent(support.id)}`}
          >
            {support.file_name} 다운로드
          </a>
        </p>
      )}
      {logs.isPending ? (
        <p role="status">로그 조회 중…</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>시간</th>
              <th>이벤트</th>
              <th>장비</th>
              <th>메시지</th>
            </tr>
          </thead>
          <tbody>
            {(logs.data?.items ?? []).map((entry) => (
              <tr key={entry.id}>
                <td>{entry.timestamp}</td>
                <td>{entry.event}</td>
                <td>{entry.device_id || "—"}</td>
                <td>{safeText(entry.message)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
