#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Rodando testes com cobertura..."
python -m pytest tests/ --cov=core --cov=staging --cov=extract --cov-report=html -q

echo ""
echo "==> Relatório gerado em htmlcov/index.html"

# Tenta abrir no browser, caso xdg-utils esteja disponível
if command -v xdg-open > /dev/null 2>&1; then
    xdg-open htmlcov/index.html
else
    echo ""
    echo "xdg-utils não encontrado. Subindo servidor HTTP..."
    echo "Acesse: http://localhost:9000/"
    python3 -m http.server 9000 --directory htmlcov
fi
