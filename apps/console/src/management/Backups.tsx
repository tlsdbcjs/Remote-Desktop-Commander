import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api, type Session } from "../api";

const backupSchema = z.object({
  id: z.string(),
  state: z.string(),
  manifest_path: z.string(),
  sha256: z.string(),
  schema_version: z.number(),
  instance_id: z.string(),
  created_at: z.string(),
});
const restoreSchema = z.object({
  backup_id: z.string(),
  valid: z.boolean(),
  schema_version: z.number(),
  instance_id: z.string(),
  database_integrity: z.string(),
  warnings: z.array(z.string()),
});

export function ManagementBackups({
  session,
  failure,
}: {
  session: Session;
  failure: (error: unknown) => void;
}) {
  const cache = useQueryClient();
  const backups = useQuery({
    queryKey: ["management", "backups"],
    queryFn: () => api("/management/backups", z.array(backupSchema), session),
  });
  const create = useMutation({
    mutationFn: () =>
      api("/management/backups", backupSchema, session, "POST", {
        idempotency_key: `console-${crypto.randomUUID()}`,
      }),
    onSuccess: () =>
      void cache.invalidateQueries({ queryKey: ["management", "backups"] }),
    onError: failure,
  });
  const preview = useMutation({
    mutationFn: (id: string) =>
      api(
        `/management/backups/${encodeURIComponent(id)}/restore-preview`,
        restoreSchema,
        session,
      ),
    onError: failure,
  });
  return (
    <section className="panel">
      <div className="section-title">
        <h2>백업·복원</h2>
        <button disabled={create.isPending} onClick={() => create.mutate()}>
          새 백업
        </button>
      </div>
      {(backups.data ?? []).map((backup) => (
        <article key={backup.id}>
          <strong>{backup.id}</strong> · {backup.created_at} · schema{" "}
          {backup.schema_version}
          <button className="quiet" onClick={() => preview.mutate(backup.id)}>
            복원 사전검증
          </button>
        </article>
      ))}
      {preview.data && (
        <p role="status">
          {preview.data.backup_id}:{" "}
          {preview.data.valid ? "복원 검증 통과" : "복원 불가"} ·{" "}
          {preview.data.database_integrity}
        </p>
      )}
    </section>
  );
}
