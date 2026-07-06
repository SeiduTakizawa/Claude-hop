# E2E test image: sshd + rsync + claude-hop, with the tester user's home at
# a build-arg location so one image plays a Linux box (/home/tester) and the
# other a macOS-style box (/Users/tester).
FROM debian:bookworm-slim

ARG HOME_DIR=/home/tester

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server rsync python3 python3-venv procps ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /run/sshd

RUN useradd --create-home --home-dir "$HOME_DIR" --shell /bin/bash tester

COPY pyproject.toml README.md LICENSE /src/
COPY src /src/src
RUN python3 -m venv /opt/claude-hop \
    && /opt/claude-hop/bin/pip install --no-cache-dir /src \
    && ln -s /opt/claude-hop/bin/claude-hop /usr/local/bin/claude-hop

EXPOSE 22
CMD ["/bin/sh", "-c", "ssh-keygen -A && exec /usr/sbin/sshd -D -e"]
