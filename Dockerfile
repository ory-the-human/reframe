FROM python:3.12-slim

# adb talks to the frame over Wi-Fi (port 5555).
RUN apt-get update \
    && apt-get install -y --no-install-recommends adb \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg converts videos for the frames; a static build is a quarter of Debian's package.
COPY --from=mwader/static-ffmpeg:7.1@sha256:a8090df5f5608daef387e1b2e93b98aaacb4d92153ad904e7d715c725724fca4 /ffmpeg /usr/local/bin/

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

ENTRYPOINT ["reframe"]
CMD ["web"]
