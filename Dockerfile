ARG PYTHON_VERSION=3.12
ARG SOURCE_DIR=bridge_indexer
ARG POETRY_PATH=/opt/poetry
ARG VENV_PATH=/opt/venv
ARG APP_PATH=/opt/app
ARG APP_USER=dipdup

FROM python:${PYTHON_VERSION}-slim as builder-base

ARG VENV_PATH
ARG POETRY_PATH
ENV PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_HOME="$POETRY_PATH" \
    VIRTUAL_ENV=$VENV_PATH \
    PATH="$POETRY_PATH/bin:$VENV_PATH/bin:$PATH"

RUN apt-get update \
 && apt-get install --no-install-recommends -y \
        # deps for installing poetry
        curl \
        # deps for building python deps
        build-essential \
        # pytezos deps
        libsodium-dev libgmp-dev pkg-config \
    \
    # install poetry
 && curl -sSL https://install.python-poetry.org | python - \
    \
    # configure poetry & make a virtualenv ahead of time since we only need one
 && python -m venv $VENV_PATH \
 && poetry config virtualenvs.create false \
    \
    # cleanup
 && rm -rf /tmp/* \
 && rm -rf /root/.cache \
 && rm -rf `find /usr/local/lib $POETRY_PATH/venv/lib $VENV_PATH/lib -name __pycache__` \
 && rm -rf /var/lib/apt/lists/*

FROM builder-base as builder-production

COPY ["poetry.lock", "pyproject.toml", "./"]

RUN poetry install --only main --sync --no-root --no-interaction --no-ansi -vvv \
 && rm -rf /tmp \
 && rm -rf /root/.cache \
 && rm -rf $VIRTUAL_ENV/src \
 && rm -rf `find $VIRTUAL_ENV/lib -name __pycache__`


FROM python:${PYTHON_VERSION}-slim as runtime-base

ARG VENV_PATH
ENV PATH="$VENV_PATH/bin:$PATH"

ARG APP_PATH
WORKDIR $APP_PATH

ARG APP_USER
RUN useradd -ms /bin/bash $APP_USER

FROM runtime-base as runtime

RUN apt-get update \
 && apt-get install --no-install-recommends -y \
        libsodium-dev libgmp-dev pkg-config \
    # cleanup
 && rm -rf /tmp/* \
 && rm -rf /root/.cache \
 && rm -rf /var/lib/apt/lists/*

ARG VENV_PATH
COPY --from=builder-production ["$VENV_PATH", "$VENV_PATH"]

ARG APP_USER
USER $APP_USER
ARG SOURCE_DIR
COPY --chown=$APP_USER $SOURCE_DIR ./$SOURCE_DIR

ENTRYPOINT ["dipdup"]
CMD ["run"]
