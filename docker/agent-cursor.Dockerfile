# Cursor Agent CLI image.
# Build: scripts/build_agent_images.sh cursor
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       git ripgrep curl ca-certificates python3 bash tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash agent
USER agent
WORKDIR /home/agent

# Cursor's installer places the CLI in the user's local bin directory.
RUN curl https://cursor.com/install -fsS | bash \
    && if [ -x /home/agent/.local/bin/agent ] && [ ! -e /home/agent/.local/bin/cursor-agent ]; then \
         ln -s agent /home/agent/.local/bin/cursor-agent; \
       fi
ENV PATH=/home/agent/.local/bin:$PATH

ENV TERM=xterm-256color COLORTERM=truecolor LANG=C.UTF-8 LC_ALL=C.UTF-8
ENTRYPOINT ["tini", "--"]
CMD ["cursor-agent"]
