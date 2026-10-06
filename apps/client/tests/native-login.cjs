// Opt-in native Windows registry round-trip; uses a unique, test-owned value only.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { _electron } = require("@playwright/test");
async function main() {
    if (process.platform !== "win32") throw Error("Native Windows test only");
    const state = await fs.mkdtemp(path.join(os.tmpdir(), "racp-login-test-"));
    const name = "RACP-Acceptance-" + require("node:crypto").randomUUID();
    let application;
    try {
        application = await _electron.launch({
            args: [path.resolve(__dirname, "..")],
            env: {
                ...process.env,
                RACP_CLIENT_BACKEND: path.resolve("dist/client-agent-v6"),
                RACP_CLIENT_TEST_STATE: state,
                RACP_CLIENT_TEST_HIDDEN: "1",
            },
        });
        await (
            await application.firstWindow()
        )
            .getByRole("heading", { name: "새 PC 연결" })
            .waitFor();
        const result = await application.evaluate(
            async ({ app }, config) => {
                const model = process
                    .getBuiltinModule("module")
                    .createRequire(config.module)(config.module);
                app.setAppUserModelId(config.name);
                let observed;
                const adapter = {
                    isPackaged: true,
                    getLoginItemSettings: (options) => {
                        const value = app.getLoginItemSettings(options);
                        observed = {
                            ...value,
                            launchItems: value.launchItems.filter(
                                (item) => item.name === config.name,
                            ),
                            executable: process.execPath,
                        };
                        return {
                            ...value,
                            launchItems: value.launchItems.map((item) => ({
                                ...item,
                                name:
                                    item.name === config.name
                                        ? model.LOGIN_NAME
                                        : item.name,
                            })),
                        };
                    },
                    setLoginItemSettings: (options) =>
                        app.setLoginItemSettings({
                            ...options,
                            name: config.name,
                        }),
                };
                try {
                    const before = model.loginSettings(adapter);
                    const enabled = await model.setLogin(
                        adapter,
                        true,
                        async () => ({ configured: true }),
                    );
                    const native = app.getLoginItemSettings({
                        path: process.execPath,
                    });
                    const exact = native.launchItems.find(
                        (item) => item.name === config.name,
                    );
                    const disabled = await model.setLogin(
                        adapter,
                        false,
                        async () => {
                            throw Error("Disable must not consume credentials");
                        },
                    );
                    return { before, enabled, disabled, exact };
                } catch (error) {
                    const py = process
                        .getBuiltinModule("child_process")
                        .execFileSync(
                            config.python,
                            [
                                "-I",
                                "-c",
                                "import winreg,sys; k=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\\Microsoft\\Windows\\CurrentVersion\\Run'); print(winreg.QueryValueEx(k,sys.argv[1])[0])",
                                config.name,
                            ],
                            { windowsHide: true },
                        );
                    observed.rawCommand = py.toString("utf8").trim();
                    return { error: error.message, observed };
                } finally {
                    app.setLoginItemSettings({
                        name: config.name,
                        path: process.execPath,
                        args: model.LOGIN_ARGS,
                        openAtLogin: false,
                    });
                }
            },
            {
                name,
                module: path.resolve(__dirname, "../login.cjs"),
                python: path.resolve("dist/client-agent-v6/runtime/python.exe"),
            },
        );
        assert.equal(result.error, undefined, JSON.stringify(result.observed));
        assert.equal(result.before.registered, false);
        assert.equal(result.enabled.enabled, true);
        assert.equal(result.exact.name, name);
        assert.equal(result.exact.scope, "user");
        assert.deepEqual(result.exact.args, ["racp-background-agent"]);
        assert.equal(result.disabled.registered, false);
        console.log(
            "PASS: native Windows login registration and removal; unique test-owned HKCU value",
        );
    } finally {
        // Remove only the unique fixture name, including Windows' approval metadata.
        const code =
            "import winreg,sys\nfor suffix in ['Run','Explorer/StartupApproved/Run']:\n try:\n  with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Software/Microsoft/Windows/CurrentVersion/'.replace('/',chr(92))+suffix.replace('/',chr(92)),0,winreg.KEY_SET_VALUE) as key: winreg.DeleteValue(key,sys.argv[1])\n except FileNotFoundError: pass\n";
        const cleanup = spawnSync(
            path.resolve("dist/client-agent-v6/runtime/python.exe"),
            ["-I", "-c", code, name],
            { windowsHide: true },
        );
        if (application) await application.close();
        if (cleanup.status !== 0)
            throw Error("Test-owned registry cleanup needs inspection");
    }
}
main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
});
