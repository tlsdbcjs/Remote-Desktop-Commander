// All UI/tray exits share this graceful stop path. A failed stop keeps the app visible.
const { knownFailure } = require("./errors.cjs");
function createController({ app, window, Tray, Menu, assets, request }) {
    let quitting = false,
        busy = false,
        sampling = null,
        timer = null;
    let state = {
        status: null,
        activity: { events: [] },
        updatedAt: null,
        error: "",
    };
    let tray = null;
    function show() {
        if (!window.isDestroyed()) {
            if (window.isMinimized()) window.restore();
            window.show();
            window.focus();
        }
    }
    function menu() {
        if (!tray) return;
        const status = state.status;
        const label = !status
            ? "상태 확인 불가"
            : status.connected
              ? "Gateway 연결됨"
              : status.state === "RUNNING"
                ? "연결 중"
                : "Agent 종료됨";
        tray.setToolTip("RACP · " + label);
        tray.setImage(
            assets[
                busy || status?.active_operations > 0
                    ? "busy"
                    : status?.connected
                      ? "connected"
                      : "offline"
            ],
        );
        tray.setContextMenu(
            Menu.buildFromTemplate([
                { label: "RACP · " + label, enabled: false },
                { label: "현황 창 열기", click: show },
                { type: "separator" },
                {
                    label: "Agent 시작",
                    enabled: !busy && status?.state === "STOPPED",
                    click: () => perform("start").catch(() => {}),
                },
                {
                    label: "Agent 종료",
                    enabled: !busy && status?.state === "RUNNING",
                    click: () => perform("stop").catch(() => {}),
                },
                { type: "separator" },
                {
                    label: "완전 종료 · Agent와 앱",
                    enabled: !busy,
                    click: () => exit().catch(() => {}),
                },
            ]),
        );
    }
    async function refresh() {
        if (sampling) return sampling;
        sampling = (async () => {
            try {
                const status = await request("status");
                const activity = await request("activity");
                state = {
                    status,
                    activity,
                    updatedAt: new Date().toISOString(),
                    error: "",
                };
            } catch {
                state = {
                    ...state,
                    status: null,
                    error: "Agent 상태를 확인할 수 없습니다. 연결 설정과 Agent를 확인해 주세요.",
                };
            } finally {
                sampling = null;
                menu();
            }
            return overview();
        })();
        return sampling;
    }
    function overview() {
        return { ...state, busy, tray_available: Boolean(tray) };
    }
    async function perform(action, data) {
        if (busy) throw Error("다른 Agent 요청을 처리 중입니다.");
        busy = true;
        menu();
        try {
            if (sampling) await sampling;
            const result = await request(action, data);
            await refresh();
            return result;
        } catch (error) {
            state.error = knownFailure(error);
            show();
            throw Error(state.error);
        } finally {
            busy = false;
            menu();
        }
    }
    async function exit() {
        if (busy || quitting) return;
        busy = true;
        menu();
        try {
            const result = await request("stop");
            if (
                result.state !== "STOPPED" ||
                result.cleanup_status === "unknown"
            )
                throw Error("Unconfirmed cleanup");
            quitting = true;
            if (timer) clearInterval(timer);
            tray?.destroy();
            tray = null;
            app.quit();
        } catch {
            state.error =
                "Agent 종료를 확인하지 못했습니다. 앱을 유지합니다. 상태 확인 후 다시 종료해 주세요.";
            show();
            throw Error(state.error);
        } finally {
            busy = false;
            menu();
        }
    }
    try {
        tray = new Tray(assets.offline);
        tray.on("double-click", show);
        tray.on("click", show);
    } catch {
        state.error = "트레이 아이콘을 만들 수 없습니다. 창을 열어 둡니다.";
        show();
    }
    window.on("close", (event) => {
        if (!quitting && tray) {
            event.preventDefault();
            window.hide();
        }
    });
    app.on("before-quit", (event) => {
        if (!quitting) {
            event.preventDefault();
            exit().catch(() => {});
        }
    });
    timer = setInterval(() => refresh(), 3000);
    timer.unref?.();
    menu();
    return { refresh, overview, perform, exit, show };
}
module.exports = { createController };
