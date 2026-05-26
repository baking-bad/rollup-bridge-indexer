ARG PYTHON_VERSION=3.12-slim-bookworm
ARG APP_PATH=/opt/app
ARG APP_USER=dipdup

FROM python:${PYTHON_VERSION} AS builder-base

SHELL ["/bin/bash", "-exc"]

RUN apt-get update -qy \
 && apt-get install --no-install-recommends --no-install-suggests -qy \
        git \
        # deps for building python deps
        build-essential \
        # pytezos deps
        libsodium-dev libgmp-dev pkg-config \
    \
    # cleanup \
 && apt-get clean \
 && rm -rf /tmp/* \
 && rm -rf /var/tmp/* \
 && rm -rf /root/.cache \
 && rm -rf /var/lib/apt/lists/*

ARG APP_PATH
ENV UV_PROJECT_ENVIRONMENT=$APP_PATH


FROM builder-base AS builder-production

RUN --mount=from=ghcr.io/astral-sh/uv,source=/uv,target=/bin/uv \
	--mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
	uv sync --frozen --exact --no-install-project --no-editable --no-dev --no-installer-metadata


FROM python:${PYTHON_VERSION} AS runtime-base

SHELL ["/bin/bash", "-exc"]

RUN apt-get update -qy \
 && apt-get install --no-install-recommends --no-install-suggests -qy \
        # pytezos deps
        libsodium-dev libgmp-dev pkg-config \
    \
    # cleanup \
 && apt-get clean \
 && rm -rf /tmp/* \
 && rm -rf /var/tmp/* \
 && rm -rf /root/.cache \
 && rm -rf /var/lib/apt/lists/*

ARG APP_PATH
ENV PATH=$APP_PATH/bin:$PATH

WORKDIR $APP_PATH

ARG APP_USER
RUN useradd -ms /bin/bash $APP_USER


FROM runtime-base AS runtime

ARG APP_USER
ARG APP_PATH
COPY --from=builder-production --chown=$APP_USER ["$APP_PATH", "$APP_PATH"]

# DipDup's `package.initialize()` runs on EVERY command and (re)creates marker files
# (py.typed, **/.keep) at the package root. WORKDIR created $APP_PATH as root, so hand the
# directory itself to $APP_USER too — otherwise the unprivileged runtime user gets
# `PermissionError: ... /opt/app/py.typed` before the indexer can start.
RUN chown $APP_USER $APP_PATH

USER $APP_USER
# Package code, dipdup.yaml and configs/ now live at the repo root (the package is the
# repo itself; rollup_bridge_indexer is a self-symlink -> .). .dockerignore narrows this
# COPY to the package files + the symlink. WORKDIR is $APP_PATH, so config is at
# $APP_PATH/dipdup.yaml and overlays at $APP_PATH/configs/<network>.yaml.
#
# DipDup derives the package NAME from the basename of DIPDUP_PACKAGE_PATH. Pointing it at
# $APP_PATH (=/opt/app) would name the package `app`, breaking every `rollup_bridge_indexer.*`
# import and the project init. Point it at the committed `rollup_bridge_indexer -> .`
# self-symlink instead: same files, but a basename that matches the real package name.
ENV DIPDUP_PACKAGE_PATH=$APP_PATH/rollup_bridge_indexer
COPY --chown=$APP_USER . ./

ENTRYPOINT ["dipdup"]
CMD ["run"]
