# syntax=docker/dockerfile:1
ARG BASE_IMAGE=quiver-base:stable
FROM ${BASE_IMAGE}

ARG REQUIRED_PACKAGES
ARG OPTIONAL_PACKAGES

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN test -n "$REQUIRED_PACKAGES" \
    && pacman -Sy --noconfirm --needed $REQUIRED_PACKAGES \
    && for package in $OPTIONAL_PACKAGES; do \
        pacman -S --noconfirm --needed "$package" || echo "Skipping unavailable optional package: $package"; \
       done \
    && pacman -Scc --noconfirm

COPY packages/web.yaml /etc/quiver/packages/web.yaml
