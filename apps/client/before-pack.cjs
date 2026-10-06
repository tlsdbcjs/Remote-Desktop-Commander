const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
module.exports = async (context) => {
    const root = path.resolve(__dirname, "../../dist/client-agent-v8");
    const manifest = JSON.parse(
        fs.readFileSync(
            path.resolve(
                __dirname,
                "../../dist/client-agent-v8/agent-manifest.json",
            ),
            "utf8",
        ),
    );
    const arch = require("electron-builder").Arch[context.arch];
    if (
        manifest.platform !== context.electronPlatformName ||
        manifest.arch !== arch
    )
        throw new Error(
            "Agent runtime must be built on the matching target OS and architecture",
        );
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
