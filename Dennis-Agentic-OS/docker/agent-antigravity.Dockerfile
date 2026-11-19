# Antigravity CLI (agy) agent image.
#
# The agy CLI is a standalone binary distributed by Google Antigravity, not an
# npm package, so the build script copies the local binary from ~/.local/bin
# into the build context (docker build cannot read outside its context).
# Build:  scripts/build_agent_images.sh antigravity
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git ripgrep curl ca-certificates python3 bash tini \
    && rm -rf /var/lib/apt/lists/*

COPY agy /usr/local/bin/agy
RUN chmod 0755 /usr/local/bin/agy

RUN useradd -m -u 1000 -s /bin/bash agent
USER agent
WORKDIR /home/agent
ENV TERM=xterm-256color COLORTERM=truecolor LANG=C.UTF-8 LC_ALL=C.UTF-8
ENTRYPOINT ["tini", "--"]
CMD ["agy"]
