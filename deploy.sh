#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# finv deploy/update script for RedOS 8.0.2
# Usage:
#   sudo ./deploy.sh            # fresh install
#   sudo ./deploy.sh --update   # update existing installation
#   sudo ./deploy.sh --uninstall  # remove everything
# ──────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="finv"
APP_USER="finv"
APP_DIR="/opt/${APP_NAME}"
DB_NAME="${APP_NAME}"
DB_USER="${APP_USER}_user"
DB_PASS="finv_password"
PYTHON="python3"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
REQUIREMENTS="${SCRIPT_DIR}/requirements.txt"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# root check
if [[ $EUID -ne 0 ]]; then
    log_error "Run with sudo or as root."
    exit 1
fi

# ─── UNINSTALL ─────────────────────────────────
uninstall() {
    log_warn "Removing ${APP_NAME}..."

    systemctl stop "${APP_NAME}" 2>/dev/null || true
    systemctl disable "${APP_NAME}" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload

    if id "${APP_USER}" &>/dev/null; then
        userdel -r "${APP_USER}" 2>/dev/null || true
    fi

    rm -rf "$APP_DIR"

    sudo -u postgres psql -c "DROP DATABASE IF EXISTS ${DB_NAME};" 2>/dev/null || true
    sudo -u postgres psql -c "DROP USER IF EXISTS ${DB_USER};" 2>/dev/null || true

    log_info "Done. ${APP_NAME} removed."
    exit 0
}

# ─── MAIN ──────────────────────────────────────
MODE="install"
for arg in "$@"; do
    case "$arg" in
        --uninstall) uninstall ;;
        --update)    MODE="update" ;;
    esac
done

log_info "Mode: ${MODE}"

# ─── 1. System dependencies ───────────────────
log_info "Installing system dependencies..."
dnf install -y python3 python3-pip python3-devel \
              postgresql-server postgresql-devel \
              policycoreutils-python-utils 2>/dev/null || true

# ─── 2. PostgreSQL ────────────────────────────
if ! systemctl is-active --quiet postgresql; then
    log_info "Initialising PostgreSQL..."
    postgresql-setup --initdb
    systemctl enable --now postgresql
    sleep 2
fi

# configure pg_hba.conf:
#   - local socket → peer (so sudo -u postgres works without password)
#   - TCP on 127.0.0.1 → md5 (so Django/psycopg2 can connect)
PG_HBA="$(find /var/lib/pgsql -name pg_hba.conf 2>/dev/null | head -1)"
if [[ -n "$PG_HBA" ]]; then
    # local socket → peer (sudo -u postgres works without password)
    sed -i 's/^local\s\+all\s\+all\s\+md5/local   all             all                                     peer/' "$PG_HBA"
    sed -i 's/^local\s\+all\s\+all\s\+scram-sha-256/local   all             all                                     peer/' "$PG_HBA"
    # localhost TCP → md5 (psycopg2 connects via TCP, needs password)
    sed -i 's/^host\s\+all\s\+all\s\+127\.0\.0\.1\/32\s\+ident/host    all             all             127.0.0.1\/32            md5/' "$PG_HBA"
    sed -i 's/^host\s\+all\s\+all\s\+::1\/128\s\+ident/host    all             all             ::1\/128                 md5/' "$PG_HBA"
    systemctl restart postgresql
    sleep 2
fi

# create role + database (idempotent)
log_info "Configuring PostgreSQL..."
sudo -u postgres psql -t -c "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" 2>/dev/null | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';" || true
sudo -u postgres psql -t -c "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" 2>/dev/null | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};" || true
sudo -u postgres psql -c "ALTER USER ${DB_USER} CREATEDB;" 2>/dev/null || true

# ─── 3. Create system user ────────────────────
if ! id "${APP_USER}" &>/dev/null; then
    log_info "Creating user ${APP_USER}..."
    useradd --system --no-create-home --shell /sbin/nologin "${APP_USER}"
fi

# ─── 4. Copy project files ────────────────────
log_info "Copying project to ${APP_DIR}..."
install -d -o "${APP_USER}" -g "${APP_USER}" "${APP_DIR}"

# careful copy preserving permissions
if [[ "$MODE" == "update" ]]; then
    # keep venv, db, etc.
    rsync -a --delete \
        --exclude='venv' \
        --exclude='db.sqlite3' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.git' \
        "${SCRIPT_DIR}/" "${APP_DIR}/"
