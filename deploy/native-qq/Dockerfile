FROM qqbot-napcat:4.0.0-amd64
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -f install -y --no-install-recommends && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends dbus-x11 fluxbox x11vnc novnc python3-pyatspi at-spi2-core xdotool fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
COPY linuxqq.deb /tmp/linuxqq.deb
RUN echo '436c65e1dd28422e128a21cbddb5fe4e2e6c079d7f27fec2541e014d38cc336e  /tmp/linuxqq.deb' | sha256sum -c - \
 && mkdir /tmp/qq-official && dpkg-deb -x /tmp/linuxqq.deb /tmp/qq-official \
 && cp /tmp/qq-official/opt/QQ/resources/app/package.json /opt/QQ/resources/app/package.json \
 && rm -rf /tmp/qq-official /tmp/linuxqq.deb /app/napcat /app/NapCat.Shell.zip \
 && rm -f /opt/QQ/resources/app/loadNapCat.js \
 && test -z "$(dpkg -V linuxqq)" \
 && useradd --uid 10001 --home-dir /app --shell /bin/bash nativeqq \
 && mkdir -p /app/.config /app/.cache /app/.local/share \
 && chown nativeqq:nativeqq /app /app/.config /app/.cache /app/.local /app/.local/share
COPY start-native-qq.sh /usr/local/bin/start-native-qq
RUN chmod 755 /usr/local/bin/start-native-qq
ENV HOME=/app DISPLAY=:1 QT_ACCESSIBILITY=1 GTK_MODULES=gail:atk-bridge
ENTRYPOINT ["/usr/local/bin/start-native-qq"]
