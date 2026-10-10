// Source-only Client permission acceptance. Starts Vite without production build.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const net = require("node:net");
const { spawn } = require("node:child_process");
const { chromium, expect } = require("@playwright/test");

async function main() {
    const root = path.resolve(__dirname, "..");
    const reservation = net.createServer();
    await new Promise((resolve) => reservation.listen(0, "127.0.0.1", resolve));
    const port = reservation.address().port;
    await new Promise((resolve) => reservation.close(resolve));
    const server = spawn(
        process.execPath,
        [
            path.join(root, "node_modules/vite/bin/vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            String(port),
            "--strictPort",
        ],
        {
            cwd: root,
            windowsHide: true,
            stdio: "ignore",
        },
    );
    let browser;
    const destination = path.resolve(root, process.argv[2] || "../../dist/acceptance");
    await fs.mkdir(destination, { recursive: true });
    const url = `http://127.0.0.1:${port}`;
    try {
        for (let i = 0; i < 150; i++) {
            if (
                await fetch(url)
                    .then((r) => r.ok)
                    .catch(() => false)
            )
                break;
            if (server.exitCode !== null) throw Error("Source server exited");
            await new Promise((resolve) => setTimeout(resolve, 200));
        }
        browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({
            viewport: { width: 1300, height: 1000 },
        });
        await context.addInitScript(() => {
            const restored = JSON.parse(
                sessionStorage.getItem("permission-fixture") || "null",
            );
            const base = {
                configured: !!restored,
                execution_identity: "UI own fixture",
                ...(restored?.editor || {}),
            };
            window.acceptance = restored || { saved: [], failSave: false };
            const overview = () => ({
                status: { state: "STOPPED", connected: false },
                activity: { events: [] },
                updatedAt: null,
                error: "",
                busy: false,
                tray_available: true,
            });
            window.racpClient = {
                info: async () => base,
                overview: async () => overview(),
                refresh: async () => overview(),
                loginSettings: async () => ({
                    available: false,
                    registered: false,
                    enabled: false,
                }),
                folder: async () => "C:/Lab/workspace",
                ca: async () => "",
                stop: async () => ({}),
                connection: async () => ({
                    gateway: "https://gateway.example",
                    expires_at: "2099-01-01T00:00:00Z",
                    ca_sha256: null,
                }),
                enroll: async (value) => {
                    window.acceptance.saved.push(value);
                    return base;
                },
                enrollConnection: async (value) => {
                    window.acceptance.saved.push(value);
                    return base;
                },
                settings: async () => window.acceptance.editor,
                updateSettings: async (value) => {
                    if (window.acceptance.failSave)
                        throw Error("fixture rejected save");
                    window.acceptance.saved.push(value);
                    return base;
                },
            };
        });
        const page = await context.newPage();
        await page.goto(url);
        await page.getByRole("heading", { name: "세부 권한" }).waitFor();
        const category = page.getByRole("switch", { name: "파일 보기 사용" });
        const read = page.getByRole("checkbox", {
            name: "텍스트 구간 읽기 (files.read.text)",
            exact: true,
        });
        await expect(read).toBeChecked();
        await category.uncheck();
        await expect(read).toBeChecked();
        await expect(read).toBeDisabled();
        await category.check();
        await expect(read).toBeEnabled();
        await read.uncheck();
        await page
            .getByRole("button", { name: "폴더 선택", exact: true })
            .click();
        await page
            .getByRole("button", { name: "연결 파일 선택", exact: true })
            .click();
        await page
            .getByRole("button", { name: "PC 등록", exact: true })
            .click();
        await expect
            .poll(() => page.evaluate(() => window.acceptance.saved.length))
            .toBe(1);
        const imported = await page.evaluate(() => window.acceptance.saved[0]);
        assert.equal(imported.permissions.grants["files.read.text"], "deny");
        await page
            .getByRole("button", { name: "직접 입력", exact: true })
            .click();
        await page.getByLabel("Gateway 주소").fill("https://gateway.example");
        await page
            .getByLabel("일회용 등록 토큰")
            .fill("fixture-one-use-enrollment");
        await page
            .getByRole("button", { name: "PC 등록", exact: true })
            .click();
        await expect
            .poll(() => page.evaluate(() => window.acceptance.saved.length))
            .toBe(2);
        const manual = await page.evaluate(() => window.acceptance.saved[1]);
        assert.deepEqual(manual.permissions, imported.permissions);
        await page.evaluate((value) => {
            window.acceptance.editor = {
                ...value,
                device_id: "dev_ui_fixture",
                revision: "a".repeat(64),
            };
            sessionStorage.setItem(
                "permission-fixture",
                JSON.stringify(window.acceptance),
            );
        }, manual);
        await page.reload();
        await page
            .getByRole("button", { name: "PC 설정", exact: true })
            .click();
        await page
            .getByRole("button", { name: "등록 정보 편집", exact: true })
            .click();
        await page.getByRole("heading", { name: "세부 권한" }).waitFor();
        await expect(read).not.toBeChecked();
        await read.check();
        await page.evaluate(() => {
            window.acceptance.failSave = true;
        });
        await page
            .getByRole("button", { name: "설정 저장", exact: true })
            .click();
        await expect(page.getByRole("alert").last()).toBeVisible();
        await expect(read).toBeChecked();
        await page.screenshot({
            path: path.join(destination, "agent-permissions-source-ui.png"),
            fullPage: true,
        });
        await page.evaluate(() => {
            window.acceptance.failSave = false;
        });
        await page
            .getByRole("button", { name: "설정 저장", exact: true })
            .click();
        await expect
            .poll(() => page.evaluate(() => window.acceptance.saved.length))
            .toBe(3);
        const edited = await page.evaluate(() => window.acceptance.saved[2]);
        assert.equal(edited.permissions.grants["files.read.text"], "allow");
        const report = {
            status: "PASS",
            source_only: true,
            routes: ["connection", "manual", "settings"],
            checks: [
                "parent-deny-preserves-leaves",
                "identical-enrollment-payloads",
                "save-failure-preserves-selection",
            ],
        };
        await fs.writeFile(
            path.join(destination, "agent-permissions-source-ui.json"),
            JSON.stringify(report, null, 2),
        );
        console.log(JSON.stringify(report));
    } finally {
        if (browser) await browser.close();
        if (server.exitCode === null) server.kill();
        await new Promise((resolve) =>
            server.exitCode !== null ? resolve() : server.once("exit", resolve),
        );
    }
}
main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
