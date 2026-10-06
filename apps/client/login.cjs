const path = require("node:path");
const LOGIN_NAME = "app.racp.client";
const LOGIN_ARGS = ["racp-background-agent"];

function supported(app, platform = process.platform) {
    return platform === "win32" && app.isPackaged;
}

function loginSettings(
    app,
    executable = process.execPath,
    platform = process.platform,
) {
    if (!supported(app, platform))
        return { available: false, registered: false, enabled: false };
    const settings = app.getLoginItemSettings({
        path: executable,
        args: LOGIN_ARGS,
    });
    const inventory = app.getLoginItemSettings({ path: executable });
    const entry = inventory.launchItems?.find(
        (item) =>
            item.name === LOGIN_NAME &&
            item.scope === "user" &&
            path.resolve(item.path).toLowerCase() ===
                path.resolve(executable).toLowerCase() &&
            JSON.stringify(item.args) === JSON.stringify(LOGIN_ARGS),
    );
    return {
        available: true,
        registered: Boolean(entry && settings.openAtLogin),
        enabled: Boolean(entry?.enabled && settings.openAtLogin),
    };
}

async function setLogin(
    app,
    enabled,
    backend,
    executable = process.execPath,
    platform = process.platform,
) {
    if (typeof enabled !== "boolean" || !supported(app, platform))
        throw new Error(
            "이 설정은 설치된 Windows 클라이언트에서만 사용할 수 있습니다.",
        );
    if (enabled && !(await backend("info")).configured)
        throw new Error("PC 등록을 완료한 뒤 자동 연결을 설정해 주세요.");
    app.setLoginItemSettings({
        name: LOGIN_NAME,
        path: executable,
        args: LOGIN_ARGS,
        openAtLogin: enabled,
        enabled,
    });
    const result = loginSettings(app, executable, platform);
    if (result.registered !== enabled || (enabled && !result.enabled))
        throw new Error(
            "Windows 자동 시작 설정을 확인할 수 없습니다. 시작 앱 설정을 확인해 주세요.",
        );
    return result;
}

async function runLoginAgent(
    app,
    backend,
    executable = process.execPath,
    platform = process.platform,
) {
    if (!loginSettings(app, executable, platform).enabled)
        return { started: false };
    if (!(await backend("info")).configured)
        throw new Error("등록 설정을 확인할 수 없습니다.");
    const result = await backend("start");
    if (result.state !== "RUNNING")
        throw new Error("로그인 후 Agent 시작을 확인할 수 없습니다.");
    return { started: true };
}

module.exports = {
    LOGIN_NAME,
    LOGIN_ARGS,
    loginSettings,
    setLogin,
    runLoginAgent,
};
