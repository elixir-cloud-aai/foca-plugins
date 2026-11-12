# Image of the decryption executor of `tes.data.crypt4gh`: Python, the
# `crypt4gh` package and the decryption script (`decrypt.py` of the plugin's
# package) as `crypt4gh-decrypt`. Build it from the plugin's directory, the
# build context (only the files of `.dockerignore`'s allowlist go into it):
#
#   docker build -f image/Dockerfile -t crypt4gh-decrypt plugins/tes/data/crypt4gh
#
# The successor of the image of `protes-middleware-crypt4gh`

# Base image by digest
FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

LABEL org.opencontainers.image.title="Crypt4GH decryption executor"
LABEL org.opencontainers.image.description="Decrypts the Crypt4GH inputs of TES tasks, as the executor that the FOCA plugin tes.data.crypt4gh adds"
LABEL org.opencontainers.image.url="https://github.com/elixir-cloud-aai/foca-plugins/tree/dev/plugins/tes/data/crypt4gh"
LABEL org.opencontainers.image.source="https://github.com/elixir-cloud-aai/foca-plugins"
LABEL org.opencontainers.image.documentation="https://foca-plugins.readthedocs.io/"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.authors="ELIXIR On-Cloud <cloud-service@elixir-europe.org>"
LABEL org.opencontainers.image.vendor="ELIXIR On-Cloud"

# The Debian updates available at build time, which the pinned base image
# gets only once upstream rebuilds it (as in FOCA's image)
RUN apt-get update --error-on=any && \
    DEBIAN_FRONTEND=noninteractive \
        apt-get upgrade -y --with-new-pkgs --no-install-recommends \
            -o Dpkg::Options::=--force-confdef \
            -o Dpkg::Options::=--force-confold && \
    rm -rf /var/lib/apt/lists/*

# `crypt4gh` and its dependencies, as compiled in `requirements.txt` with
# their hashes, from wheels only
RUN --mount=type=bind,source=image/requirements.txt,target=/tmp/requirements.txt \
    pip install --no-cache-dir --require-hashes --only-binary :all: \
        -r /tmp/requirements.txt

COPY --chmod=0755 src/foca_plugins/tes/data/crypt4gh/decrypt.py /usr/local/bin/crypt4gh-decrypt

# Runs as root, the image's default user: TES implementations create the
# task's volumes (the volume of the decrypted files, too) for the executors,
# and some (e.g., Funnel with host directories) make them writable for root
# only; the script writes nothing but the decrypted files, readable by all,
# and only reads the inputs
ENTRYPOINT []
CMD ["crypt4gh-decrypt", "--help"]
