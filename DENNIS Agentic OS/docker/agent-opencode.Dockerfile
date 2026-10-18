# OpenCode agent image (prebuilt: the CLI is installed at build time).
# Build:  scripts/build_agent_images.sh opencode
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git ripgrep curl ca-certificates python3 bash tini \
    && rm -rf /var/lib/apt/lists/*

# Official npm package for the OpenCode CLI.
RUN npm install -g opencode-ai@latest

RUN useradd -m -u 1000 -s /bin/bash agent
USER agent
WORKDIR /home/agent
ENV TERM=xterm-256color COLORTERM=truecolor LANG=C.UTF-8 LC_ALL=C.UTF-8
ENTRYPOINT ["tini", "--"]
CMD ["opencode"]
