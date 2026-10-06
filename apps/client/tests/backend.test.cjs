const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { runtimePath, callBackend } = require("../backend.cjs");
test("native runtime paths cover all target platforms", () => {
    assert.equal(
        runtimePath("agent", "win32"),
        path.join("agent", "runtime", "python.exe"),
    );
    for (const platform of ["darwin", "linux"])
        assert.equal(
            runtimePath("agent", platform),
            path.join("agent", "runtime", "bin", "python3"),
        );
    assert.throws(() => runtimePath("agent", "other"));
});
test("unsupported actions and oversized inputs never spawn a worker", async () => {
    await assert.rejects(
        callBackend("missing", "missing", "exec"),
        /Unsupported/,
    );
    await assert.rejects(
        callBackend("missing", "missing", "enroll", {
            token: "x".repeat(20000),
        }),
        /limit/,
    );
});
test("missing native runtime fails without disclosing request secrets", async () => {
    const secret = "private-token-do-not-print";
    await assert.rejects(
        callBackend("missing", "missing", "enroll", { token: secret }),
        (error) => !error.message.includes(secret) && error.message.includes("Agent 런타임"),
    );
});
