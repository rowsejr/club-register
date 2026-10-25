FROM python:3.12-slim

WORKDIR /srv
COPY pyproject.toml README.md /srv/
COPY app /srv/app
COPY migrations /srv/migrations
RUN pip install --no-cache-dir -e .

RUN useradd --create-home --uid 10001 club && mkdir -p /data && chown -R club /srv /data
USER club
ENV CLUB_ENV=production DATABASE_URL=sqlite:////data/club.db

EXPOSE 8000
CMD ["sh", "-c", "python -m app.cli init-db && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
