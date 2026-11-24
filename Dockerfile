FROM python:3.12-slim

# adb talks to the frame over Wi-Fi (port 5555).
RUN apt-get update \
    && apt-get install -y --no-install-recommends adb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# HOME points at the volume so adb's key (~/.android/adbkey) survives restarts;
# otherwise the frame would ask to "Allow USB debugging" after every rebuild.
ENV DATA_DIR=/data \
    HOME=/data \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 1992

ENTRYPOINT ["cozysync"]
CMD ["web"]
