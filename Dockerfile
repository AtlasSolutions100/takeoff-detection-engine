# CORE takeoff review UI.
#
# One image, run as ONE container. This is deliberate and not a limitation to
# design around later: JOBS lives in process memory and every artifact lives on
# local disk under out/jobs, so a second replica would hold half the state and
# the UI would 404 at random. Scale this box up, never out, until the job store
# moves somewhere shared.
FROM python:3.12-slim-bookworm

# tesseract: raster OCR on scanned sheets. Without it ocr_words returns []
# silently and a scanned set reads as empty rather than as an error.
# libgl / libglib: opencv-contrib-python-headless still links them.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# deps first: they change far less often than the source, so a code-only push
# reuses this layer and the build is seconds instead of minutes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY takeoff_engine/ ./takeoff_engine/
# the cut / fill engine (mounted under /cutfill; its UI is under webui/cutfill/)
COPY cutfill/ ./cutfill/
COPY webui/ ./webui/
COPY server.py .
# The demo tab IS this file (server.py _load_demo_plans reads it, and
# /api/demo-plans renders the cards from nothing else). Leaving it out of the
# image does not degrade the tab, it empties it: the loader takes OSError,
# DEMO_PLANS becomes {}, and the endpoint answers with zero cards on a box
# that has every one of the plans uploaded. That shipped, and it cost a
# seeding session to find. A new single-file source of truth belongs here.
COPY demo-plans.json .
# ops tool, not part of the service: creates the demo logins, and under
# --rewrite-owners moves the owner-less non-demo rows to staff (a bare run
# is a dry run; a row that has an owner is never touched). It carries NO
# customer addresses (2026-10-01): it reads them at run time from
# /data/jobs/provision-accounts.txt on the volume, so nobody holding this
# image learns who the customers are. Never COPY that roster. In the image so
# it can be re-run later with `docker compose exec app python
# provision_demo.py` - without -T when you want the y/N prompt, with -T
# only alongside --yes.
COPY provision_demo.py .

# The job store. Mounted as a volume in compose - it holds the detection cache,
# the page renders and the approved exports, and losing it means every demo is
# cold again (90s+ per sheet on a big set).
RUN mkdir -p /data/jobs
ENV TAKEOFF_JOBS_DIR=/data/jobs

# Hosted defaults. TAKEOFF_HOSTED refuses client-supplied filesystem paths,
# which would otherwise be an arbitrary file read on a public box. The password
# is NOT defaulted here on purpose: it comes from the environment, and the
# entrypoint refuses to start without it.
ENV TAKEOFF_HOSTED=1 \
    TAKEOFF_WORKERS=4 \
    TAKEOFF_PAGE_BUDGET_S=900 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8100
# 401 is HEALTHY - the site password being on is the whole point. Note the
# trap this used to fall into: urllib.request.urlopen RAISES HTTPError on a
# 401 instead of returning a response, so `r.status in (200,401)` never ran
# and the container reported unhealthy forever while serving every request
# fine. http.client returns the status rather than raising, so no exception
# handling is needed; a genuinely dead app still fails by refusing connect.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import http.client,sys; c=http.client.HTTPConnection('127.0.0.1',8100,timeout=4); c.request('GET','/api/config'); sys.exit(0 if c.getresponse().status in (200,401) else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8100"]
