#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HELPER="${SCRIPT_DIR}/tools/deploy_safe.py"

# This entry point intentionally supports only a clean 4.1 installation.
if (( $# == 1 )) && [[ $1 == "--help" || $1 == "-h" ]]; then
    printf '%s\n' 'Usage: sudo env FINV_ALLOWED_HOSTS=SERVER_IP bash deploy.sh [--install]'
    printf '%s\n' 'Clean finv 4.1 installation only; existing applications and databases are never imported or replaced.'
    exit 0
fi
if (( $# > 1 )) || (( $# == 1 )) && [[ $1 != "--install" ]]; then
    printf '%s\n' 'Only --install is supported. This script never updates or imports a 4.0 installation.' >&2
    exit 2
fi

if (( EUID != 0 )); then
    printf '%s\n' 'Fresh installation requires root; run with sudo.' >&2
    exit 1
fi
if [[ -e /opt/finv || -e /etc/systemd/system/finv.service ]] || id finv >/dev/null 2>&1; then
    printf '%s\n' 'Fresh installation refused: /opt/finv, finv.service or system account finv already exists.' >&2
    exit 1
fi
if [[ -z ${FINV_ALLOWED_HOSTS:-} ]]; then
    printf '%s\n' 'Set FINV_ALLOWED_HOSTS to the server DNS name or IP address.' >&2
    printf '%s\n' "Example: sudo env FINV_ALLOWED_HOSTS=172.27.5.2 bash '$0' --install" >&2
    exit 1
fi
for required in finv inventory templates static manage.py requirements.txt tools/deploy_safe.py; do
    if [[ ! -e ${SCRIPT_DIR}/${required} || -L ${SCRIPT_DIR}/${required} ]]; then
        printf '%s\n' "Incomplete release or unsupported symlink: ${required}" >&2
        exit 1
    fi
done
case ${FINV_TRANSPORT:-http} in
    http|https-proxy) ;;
    *) printf '%s\n' 'FINV_TRANSPORT must be http or https-proxy.' >&2; exit 1 ;;
esac
if [[ ${FINV_TRANSPORT:-http} == https-proxy && ${FINV_READY_URL:-} != https://* ]]; then
    printf '%s\n' 'HTTPS proxy mode requires FINV_READY_URL=https://.../login/.' >&2
    exit 1
fi

printf '%s\n' '[1/8] Installing RedOS packages...'
dnf install -y python3 python3-pip python3-devel python3-psycopg2 \
    postgresql-server postgresql-devel rsync tar nginx policycoreutils-python-utils
python3 -B -c 'import sys; assert sys.version_info >= (3, 10), "Python >= 3.10 required"'
python3 -B -c 'import psycopg2' || {
    printf '%s\n' 'The installed python3 cannot import psycopg2.' >&2
    exit 1
}
python3 -B -c 'import os,re,sys; hosts=os.environ["FINV_ALLOWED_HOSTS"].split(","); sys.exit(not all(re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", host.strip()) for host in hosts))' || {
    printf '%s\n' 'FINV_ALLOWED_HOSTS must contain comma-separated DNS names or IP addresses without wildcards.' >&2
    exit 1
}

printf '%s\n' '[2/8] Initializing PostgreSQL...'
if [[ ! -s /var/lib/pgsql/data/PG_VERSION ]]; then
    postgresql-setup --initdb
fi
systemctl enable --now postgresql

ROLE_EXISTS="$(runuser -u postgres -- psql --set=ON_ERROR_STOP=1 -Atqc "SELECT 1 FROM pg_roles WHERE rolname='finv_user'")"
if [[ ${ROLE_EXISTS} == 1 ]]; then
    printf '%s\n' 'Fresh installation refused: PostgreSQL role finv_user already exists.' >&2
    exit 1
fi

DATABASE_EXISTS="$(runuser -u postgres -- psql --set=ON_ERROR_STOP=1 -Atqc "SELECT 1 FROM pg_database WHERE datname='finv'")"
if [[ ${DATABASE_EXISTS} == 1 ]]; then
    printf '%s\n' 'Fresh installation refused: PostgreSQL database finv already exists.' >&2
    exit 1
fi

printf '%s\n' '[3/8] Creating the finv system account...'
useradd --system --no-create-home --shell /sbin/nologin finv

printf '%s\n' '[4/8] Creating the PostgreSQL role and database...'
FINV_DB_PASSWORD="$(python3 -B -c 'import secrets; print(secrets.token_hex(32))')"
runuser -u postgres -- psql --set=ON_ERROR_STOP=1 --dbname=postgres <<SQL
SET password_encryption = 'scram-sha-256';
CREATE ROLE finv_user LOGIN PASSWORD '${FINV_DB_PASSWORD}';
SQL
runuser -u postgres -- createdb --owner=finv_user finv

HBA_FILE="$(runuser -u postgres -- psql -Atqc 'SHOW hba_file')"
HBA_RULE='host    finv    finv_user    127.0.0.1/32    scram-sha-256'
if ! grep -Fqx "${HBA_RULE}" "${HBA_FILE}"; then
    HBA_TMP="$(mktemp "${HBA_FILE}.finv.XXXXXX")"
    {
        printf '%s\n' '# finv local application connection'
        printf '%s\n' "${HBA_RULE}"
        command cat "${HBA_FILE}"
    } >"${HBA_TMP}"
    chown postgres:postgres "${HBA_TMP}"
    chmod 600 "${HBA_TMP}"
    mv -f "${HBA_TMP}" "${HBA_FILE}"
    restorecon "${HBA_FILE}" 2>/dev/null || true
    systemctl reload postgresql
fi

if ! runuser -u finv -- env PGPASSWORD="${FINV_DB_PASSWORD}" \
        psql --host=127.0.0.1 --username=finv_user --dbname=finv \
        --no-password -Atqc 'SELECT current_user' | grep -qx finv_user; then
    printf '%s\n' 'PostgreSQL password authentication check failed.' >&2
    exit 1
fi

printf '%s\n' '[5/8] Running guarded application installation...'
export FINV_DB_ENGINE=postgresql
export FINV_DB_NAME=finv
export FINV_DB_USER=finv_user
export FINV_DB_PASSWORD
export FINV_DB_HOST=127.0.0.1
export FINV_DB_PORT=5432
export FINV_TRANSPORT="${FINV_TRANSPORT:-http}"
if [[ ${FINV_TRANSPORT} == http ]]; then
    export FINV_READY_URL=http://127.0.0.1:8001/login/
fi
python3 -B "${HELPER}" --install

printf '%s\n' '[6/8] Preparing static and uploaded files...'
install -d -m 750 -o finv -g finv /opt/finv/media
chmod 750 /opt/finv
chmod -R g+rX /opt/finv/staticfiles /opt/finv/media

printf '%s\n' '[7/8] Configuring HTTP frontend and RedOS file contexts...'
if command -v restorecon >/dev/null 2>&1; then
    if command -v semanage >/dev/null 2>&1; then
        semanage fcontext -a -t httpd_sys_content_t '/opt/finv/staticfiles(/.*)?' 2>/dev/null || \
            semanage fcontext -m -t httpd_sys_content_t '/opt/finv/staticfiles(/.*)?' || true
        semanage fcontext -a -t httpd_sys_rw_content_t '/opt/finv/media(/.*)?' 2>/dev/null || \
            semanage fcontext -m -t httpd_sys_rw_content_t '/opt/finv/media(/.*)?' || true
    fi
    restorecon -R /opt/finv/staticfiles /opt/finv/media /etc/systemd/system/finv.service || true
fi

if [[ ${FINV_TRANSPORT} == http ]]; then
    usermod -a -G finv nginx
    command cat >/etc/nginx/conf.d/finv.conf <<'NGINX'
server {
    listen 8000;
    server_name _;
    client_max_body_size 6m;

    location /static/ {
        alias /opt/finv/staticfiles/;
    }
    location /media/ {
        alias /opt/finv/media/;
    }
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
    if command -v setsebool >/dev/null 2>&1 \
            && { ! command -v getenforce >/dev/null 2>&1 || [[ $(getenforce) != Disabled ]]; }; then
        setsebool -P httpd_can_network_connect on
        semanage port -a -t http_port_t -p tcp 8000 2>/dev/null || \
            semanage port -m -t http_port_t -p tcp 8000
    fi
    nginx -t
    systemctl enable --now nginx
    if systemctl is-active --quiet firewalld && command -v firewall-cmd >/dev/null 2>&1; then
        firewall-cmd --permanent --add-port=8000/tcp
        firewall-cmd --reload
    fi
    python3 -B -c 'import sys,urllib.request; host=sys.argv[1].split(",")[0].strip(); opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); request=urllib.request.Request("http://127.0.0.1:8000/login/", headers={"Host": host}); response=opener.open(request, timeout=10); assert response.status == 200' "${FINV_ALLOWED_HOSTS}"
fi

printf '%s\n' '[8/8] Installation complete.'
printf '%s\n' 'Create the first administrator with:'
printf '%s\n' "  sudo -u finv bash -c 'set -a; source /opt/finv/.env; set +a; /opt/finv/venv/bin/python /opt/finv/manage.py createsuperuser'"
if [[ ${FINV_TRANSPORT} == http ]]; then
    printf '%s\n' "Open http://${FINV_ALLOWED_HOSTS%%,*}:8000/"
fi
