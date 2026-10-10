import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api, type Session } from "../api";

const updateStatusSchema = z.object({
  configured: z.boolean(),
  current_version: z.string(),
  channel: z.string(),
  feed_configured: z.boolean(),
  trust_key_configured: z.boolean(),
  automatic_check: z.boolean(),
  apply_enabled: z.boolean(),
  reason: z.string().nullable(),
});
const revisionSchema = z.object({
  settings_revision: z.number().int().positive(),
});
const candidateSchema = z.object({
  release_id: z.string(),
  version: z.string(),
  platform: z.literal("win-x64"),
  manifest_sha256: z.string(),
  schema_rollback_compatible: z.boolean(),
  checked_at: z.string(),
});
const maintenanceSchema = z.object({
  id: z.string(),
  kind: z.literal("update"),
  state: z.enum(["PENDING", "RUNNING", "DEFERRED", "SUCCEEDED", "FAILED"]),
  actor_id: z.string(),
  realm_id: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  progress: z.number().nullable(),
  error: z.string().nullable(),
  receipt_id: z.string().nullable(),
});

export function ManagementUpdates({
  session,
  failure,
}: {
  session: Session;
  failure: (error: unknown) => void;
}) {
  const cache = useQueryClient();
  const [candidate, setCandidate] = useState<z.infer<
    typeof candidateSchema
  > | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ["management", "updates"],
    queryFn: () =>
      api("/management/updates/status", updateStatusSchema, session),
  });
  const revision = useQuery({
    queryKey: ["management", "status", "update-revision"],
    queryFn: () => api("/management/status", revisionSchema, session),
  });
  const check = useMutation({
    mutationFn: () =>
      api("/management/updates/check", candidateSchema, session, "POST", {
        expected_revision: revision.data?.settings_revision,
      }),
    onSuccess: (value) => {
      setCandidate(value);
      setJobId(null);
    },
    onError: failure,
  });
  const apply = useMutation({
    mutationFn: () => {
      if (!candidate) throw new Error("No verified update candidate");
      return api(
        "/management/updates/apply",
        maintenanceSchema,
        session,
        "POST",
        {
          release_id: candidate.release_id,
          expected_revision: revision.data?.settings_revision,
          idempotency_key: `console-${candidate.release_id}`,
          confirm: true,
        },
      );
    },
    onSuccess: (value) => {
      setJobId(value.id);
      void cache.invalidateQueries({ queryKey: ["management", "updates"] });
    },
    onError: failure,
  });
  const job = useQuery({
    queryKey: ["management", "maintenance-job", jobId],
    enabled: Boolean(jobId),
    queryFn: () =>
      api(`/management/maintenance-jobs/${jobId}`, maintenanceSchema, session),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && ["SUCCEEDED", "FAILED", "DEFERRED"].includes(state)
        ? false
        : 1000;
    },
  });
  return (
    <section className="panel">
      <h2>서명 업데이트</h2>
      {status.isPending || !status.data ? (
        <p role="status">업데이트 설정 확인 중…</p>
      ) : (
        <>
          <dl>
            <dt>현재 버전</dt>
            <dd>{status.data.current_version}</dd>
            <dt>채널</dt>
            <dd>{status.data.channel}</dd>
            <dt>서명 feed</dt>
            <dd>
              {status.data.feed_configured ? "configured" : "not configured"}
            </dd>
            <dt>신뢰 public key</dt>
            <dd>
              {status.data.trust_key_configured
                ? "configured"
                : "not configured"}
            </dd>
            <dt>웹 적용</dt>
            <dd>
              {status.data.apply_enabled ? "enabled" : status.data.reason}
            </dd>
          </dl>
          <button
            disabled={
              !status.data.apply_enabled || !revision.data || check.isPending
            }
            onClick={() => check.mutate()}
          >
            서명 후보 확인
          </button>
          {candidate && (
            <div>
              <p>
                검증 후보 {candidate.version} ({candidate.platform})
              </p>
              <p>manifest {candidate.manifest_sha256}</p>
              <button disabled={apply.isPending} onClick={() => apply.mutate()}>
                검증된 업데이트 적용
              </button>
            </div>
          )}
          {job.data && (
            <p role="status">
              update job {job.data.id}: {job.data.state}
              {job.data.error ? ` — ${job.data.error}` : ""}
            </p>
          )}
        </>
      )}
    </section>
  );
}
