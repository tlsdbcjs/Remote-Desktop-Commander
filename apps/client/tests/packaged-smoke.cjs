// Native packaged smoke only when this Windows user's production client is unregistered.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { _electron, expect } = require("@playwright/test");
const messages = require("../messages.json");
async function main() {
    const executable = path.resolve(process.argv[2]);
    const credential = path.join(
        process.env.APPDATA,
        "@racp",
        "client",
        "agent",
        "credential.bin",
    );
    try {
        await fs.access(credential);
        throw Error(
            "Packaged smoke requires an unregistered local user; refusing existing Device state",
        );
    } catch (error) {
        if (error.code !== "ENOENT") throw error;
    }
    const offerFolder = await fs.mkdtemp(
        path.join(os.tmpdir(), "racp-packaged-offer-"),
    );
    const offerPath = path.join(offerFolder, "RACP-connection.racp");
    const fixtureToken =
        "unused-native-offer-" + require("node:crypto").randomUUID();
    await fs.writeFile(
        offerPath,
        JSON.stringify({
            version: 1,
            gateway: "https://gateway.example",
            token: fixtureToken,
            expires_at: new Date(Date.now() + 600000).toISOString(),
            ca_pem: null,
        }),
    );
    let application;
    try {
        application = await _electron.launch({ executablePath: executable });
        const page = await application.firstWindow();
        await page.getByRole("heading", { name: "새 PC 연결" }).waitFor();
        const version = await application.evaluate(({ app }) => ({
            packaged: app.isPackaged,
            version: app.getVersion(),
            userData: app.getPath("userData"),
        }));
        assert.equal(version.packaged, true);
        assert.equal(version.version, "0.1.7");
        assert.equal(
            path.join(version.userData, "agent", "credential.bin"),
            credential,
        );
        const info = await page.evaluate(() => window.racpClient.info());
        assert.equal(info.configured, false);
        assert.ok(info.execution_identity);
        await application.evaluate(({ dialog }, file) => {
            dialog.showOpenDialog = async () => ({
                canceled: false,
                filePaths: [file],
            });
        }, offerPath);
        await page
            .getByRole("button", { name: "연결 파일 선택", exact: true })
            .click();
        await page
            .getByText("https://gateway.example", { exact: true })
            .waitFor();
        await expect(
            page.getByRole("button", { name: "연결 파일 선택", exact: true }),
        ).toBeEnabled();
        assert.equal(await page.getByLabel("일회용 등록 토큰").count(), 0);
        assert.equal(
            (await page.locator("body").innerText()).includes(fixtureToken),
            false,
        );
        await page.screenshot({
            path: path.resolve("dist/client-connection-setup.png"),
            fullPage: true,
        });
        const failure = await page.evaluate(async (workspace) => {
            try {
                await window.racpClient.enroll({
                    gateway: "https://gateway.example",
                    workspace,
                    ca_file: workspace + "/racp-acceptance-nonexistent-ca.pem",
                    profile: "read_only",
                    token: "unused-acceptance-token-never-sent",
                    allowed_workspaces: [],
                });
                return "unexpected enrollment";
            } catch (error) {
                return error.message;
            }
        }, path.dirname(executable));
        assert.ok(failure.endsWith(messages.CA_INVALID));
        assert.equal(failure.includes("unused-acceptance-token"), false);
        assert.equal(
            (await page.evaluate(() => window.racpClient.info())).configured,
            false,
        );
        await page.evaluate(() => {
            window.racpClient.exit().catch(() => {});
        });
        await application.close();
        application = null;
        console.log(
            "PASS: packaged 0.1.7 connection-file preview/token isolation, runtime, safe CA diagnosis and full exit",
        );
    } finally {
        if (application) await application.close();
        await fs.unlink(offerPath);
        await fs.rmdir(offerFolder);
    }
}
main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
});
