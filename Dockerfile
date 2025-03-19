ARG PYTHON_VERSION=3.12-slim-bookworm
ARG SOURCE_DIR=bridge_indexer
ARG APP_PATH=/opt/app
ARG APP_USER=dipdup

FROM python:${PYTHON_VERSION} AS builder-base

SHELL ["/bin/bash", "-exc"]

RUN apt-get update -qy \
 && apt-get install --no-install-recommends --no-install-suggests -qyy \
        # deps for building python deps
        build-essential \
        # pytezos deps
        libsodium-dev libgmp-dev pkg-config \
    	git \
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
 && apt-get install --no-install-recommends --no-install-suggests -qyy \
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

USER $APP_USER
ARG SOURCE_DIR
ENV DIPDUP_PACKAGE_PATH=$APP_PATH/$SOURCE_DIR
COPY --chown=$APP_USER $SOURCE_DIR ./$SOURCE_DIR

ENTRYPOINT ["dipdup"]
CMD ["run"]
