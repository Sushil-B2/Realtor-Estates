# Realtor Estates — Production Container (Hostinger Docker / Railway / Render / Fly.io)
FROM python:3.13-slim

RUN useradd --create-home --shell /bin/bash realtor
WORKDIR /home/realtor/app

COPY app ./app
COPY web ./web
COPY db ./db
COPY package.json README.md ./

RUN chown -R realtor:realtor /home/realtor/app
USER realtor

ENV PYTHONUNBUFFERED=1 \
    RE_ENV=production \
    PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["python3", "-m", "app.server", "--host", "0.0.0.0", "--port", "8000"]
