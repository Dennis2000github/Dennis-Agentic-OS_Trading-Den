# OpenClaw CLI image.
# Build: scripts/build_agent_images.sh openclaw
FROM node:26-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git ripgrep curl ca-certificates python3 bash tini \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g openclaw@latest \
    && useradd -m -u 1000 -s /bin/bash agent

USER agent
WORKDIR /home/agent
ENV TERM=xterm-256color COLORTERM=truecolor LANG=C.UTF-8 LC_ALL=C.UTF-8
ENTRYPOINT ["tini", "--"]
CMD ["openclaw", "tui", "--local"]
