#!/bin/bash

echo "============================================"
echo "  finv - Inventory Management System"
echo "  Local development server"
echo "============================================"
echo ""

REQUIRED_PKGS=("python3" "python3-venv" "python3-pip")
MISSING_PKGS=()

for pkg in "${REQUIRED_PKGS[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "[..] Missing system packages: ${MISSING_PKGS[*]}"
    echo "[..] Installing with sudo (you may be asked for your password)..."
    echo ""
    sudo apt update && sudo apt install -y "${MISSING_PKGS[@]}"
    if [ $? -ne 0 ]; then
        echo "[ERR] Failed to install system packages."
        echo "       Try manually: sudo apt install ${MISSING_PKGS[*]}"
        read -p "Press Enter to continue..."
        exit 1
    fi
    echo ""
fi

if [ ! -d "venv" ]; then
    echo "[..] Creating virtual environment..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "[ERR] Failed to create venv."
        read -p "Press Enter to continue..."
        exit 1
    fi
fi

source venv/bin/activate
if [ $? -ne 0 ]; then
    echo "[ERR] Failed to activate virtual environment."
    read -p "Press Enter to continue..."
    exit 1
fi

echo "[..] Installing dependencies..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[ERR] Failed to install dependencies."
    read -p "Press Enter to continue..."
    exit 1
fi

echo "[..] Running migrations..."
python manage.py migrate --noinput
if [ $? -ne 0 ]; then
    echo "[ERR] Migration failed."
    read -p "Press Enter to continue..."
    exit 1
fi

echo "[..] Collecting static files..."
python manage.py collectstatic --noinput --clear
if [ $? -ne 0 ]; then
    echo "[ERR] Static files collection failed."
    read -p "Press Enter to continue..."
    exit 1
fi

echo ""
echo "============================================"
echo "  Server:  http://127.0.0.1:8000"
echo "  Admin:   http://127.0.0.1:8000/admin/"
echo "  Create an administrator if needed:"
echo "  python manage.py createsuperuser"
echo "  Stop:    Ctrl+C"
echo "============================================"
echo ""

python manage.py runserver 127.0.0.1:8000

echo ""
echo "[INFO] Server stopped."
read -p "Press Enter to continue..."
