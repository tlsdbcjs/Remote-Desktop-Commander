const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { Arch } = require("electron-builder");
const beforePack = require("../before-pack.cjs");
const { version } = require("../package.json");
const sha256 = (value) => crypto.createHash("sha256").update(value).digest("hex");

test("packaging accepts the matching bundle and rejects stale or modified resources", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "racp-packaging-"));
    try {
        const contents = "native-runtime-fixture";
        await fs.writeFile(path.join(root, "runtime-fixture"), contents);
        const manifest = {
            version,
            platform: process.platform,
            arch: process.arch,
            lock_sha256: sha256(await fs.readFile(path.resolve(__dirname, "../../../uv.lock"))),
            files: [{ file: "runtime-fixture", sha256: sha256(contents) }],
        };
        const context = {
            arch: Arch[process.arch],
            electronPlatformName: process.platform,
            packager: { config: { extraResources: [{ from: root, to: "agent" }] } },
        };
        const writeManifest = (overrides = {}) => fs.writeFile(
            path.join(root, "agent-manifest.json"), JSON.stringify({ ...manifest, ...overrides }),
        );
        await writeManifest();
        await beforePack(context);
        for (const mismatch of [
            { version: "0.0.0" },
            { platform: "unsupported" },
            { arch: "unsupported" },
        ]) {
            await writeManifest(mismatch);
            await assert.rejects(beforePack(context), /must match the target/);
        }
        await writeManifest({ lock_sha256: "stale" });
        await assert.rejects(beforePack(context), /rebuilt after uv.lock changes/);
        await writeManifest();
        await fs.writeFile(path.join(root, "runtime-fixture"), "changed");
        await assert.rejects(beforePack(context), /differs from its build manifest/);
        await writeManifest({ files: [{ file: "../escape", sha256: "irrelevant" }] });
        await assert.rejects(beforePack(context), /Invalid Agent manifest path/);
    } finally {
        assert.equal(path.dirname(root), path.resolve(os.tmpdir()));
        assert.ok(path.basename(root).startsWith("racp-packaging-"));
        await fs.rm(root, { recursive: true, force: true });
    }
});
