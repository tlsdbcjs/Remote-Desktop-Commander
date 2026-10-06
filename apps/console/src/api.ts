import { z } from "zod";
import type { components } from "./generated";

export const sessionSchema: z.ZodType<Session> = z.object({
  owner_id: z.string(),
  csrf_token: z.string(),
  expires_at: z.string(),
  idle_seconds: z.number(),
  absolute_seconds: z.number(),
});
export type Session = components["schemas"]["ConsoleSession"];
export const deviceSchema = z.object({
  id: z.string(),
  name: z.string(),
  revoked: z.number(),
  info: z.object({
    status: z.string().default("OFFLINE"),
    platform: z.string().optional(),
    agent_version: z.string().optional(),
    execution_identity: z.string().optional(),
    agent_boot_id: z.string().optional(),
    last_seen_at: z.string().optional(),
    capabilities: z
      .array(
        z.object({
          name: z.string(),
          version: z.string(),
          operations: z.array(z.string()),
          installed: z.boolean().optional(),
          supported: z.boolean().optional(),
          healthy: z.boolean().optional(),
          enabled: z.boolean().optional(),
          unavailable_reason: z.string().nullable().optional(),
          attributes: z.record(z.string(), z.unknown()).optional(),
        }),
      )
      .default([]),
  }),
});
export type Device = z.infer<typeof deviceSchema>;
export const jobSchema = z.object({
  job_id: z.string(),
  operation_id: z.string(),
  device_id: z.string(),
  state: z.string(),
  operation: z.string(),
  created_at: z.string(),
  waiting_reason: z.string().nullable(),
  result: z.record(z.string(), z.unknown()).nullable(),
  error: z.record(z.string(), z.unknown()).nullable(),
  outcome_available: z.boolean(),
});
export const approvalSchema = z.object({
  id: z.string(),
  operation_id: z.string(),
  device_id: z.string(),
  device_name: z.string(),
  state: z.string(),
  operation: z.string(),
  expires: z.number(),
  execution_identity: z.string().nullable(),
  agent_boot_id: z.string().nullable(),
  profile: z.string(),
  target: z.record(z.string(), z.unknown()),
});
export const eventSchema: z.ZodType<components["schemas"]["ConsoleEvent"]> =
  z.object({
    event_id: z.string(),
    type: z.enum(["ready", "change", "refresh", "session_expired"]),
    full_refresh: z.boolean(),
    observed_at: z.string(),
    resource: z.string(),
    device_id: z.string().nullable(),
    operation_id: z.string().nullable(),
  });
export const objectSchema = z.record(z.string(), z.unknown());
export const artifactSchema: z.ZodType<components["schemas"]["ArtifactView"]> =
  z.object({
    id: z.string(),
    operation_id: z.string(),
    device_id: z.string(),
    owner_id: z.string(),
    sha256: z.string(),
    size_bytes: z.number(),
    created_at: z.string(),
    media_type: z.string(),
    state: z.string(),
    expires_at: z.string(),
  });
export const auditSchema: z.ZodType<components["schemas"]["AuditView"]> =
  z.object({
    id: z.string(),
    timestamp: z.string(),
    event: z.string(),
    device_id: z.string(),
    operation_id: z.string(),
    operation: z.string(),
    trace_id: z.string(),
    request_id: z.string(),
    owner_id: z.string(),
    summary: objectSchema,
  });
export const listOf = <T extends z.ZodType>(item: T) =>
  z.object({
    items: z.array(item),
    next_cursor: z.string().nullable().optional(),
  });

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  session: Session | null,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(session && method !== "GET"
        ? { "X-CSRF-Token": session.csrf_token }
        : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value: unknown = await response.json();
  if (!response.ok) {
    const parsed = z
      .object({
        error: z.object({
          code: z.string(),
          message: z.string(),
          details: objectSchema.optional(),
        }),
      })
      .safeParse(value);
    throw parsed.success
      ? new ApiError(
          response.status,
          parsed.data.error.code,
          parsed.data.error.message,
          parsed.data.error.details,
        )
      : new ApiError(
          response.status,
          "TRANSPORT_ERROR",
          "응답을 확인할 수 없습니다.",
        );
  }
  return schema.parse(value);
}

// Display-only output: ESC/C0 bytes become visible notation, never terminal actions.
export function safeText(text: string): string {
  return text.replace(
    /[\x00-\x08\x0b-\x1f\x7f]/g,
    (value) => `\\x${value.charCodeAt(0).toString(16).padStart(2, "0")}`,
  );
}
