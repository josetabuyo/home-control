#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌  No existe .env. Copiá .env.example y completá tus datos:"
  echo "    cp .env.example .env && nano .env"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "→ Creando entorno virtual..."
  python3 -m venv .venv
fi

source .venv/bin/activate

echo "→ Instalando dependencias..."
pip install -q -r requirements.txt

echo "→ Buscando dispositivos en la red..."
python3 discover.py || echo "  (continuando de todas formas)"

echo ""
echo "✅  Dashboard corriendo en http://localhost:9000"
echo "   (Ctrl+C para detener)"
echo ""

uvicorn main:app --host 0.0.0.0 --port 9000 --workers 1
