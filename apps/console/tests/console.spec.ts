import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { createHash } from "node:crypto";

const owner = process.env.RACP_OWNER_TOKEN!;
const device = process.env.RACP_DEVICE_ID!;
const python = process.env.RACP_TEST_PYTHON!;
const auth = { Authorization: `Bearer ${owner}` };

async function login(page: Page, request: APIRequestContext) {
  const setup = await request.post("/api/v1/console/setup-token", {
    headers: auth,
  });
  const { setup_secret } = await setup.json();
  await page.goto("/console/");
  await page.getByLabel("일회성 setup secret").fill(setup_secret);
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("button", { name: "로그인", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("status").filter({ hasText: "이벤트 연결됨" }),
  ).toBeVisible();
}
async function shell(page: Page, code: string, profile = "trusted_personal") {
  await page.getByRole("button", { name: "장비 Devices", exact: true }).click();
  await page
    .getByLabel("argv · JSON 배열")
    .fill(JSON.stringify([python, "-X", "utf8", "-c", code]));
  await page.getByLabel("Execution profile").selectOption(profile);
  await page.getByRole("button", { name: "Job 접수", exact: true }).click();
}

test("cookie login, safe storage, dashboard, mobile and logout", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page, request);
  await expect(
    page.getByRole("button", { name: /Console fixture/ }),
  ).toContainText("ONLINE");
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "racp_console",
  );
  await page.getByRole("button", { name: "진단 Doctor", exact: true }).click();
  await expect(page.locator("pre")).toContainText("execution_identity");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "진단", level: 1, exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("button", { name: "대시보드 Overview", exact: true })
    .click();
  await page.screenshot({
    path: "../../dist/console-overview.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page.getByLabel("일회성 setup secret")).toBeVisible();
  expect(errors).toEqual([]);
});

test("desktop scope shows the configured Windows session and logon account", async ({
  page,
  request,
}) => {
  test.skip(
    !process.env.RACP_DESKTOP_SESSION_ID,
    "Windows user session Broker fixture",
  );
  await login(page, request);
  await page.getByRole("button", { name: "장비 Devices", exact: true }).click();
  const scope = page.getByRole("region", { name: "Desktop 실행 범위" });
  await expect(scope).toContainText("로그온 사용자 권한");
  await expect(scope).toContainText(
    `세션 ${process.env.RACP_DESKTOP_SESSION_ID}`,
  );
  await expect(scope).toContainText(/입력 가능|현재 입력 불가/);
  await expect(scope).not.toContainText("계정 미관측");
});

test("standard profile approval carries context and runs the same request once", async ({
  page,
  request,
}) => {
  await login(page, request);
  await shell(
    page,
    "from pathlib import Path; p=Path('ui-once'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('ui-approved')",
    "standard",
  );
  await page
    .getByRole("button", { name: "승인 Approvals", exact: true })
    .click();
  const approval = page.locator(".approval").filter({ hasText: "ui-once" });
  await expect(approval).toContainText(device);
  await expect(approval).toContainText("실행 계정");
  page.once("dialog", (dialog) => {
    expect(dialog.message()).toContain(device);
    void dialog.accept();
  });
  await approval
    .getByRole("button", { name: "Approve once", exact: true })
    .click();
  await approval
    .getByRole("button", { name: "승인한 동일 요청 실행", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "작업 조사", exact: true }),
  ).toContainText("ui-approved");
  await page.getByRole("button", { name: "작업 Jobs", exact: true }).click();
  await expect(
    page.locator("tbody tr").filter({ hasText: "shell.exec" }).first(),
  ).toContainText("COMPLETED");
  const counted = await request.post("/api/v1/operations", {
    headers: auth,
    data: {
      device_id: device,
      operation: "filesystem.read",
      payload: { path: "ui-once" },
    },
  });
  expect((await counted.json()).result.text).toBe("1");
});

