# syntax=docker/dockerfile:1
#
# Two stages, for one reason: the cold start of this service is dominated by
# pulling its own image, so nothing that is only needed to *build* the wheels
# may survive into the thing Cloud Run downloads.
#
# Measured on revision midas-backtester-00007-hhv (image digest b6303a303bde,
# the single-stage predecessor of this file): the `apt-get install gcc g++`
# layer alone was 258.9 MB uncompressed / 93.7 MB compressed — 20.6% of the
# whole 455.7 MB pull — for a toolchain no request has ever invoked.

# ---------------------------------------------------------------------------
# Stage 1 — builder. Owns the C toolchain. Nothing here ships except /opt/venv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# gcc/g++ stay in this stage. A few pins in requirements.txt have no cp312
# manylinux wheel (peewee builds optional C speedups; pandas-ta is a beta
# sdist), so the toolchain is genuinely needed to install — it is just never
# needed to serve.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# A venv at a path that is IDENTICAL in both stages. `pip install --prefix`
# would be lighter but rewrites console-script shebangs against the build-time
# prefix, and the CMD below runs `uvicorn` as a console script.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# The service's OWN closure, not the monorepo's. The root requirements.txt
# covers 6 workflows, local dev, the sandbox and the agents; installing it here
# put ccxt, plotly, streamlit, pydeck, lxml and pytest in an image whose cold
# start is dominated by pulling itself. Versions are copied verbatim from the
# root lockfile, so this narrows the package set without ever resolving a
# different version than CI tests. See backtester/requirements.txt.
COPY backtester/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# DO NOT prune vendored test suites here. It was tried, and it broke the
# service: deleting every */tests directory under site-packages saves ~110 MB
# (pandas 32.6, scipy 30.2, numpy 13.8, sklearn 13.1, numba 12.2, matplotlib
# 5.3) and makes the container fail to start with
#
#   ModuleNotFoundError: No module named 'numpy._core.tests'
#
# raised while importing backtester.allocation. "Nothing imports a package's
# tests at runtime" is a reasonable-sounding rule and it is false for numpy,
# which resolves that module on its own import path. Revision 00008-m7v is the
# evidence; it never listened on 8080 and never took traffic.
#
# The honest reading is that this prune is not verifiable without building and
# starting the image, and it buys the least of the three savings here — the
# registry region and pyarrow are where the cold start actually goes. If it is
# ever revisited, it must be per-package and each package must be proved to
# still import in a started container, not merely in a passing test suite.
#
# NOTE the same reasoning applied in advance to __pycache__ (143.4 MB), which
# is likewise NOT pruned: the interpreter would re-compile pandas, scipy, numba
# and sklearn on every cold start, onto a tmpfs, burning the exact seconds this
# file exists to save.
RUN rm -rf /opt/venv/lib/python3.12/site-packages/pip \
           /opt/venv/lib/python3.12/site-packages/pip-*.dist-info

# ---------------------------------------------------------------------------
# Stage 2 — runtime. No apt layer, no compiler, no pip.
# ---------------------------------------------------------------------------
# Dropping the apt layer also drops whatever RUNTIME shared libraries it
# happened to provide, so that was checked against the published image's actual
# layer contents rather than assumed:
#
#   * libstdc++.so.6 and libgcc_s.so.1 come from the DEBIAN BASE layer, which
#     both stages share. The gcc/g++ layer only ever added the link-time
#     variants (libstdc++.a, the unversioned libstdc++.so) under
#     /usr/lib/gcc/x86_64-linux-gnu/14/.
#   * libgomp.so.1 is in NO system layer at all — not the base, not the apt
#     layer, which carries only libgomp.a/.so/.spec. scikit-learn has always
#     run on the copy its own wheel vendors, scikit_learn.libs/libgomp-*.so.1.
#   * scipy, numpy and pillow likewise vendor their OpenBLAS, gfortran,
#     quadmath, freetype and friends under *.libs/.
#
# i.e. nothing in site-packages resolves a shared library out of the layer this
# stage stops shipping.
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY engine /app/engine
COPY backtester /app/backtester
COPY roster.yaml /app/roster.yaml

# data/market/ohlcv, not data/market: the service reads the OHLCV store through
# engine.market_data, and nothing on its import graph touches data/market/news
# (43 files) or today.json.
COPY data/market/ohlcv /app/data/market/ohlcv
COPY data/portfolios /app/data/portfolios
COPY data/strategies /app/data/strategies
COPY data/universes /app/data/universes

# data/baselines is NOT copied. The only occurrences of the word in this
# service are three comments and a test name; engine.baselines is never on the
# import graph of backtester.app.

# Byte-compile our own modules once, at build time, rather than on the first
# request of every cold start.
RUN python -m compileall -q /app/engine /app/backtester

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn backtester.app:app --host 0.0.0.0 --port ${PORT}"]
