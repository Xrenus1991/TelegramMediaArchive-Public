FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 archive \
    && useradd --uid 10001 --gid archive --no-create-home archive \
    && mkdir -p /data /srv/app \
    && chown -R archive:archive /data
COPY --chown=archive:archive app /srv/app
USER archive
CMD ["python", "-m", "app.main"]
