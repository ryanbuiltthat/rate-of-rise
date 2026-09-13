ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# libgomp1 = OpenMP runtime that XGBoost/LightGBM link against.
# sqlite3 = the CLI, not the Python module (that ships with python3). Needed because
# annotating a storm is a manual UPDATE against events.sqlite (spec §7 Phase 3), and
# without the binary that documented step just fails.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-venv libgomp1 sqlite3 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --break-system-packages -r /tmp/requirements.txt

COPY run.sh /run.sh
COPY app/ /app/
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
