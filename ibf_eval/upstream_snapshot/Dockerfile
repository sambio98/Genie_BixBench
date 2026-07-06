FROM python:3.13-slim

WORKDIR /app

# Install system deps: matplotlib, pandoc + texlive (PDF rendering), chromium (mermaid),
# git (genie_api/git_mirror pulls origin/main on POST /runs + GET /specs),
# bioinformatics CLI tools reached by the code agent via run_tool() (samtools,
# bcftools, bwa, bowtie2, mafft, bedtools, hmmer, fasttree, trimmomatic -- all
# Debian-bookworm main, ~30 MB). fasttree gives the code agent a tree-inference
# step (it has mafft to align + phykit to measure, but nothing to BUILD a tree)
# so it can do phylogenomics from single-copy orthologs instead of returning NA.
# trimmomatic (+ its default-jre / libjbzip2-java deps) gives the code agent
# read/adapter QC trimming -- without it, a question specifying exact
# Trimmomatic flags forced a from-scratch Python reimplementation of
# ILLUMINACLIP/SLIDINGWINDOW that landed ~77x off the real tool's output on a
# real BixBench capsule (bix-61-q1).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    gcc \
    libc6-dev \
    fonts-liberation \
    fonts-dejavu \
    pandoc \
    texlive-xetex \
    texlive-latex-extra \
    texlive-bibtex-extra \
    texlive-science \
    texlive-fonts-extra \
    lmodern \
    nodejs \
    npm \
    chromium \
    samtools \
    bcftools \
    bwa \
    bowtie2 \
    mafft \
    bedtools \
    hmmer \
    fasttree \
    trimmomatic \
    && rm -rf /var/lib/apt/lists/*

# Ensure the lowercase `fasttree` name that run_tool() whitelists is on PATH --
# Debian's fasttree package has shipped the binary as both `fasttree` and
# `FastTree` across versions. No-op when `fasttree` already exists; fails the
# build loudly if neither name is present (a broken install we want surfaced).
RUN command -v fasttree >/dev/null 2>&1 || ln -s "$(command -v FastTree)" /usr/local/bin/fasttree

# Debian's trimmomatic package ships two separate entrypoints (TrimmomaticPE /
# TrimmomaticSE), not the unified `trimmomatic PE|SE ...` CLI that bioconda
# ships and that run_tool()'s ALLOWED_BINARIES["trimmomatic"] / the code-agent
# cookbook call (`run_tool("trimmomatic", "PE", ...)`). Shim the bioconda
# convention on top so the same run_tool() call works regardless of which
# packaging produced the binaries.
RUN printf '#!/bin/sh\nMODE="$1"; shift\nexec "Trimmomatic${MODE}" "$@"\n' > /usr/local/bin/trimmomatic \
    && chmod +x /usr/local/bin/trimmomatic

# docker CLI (client only -- NOT the daemon). genie-api shells out to
# `docker run` to launch a per-run container from a pinned image, giving a
# version-pinned run the pinned ref's code AND deps (full fidelity). The client
# reaches the daemon via the docker-socket-proxy (DOCKER_HOST=tcp://... set in
# docker-compose.yml); genie-api does NOT bind-mount the raw /var/run/docker.sock.
# The static binary is the lightest CLI-only install -- apt's docker.io would
# also pull the daemon. Kept in the cached system-deps region so a code change
# never re-downloads it. Pinned to a confirmed-available stable static release.
ARG DOCKER_CLI_VERSION=29.5.2
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xzC /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker \
    && docker --version

# mermaid-filter for diagram rendering in PDFs
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
RUN npm install -g mermaid-filter && npm cache clean --force

ENV MPLBACKEND=Agg

# Install dependencies from requirements first (better layer caching)
COPY env/ env/
RUN pip install --no-cache-dir -r env/requirements.txt

# Copy project files and install package with API + sim-dashboard extras.
# Both the chat-api and the sim-dashboard service run from this single image
# (different uvicorn CMDs in docker-compose.yml). sim-dashboard pulls in
# jinja2 + sentence-transformers + torch (CPU-only); chat-api ignores them.
# Protocol assets (tex template, CSL, Lua filter, reference DOCX) ship inside
# ibiofoundry_ai/tools/protocol_assets/ -- no separate COPY needed
COPY pyproject.toml .
COPY ibiofoundry_ai/ ibiofoundry_ai/
RUN pip install --no-cache-dir -e ".[api,sim-dashboard]"

# Build identity, injected by docker-compose build.args (see deploy.sh /
# docker-compose.yml x-ibiofoundry-build anchor). Declared AFTER the deps +
# package layers so a new build id never busts the expensive apt/pip cache.
#   BUILD_COMMIT  -- full git SHA of HEAD (org.opencontainers.image.revision)
#   BUILD_VERSION -- pyproject [project].version, the real (frozen-until-bumped)
#                    semver release contract (NOT a per-commit count alias)
#   BUILD_ID      -- the canonical <date>-<short-sha> image tag deploy.sh assigns
ARG BUILD_COMMIT=unknown
ARG BUILD_VERSION=local
ARG BUILD_ID=local
# OCI labels are HOST-side metadata only -- `make versions` reads them via
# `docker inspect`. They are NOT readable from inside the container (no docker
# socket), so the ENV below is how the running process learns its own deployed
# commit (genie-api's prod ref-default reads IBIOFOUNDRY_BUILD_COMMIT).
LABEL org.opencontainers.image.revision=$BUILD_COMMIT \
      org.opencontainers.image.version=$BUILD_VERSION
ENV IBIOFOUNDRY_BUILD_COMMIT=$BUILD_COMMIT \
    IBIOFOUNDRY_BUILD_VERSION=$BUILD_VERSION \
    IBIOFOUNDRY_BUILD_ID=$BUILD_ID

EXPOSE 8000

CMD ["uvicorn", "ibiofoundry_ai.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "20"]