test("job cancellation confirms termination and Agent crash exposes UNKNOWN without rerun", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  await login(page, request);
  await shell(page, "import time; time.sleep(90)");
  await page.getByRole("button", { name: "작업 Jobs", exact: true }).click();
  const active = page.locator("tbody tr").filter({ hasText: "RUNNING" });
  await expect(active).toHaveCount(1);
  page.once("dialog", (dialog) => {
    expect(dialog.message()).toContain("취소를 요청");
    void dialog.accept();
  });
  await active.getByRole("button", { name: "취소 요청", exact: true }).click();
  await expect(
    page.locator("tbody tr").filter({ hasText: "CANCELLED" }),
  ).toHaveCount(1);
  await shell(page, "import time; time.sleep(90)");
  await expect(
    page.getByRole("region", { name: "작업 조사", exact: true }),
  ).toContainText("RUNNING");
  const restarted = await request.post("/fixture/restart-agent", {
    headers: auth,
  });
  expect(restarted.status()).toBe(200);
  await expect(
    page.getByRole("region", { name: "작업 조사", exact: true }),
  ).toContainText("실행 여부가 불확정");
  expect(
    await page.getByRole("button", { name: /다시 실행|재실행/ }).count(),
  ).toBe(0);
});

test("SSE disconnect marks stale observations and safe terminal output cannot run escapes", async ({
  page,
  request,
}) => {
  await login(page, request);
  await page.context().setOffline(true);
  await expect(
    page.getByRole("status").filter({ hasText: "재연결 중" }),
  ).toBeVisible();
  await page.context().setOffline(false);
  await expect(
    page.getByRole("status").filter({ hasText: "이벤트 연결됨" }),
  ).toBeVisible();
  const events: string[] = [];
  page.on("request", (event) => {
    if (event.url().includes("evil.example")) events.push(event.url());
  });
  const opened = await request.post("/api/v1/operations", {
    headers: auth,
    data: {
      device_id: device,
      operation: "terminal.open",
      payload: {
        argv: [
          python,
          "-u",
          "-c",
          "print('\\x1b]52;c;ZXZpbA==\\x07\\x1b]8;;https://evil.example\\x07label\\x1b]8;;\\x07'); import time; time.sleep(90)",
        ],
      },
      execution_profile_id: "trusted_personal",
      idempotency_key: "ui-terminal-fixture",
    },
  });
  const openedBody = await opened.json();
  const handle = openedBody.result.handle_id ?? openedBody.result.id;
  expect(typeof handle).toBe("string");
  await page
    .getByRole("button", { name: "세션 Sessions", exact: true })
    .click();
  await page.getByLabel("Terminal Handle ID").fill(handle);
  await page.getByRole("button", { name: "출력 읽기", exact: true }).click();
  await expect(page.getByLabel("Terminal 출력")).toContainText("evil.example");
  expect(events).toEqual([]);
  expect(await page.getByLabel("Terminal 출력").locator("a").count()).toBe(0);
  await request.post("/api/v1/operations", {
    headers: auth,
    data: {
      device_id: device,
      operation: "terminal.close",
      payload: { handle_id: handle },
      execution_profile_id: "trusted_personal",
      idempotency_key: "ui-terminal-close",
    },
  });
});

test("Device online status changes through SSE without reloading the page", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  await login(page, request);
  const card = page.getByRole("button", { name: /Console fixture/ });
  await expect(card).toContainText("ONLINE");
  await request.post("/fixture/stop-agent", { headers: auth });
  await expect(card).toContainText("OFFLINE");
  const restarted = await request.post("/fixture/restart-agent", {
    headers: auth,
  });
  expect(restarted.status()).toBe(200);
  await expect(card).toContainText("ONLINE");
});

test("keyset paging, filters and SSE gap refresh restore the first page", async ({
  page,
  request,
}) => {
  for (let index = 0; index < 12; index++) {
    const setup = await request.post("/api/v1/enrollment-tokens", {
      headers: auth,
      data: { name: `UI page ${index}` },
    });
    const { token } = await setup.json();
    await request.post("/agent/v1/enroll", { data: { token } });
  }
  await login(page, request);
  await page.getByRole("button", { name: "장비 Devices", exact: true }).click();
  const controls = page.getByLabel("devices 목록 탐색");
  await page.getByLabel("devices 페이지 크기").selectOption("10");
  await expect(page.locator(".device-card")).toHaveCount(10);
  const first = await page.locator(".device-card code").allTextContents();
  await controls.getByRole("button", { name: "다음", exact: true }).click();
  await expect(page.locator(".device-card")).toHaveCount(3);
  const second = await page.locator(".device-card code").allTextContents();
  expect(first.some((value) => second.includes(value))).toBeFalsy();
  await request.post("/fixture/event-gap", { headers: auth });
  await expect(
    page.getByRole("status").filter({ hasText: "이벤트 기록 범위" }),
  ).toBeVisible();
  await expect(controls).toContainText("페이지 1");
  await page.getByLabel("devices 상태 필터").selectOption("ONLINE");
  await expect(page.locator(".device-card")).toHaveCount(1);
  await expect(page.locator(".device-card")).toContainText("Console fixture");
});

