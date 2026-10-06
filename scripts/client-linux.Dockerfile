FROM ubuntu@sha256:a853f94d226358a79c740cfc7bce0c289748f3fe3488d921d038ccd752c61b60
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install --no-install-recommends -y ca-certificates curl xz-utils python3-venv libopenjp2-tools fakeroot && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 https://nodejs.org/dist/v22.23.0/node-v22.23.0-linux-x64.tar.xz -o /tmp/node.tar.xz && \
    curl --fail --location --retry 3 https://nodejs.org/dist/v22.23.0/SHASUMS256.txt -o /tmp/node-sha.txt && \
    awk '$2 == "node-v22.23.0-linux-x64.tar.xz" {print $1 "  /tmp/node.tar.xz"}' /tmp/node-sha.txt > /tmp/check-node && \
    test -s /tmp/check-node && sha256sum --check /tmp/check-node && \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 && \
    npm install --global pnpm@11.19.0 && \
    python3 -m venv /opt/uv && /opt/uv/bin/pip install --no-cache-dir uv==0.12.11
ENV PATH=/opt/uv/bin:$PATH
WORKDIR /workspace
COPY pyproject.toml uv.lock .python-version package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps ./apps
COPY packages ./packages
COPY scripts ./scripts
COPY tests ./tests
RUN uv python install 3.12.11 && uv sync --all-packages --frozen && \
    pnpm install --frozen-lockfile && node apps/client/node_modules/electron/install.js
ENV PLAYWRIGHT_BROWSERS_PATH=/workspace/.tools/client-browsers
RUN uv run python -m playwright install --with-deps chromium && \
    uv run python scripts/build.py && \
    uv run python scripts/stage_client_agent.py --browser-cache .tools/client-browsers && \
    pnpm --dir apps/client build && pnpm --dir apps/client test && \
    uv run pytest -q tests/unit/test_desktop_control.py && \
    pnpm --dir apps/client package --linux --x64 --publish never
