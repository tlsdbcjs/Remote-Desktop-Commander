const { spawn } = require("node:child_process");
const path = require("node:path");
const { failureMessage } = require("./errors.cjs");

function runtimePath(root, platform = process.platform) {
    if (platform === "win32") return path.join(root, "runtime", "python.exe");
    if (["darwin", "linux"].includes(platform))
        return path.join(root, "runtime", "bin", "python3");
    throw new Error("Unsupported client platform");
}

function callBackend(root, stateDir, action, data = {}) {
    if (
        ![
            "info",
            "settings",
            "update_settings",
            "enroll",
            "start",
            "status",
            "stop",
            "activity",
            "inspect_connection",
            "enroll_connection",
        ].includes(action)
    )
        return Promise.reject(new Error("Unsupported client action"));
    const input = JSON.stringify({ ...data, action });
    if (Buffer.byteLength(input) > 16384)
        return Promise.reject(new Error("Client request exceeds limit"));
    return new Promise((resolve, reject) => {
        const child = spawn(
            runtimePath(root),
            ["-I", "-m", "racp_agent.desktop_control", "--state-dir", stateDir],
            {
                windowsHide: true,
                shell: false,
                stdio: ["pipe", "pipe", "pipe"],
                env: {
                    ...process.env,
                    PLAYWRIGHT_BROWSERS_PATH: path.join(root, "browsers"),
                },
            },
        );
        const output = [];
        let bytes = 0,
            settled = false;
        const fail = (code = "REQUEST_FAILED") => {
            if (!settled) {
                settled = true;
                reject(new Error(failureMessage(code)));
            }
        };
        const timer = setTimeout(() => fail("REQUEST_TIMEOUT"), 45000);
        child.on("error", () => fail("RUNTIME_UNAVAILABLE"));
        child.stdin.on("error", () => fail("RUNTIME_UNAVAILABLE"));
        child.stdout.on("data", (chunk) => {
            bytes += chunk.length;
            if (bytes > 65536) fail();
            else output.push(chunk);
        });
        child.stderr.resume(); // Never forward potentially sensitive diagnostics to the renderer.
        child.on("close", (code) => {
            clearTimeout(timer);
            if (settled) return;
            try {
                const reply = JSON.parse(
                    Buffer.concat(output).toString("utf8"),
                );
                settled = true;
                if (code !== 0 || reply.ok !== true)
                    reject(new Error(failureMessage(reply.code)));
                else resolve(reply.result);
            } catch {
                fail("RUNTIME_RESPONSE_INVALID");
            }
        });
        child.stdin.end(input + "\n");
    });
}
module.exports = { runtimePath, callBackend };