else
    rsync -a \
        --exclude='venv' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.git' \
        --exclude='deploy.sh' \
        "${SCRIPT_DIR}/" "${APP_DIR}/"
fi

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
chmod 750 "${APP_DIR}"

# ─── 5. Virtual environment ───────────────────
log_info "Setting up Python virtual environment..."
if [[ ! -d "${APP_DIR}/venv" ]]; then
    ${PYTHON} -m venv "${APP_DIR}/venv"
fi

"${APP_DIR}/venv/bin/pip" install --upgrade pip setuptools wheel
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
"${APP_DIR}/venv/bin/pip" install psycopg2-binary gunicorn

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/venv"

# ─── 6. Environment file ──────────────────────
log_info "Creating environment file..."
ENV_FILE="${APP_DIR}/.env"
cat > "$ENV_FILE" <<ENVEOF
FINV_DB_ENGINE=postgresql
FINV_DB_NAME=${DB_NAME}
FINV_DB_USER=${DB_USER}
FINV_DB_PASSWORD=${DB_PASS}
FINV_DB_HOST=localhost
FINV_DB_PORT=5432
DJANGO_SETTINGS_MODULE=finv.settings
ENVEOF
chown "${APP_USER}:${APP_USER}" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ─── 7. Django: migrate + static ──────────────
log_info "Running Django migrations..."
sudo -u "${APP_USER}" bash -c "set -a; source ${APP_DIR}/.env; set +a; cd ${APP_DIR} && source venv/bin/activate && python manage.py migrate --noinput"

log_info "Collecting static files..."
sudo -u "${APP_USER}" bash -c "set -a; source ${APP_DIR}/.env; set +a; cd ${APP_DIR} && source venv/bin/activate && python manage.py collectstatic --noinput --clear"

# ─── 8. Create default superuser ──────────────
log_info "Creating default superuser (admin:admin123)..."
sudo -u "${APP_USER}" bash -c "set -a; source ${APP_DIR}/.env; set +a; cd ${APP_DIR} && source venv/bin/activate && DJANGO_SUPERUSER_PASSWORD=admin123 python manage.py createsuperuser --username=admin --email=admin@example.com --noinput" 2>/dev/null || true

# ─── 9. SELinux ────────────────────────────────
if command -v getenforce &>/dev/null && [[ "$(getenforce)" != "Disabled" ]]; then
    log_info "Configuring SELinux..."
    # remove any previously registered broad rule that covered .env
    semanage fcontext -d "${APP_DIR}(/.*)?" 2>/dev/null || true
    # apply httpd context only to static files (for reverse proxy), not .env
    semanage fcontext -a -t httpd_sys_content_t "${APP_DIR}/staticfiles(/.*)?" 2>/dev/null || true
    restorecon -R "${APP_DIR}/staticfiles" 2>/dev/null || true
    # reset .env to default context so systemd can read it
    restorecon "${APP_DIR}/.env" 2>/dev/null || true
    setsebool -P httpd_can_network_connect on 2>/dev/null || true
fi

# ─── 10. systemd service ──────────────────────
log_info "Creating systemd service..."
cat > "$SERVICE_FILE" <<SERVICEEOF
[Unit]
Description=finv inventory system
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PATH=${APP_DIR}/venv/bin
ExecStart=${APP_DIR}/venv/bin/gunicorn finv.wsgi:application -b 0.0.0.0:8000 -w 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload
systemctl enable "${APP_NAME}"

# ─── 11. Start ────────────────────────────────
log_info "Starting ${APP_NAME}..."
systemctl restart "${APP_NAME}"

# ─── 12. Verify ───────────────────────────────
sleep 3
if systemctl is-active --quiet "${APP_NAME}"; then
    log_info "${APP_NAME} is RUNNING and enabled on port 8000."
    echo -e "  ${GREEN}✓${NC} Service:  ${SERVICE_FILE}"
    echo -e "  ${GREEN}✓${NC} App dir:  ${APP_DIR}"
    echo -e "  ${GREEN}✓${NC} DB:       ${DB_NAME} @ localhost:5432"
    echo -e "  ${GREEN}✓${NC} Superuser:   admin / admin123"
else
    log_error "${APP_NAME} failed to start. Check logs:"
    echo "  journalctl -u ${APP_NAME} -n 50 --no-pager"
    echo "  ${APP_DIR}/venv/bin/python ${APP_DIR}/manage.py check --deploy"
    exit 1
fi
