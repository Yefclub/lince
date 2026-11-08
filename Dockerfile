# syntax=docker/dockerfile:1.7
# Serviço HTTP do Lince. Dependências travadas pelo uv.lock; roda sem root. Python 3.14: o metafile-render (EMF e
# WMF) ainda não instala no 3.15.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra servidor --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra servidor --no-editable
# A imagem não sai com dependência de licença fora da lista; os créditos são gerados do que foi instalado.
COPY scripts/licencas.py scripts/excecoes-de-licenca.toml ./
RUN .venv/bin/python -I licencas.py --checar && .venv/bin/python -I licencas.py --gerar THIRD_PARTY_NOTICES.md

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
# DejaVu: fonte dos números na folha de recortes agrupados (licença Bitstream Vera, permissiva)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && apt-get clean && find /var/lib/apt/lists -type f -delete \
    && useradd --system --uid 10001 --home-dir /app lince
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/THIRD_PARTY_NOTICES.md /app/LICENSE /app/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER lince
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=2).status != 200)"]
ENTRYPOINT ["lince-servidor", "--porta", "8000"]
