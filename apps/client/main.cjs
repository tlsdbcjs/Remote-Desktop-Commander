const {
    app,
    BrowserWindow,
    ipcMain,
    dialog,
    protocol,
    session,
    Tray,
    Menu,
} = require("electron");
const path = require("node:path");
const fs = require("node:fs/promises");
const { callBackend } = require("./backend.cjs");
const { loginSettings, setLogin, runLoginAgent } = require("./login.cjs");
const { createController } = require("./controller.cjs");
const { failureMessage } = require("./errors.cjs");
protocol.registerSchemesAsPrivileged([
    { scheme: "racp-client", privileges: { standard: true, secure: true } },
]);
let window;
if (!app.isPackaged && process.env.RACP_CLIENT_TEST_STATE) {
    const testData = path.join(
        path.resolve(process.env.RACP_CLIENT_TEST_STATE),
        "electron",
    );
    require("node:fs").mkdirSync(testData, { recursive: true });
    app.setPath("userData", testData);
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
    app.on("second-instance", () => {
        window?.show();
        window?.focus();
    });
    app.whenReady().then(async () => {
        if (process.platform === "win32")
            app.setAppUserModelId("app.racp.client");
        const renderer = path.join(app.getAppPath(), "renderer-dist");
        const backend = app.isPackaged
            ? path.join(process.resourcesPath, "agent")
            : process.env.RACP_CLIENT_BACKEND;
        const stateDir =
            !app.isPackaged && process.env.RACP_CLIENT_TEST_STATE
                ? process.env.RACP_CLIENT_TEST_STATE
                : path.join(app.getPath("userData"), "agent");
        const request = (action, data) => {
            if (!backend)
                throw new Error("Bundled Agent runtime is unavailable");
            return callBackend(backend, stateDir, action, data);
        };
        let loginLaunch = false;
        if (process.argv.includes("racp-background-agent")) {
            try {
                loginLaunch = (await runLoginAgent(app, request)).started;
                if (!loginLaunch) {
                    app.quit();
                    return;
                }
            } catch {
                // Show the normal local UI to allow recovery; never log credentials.
            }
        }
        protocol.handle("racp-client", async (request) => {
            try {
                const url = new URL(request.url);
                const file = path.resolve(
                    renderer,
                    "." + decodeURIComponent(url.pathname),
                );
                if (
                    url.hostname !== "app" ||
                    !file.startsWith(renderer + path.sep)
                )
                    return new Response("Denied", { status: 403 });
                const mime = {
                    ".html": "text/html",
                    ".js": "text/javascript",
                    ".css": "text/css",
                }[path.extname(file)];
                if (!mime) return new Response("Not found", { status: 404 });
                return new Response(await fs.readFile(file), {
                    headers: {
                        "Content-Type": mime + "; charset=utf-8",
                        "Content-Security-Policy":
                            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; img-src 'self' data:",
                    },
                });
            } catch {
                return new Response("Not found", { status: 404 });
            }
        });
        session.defaultSession.setPermissionRequestHandler(
            (_web, _permission, callback) => callback(false),
        );
        session.defaultSession.setPermissionCheckHandler(() => false);
        window = new BrowserWindow({
            width: 860,
            height: 760,
            minWidth: 620,
            minHeight: 620,
            show:
                !loginLaunch &&
                (app.isPackaged || process.env.RACP_CLIENT_TEST_HIDDEN !== "1"),
            title: "RACP Client",
            icon: path.join(__dirname, "assets/app.ico"),
            webPreferences: {
                preload: path.join(__dirname, "preload.cjs"),
                nodeIntegration: false,
                contextIsolation: true,
                sandbox: true,
                webSecurity: true,
            },
        });
        const controller = createController({
            app,
            window,
            Tray,
            Menu,
            request,
            assets: Object.fromEntries(
                ["connected", "offline", "busy"].map((name) => [
                    name,
                    path.join(__dirname, "assets", name + ".ico"),
                ]),
            ),
        });
        window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
        window.webContents.on("will-navigate", (event) =>
            event.preventDefault(),
        );
        function trusted(event) {
            if (
                event.sender !== window.webContents ||
                event.senderFrame !== window.webContents.mainFrame ||
                event.senderFrame.url !== "racp-client://app/index.html"
            )
                throw new Error("Untrusted client frame");
        }
        let selectedConnection = null;
        ipcMain.handle("client:connection-file", async (event) => {
            trusted(event);
            selectedConnection = null;
            const selected = await dialog.showOpenDialog(window, {
                properties: ["openFile"],
                filters: [{ name: "RACP connection", extensions: ["racp"] }],
            });
            if (selected.canceled) return null;
            const preview = await request("inspect_connection", {
                path: selected.filePaths[0],
            });
            selectedConnection = {
                path: selected.filePaths[0],
                digest: preview.file_sha256,
            };
            return {
                gateway: preview.gateway,
                expires_at: preview.expires_at,
                ca_sha256: preview.ca_sha256,
            };
        });
        ipcMain.handle("client:enroll-connection", async (event, data) => {
            trusted(event);
            if (!selectedConnection)
                throw Error(failureMessage("CONNECTION_FILE_REQUIRED"));
            const selection = selectedConnection;
            try {
                return await controller.perform("enroll_connection", {
                    path: selection.path,
                    file_sha256: selection.digest,
                    workspace: data?.workspace,
                    profile: data?.profile ?? "read_only",
                    allowed_workspaces: data?.allowed_workspaces ?? [],
                    desktop_enabled: data?.desktop_enabled ?? false,
                    permissions: data?.permissions,
                });
            } finally {
                selectedConnection = null;
            }
        });
        for (const action of [
            "info",
            "settings",
            "update_settings",
            "enroll",
            "start",
            "status",
            "stop",
        ]) {
            ipcMain.handle("client:" + action, async (event, data) => {
                trusted(event);
                if (!backend)
                    throw new Error("Bundled Agent runtime is unavailable");
                return ["enroll", "update_settings", "start", "stop"].includes(
                    action,
                )
                    ? controller.perform(
                          action,
                          ["enroll", "update_settings"].includes(action)
                              ? data
                              : {},
                      )
                    : request(action);
            });
        }
        ipcMain.handle("client:overview", (event) => {
            trusted(event);
            return controller.overview();
        });
        ipcMain.handle("client:refresh", async (event) => {
            trusted(event);
            return controller.refresh();
        });
        ipcMain.handle("client:exit", async (event) => {
            trusted(event);
            await controller.exit();
        });
        ipcMain.handle("client:login-settings", (event) => {
            trusted(event);
            return loginSettings(app);
        });
        ipcMain.handle("client:set-login", async (event, enabled) => {
            trusted(event);
            return setLogin(app, enabled, request);
        });
        ipcMain.handle("client:folder", async (event) => {
            trusted(event);
            const selected = await dialog.showOpenDialog(window, {
                properties: ["openDirectory"],
            });
            return selected.canceled ? "" : selected.filePaths[0];
        });
        ipcMain.handle("client:ca", async (event) => {
            trusted(event);
            const selected = await dialog.showOpenDialog(window, {
                properties: ["openFile"],
                filters: [
                    { name: "CA certificate", extensions: ["pem", "crt"] },
                ],
            });
            return selected.canceled ? "" : selected.filePaths[0];
        });
        await window.loadURL("racp-client://app/index.html");
        await controller.refresh();
    });
    app.on("window-all-closed", () => app.quit());
}
