// NSIS launchers do not forward the inspector pipe used by _electron.launch.
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs/promises");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("@playwright/test");
const { version } = require("../package.json");

async function main() {
    const executable = path.resolve(process.argv[2]);
    const profile = await fs.mkdtemp(
        path.join(os.tmpdir(), "racp-portable-smoke-"),
    );
    const server = net.createServer();
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const port = server.address().port;
    await new Promise((resolve) => server.close(resolve));
    const profileArgument = "--user-data-dir=" + profile;
    const launcher = spawn(
        executable,
        [
            profileArgument,
            "--enable-automation",
            "--remote-debugging-address=127.0.0.1",
            `--remote-debugging-port=${port}`,
        ],
        { windowsHide: true, stdio: "ignore" },
    );
    const exited = new Promise((resolve) => launcher.once("exit", resolve));
    let launchError;
    launcher.on("error", (error) => {
        launchError = error;
    });
    let browser;
    try {
        const endpoint = `http://127.0.0.1:${port}`;
        const deadline = Date.now() + 120000;
        for (;;) {
            if (launchError) throw launchError;
            if (launcher.exitCode !== null)
                throw Error("Portable launcher exited before readiness");
            try {
                const response = await fetch(endpoint + "/json/version", {
                    signal: AbortSignal.timeout(1000),
                });
                if (response.ok) break;
            } catch {}
            if (Date.now() > deadline)
                throw Error("Portable Chromium endpoint did not become ready");
            await new Promise((resolve) => setTimeout(resolve, 250));
        }
        browser = await chromium.connectOverCDP(endpoint);
        const session = await browser.newBrowserCDPSession();
        const command = await session.send("Browser.getBrowserCommandLine");
        assert.ok(
            command.arguments.includes(profileArgument),
            "Portable profile is not isolated",
        );
        const page = browser.contexts()[0].pages()[0];
        await page
            .getByRole("heading", { name: "새 PC 연결", exact: true })
            .waitFor();
        const permission = page.getByRole("checkbox", {
            name: "이 PC의 Windows 화면 캡처·마우스·키보드 조작 허용",
        });
        assert.equal(await permission.count(), 1);
        assert.equal(await permission.isChecked(), false);
        assert.equal(
            await page.locator(".version-badge").innerText(),
            "v" + version,
        );
        const info = await page.evaluate(() => window.racpClient.info());
        assert.equal(info.configured, false);
        assert.equal(info.desktop_supported, true);
        assert.ok(info.execution_identity);
        await page.evaluate(() => {
            window.racpClient.exit().catch(() => {});
        });
        await Promise.race([
            exited,
            new Promise((resolve) => setTimeout(resolve, 15000)),
        ]);
        assert.notEqual(
            launcher.exitCode,
            null,
            "Portable full exit was not confirmed",
        );
        console.log(
            `PASS: portable ${version} launcher, isolated profile, version, native Agent bridge and full exit`,
        );
    } finally {
        if (browser) await browser.close().catch(() => {});
        if (launcher.pid && launcher.exitCode === null) {
            // Retire only the still-owned launcher and its descendants, after checking its executable.
            const cleanup = spawnSync(
                "uv",
                [
                    "run",
                    "python",
                    "-c",
                    [
                        "import psutil,sys",
                        "from pathlib import Path",
                        "try:",
                        " p=psutil.Process(int(sys.argv[1]))",
                        " assert Path(p.exe()).resolve()==Path(sys.argv[2]).resolve()",
                        " owned=p.children(recursive=True)+[p]",
                        " for child in reversed(owned):",
                        "  try: child.terminate()",
                        "  except psutil.NoSuchProcess: pass",
                        " _,alive=psutil.wait_procs(owned,timeout=5)",
                        " for child in alive:",
                        "  try: child.kill()",
                        "  except psutil.NoSuchProcess: pass",
                        "except psutil.NoSuchProcess: pass",
                    ].join("\n"),
                    String(launcher.pid),
                    executable,
                ],
                {
                    windowsHide: true,
                    encoding: "utf8",
                    timeout: 30000,
                },
            );
            assert.equal(cleanup.status, 0, cleanup.stderr);
        }
        assert.equal(path.dirname(profile), path.resolve(os.tmpdir()));
        assert.ok(path.basename(profile).startsWith("racp-portable-smoke-"));
        await fs.rm(profile, { recursive: true, force: true });
    }
}
main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
});
