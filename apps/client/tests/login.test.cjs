const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
    LOGIN_NAME,
    LOGIN_ARGS,
    loginSettings,
    setLogin,
    runLoginAgent,
} = require("../login.cjs");
const executable = "C:/RACP Test/RACP Client.exe";
function fixture() {
    let entries = [],
        open = false;
    const writes = [];
    return {
        writes,
        app: {
            isPackaged: true,
            getLoginItemSettings: (value) => {
                assert.equal(value.path, executable);
                if (value.args) assert.deepEqual(value.args, LOGIN_ARGS);
                return { openAtLogin: open, launchItems: entries };
            },
            setLoginItemSettings: (value) => {
                writes.push(value);
                open = value.openAtLogin;
                entries = open
                    ? [
                          {
                              name: value.name,
                              path: value.path,
                              args: value.args,
                              scope: "user",
                              enabled: value.enabled,
                          },
                      ]
                    : [];
            },
        },
        disableExternally() {
            entries[0].enabled = false;
        },
    };
}
test("only a registered Windows packaged client can enable its fixed login command", async () => {
    const f = fixture();
    await assert.rejects(
        setLogin(
            f.app,
            true,
            async () => ({ configured: false }),
            executable,
            "win32",
        ),
    );
    await assert.rejects(
        setLogin(f.app, "true", async () => ({}), executable, "win32"),
    );
    await assert.rejects(
        setLogin(f.app, true, async () => ({}), executable, "linux"),
    );
    assert.equal(f.writes.length, 0);
    const result = await setLogin(
        f.app,
        true,
        async () => ({ configured: true }),
        executable,
        "win32",
    );
    assert.equal(result.enabled, true);
    assert.deepEqual(f.writes[0], {
        name: LOGIN_NAME,
        path: executable,
        args: LOGIN_ARGS,
        openAtLogin: true,
        enabled: true,
    });
    f.disableExternally();
    assert.deepEqual(loginSettings(f.app, executable, "win32"), {
        available: true,
        registered: true,
        enabled: false,
    });
    await setLogin(
        f.app,
        false,
        () => {
            throw Error("must not require credentials to disable");
        },
        executable,
        "win32",
    );
    assert.equal(loginSettings(f.app, executable, "win32").registered, false);
});
test("logon starts only the saved Agent when enabled and requires a verified running result", async () => {
    const f = fixture(),
        calls = [];
    const backend = async (action) => {
        calls.push(action);
        return action === "info" ? { configured: true } : { state: "RUNNING" };
    };
    assert.equal(
        (await runLoginAgent(f.app, backend, executable, "win32")).started,
        false,
    );
    assert.deepEqual(calls, []);
    await setLogin(f.app, true, backend, executable, "win32");
    calls.length = 0;
    assert.equal(
        (await runLoginAgent(f.app, backend, executable, "win32")).started,
        true,
    );
    assert.deepEqual(calls, ["info", "start"]);
    await assert.rejects(
        runLoginAgent(
            f.app,
            async (action) =>
                action === "info" ? { configured: true } : { state: "STOPPED" },
            executable,
            "win32",
        ),
    );
    f.disableExternally();
    calls.length = 0;
    assert.equal(
        (await runLoginAgent(f.app, backend, executable, "win32")).started,
        false,
    );
    assert.deepEqual(calls, []);
});
test("foreign login records cannot cause a background Agent start", async () => {
    const app = {
        isPackaged: true,
        getLoginItemSettings: () => ({
            openAtLogin: true,
            launchItems: [
                {
                    name: "another-app",
                    scope: "user",
                    path: executable,
                    args: LOGIN_ARGS,
                    enabled: true,
                },
            ],
        }),
    };
    assert.equal(
        (
            await runLoginAgent(
                app,
                () => {
                    throw Error("unexpected start");
                },
                executable,
                "win32",
            )
        ).started,
        false,
    );
});
