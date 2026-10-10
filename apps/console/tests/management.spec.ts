import { expect, test, type Page } from "@playwright/test";

const now = "2026-10-08T00:00:00Z";
const version = process.env.RACP_TEST_VERSION ?? "0.1.19";

async function installManagementFixture(page: Page) {
  await page.addInitScript(() => {
    class FixtureEventSource {
      onerror: ((event: Event) => unknown) | null = null;

      constructor(_url: string | URL) {}

      addEventListener(
        type: string,
        listener: EventListenerOrEventListenerObject,
      ) {
        if (type !== "ready") return;
        const event = new MessageEvent("ready", {
          data: JSON.stringify({
            event_id: "evt_ready",
            type: "ready",
            full_refresh: false,
            observed_at: "2026-10-08T00:00:00Z",
            resource: "keepalive",
            device_id: null,
            operation_id: null,
          }),
        });
        queueMicrotask(() => {
          if (typeof listener === "function") listener(event);
          else listener.handleEvent(event);
        });
      }

      removeEventListener() {}
      close() {}
    }
    Object.defineProperty(window, "EventSource", { value: FixtureEventSource });
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    let body: unknown;
    if (path === "/api/v1/console/session" && method === "GET") {
      body = {
        owner_id: "owner_local",
        csrf_token: "csrf-fixture",
        expires_at: "2026-10-09T00:00:00Z",
        idle_seconds: 1800,
        absolute_seconds: 28800,
      };
    } else if (path === "/api/v1/devices") {
      body = { items: [], next_cursor: null };
    } else if (path === "/api/v1/jobs") {
      body = { items: [], next_cursor: null };
    } else if (path === "/api/v1/approvals") {
      body = { items: [], next_cursor: null };
    } else if (path === "/api/v1/management/status") {
      body = {
        lifecycle: "READY",
        ready: true,
        version,
        instance_id: "gateway_fixture",
        mode: "portable",
        setup_mode: "local_owner",
        database: "ready",
        mcp: "owner_bearer",
        settings_revision: 7,
        connected_devices: 2,
        total_devices: 3,
        event_streams: 1,
        tls_configured: true,
        tls_expires_at: "2027-10-08T00:00:00Z",
        disk_free_bytes: 10_000_000_000,
        warnings: [],
      };
    } else if (path === "/api/v1/management/settings" && method === "GET") {
      body = {
        revision: 7,
        values: { port: 8765, public_origin: "https://gateway.example" },
        restart_required: false,
      };
    } else if (path === "/api/v1/management/settings" && method === "PUT") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      body = {
        revision: 8,
        values: { port: 18776, public_origin: "https://gateway.example" },
        restart_required: true,
      };
    } else if (path === "/api/v1/management/settings/stage") {
      body = { valid: true, restart_required: true, revision: 7, warnings: [] };
    } else if (path === "/api/v1/management/logs") {
      body = {
        items: [
          {
            id: "log_fixture",
            timestamp: now,
            level: "INFO",
            event: "gateway.ready",
            message: "ready",
            device_id: "",
            request_id: "",
            actor_id: "owner_local",
            fields: {},
          },
        ],
        next_cursor: null,
      };
    } else if (
      path === "/api/v1/management/support-bundles" &&
      method === "POST"
    ) {
      expect(request.headers()["x-csrf-token"]).toBe("csrf-fixture");
      body = {
        id: "sup_0123456789abcdef0123456789abcdef",
        file_name: "racp-support.zip",
        sha256: "a".repeat(64),
        size_bytes: 4096,
        created_at: now,
        log_entries: 1,
      };
    } else if (path === "/api/v1/management/backups") {
      body = [];
    } else if (path === "/api/v1/management/users") {
      body = [
        {
          id: "user_fixture",
          display_name: "Fixture Admin",
          issuer: "local",
          subject: "owner_local",
          role: "owner",
          active: true,
          auth_revision: 3,
          device_grants: [],
          output_grants: [],
          operation_grants: [],
        },
      ];
    } else if (path === "/api/v1/management/updates/status") {
      body = {
        configured: true,
        current_version: version,
        channel: "stable",
        feed_configured: true,
        trust_key_configured: true,
        automatic_check: true,
        apply_enabled: false,
        reason: "manual approval required",
      };
    } else {
      body = {};
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

test("management routes share a session and expose sanitized support bundle workflow", async ({
  page,
}) => {
  await installManagementFixture(page);
  await page.goto("/console/");

  const gateway = page.getByRole("region", { name: "Gateway 관리 상태" });
  await expect(gateway).toContainText(version);
  await expect(gateway).toContainText("2 online / 3 total");
  await expect(gateway).toContainText("revision");

  await page
    .getByRole("button", { name: "설정 Settings", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Gateway 설정" }),
  ).toBeVisible();
  await expect(page.getByText("revision 7", { exact: true })).toBeVisible();
  await page.getByLabel("포트 변경 사전검증").fill("18776");
  await page.getByRole("button", { name: "변경 영향 검사" }).click();
  await page.getByRole("button", { name: "검증된 설정 적용" }).click();
  await expect(page.getByText("revision 8", { exact: true })).toBeVisible();

  await page
    .getByRole("button", { name: "운영 로그 Logs", exact: true })
    .click();
  await expect(page.getByText("gateway.ready", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "지원 번들 생성", exact: true })
    .click();
  const support = page.getByRole("link", { name: "racp-support.zip 다운로드" });
  await expect(support).toHaveAttribute(
    "href",
    "/api/v1/management/support-bundles/sup_0123456789abcdef0123456789abcdef",
  );

  await page.getByRole("button", { name: "백업 Backups", exact: true }).click();
  await expect(page.getByRole("heading", { name: "백업·복원" })).toBeVisible();

  await page
    .getByRole("button", { name: "관리 사용자 Users", exact: true })
    .click();
  await expect(page.getByText("Fixture Admin", { exact: true })).toBeVisible();

  await page
    .getByRole("button", { name: "업데이트 Updates", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "서명 업데이트" }),
  ).toBeVisible();
  await expect(page.getByText("stable", { exact: true })).toBeVisible();
});
