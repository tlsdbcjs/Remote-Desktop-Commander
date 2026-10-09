// Native packaged smoke uses a new profile and checks isolation before IPC calls.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { _electron, expect } = require("@playwright/test");
const messages = require("../messages.json");
async function main() {
    const executable = path.resolve(process.argv[2]);
    const offerFolder = await fs.mkdtemp(
        path.join(os.tmpdir(), "racp-packaged-offer-"),
    );
    const offerPath = path.join(offerFolder, "RACP-connection.racp");
    const profile = path.join(offerFolder, "profile");
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
        application = await _electron.launch({
            executablePath: executable,
            args: ["--user-data-dir=" + profile],
            timeout: 120000,
        });
        const version = await application.evaluate(({ app }) => {
            return {
                packaged: app.isPackaged,
                version: app.getVersion(),
                userData: app.getPath("userData"),
            };
        });
        const expectedVersion = require("../package.json").version;
        assert.equal(version.packaged, true);
        assert.equal(version.version, expectedVersion);
        assert.equal(
            path.resolve(version.userData).toLowerCase(),
            profile.toLowerCase(),
        );
        const page = await application.firstWindow();
        await page.getByRole("heading", { name: "새 PC 연결" }).waitFor();
        await expect(
            page.getByRole("checkbox", {
                name: "이 PC의 Windows 화면 캡처·마우스·키보드 조작 허용",
            }),
        ).not.toBeChecked();
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
            `PASS: packaged ${expectedVersion} isolated profile, connection-file preview/token isolation, runtime, safe CA diagnosis and full exit`,
        );
    } finally {
        if (application) await application.close();
        assert.equal(path.dirname(offerFolder), path.resolve(os.tmpdir()));
        assert.ok(
            path.basename(offerFolder).startsWith("racp-packaged-offer-"),
        );
        await fs.rm(offerFolder, { recursive: true, force: true });
    }
}
main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
});
