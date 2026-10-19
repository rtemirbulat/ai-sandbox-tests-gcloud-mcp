FROM python:3.13-slim
ARG TARGETARCH
ARG GITLEAKS_VERSION=8.30.1
ARG TRIVY_VERSION=0.76.0
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends git curl ca-certificates; \
    case "${TARGETARCH:-amd64}" in \
      amd64) GL_ARCH=x64; TV_ARCH=64bit ;; \
      arm64) GL_ARCH=arm64; TV_ARCH=ARM64 ;; \
      *) echo "unsupported arch ${TARGETARCH}" && exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GL_ARCH}.tar.gz" \
      | tar -xz -C /usr/local/bin gitleaks; \
    curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-${TV_ARCH}.tar.gz" \
      | tar -xz -C /usr/local/bin trivy; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && useradd -m -u 10001 sandbox \
 && mkdir -p /work /home/sandbox/.cache && chown -R sandbox /work /home/sandbox
USER sandbox
ENV SANDBOX_WORKDIR=/work \
    TRIVY_CACHE_DIR=/work/.trivy-cache \
    PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["sandbox-mcp"]
CMD ["serve"]
