"""Build two native installers with isolated application/registry identities for acceptance."""

import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", type=Path, required=True)
    args = parser.parse_args()
    node = args.node.absolute()
    identifier = uuid.uuid4().hex
    root = Path("dist") / ("installer-fixture-" + identifier)
    root.mkdir(parents=True)
    metadata = {
        "id": identifier,
        "app_id": "app.racp.acceptance." + identifier,
        "package": "racp-install-acceptance-" + identifier,
        "product": "RACP Acceptance " + identifier[:8],
        "versions": [],
    }
    environment = dict(os.environ)
    environment["PATH"] = str(node.parent) + os.pathsep + environment["PATH"]
    for version in ["0.1.3", "0.1.4"]:
        output = (root / version).absolute()
        common = [
            str(node),
            "node_modules/electron-builder/cli.js",
            "--config",
            "electron-builder.cjs",
            "--win",
            "--x64",
            "--publish",
            "never",
            "--config.appId=" + metadata["app_id"],
            "--config.productName=" + metadata["product"],
            "--config.extraMetadata.name=" + metadata["package"],
            "--config.extraMetadata.version=" + version,
            "--config.directories.output=" + str(output),
            "--config.nsis.artifactName=${productName} Setup ${version}.${ext}",
        ]
        subprocess.run([*common, "--dir"], cwd="apps/client", env=environment, check=True)
        subprocess.run(
            [*common, "--prepackaged", str(output / "win-unpacked"), "--config.win.target=nsis"],
            cwd="apps/client",
            env=environment,
            check=True,
        )
        metadata["versions"].append(
            {
                "version": version,
                "installer": str(output / (metadata["product"] + " Setup " + version + ".exe")),
            }
        )
        (root / "fixture.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"fixture": str(root / "fixture.json")}))


if __name__ == "__main__":
    main()
