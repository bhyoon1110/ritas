FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
       nginx openssl curl python3-pytest iproute2 util-linux shellcheck \
    && apt-get clean

WORKDIR /src
# Mount the repository read-only at /src. Do not mount host /etc or Docker socket.
CMD ["bash", "-c", "shellcheck edge_api_server/deploy/setup_https.sh && python3 -m pytest -p no:cacheprovider -q edge_api_server/deploy/tests/test_setup_https.py"]