test("Artifact preview renders hostile text safely and PNG pixels, download remains attachment", async ({
  page,
  request,
}) => {
  async function upload(bytes: Buffer, media: string) {
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    const created = await request.post("/api/v1/artifact-transfers", {
      headers: auth,
      data: {
        device_id: device,
        size_bytes: bytes.length,
        sha256,
        media_type: media,
      },
    });
    const transfer = await created.json();
    const scope = { Authorization: `Bearer ${transfer.credential}` };
    await request.put(`/api/v1/artifact-transfers/${transfer.id}/content`, {
      headers: {
        ...scope,
        "Content-Range": `bytes 0-${bytes.length - 1}/${bytes.length}`,
        "X-Chunk-SHA256": sha256,
      },
      data: bytes,
    });
    const done = await request.post(
      `/api/v1/artifact-transfers/${transfer.id}/complete`,
      { headers: scope, data: { size_bytes: bytes.length, sha256 } },
    );
    expect(done.ok()).toBeTruthy();
    return (await done.json()).id as string;
  }
  const hostile = Buffer.from(
    "<script>window.__artifactExecuted=true</script><svg onload='alert(1)'></svg>" +
      "x".repeat(70000),
  );
  const textId = await upload(hostile, "text/plain");
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1EAAAAASUVORK5CYII=",
    "base64",
  );
  const imageId = await upload(png, "image/png");
  await login(page, request);
  await page
    .getByRole("button", { name: "파일 Artifacts", exact: true })
    .click();
  const textRow = page.locator("tbody tr").filter({ hasText: textId });
  await textRow.getByRole("button", { name: "미리보기", exact: true }).click();
  const preview = page.getByRole("region", { name: "Artifact 미리보기" });
  await expect(preview.locator("pre")).toContainText("<script>");
  await expect(preview).toContainText("앞부분 64 KiB");
  expect(
    await page.evaluate(() => Reflect.get(window, "__artifactExecuted")),
  ).toBeUndefined();
  const body = await request.get(`/api/v1/artifacts/${textId}/content`, {
    headers: auth,
  });
  expect(body.headers()["content-disposition"]).toContain("attachment");
  expect(body.headers()["content-type"]).toContain("application/octet-stream");
  await preview.getByRole("button", { name: "미리보기 닫기" }).click();
  await page
    .locator("tbody tr")
    .filter({ hasText: imageId })
    .getByRole("button", { name: "미리보기", exact: true })
    .click();
  const image = preview.getByAltText("검증된 Artifact 이미지");
  await expect(image).toBeVisible();
  await expect
    .poll(() =>
      image.evaluate((element) => (element as HTMLImageElement).naturalWidth),
    )
    .toBe(1);
  await page.screenshot({
    path: "../../dist/console-artifacts.png",
    fullPage: true,
  });
});

test("expired approvals and outcomes show investigation states without executing again", async ({
  page,
  request,
}) => {
  const pending = await request.post("/api/v1/operations", {
    headers: auth,
    data: {
      device_id: device,
      operation: "filesystem.write",
      payload: { path: "must-not-execute-expired", content: "once" },
      execution_profile_id: "standard",
      idempotency_key: "ui-expired-approval",
    },
  });
  const approvalId = (await pending.json()).error.details.approval_id;
  await login(page, request);
  await page
    .getByRole("button", { name: "승인 Approvals", exact: true })
    .click();
  const row = page
    .locator(".approval")
    .filter({ hasText: "must-not-execute-expired" });
  await expect(row).toContainText("PENDING");
  await request.post("/fixture/approval-expire", {
    headers: auth,
    data: { id: approvalId },
  });
  await expect(row).toContainText("승인이 만료되었습니다");
  expect(
    await row
      .getByRole("button", { name: "Approve once", exact: true })
      .count(),
  ).toBe(0);
  await page.getByRole("button", { name: "작업 Jobs", exact: true }).click();
  const completed = page
    .locator("tbody tr")
    .filter({ hasText: "COMPLETED" })
    .first();
  await completed.getByRole("button", { name: "조사", exact: true }).click();
  await request.post("/fixture/expire-outcomes", { headers: auth });
  await expect(page.getByRole("alert")).toContainText("결과가 만료되었습니다");
  const check = await request.post("/api/v1/operations", {
    headers: auth,
    data: {
      device_id: device,
      operation: "filesystem.stat",
      payload: { path: "must-not-execute-expired" },
    },
  });
  expect((await check.json()).error.code).toBe("PATH_NOT_FOUND");
});

