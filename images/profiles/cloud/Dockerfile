# syntax=docker/dockerfile:1
ARG BASE_IMAGE=quiver-base:stable
FROM ${BASE_IMAGE}

ARG REQUIRED_PACKAGES
ARG OPTIONAL_PACKAGES
ARG TARGETARCH

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN test -n "$REQUIRED_PACKAGES" \
    && pacman -S --noconfirm --needed $REQUIRED_PACKAGES \
    && for package in $OPTIONAL_PACKAGES; do \
        pacman -S --noconfirm --needed "$package" || echo "Skipping unavailable optional package: $package"; \
       done \
    && pacman -Scc --noconfirm

# Google does not publish gcloud through Arch or Arch Linux ARM repositories.
# Its official archive has native x86_64 and ARM variants.
RUN case "$TARGETARCH" in \
        amd64) gcloud_arch=x86_64 ;; \
        arm64) gcloud_arch=arm ;; \
        *) echo "unsupported gcloud architecture: $TARGETARCH" >&2; exit 64 ;; \
    esac \
    && curl --fail --location --retry 5 --retry-all-errors \
        "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-${gcloud_arch}.tar.gz" \
        --output /tmp/google-cloud-cli.tar.gz \
    && tar -xzf /tmp/google-cloud-cli.tar.gz -C /opt \
    && rm -f /tmp/google-cloud-cli.tar.gz \
    && /opt/google-cloud-sdk/bin/gcloud --version

ENV PATH="/opt/google-cloud-sdk/bin:${PATH}"

COPY packages/cloud.yaml /etc/quiver/packages/cloud.yaml
