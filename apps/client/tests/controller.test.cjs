const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createController } = require("../controller.cjs");
const { failureMessage, knownFailure } = require("../errors.cjs");
function fixture(request) {
    const app = new EventEmitter(),
        window = new EventEmitter();
    let visible = true,
        menu,
        destroyed = false,
        quits = 0;
    window.isDestroyed = () => false;
    window.isMinimized = () => false;
    window.show = () => {
        visible = true;
    };
    window.hide = () => {
        visible = false;
    };
    window.focus = () => {};
    app.quit = () => {
        const event = {
            preventDefault() {
                this.prevented = true;
            },
        };
        app.emit("before-quit", event);
        if (!event.prevented) quits++;
    };
    class Tray extends EventEmitter {
        setToolTip() {}
        setImage() {}
        setContextMenu(value) {
            menu = value;
        }
        destroy() {
            destroyed = true;
        }
    }
    const controller = createController({
        app,
        window,
        Tray,
        Menu: { buildFromTemplate: (value) => value },
        assets: { offline: "offline", connected: "connected", busy: "busy" },
        request,
    });
    return {
        controller,
        window,
        app,
        get visible() {
            return visible;
        },
        get menu() {
            return menu;
        },
        get destroyed() {
            return destroyed;
        },
        get quits() {
            return quits;
        },
    };
}
test("close hides in tray and menu reopens; full exit waits for confirmed cleanup", async () => {
    let release;
    const stopped = new Promise((resolve) => {
        release = resolve;
    });
    const f = fixture((action) =>
        action === "stop"
            ? stopped
            : Promise.resolve(
                  action === "status"
                      ? { state: "RUNNING", connected: true }
                      : { events: [] },
              ),
    );
    await f.controller.refresh();
    let prevented = false;
    f.window.emit("close", {
        preventDefault() {
            prevented = true;
        },
    });
    assert.equal(prevented, true);
    assert.equal(f.visible, false);
    f.menu.find((item) => item.label === "현황 창 열기").click();
    assert.equal(f.visible, true);
    const exiting = f.controller.exit();
    assert.equal(f.quits, 0);
    assert.equal(f.destroyed, false);
    release({ state: "STOPPED", cleanup_status: "complete" });
    await exiting;
    assert.equal(f.quits, 1);
    assert.equal(f.destroyed, true);
});
test("unconfirmed Agent cleanup keeps tray and UI alive, and retry can finish", async () => {
    let result = { state: "STOPPED", cleanup_status: "unknown" };
    const f = fixture(async (action) =>
        action === "stop" ? result : { events: [] },
    );
    await assert.rejects(f.controller.exit(), /종료를 확인/);
    assert.equal(f.quits, 0);
    assert.equal(f.destroyed, false);
    assert.equal(f.visible, true);
    result = { state: "STOPPED", cleanup_status: "complete" };
    await f.controller.exit();
    assert.equal(f.quits, 1);
});
test("failed status clears stale ONLINE and retains a last observation timestamp", async () => {
    let fail = false;
    const f = fixture(async (action) => {
        if (action === "stop") return { state: "STOPPED" };
        if (fail) throw Error("private secret");
        return action === "status"
            ? { state: "RUNNING", connected: true }
            : { events: [] };
    });
    await f.controller.refresh();
    assert.equal(f.controller.overview().status.connected, true);
    fail = true;
    await f.controller.refresh();
    assert.equal(f.controller.overview().status, null);
    assert.equal(
        f.controller.overview().error.includes("private secret"),
        false,
    );
    assert.ok(f.controller.overview().updatedAt);
    await f.controller.exit();
});

test("enrollment diagnostics survive the controller and reject unknown backend text", async () => {
    let failure = Error(failureMessage("TOKEN_REJECTED"));
    const f = fixture(async (action) => {
        if (action === "stop") return { state: "STOPPED" };
        if (action === "enroll") throw failure;
        return action === "status" ? { state: "STOPPED" } : { events: [] };
    });
    await assert.rejects(f.controller.perform("enroll"), /등록 토큰이 거부/);
    assert.equal(f.controller.overview().error, failureMessage("TOKEN_REJECTED"));
    failure = Error("private-enrollment-secret");
    await assert.rejects(f.controller.perform("enroll"), /요청을 완료/);
    assert.equal(f.controller.overview().error, failureMessage("REQUEST_FAILED"));
    assert.equal(knownFailure(failure), failureMessage("REQUEST_FAILED"));
    assert.equal(failureMessage("__proto__"), failureMessage("REQUEST_FAILED"));
    await f.controller.exit();
});