test("cookie terminal stream displays updates, resumes cursor and logout releases subscription", async ({
  page,
  request,
}) => {
  const opened = await request.post("/api/v1/operations", {
    headers: auth,
    data: {
      device_id: device,
      operation: "terminal.open",
      payload: {
        argv: [
          python,
          "-u",
          "-c",
          "import time; print('push-ready'); [(time.sleep(0.2),print('push-'+str(n))) for n in range(100)]; time.sleep(90)",
        ],
      },
      execution_profile_id: "trusted_personal",
      idempotency_key: "ui-push-fixture",
    },
  });
  const handle = (await opened.json()).result.handle_id;
  await login(page, request);
  await page
    .getByRole("button", { name: "세션 Sessions", exact: true })
    .click();
  await page.getByLabel("장비", { exact: true }).selectOption(device);
  await page.getByLabel("Terminal Handle ID").fill(handle);
  await page.getByRole("button", { name: "실시간 구독", exact: true }).click();
  const viewer = page.getByRole("region", { name: "Terminal 실시간 viewer" });
  await expect(viewer.getByLabel("Terminal 실시간 출력")).toContainText(
    "push-ready",
  );
  await expect(viewer.getByLabel("Terminal 실시간 출력")).toContainText(
    "push-1",
  );
  await viewer.getByRole("button", { name: "구독 중지", exact: true }).click();
  await viewer
    .getByRole("button", { name: "마지막 cursor 사용", exact: true })
    .click();
  const cursor = await page.getByLabel("Byte cursor").inputValue();
  expect(BigInt(cursor) > 0n).toBeTruthy();
  await viewer
    .getByRole("button", { name: "실시간 구독", exact: true })
    .click();
  await expect(viewer.getByLabel("Terminal 실시간 출력")).toContainText(
    "push-",
  );
  expect(
    await viewer.getByLabel("Terminal 실시간 출력").textContent(),
  ).not.toContain("push-ready");
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page.getByLabel("일회성 setup secret")).toBeVisible();
  await request.post("/api/v1/operations", {
    headers: auth,
    data: {
      device_id: device,
      operation: "terminal.close",
      payload: { handle_id: handle },
      execution_profile_id: "trusted_personal",
      idempotency_key: "ui-push-close",
    },
  });
});

test("silent SSE stream becomes stale after watchdog interval", async ({
  page,
  request,
}) => {
  await page.addInitScript(() => {
    const Native = window.EventSource;
    class DroppingEvents extends Native {
      override addEventListener(
        type: string,
        listener: EventListenerOrEventListenerObject | null,
        options?: boolean | AddEventListenerOptions,
      ) {
        const guarded: EventListener = (event) => {
          if (Reflect.get(window, "__dropSSE")) return;
          if (typeof listener === "function") listener.call(this, event);
          else listener?.handleEvent(event);
        };
        super.addEventListener(type, guarded, options);
      }
    }
    window.EventSource = DroppingEvents;
  });
  await page.clock.install();
  await login(page, request);
  await page.evaluate(() => Reflect.set(window, "__dropSSE", true));
  await page.clock.fastForward(20001);
  await expect(
    page.getByRole("status").filter({ hasText: "재연결 중" }),
  ).toBeVisible();
  await expect(page.locator(".device-card").first()).toContainText("확인 필요");
});

test("files and commands remain usable with an unavailable optional browser", async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  const restarted = await request.post("/fixture/degraded-agent", {
    headers: auth,
  });
  expect(restarted.status()).toBe(200);
  try {
    await login(page, request);
    await page
      .getByRole("button", { name: "장비 Devices", exact: true })
      .click();
    const card = page.getByRole("button", { name: /Console fixture/ });
    await expect(card).toContainText("ONLINE");
    await card.click();
    await expect(
      page.getByText("BROWSER_RUNTIME_UNAVAILABLE", { exact: false }),
    ).toBeVisible();
    const details = page.getByRole("region", {
      name: "선택한 PC 정보",
      exact: true,
    });
    await expect(details).toContainText(process.env.RACP_TEST_WORKSPACE!);
    await expect(details).toContainText("trusted_personal");
    await page.screenshot({
      path: "../../dist/console-connect.png",
      fullPage: true,
    });
    await shell(page, "print('CORE_WITHOUT_BROWSER')");
    await expect(
      page.getByRole("region", { name: "작업 조사", exact: true }),
    ).toContainText("CORE_WITHOUT_BROWSER");
  } finally {
    const restored = await request.post("/fixture/restart-agent", {
      headers: auth,
    });
    expect(restored.status()).toBe(200);
  }
});

