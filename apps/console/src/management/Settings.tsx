import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { api, type Session } from "../api";

const settingsSchema = z.object({
  revision: z.number().int().positive(),
  values: z.record(z.string(), z.unknown()),
  restart_required: z.boolean(),
});
const configCheckSchema = z.object({
  valid: z.boolean(),
  errors: z.array(z.string()).default([]),
  warnings: z.array(z.string()).default([]),
  restart_required: z.boolean(),
});

export function ManagementSettings({
  session,
  failure,
}: {
  session: Session;
  failure: (error: unknown) => void;
}) {
  const cache = useQueryClient();
  const [port, setPort] = useState("");
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const settings = useQuery({
    queryKey: ["management", "settings"],
    queryFn: () => api("/management/settings", settingsSchema, session),
  });
  const stage = useMutation({
    mutationFn: (nextPort: number) =>
      api("/management/settings/stage", configCheckSchema, session, "POST", {
        expected_revision: settings.data?.revision,
        changes: { port: nextPort },
      }),
    onSuccess: (value) => {
      setPreview(value);
      void cache.invalidateQueries({ queryKey: ["management", "status"] });
    },
    onError: failure,
  });
  const apply = useMutation({
    mutationFn: (nextPort: number) =>
      api("/management/settings", settingsSchema, session, "PUT", {
        expected_revision: settings.data?.revision,
        changes: { port: nextPort },
      }),
    onSuccess: (value) => {
      setPreview(null);
      setPort("");
      cache.setQueryData(["management", "settings"], value);
      void cache.invalidateQueries({ queryKey: ["management", "status"] });
    },
    onError: failure,
  });
  return (
    <section className="panel">
      <h2>Gateway 설정</h2>
      {settings.isPending || !settings.data ? (
        <p role="status">설정 조회 중…</p>
      ) : (
        <>
          <p>revision {settings.data.revision}</p>
          <pre>{JSON.stringify(settings.data.values, null, 2)}</pre>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const value = Number(port);
              if (Number.isInteger(value) && value >= 1 && value <= 65535) {
                stage.mutate(value);
              }
            }}
          >
            <label htmlFor="gateway-port">포트 변경 사전검증</label>
            <input
              id="gateway-port"
              inputMode="numeric"
              value={port}
              onChange={(event) => setPort(event.target.value)}
              placeholder={String(settings.data.values.port ?? "")}
            />
            <button disabled={stage.isPending}>변경 영향 검사</button>
          </form>
          {preview && (
            <>
              <pre>{JSON.stringify(preview, null, 2)}</pre>
              <button
                disabled={!preview.valid || apply.isPending}
                onClick={() => {
                  const value = Number(port);
                  if (Number.isInteger(value) && value >= 1 && value <= 65535) {
                    apply.mutate(value);
                  }
                }}
              >
                검증된 설정 적용
              </button>
            </>
          )}
        </>
      )}
    </section>
  );
}
