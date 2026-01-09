FROM postgres:17

# Builds & installs the `pg_textsearch` extension into this Postgres image.
# Note: This is for local/dev usage. If builds become flaky, pin PG_TEXTSEARCH_REF
# to a tag/commit that you’ve verified works.
ARG PG_TEXTSEARCH_REF=main

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    build-essential \
    postgresql-server-dev-17 \
  && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${PG_TEXTSEARCH_REF}" https://github.com/timescale/pg_textsearch.git /tmp/pg_textsearch \
  && cd /tmp/pg_textsearch \
  && make \
  && make install \
  && rm -rf /tmp/pg_textsearch

# Create extension at DB init time (only runs on a fresh volume).
COPY postgres/initdb/ /docker-entrypoint-initdb.d/


