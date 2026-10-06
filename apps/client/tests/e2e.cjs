const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { _electron, expect } = require("@playwright/test");
const { callBackend, runtimePath } = require("../backend.cjs");
const { runLoginAgent, LOGIN_NAME, LOGIN_ARGS } = require("../login.cjs");
let secrets = [];
async function main() {
    let input = "";
    for await (const chunk of process.stdin) input += chunk;
    const fixture = JSON.parse(input);
    secrets = [fixture.owner, fixture.token, fixture.expired_token];
    let application;
    const options = {
        env: {
            ...process.env,
            RACP_CLIENT_BACKEND: fixture.backend,
            RACP_CLIENT_TEST_STATE: fixture.state,
            RACP_CLIENT_TEST_HIDDEN: "1",
        },
    };
    if (fixture.executable) options.executablePath = fixture.executable;
    else options.args = [path.resolve(__dirname, "..")];
    async function remote(operation, payload, extra = {}) {
        const response = await fetch(fixture.gateway + "/api/v1/operations", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: "Bearer " + fixture.owner,
            },
            body: JSON.stringify({
                device_id: fixture.device,
                operation,
                payload,
                idempotency_key: require("node:crypto").randomUUID(),
                execution_profile_id: "trusted_personal",
                ...extra,
            }),
        });
        assert.equal(
            response.status,
            extra.execution_mode === "job" ? 202 : 200,
        );
        const result = await response.json();
        if (extra.execution_mode !== "job")
            assert.equal(result.state, "SUCCEEDED");
        return result;
    }
    try {
        application = await _electron.launch(options);
        const page = await application.firstWindow();
        await page.getByRole("heading", { name: "새 PC 연결" }).waitFor();
        assert.equal(
            await page.evaluate(() => typeof window.require),
            "undefined",
        );
        const preferences = await application.evaluate(({ BrowserWindow }) =>
            BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences(),
        );
        assert.equal(preferences.nodeIntegration, false);
        assert.equal(preferences.contextIsolation, true);
        assert.equal(preferences.sandbox, true);
        await application.evaluate(({ Tray }) => {
            const original = Tray.prototype.setContextMenu;
            Tray.prototype.setContextMenu = function (menu) {
                globalThis.racpAcceptanceTray = this;
                globalThis.racpAcceptanceMenu = menu;
                return original.call(this, menu);
            };
        });
        await application.evaluate(({ dialog }, config) => {
            globalThis.racpAcceptanceConnectionFile =
                config.expired_connection_path;
            dialog.showOpenDialog = async (_window, options) => ({
                canceled: false,
                filePaths: [
                    options.filters?.[0]?.extensions?.includes("racp")
                        ? globalThis.racpAcceptanceConnectionFile
                        : options.filters
                          ? config.ca
                          : config.workspace,
                ],
            });
        }, fixture);
        assert.equal(await page.getByLabel("Gateway 주소").count(), 0);
        assert.equal(await page.getByLabel("일회용 등록 토큰").count(), 0);
        await page
            .getByRole("button", { name: "연결 파일 선택", exact: true })
            .click();
        await page
            .getByRole("alert")
            .filter({ hasText: "연결 파일이 만료되었습니다" })
            .waitFor();
        await page
            .getByRole("button", { name: "직접 입력", exact: true })
            .click();
        await page.getByLabel("Gateway 주소").fill(fixture.gateway);
        await page
            .getByRole("button", { name: "폴더 선택", exact: true })
            .click();
        await page.getByLabel("실행 권한").selectOption("trusted_personal");
        await page
            .getByRole("button", { name: "인증서 선택", exact: true })
            .click();
        await page.getByLabel("일회용 등록 토큰").fill(fixture.expired_token);
        await page
            .getByRole("button", { name: "PC 등록", exact: true })
            .click();
        await page
            .getByRole("alert")
            .filter({ hasText: "등록 토큰이 거부되었습니다" })
            .waitFor();
        assert.equal(
            (await page.getByRole("alert").first().innerText()).includes(
                "Error invoking remote method",
            ),
            false,
        );
        await page.waitForFunction(
            () => document.querySelector('input[type="password"]').value === "",
        );
        assert.equal(
            (await callBackend(fixture.backend, fixture.state, "info"))
                .configured,
            false,
        );
        await page
            .getByRole("button", { name: "연결 파일로 등록", exact: true })
            .click();
        await application.evaluate((_electron, file) => {
            globalThis.racpAcceptanceConnectionFile = file;
        }, fixture.connection_path);
        await page
            .getByRole("button", { name: "연결 파일 선택", exact: true })
            .click();
        await page.getByText(fixture.gateway, { exact: true }).waitFor();
        assert.equal(
            (await page.locator("body").innerText()).includes(fixture.token),
            false,
        );
        const originalOffer = await fs.readFile(fixture.connection_path);
        await fs.appendFile(fixture.connection_path, "\n");
        await page
            .getByRole("button", { name: "PC 등록", exact: true })
            .click();
        await page
            .getByRole("alert")
            .filter({ hasText: "선택한 연결 파일이 변경되었습니다" })
            .waitFor();
        assert.equal(
            (await callBackend(fixture.backend, fixture.state, "info"))
                .configured,
            false,
        );
        await fs.writeFile(fixture.connection_path, originalOffer);
        await page
            .getByRole("button", { name: "연결 파일 선택", exact: true })
            .click();
        await page
            .getByRole("button", { name: "PC 등록", exact: true })
            .click();
        await page
            .getByRole("button", { name: "PC 설정", exact: true })
            .click();
        await page.getByRole("heading", { name: "등록한 PC" }).waitFor();
        fixture.device = (
            await page.evaluate(() => window.racpClient.info())
        ).device_id;
        const replayState = path.join(fixture.state, "consumed-token-fixture");
        await assert.rejects(
            callBackend(fixture.backend, replayState, "enroll", {
                gateway: fixture.gateway,
                workspace: fixture.workspace,
                profile: "trusted_personal",
                token: fixture.token,
                ca_file: fixture.ca,
            }),
            /등록 토큰이 거부되었습니다/,
        );
        assert.equal(
            (await callBackend(fixture.backend, replayState, "info"))
                .configured,
            false,
        );
        await fs.unlink(fixture.connection_path);
        await fs.unlink(fixture.expired_connection_path);
        assert.equal(await page.getByLabel("일회용 등록 토큰").count(), 0);
        assert.equal(
            (
                await fs.readFile(path.join(fixture.state, "credential.bin"))
            ).includes(fixture.token),
            false,
        );
        await page
            .getByRole("button", { name: "Agent 시작", exact: true })
            .click();
        await page
            .getByText("Gateway 연결됨", { exact: true })
            .waitFor({ timeout: 30000 });
        await remote("filesystem.write", {
            path: "desktop.txt",
            content: "데스크톱 원격 자료",
        });
        assert.equal(
            await fs.readFile(
                path.join(fixture.workspace, "desktop.txt"),
                "utf8",
            ),
            "데스크톱 원격 자료",
        );
        await remote("shell.exec", {
            argv: [
                runtimePath(fixture.backend),
                "-I",
                "-c",
                "from pathlib import Path; Path('executed.txt').write_text('client command OK')",
            ],
        });
        assert.equal(
            await fs.readFile(
                path.join(fixture.workspace, "executed.txt"),
                "utf8",
            ),
            "client command OK",
        );
        await page
            .getByRole("button", { name: "현황 · 최근 활동", exact: true })
            .click();
        await page
            .getByRole("log", { name: "Agent 최근 활동" })
            .getByText("filesystem.write", { exact: true })
            .first()
            .waitFor();
        const job = await remote(
            "shell.exec",
            {
                argv: [
                    runtimePath(fixture.backend),
                    "-I",
                    "-c",
                    "import os,time; from pathlib import Path; Path('tray-job.pid').write_text(str(os.getpid())); time.sleep(60)",
                ],
            },
            { execution_mode: "job" },
        );
        await page
            .locator(".operation-list")
            .getByText("shell.exec", { exact: true })
            .waitFor();
        // The journal/UI becomes RUNNING before the child executes its first
        // statement. Wait for our own process fixture's readiness receipt.
        await expect
            .poll(
                async () => {
                    try {
                        await fs.access(
                            path.join(fixture.workspace, "tray-job.pid"),
                        );
                        return true;
                    } catch {
                        return false;
                    }
                },
                { timeout: 30000 },
            )
            .toBe(true);
        const ownedPid = Number(
            await fs.readFile(
                path.join(fixture.workspace, "tray-job.pid"),
                "utf8",
            ),
        );
        await page
            .getByRole("button", { name: "상태 확인", exact: true })
            .click();
        assert.equal(
            (await page.evaluate(() => window.racpClient.overview()))
                .tray_available,
            true,
        );
        const denied = await application.evaluate(
            async ({ BrowserWindow }, preload) => {
                const extra = new BrowserWindow({
                    show: false,
                    webPreferences: {
                        preload,
                        contextIsolation: true,
                        sandbox: true,
                    },
                });
                try {
                    await extra.loadURL("racp-client://app/index.html");
                    return await extra.webContents.executeJavaScript(
                        "window.racpClient.info().then(() => false, () => true)",
                    );
                } finally {
                    extra.destroy();
                }
            },
            path.resolve(__dirname, "../preload.cjs"),
        );
        assert.equal(denied, true);
        await page.waitForFunction(
            () => !document.querySelector(".danger").disabled,
        );
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: fixture.screenshot, fullPage: true });
        await application.evaluate(({ BrowserWindow }) =>
            BrowserWindow.getAllWindows()[0].close(),
        );
        assert.equal(
            await application.evaluate(({ BrowserWindow }) =>
                BrowserWindow.getAllWindows()[0].isVisible(),
            ),
            false,
        );
        assert.equal(
            await application.evaluate(() =>
                globalThis.racpAcceptanceTray.isDestroyed(),
            ),
            false,
        );
        await application.evaluate(() =>
            globalThis.racpAcceptanceTray.emit("double-click"),
        );
        assert.equal(
            await application.evaluate(({ BrowserWindow }) =>
                BrowserWindow.getAllWindows()[0].isVisible(),
            ),
            true,
        );
        assert.equal(
            (await callBackend(fixture.backend, fixture.state, "status"))
                .connected,
            true,
        );
        await remote("filesystem.write", {
            path: "after-close.txt",
            content: "still online",
        });
        await application.evaluate(() =>
            globalThis.racpAcceptanceMenu.items
                .find((item) => item.label.startsWith("완전 종료"))
                .click(),
        );
        await application.close();
        application = null;
        assert.equal(
            (await callBackend(fixture.backend, fixture.state, "status")).state,
            "STOPPED",
        );
        assert.throws(() => process.kill(ownedPid, 0), { code: "ESRCH" });
        const fixedExecutable = runtimePath(fixture.backend);
        const registered = {
            isPackaged: true,
            getLoginItemSettings: () => ({
                openAtLogin: true,
                launchItems: [
                    {
                        name: LOGIN_NAME,
                        scope: "user",
                        path: fixedExecutable,
                        args: LOGIN_ARGS,
                        enabled: true,
                    },
                ],
            }),
        };
        const resumed = await runLoginAgent(
            registered,
            (action) => callBackend(fixture.backend, fixture.state, action),
            fixedExecutable,
            "win32",
        );
        assert.equal(resumed.started, true);
        for (let i = 0; i < 100; i++) {
            if (
                (await callBackend(fixture.backend, fixture.state, "status"))
                    .connected
            )
                break;
            await new Promise((resolve) => setTimeout(resolve, 100));
        }
        assert.equal(
            (await callBackend(fixture.backend, fixture.state, "status"))
                .connected,
            true,
        );
        await remote("filesystem.write", {
            path: "login-resumed.txt",
            content: "resumed saved Agent",
        });
        const jobResponse = await fetch(
            fixture.gateway + "/api/v1/operations/" + job.operation_id,
            { headers: { Authorization: "Bearer " + fixture.owner } },
        );
        const outcome = await jobResponse.json();
        assert.equal(outcome.state, "CANCELLED");
        assert.equal(outcome.result.cleanup_status, "complete");
        const stopped = await callBackend(
            fixture.backend,
            fixture.state,
            "stop",
        );
        assert.equal(stopped.cleanup_status, "complete");
        application = await _electron.launch(options);
        const reopened = await application.firstWindow();
        await reopened
            .getByRole("button", { name: "Agent 시작", exact: true })
            .waitFor();
        assert.equal(
            await reopened.getByRole("heading", { name: "새 PC 연결" }).count(),
            0,
        );
        await reopened
            .getByRole("button", { name: "완전 종료", exact: true })
            .click();
        await application.close();
        application = null;
        // Repair a missing external CA through the real renderer/private bridge.
        const beforeEdit = await callBackend(
            fixture.backend,
            fixture.state,
            "settings",
        );
        await fs.rename(beforeEdit.ca_file, beforeEdit.ca_file + ".held");
        try {
            application = await _electron.launch(options);
            const editPage = await application.firstWindow();
            await editPage
                .getByRole("heading", { name: "등록 정보 편집", exact: true })
                .waitFor();
            assert.equal(
                await editPage
                    .getByRole("button", { name: "등록 정보 복구", exact: true })
                    .count(),
                0,
            );
            assert.equal(
                await editPage
                    .getByRole("button", { name: "상태 확인", exact: true })
                    .count(),
                0,
            );
            assert.equal(
                await editPage
                    .getByRole("button", { name: "완전 종료", exact: true })
                    .count(),
                0,
            );
            await editPage
                .getByLabel("Gateway 주소", { exact: true })
                .fill(beforeEdit.gateway);
            await editPage
                .getByLabel("추가 CA 인증서", { exact: true })
                .fill(fixture.ca);
            await editPage
                .getByLabel("실행 권한", { exact: true })
                .selectOption("read_only");
            await editPage
                .getByRole("checkbox", { name: "이 PC의 Windows 화면 캡처·마우스·키보드 조작 허용" })
                .check();
            await editPage
                .getByRole("button", { name: "설정 저장", exact: true })
                .click();
            await editPage
                .getByRole("button", { name: "Agent 시작", exact: true })
                .waitFor();
            const edited = await callBackend(
                fixture.backend,
                fixture.state,
                "settings",
            );
            assert.equal(edited.device_id, beforeEdit.device_id);
            assert.equal(edited.profile, "read_only");
            assert.equal(edited.ca_file, fixture.ca);
            assert.equal(edited.desktop_enabled, true);
            console.log("Client E2E: desktop opt-in persisted");
            assert.equal(
                await editPage.getByLabel("일회용 등록 토큰").count(),
                0,
            );
            await editPage
                .getByRole("button", { name: "Agent 시작", exact: true })
                .click();
            await editPage
                .getByText("Gateway 연결됨", { exact: true })
                .waitFor();
            const readResult = await remote("filesystem.read", {
                path: "login-resumed.txt",
            });
            assert.equal(readResult.state, "SUCCEEDED");
            console.log("Client E2E: desktop-enabled Agent reconnected");
            await editPage
                .getByRole("button", { name: "PC 설정", exact: true })
                .click();
            await editPage
                .getByRole("button", { name: "등록 정보 편집", exact: true })
                .click();
            await editPage
                .getByRole("button", { name: "설정 저장", exact: true })
                .click();
            await editPage
                .getByRole("alert")
                .filter({ hasText: "Agent 또는 다른 설정 작업" })
                .waitFor();
            console.log("Client E2E: running Agent settings edit refused");
            await editPage.screenshot({
                path: path.join(
                    path.dirname(fixture.screenshot),
                    "client-settings-edit.png",
                ),
                fullPage: true,
            });
            await editPage
                .getByRole("button", { name: "Agent 중지", exact: true })
                .click();
            console.log("Client E2E: desktop-enabled Agent stopped");
            await editPage
                .getByRole("button", { name: "설정 저장", exact: true })
                .click();
            await editPage
                .getByRole("button", { name: "완전 종료", exact: true })
                .click();
            await application.close();
            application = null;
        } finally {
            await fs.rename(beforeEdit.ca_file + ".held", beforeEdit.ca_file);
        }
        const credentialPath = path.join(fixture.state, "credential.bin");
        const savedCredential = await fs.readFile(credentialPath);
        try {
            await fs.writeFile(
                credentialPath,
                "invalid-protected-private-credential",
            );
            application = await _electron.launch(options);
            const recovery = await application.firstWindow();
            await recovery
                .getByRole("heading", { name: "등록 정보 편집", exact: true })
                .waitFor();
            await recovery
                .getByRole("alert")
                .filter({ hasText: "저장된 PC 등록 설정을 읽을 수 없습니다" })
                .waitFor();
            assert.equal(
                await recovery
                    .getByRole("heading", { name: "새 PC 연결" })
                    .count(),
                0,
            );
            assert.equal(
                await recovery.getByLabel("일회용 등록 토큰").count(),
                0,
            );
            assert.equal(
                await fs.readFile(credentialPath, "utf8"),
                "invalid-protected-private-credential",
            );
            await application.close();
            application = null;
        } finally {
            await fs.writeFile(credentialPath, savedCredential);
        }
        console.log(
            "PASS: real HTTPS/WSS connection file, expired/changed-file rejection, safe token retry, saved registration resume, settings repair/live edit rejection, credential protection, dashboard/Tray/full exit/Job cleanup",
        );
    } finally {
        if (application) await application.close();
        await callBackend(fixture.backend, fixture.state, "stop");
    }
}
main().catch((error) => {
    let message = String(error.stack);
    for (const secret of secrets)
        message = message.replaceAll(secret, "[redacted]");
    console.error(message);
    process.exitCode = 1;
});