test("PC enrollment ticket stays in memory and is single-use", async ({
  page,
  request,
}) => {
  await page.clock.install();
  await login(page, request);
  await page.getByRole("button", { name: "장비 Devices", exact: true }).click();
  await page.getByLabel("PC 이름", { exact: true }).fill("UI new PC");
  await page
    .getByRole("button", { name: "일회용 등록 토큰 만들기", exact: true })
    .click();
  const guidance = page.getByRole("region", {
    name: "PC 등록 안내",
    exact: true,
  });
  await expect(guidance).toBeVisible();
  const ticket = await page
    .getByLabel("일회용 등록 토큰", { exact: true })
    .inputValue();
  expect(ticket.length).toBeGreaterThan(20);
  const downloadEvent = page.waitForEvent("download");
  await guidance
    .getByRole("button", { name: "연결 파일 다운로드", exact: true })
    .click();
  const connectionDownload = await downloadEvent;
  expect(connectionDownload.suggestedFilename()).toBe("RACP-connection.racp");
  const file = await connectionDownload.path();
  const offer = JSON.parse(
    await (await import("node:fs/promises")).readFile(file!, "utf8"),
  );
  expect(offer.version).toBe(1);
  expect(offer.token).toBe(ticket);
  expect(offer.gateway).toBe(new URL(page.url()).origin);
  expect(offer.ca_pem).toBeNull();
  const command = await guidance.locator("pre").innerText();
  expect(command).toContain("racp-connect --gateway");
  expect(command).not.toContain(ticket);
  expect(page.url()).not.toContain(ticket);
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain(ticket);
  await expect(guidance).toContainText("읽기 전용");
  const enrolled = await request.post("/agent/v1/enroll", {
    data: { token: ticket },
  });
  expect(enrolled.status()).toBe(200);
  expect(
    (
      await request.post("/agent/v1/enroll", { data: { token: ticket } })
    ).status(),
  ).toBe(401);
  await page
    .getByRole("button", { name: "등록 안내 닫기", exact: true })
    .click();
  await expect(guidance).toHaveCount(0);
  await page
    .getByRole("button", { name: "일회용 등록 토큰 만들기", exact: true })
    .click();
  await expect(guidance).toBeVisible();
  await page.clock.fastForward(601000);
  await expect(guidance).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: "등록 토큰이 만료" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "일회용 등록 토큰 만들기", exact: true })
    .click();
  await expect(guidance).toBeVisible();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(
    page.getByLabel("일회용 등록 토큰", { exact: true }),
  ).toHaveCount(0);
});

test("selected workspace starts the real command in its approved folder", async ({
  page,
  request,
}) => {
  await login(page, request);
  await page.getByRole("button", { name: "장비 Devices", exact: true }).click();
  await page.getByRole("button", { name: /Console fixture/ }).click();
  const details = page.getByRole("region", {
    name: "선택한 PC 정보",
    exact: true,
  });
  await expect(details).toContainText(process.env.RACP_TEST_SECOND_WORKSPACE!);
  await page.getByLabel("작업 폴더", { exact: true }).selectOption("docs");
  await shell(
    page,
    "from pathlib import Path; Path('ui-workspace.txt').write_text('UI-DOCS'); print('UI-DOCS', Path.cwd())",
  );
  await expect(
    page.getByRole("region", { name: "작업 조사", exact: true }),
  ).toContainText("UI-DOCS");
  await expect(
    page.getByRole("region", { name: "작업 조사", exact: true }),
  ).toContainText("두 번째 자료");
  const read = async (workspace_id: string) =>
    (
      await request.post("/api/v1/operations", {
        headers: auth,
        data: {
          device_id: device,
          workspace_id,
          operation: "filesystem.read",
          payload: { path: "ui-workspace.txt" },
        },
      })
    ).json();
  expect((await read("docs")).result.text).toBe("UI-DOCS");
  expect((await read("default")).error.code).toBe("PATH_NOT_FOUND");
  await page.screenshot({
    path: "../../dist/console-workspaces.png",
    fullPage: true,
  });
});
