const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
module.exports = async (context) => {
    const resources = context.packager.config.extraResources;
    const agent = resources.find((resource) => resource.to === "agent");
    const root = path.resolve(__dirname, agent.from);
    const manifest = JSON.parse(fs.readFileSync(path.join(root, "agent-manifest.json"), "utf8"));
    const arch = require("electron-builder").Arch[context.arch];
    if (
        manifest.platform !== context.electronPlatformName ||
        manifest.arch !== arch ||
        manifest.version !== require("./package.json").version
    )
        throw new Error(
            "Agent runtime must match the target OS, architecture and workspace version",
        );
    const lock = fs.readFileSync(path.resolve(__dirname, "../../uv.lock"));
    if (crypto.createHash("sha256").update(lock).digest("hex") !== manifest.lock_sha256)
        throw new Error("Agent runtime must be rebuilt after uv.lock changes");
    for (const entry of manifest.files) {
        const file = path.resolve(root, entry.file);
        if (!file.startsWith(root + path.sep))
            throw Error("Invalid Agent manifest path");
        const checksum = crypto.createHash("sha256");
        for await (const chunk of fs.createReadStream(file))
            checksum.update(chunk);
        if (checksum.digest("hex") !== entry.sha256)
            throw Error(
                "Agent runtime differs from its build manifest: " + entry.file,
            );
    }
};
