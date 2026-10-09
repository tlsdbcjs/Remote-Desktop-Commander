import { expect, test } from "@playwright/test";

test("signed update check requires explicit apply and shows maintenance result", async ({
  page,
}) => {
  await page.addInitScript(() => {
    class FixtureEventSource {
      onerror: ((event: Event) => unknown) | null = null;
      constructor(_url: string | URL) {}
      addEventListener() {}
      removeEventListener() {}
      close() {}
    }
    Object.defineProperty(window, "EventSource", { value: FixtureEventSource });
  });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    let body: unknown = {};
    if (path === "/api/v1/console/session") {
      body = {
        owner_id: "owner_local",
        csrf_token: "csrf-update",
        expires_at: "2026-10-09T00:00:00Z",
        idle_seconds: 1800,
        absolute_seconds: 28800,
      };
    } else if (
      ["/api/v1/devices", "/api/v1/jobs", "/api/v1/approvals"].includes(path)
    ) {
      body = { items: [], next_cursor: null };
    } else if (path === "/api/v1/management/status") {
      body = {
        lifecycle: "READY",
        ready: true,
        version: "0.1.19",
        instance_id: "gateway_update_fixture",
        mode: "service",
        setup_mode: "local_owner",
        database: "ready",
        mcp: "owner_bearer",
        settings_revision: 4,
        connected_devices: 0,
        total_devices: 0,
        event_streams: 0,
        tls_configured: true,
        tls_expires_at: "2027-10-08T00:00:00Z",
        disk_free_bytes: 10_000_000_000,
        warnings: [],
      };
    } else if (path === "/api/v1/management/updates/status") {
      body = {
        configured: true,
        current_version: "0.1.19",
        channel: "stable",
        feed_configured: true,
        trust_key_configured: true,
        automatic_check: true,
        apply_enabled: true,
        reason: null,
      };
    } else if (
      path === "/api/v1/management/updates/check" &&
      method === "POST"
    ) {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-update");
      expect(request.postDataJSON()).toEqual({ expected_revision: 4 });
      body = {
        release_id: "rel_020",
        version: "0.1.20",
        platform: "win-x64",
        manifest_sha256: "a".repeat(64),
        schema_rollback_compatible: true,
        checked_at: "2026-10-08T00:00:00Z",
      };
    } else if (
      path === "/api/v1/management/updates/apply" &&
      method === "POST"
    ) {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-update");
      expect(request.postDataJSON()).toEqual({
        release_id: "rel_020",
        expected_revision: 4,
        idempotency_key: "console-rel_020",
        confirm: true,
      });
      body = {
        id: "mnt_update_fixture",
        kind: "update",
        state: "RUNNING",
        actor_id: "owner_local",
        realm_id: "realm_local",
        created_at: "2026-10-08T00:00:00Z",
        updated_at: "2026-10-08T00:00:00Z",
        progress: 0.1,
        error: null,
        receipt_id: null,
      };
    } else if (
      path === "/api/v1/management/maintenance-jobs/mnt_update_fixture"
    ) {
      body = {
        id: "mnt_update_fixture",
        kind: "update",
        state: "SUCCEEDED",
        actor_id: "owner_local",
        realm_id: "realm_local",
        created_at: "2026-10-08T00:00:00Z",
        updated_at: "2026-10-08T00:00:01Z",
        progress: 1,
        error: null,
        receipt_id: "mrc_update_fixture",
      };
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });

  await page.goto("/console/");
  await page
    .getByRole("button", { name: "업데이트 Updates", exact: true })
    .click();
  await expect(page.getByText("0.1.19", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "서명 후보 확인" }).click();
  await expect(page.getByText(/검증 후보 0\.1\.20/)).toBeVisible();
  await page.getByRole("button", { name: "검증된 업데이트 적용" }).click();
  await expect(page.getByText(/mnt_update_fixture: SUCCEEDED/)).toBeVisible();
});
