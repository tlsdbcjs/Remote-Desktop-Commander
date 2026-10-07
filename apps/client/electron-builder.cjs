// Resolve native resources independently of fixture metadata overrides.
const path = require("node:path");
const config = require("./electron-builder.json");
const { version } = require("./package.json");
const target = { win32: "win", darwin: "mac", linux: "linux" }[process.platform];
if (!target || !["x64", "arm64"].includes(process.arch))
    throw new Error("Build on a supported native x64 or arm64 host");
const suffix = path.join(version, `${target}-${process.arch}`);
module.exports = {
    ...config,
    directories: { output: path.join("../../dist/client-desktop", suffix) },
    extraResources: [{
        from: path.join("../../dist/client-agent", suffix),
        to: "agent",
        filter: ["runtime/**", "browsers/**", "agent-manifest.json",
            "!**/__pycache__/**", "!**/*.pyc"],
    }],
};
