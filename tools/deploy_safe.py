#!/usr/bin/env python3
"""Guarded PostgreSQL deployment. No shell evaluation of configuration."""

import argparse
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

APP = Path('/opt/finv')
SOURCE = Path(__file__).resolve().parent.parent
BACKUPS = Path('/var/backups/finv')
UNIT = Path('/etc/systemd/system/finv.service')
MANAGED = ('finv', 'inventory', 'templates', 'static', 'manage.py', 'requirements.txt')
DB_KEYS = ('ENGINE', 'NAME', 'USER', 'PASSWORD', 'HOST', 'PORT')


class DeployError(Exception):
    pass


def read_env(path):
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, sep, value = line.partition('=')
        key = key.strip()
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key) or key in values:
            raise DeployError(f'Invalid or duplicate .env assignment at line {number}')
        try:
            parts = shlex.split(value, comments=False)
        except ValueError:
            raise DeployError(f'Invalid .env quoting at line {number}') from None
        if len(parts) > 1:
            raise DeployError(f'Quote .env value at line {number}')
        values[key] = parts[0] if parts else ''
    return values


def environment(config):
    # Never inherit caller FINV_*, PG*, PYTHON*, or Django settings.
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'HOME': str(APP), 'PYTHONDONTWRITEBYTECODE': '1'}
    env.update(config)
    env['DJANGO_SETTINGS_MODULE'] = 'finv.settings'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def validate_db(config):
    if config.get('FINV_DB_ENGINE') != 'postgresql':
        raise DeployError('Explicit FINV_DB_ENGINE=postgresql required; no SQLite fallback supported.')
    if any('FINV_DB_' + key not in config for key in DB_KEYS):
        raise DeployError('Specify all six FINV_DB_ENGINE/NAME/USER/PASSWORD/HOST/PORT keys in .env.')
    if not config['FINV_DB_NAME'] or not config['FINV_DB_USER']:
        raise DeployError('Database name and user must not be empty.')
    if '=' in config['FINV_DB_NAME'] or config['FINV_DB_NAME'].startswith(('postgres://', 'postgresql://')):
        raise DeployError('Use a plain database name, not a connection string.')


def production_config(existing, caller):
    config = dict(existing)
    if not config.get('FINV_ALLOWED_HOSTS'):
        config['FINV_ALLOWED_HOSTS'] = caller.get('FINV_ALLOWED_HOSTS', '')
    hosts = config['FINV_ALLOWED_HOSTS'].split(',')
    if not all(re.fullmatch(r'[A-Za-z0-9.\-:\[\]]+', host.strip()) for host in hosts):
        raise DeployError('Set explicit FINV_ALLOWED_HOSTS (comma-separated DNS names/IPs, no wildcard).')
    config['FINV_SECRET_KEY'] = config.get('FINV_SECRET_KEY') or secrets.token_urlsafe(48)
    config['FINV_DEBUG'] = 'false'
    mode = caller.get('FINV_TRANSPORT', config.get('FINV_TRANSPORT', 'http'))
    if mode not in ('http', 'https-proxy'):
        raise DeployError('FINV_TRANSPORT must be http or https-proxy.')
    config['FINV_TRANSPORT'] = mode
    secure = mode == 'https-proxy'
    for key in ('FINV_COOKIE_SECURE', 'FINV_SECURE_SSL_REDIRECT', 'FINV_TRUST_PROXY_HEADERS'):
        config[key] = str(secure).lower()
    config['FINV_HSTS_SECONDS'] = '31536000' if secure else '0'
    config['DJANGO_SETTINGS_MODULE'] = 'finv.settings'
    return config


def write_env(path, config, account):
    # Double-quoted systemd EnvironmentFile syntax; no shell is ever used.
    lines = []
    for key, value in config.items():
        if any(char in value for char in '\n\r\0'):
            raise DeployError('Multiline/NUL environment values are unsupported.')
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{key}="{escaped}"\n')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        os.fchmod(stream.fileno(), 0o600)
        os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
        stream.writelines(lines)
    tmp.replace(path)


def run(args, config=None, app_user=False, capture=False, cwd=None):
    options = {}
    if app_user and os.geteuid() == 0:
        account = pwd.getpwnam('finv')
        options.update(user=account.pw_uid, group=account.pw_gid, extra_groups=[])
    # Capture failures, which may contain credentials from drivers/settings.
    result = subprocess.run([str(arg) for arg in args], cwd=cwd,
                            env=environment(config or {}), text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    if result.returncode:
        if SCHEMA_CHECK in args and result.stdout.startswith('Missing tables/columns: '):
            print(result.stdout.strip())
        raise DeployError(f'{Path(str(args[0])).name} command failed (exit {result.returncode}); output withheld to protect secrets.')
    if capture:
        return result.stdout
    return None


DB_PROBE = r'''
import os, psycopg2, sys
e = os.environ
c = psycopg2.connect(dbname=e['FINV_DB_NAME'], user=e['FINV_DB_USER'],
    password=e['FINV_DB_PASSWORD'], host=e['FINV_DB_HOST'], port=e['FINV_DB_PORT'], connect_timeout=10)
c.set_session(readonly=True)
with c.cursor() as s:
    s.execute("SELECT current_setting('default_transaction_read_only'), has_schema_privilege(current_user, 'public', 'CREATE')")
    readonly, create = s.fetchone()
    s.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f')")
    count = s.fetchone()[0]
    if sys.argv[1] == 'install' and count:
        raise RuntimeError('Target database is not empty')
    if sys.argv[1] != 'check':
        if readonly != 'off' or not create:
            raise RuntimeError('Database is not writable or lacks schema CREATE permission')
        s.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND NOT pg_has_role(current_user, tableowner, 'USAGE')")
        if s.fetchone()[0]:
            raise RuntimeError('Migration role does not own existing public tables')
print('PostgreSQL connectivity and permissions: OK; relation count:', count)
c.close()
'''

DJANGO_SETUP = r'''
import django, os
django.setup()
from django.db import connection
db = connection.settings_dict
assert db['ENGINE'] == 'django.db.backends.postgresql', 'Refusing non-PostgreSQL Django settings'
for key in ('NAME', 'USER', 'PASSWORD', 'HOST', 'PORT'):
    assert str(db[key]) == os.environ['FINV_DB_' + key], 'Django DB configuration differs from deployed environment'
'''

SCHEMA_CHECK = DJANGO_SETUP + r'''
from django.apps import apps
missing = []
with connection.cursor() as cursor:
    tables = set(connection.introspection.table_names(cursor))
    for model in apps.get_models(include_auto_created=True):
        meta = model._meta
        if not meta.managed or meta.proxy or meta.swapped:
            continue
        if meta.db_table not in tables:
            missing.append(meta.db_table)
            continue
        columns = {c.name for c in connection.introspection.get_table_description(cursor, meta.db_table)}
        missing.extend(meta.db_table + '.' + f.column for f in meta.local_fields if f.column not in columns)
if missing:
    print('Missing tables/columns: ' + ', '.join(missing))
    raise SystemExit(1)
print('Current model tables and columns: OK')
'''

def python_command(python, config, *args):
    if args[0] == 'manage.py':
        args = ('-c', DJANGO_SETUP + '\nimport sys\nfrom django.core.management import execute_from_command_line\nexecute_from_command_line(["manage.py", *sys.argv[1:]])', *args[1:])
    return run([python, '-B', *args], config, app_user=True, capture=True, cwd=APP)


def preflight(mode, config, python):
    if SOURCE.resolve() == APP.resolve() or SOURCE.resolve() in APP.resolve().parents or APP.resolve() in SOURCE.resolve().parents:
        raise DeployError('Release source and destination must be separate, non-nested paths.')
    validate_db(config)
    if mode != 'check' and os.geteuid() != 0:
        raise DeployError('Install/update requires root. Help and check do not.')
    if mode != 'check':
        for tool in ('rsync', 'pg_dump', 'pg_restore', 'systemctl', 'tar'):
            if not shutil.which(tool):
                raise DeployError(f'Required tool missing: {tool}')
        if not all((SOURCE / item).exists() for item in MANAGED):
            raise DeployError('Incomplete release source.')
        if any(p.is_symlink() for name in MANAGED for p in [SOURCE / name, *(SOURCE / name).rglob('*')]):
            raise DeployError('Managed release source must not contain symlinks.')
        if not os.access(APP if APP.exists() else APP.parent, os.W_OK):
            raise DeployError('Destination is not writable.')
        pwd.getpwnam('finv')
        if BACKUPS.resolve() == APP.resolve() or APP.resolve() in BACKUPS.resolve().parents:
            raise DeployError('Backup directory must be outside the application.')
        if BACKUPS.exists() and (BACKUPS.is_symlink() or BACKUPS.stat().st_mode & 0o077 or BACKUPS.stat().st_uid != 0):
            raise DeployError('Backup parent must be root-owned, non-symlinked and mode 0700.')
        if mode == 'update' and any(p.is_symlink() for name in MANAGED for p in [APP / name, *(APP / name).rglob('*')]):
            raise DeployError('Managed destination paths must not contain symlinks.')
    if mode == 'install':
        if APP.exists() or UNIT.exists():
            raise DeployError('Fresh install refuses an existing destination or unit. Use --update; never drop the DB.')
    else:
        for path in (APP / '.env', python, UNIT):
            if not path.exists():
                raise DeployError(f'Required deployed path missing: {path}')
        if APP.is_symlink() or (APP / '.env').is_symlink():
            raise DeployError('Symlinked deployment/configuration is unsupported.')
        if mode == 'update':
            metadata = run(['systemctl', 'show', 'finv', '-p', 'User', '-p', 'WorkingDirectory',
                            '-p', 'EnvironmentFiles', '-p', 'Environment', '-p', 'UnsetEnvironment', '-p', 'ExecStart'], capture=True)
            if ('User=finv\n' not in metadata or f'WorkingDirectory={APP}\n' not in metadata
                    or f'EnvironmentFiles={APP}/.env (ignore_errors=no)\n' not in metadata
                    or f'{APP}/venv/bin/gunicorn ' not in metadata):
                raise DeployError('Unsupported service configuration; require finv user, deployed .env and venv gunicorn. Review unit manually.')
            for line in metadata.splitlines():
                if line.startswith('UnsetEnvironment=') and line != 'UnsetEnvironment=':
                    raise DeployError('Service UnsetEnvironment is unsupported; review unit manually.')
                if line.startswith('Environment='):
                    if any(not item.startswith('PATH=') for item in shlex.split(line.partition('=')[2])):
                        raise DeployError('Service configuration must live in .env (only PATH may be in Environment=).')
            run([python, '-B', '-c', 'import os,sys; assert os.access(sys.argv[1], os.W_OK) and os.access(sys.argv[2], os.W_OK)', APP, APP / 'venv'], config, app_user=True)
    run([python, '-B', '-c', 'import sys; assert sys.version_info >= (3,10)'], config)
    probe_config = dict(config)
    if mode == 'check':
        probe_config['PGOPTIONS'] = config.get('PGOPTIONS', '') + ' -c default_transaction_read_only=on'
    print('Preflight: PostgreSQL connectivity, target emptiness (install), and migration permissions.', flush=True)
    output = run([python, '-B', '-c', DB_PROBE, mode], probe_config, app_user=True, capture=True)
    print(output.strip())


def backup(config):
    if BACKUPS.resolve() == APP.resolve() or APP.resolve() in BACKUPS.resolve().parents:
        raise DeployError('Backup directory must be outside the application.')
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    if BACKUPS.is_symlink() or BACKUPS.stat().st_mode & 0o077:
        raise DeployError('Backup parent must be a real directory with mode 0700.')
    folder = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S-'), dir=BACKUPS))
    print(f'Backup/recovery directory: {folder}', flush=True)
    pg = dict(config)
    for key, target in (('NAME', 'PGDATABASE'), ('USER', 'PGUSER'), ('PASSWORD', 'PGPASSWORD'),
                        ('HOST', 'PGHOST'), ('PORT', 'PGPORT')):
        pg[target] = config['FINV_DB_' + key]
    print('Backup: full pg_dump and archive validation.', flush=True)
    run(['pg_dump', '--format=custom', '--file', folder / 'database.dump'], pg)
    run(['pg_restore', '--list', folder / 'database.dump'], pg)
    if not (folder / 'database.dump').stat().st_size:
        raise DeployError('Empty database backup.')
    print('Backup: full old application/config/venv and service metadata.', flush=True)
    run(['tar', '--acls', '--xattrs', '-cpf', folder / 'application.tar', '-C', APP.parent, APP.name])
    run(['tar', '-tf', folder / 'application.tar'])
    (folder / 'service.txt').write_text(run(['systemctl', 'cat', 'finv'], capture=True))
    (folder / 'service-state.txt').write_text(run(['systemctl', 'show', 'finv'], capture=True))
    shutil.copy2(UNIT, folder / 'finv.service')
    (folder / 'COMPLETE').write_text('Full database, application and service snapshot completed.\n')
    return folder


def readiness(config):
    host = config['FINV_ALLOWED_HOSTS'].split(',')[0].strip().lstrip('.')
    url = os.environ.get('FINV_READY_URL', 'http://127.0.0.1:8000/login/')
    if config['FINV_TRANSPORT'] == 'https-proxy' and not url.startswith('https://'):
        raise DeployError('HTTPS proxy mode requires explicit FINV_READY_URL=https://.../login/.')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for _ in range(30):
        try:
            with opener.open(urllib.request.Request(url, headers={'Host': host}), timeout=2) as response:
                if response.status == 200:
                    run(['systemctl', 'is-active', '--quiet', 'finv'])
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise DeployError('HTTP readiness failed (expected HTTP 200, no redirects).')


def deploy(mode):
    existing = read_env(APP / '.env') if mode != 'install' else {
        key: value for key, value in os.environ.items() if key.startswith(('FINV_', 'PG'))}
    python = APP / 'venv/bin/python' if mode != 'install' else Path(sys.executable)
    preflight(mode, existing, python)
    if mode == 'check':
        diagnostic = dict(existing)
        if not diagnostic.get('FINV_SECRET_KEY'):
            print('Missing FINV_SECRET_KEY: update will generate a stable secret after backup.')
            diagnostic['FINV_SECRET_KEY'] = secrets.token_urlsafe(48)
        if not diagnostic.get('FINV_ALLOWED_HOSTS'):
            print('Missing FINV_ALLOWED_HOSTS: supply explicitly for update.')
        diagnostic['PGOPTIONS'] = existing.get('PGOPTIONS', '') + ' -c default_transaction_read_only=on'
        failed = False
        for args in [('manage.py', 'check'), ('manage.py', 'showmigrations'),
                     ('manage.py', 'migrate', '--plan'), ('manage.py', 'migrate', '--check'),
                     ('-c', SCHEMA_CHECK)]:
            try:
                print(python_command(python, diagnostic, *args).strip())
            except DeployError as error:
                label = ' '.join(args[:3]) if args[0] != '-c' else 'model table/column introspection'
                print(f'Diagnostic failed: {label}: {error}')
                failed = True
        if failed:
            raise DeployError('Deployed schema/migration checks failed. Back up and use --update; do not fake migrations.')
        return
    config = production_config(existing, os.environ)
    if config['FINV_TRANSPORT'] == 'https-proxy' and not os.environ.get('FINV_READY_URL', '').startswith('https://'):
        raise DeployError('Set FINV_READY_URL=https://your-host/login/ for external TLS readiness.')
    account = pwd.getpwnam('finv')
    stopped = False
    snapshot = None
    try:
        if mode == 'update':
            stopped = True
            run(['systemctl', 'stop', 'finv'])
            snapshot = backup(existing)
        else:
            APP.mkdir(mode=0o750)
            os.chown(APP, account.pw_uid, account.pw_gid)
            stopped = True
        # Only release-owned code paths are copied. Never delete destination files.
        print('Mutation: copying managed code and saving production configuration.', flush=True)
        run(['rsync', '-a', '--chown=finv:finv', '--exclude=__pycache__', '--exclude=*.pyc',
             '--exclude=*.csv', '--exclude=*.jsonl', '--exclude=.env', '--exclude=venv',
             '--exclude=db.sqlite3*', '--exclude=media', '--exclude=logs', '--exclude=backups',
             *[SOURCE / name for name in MANAGED], str(APP) + '/'])
        write_env(APP / '.env', config, account)
        if mode == 'install':
            run([python, '-B', '-m', 'venv', APP / 'venv'], config, app_user=True)
            python = APP / 'venv/bin/python'
        print('Dependencies: installing into the server venv.', flush=True)
        python_command(python, config, '-m', 'pip', 'install', '-r', str(APP / 'requirements.txt'))
        for args in [('check',), ('migrate', '--plan'), ('migrate', '--noinput'), ('migrate', '--check')]:
            print('Running manage.py ' + ' '.join(args), flush=True)
            python_command(python, config, 'manage.py', *args)
            print('manage.py ' + ' '.join(args) + ': OK')
        print('Verifying current model tables/columns, then collecting static files.', flush=True)
        python_command(python, config, '-c', SCHEMA_CHECK)
        python_command(python, config, 'manage.py', 'collectstatic', '--noinput')
        if mode == 'install':
            bind = '127.0.0.1:8000' if config['FINV_TRANSPORT'] == 'https-proxy' else '127.0.0.1:8001'
            UNIT.write_text('[Unit]\nDescription=finv inventory\nAfter=network.target postgresql.service\n'
                            'Requires=postgresql.service\n\n'
                            '[Service]\nUser=finv\nGroup=finv\n'
                            f'WorkingDirectory={APP}\nEnvironmentFile={APP}/.env\n'
                            f'ExecStart={APP}/venv/bin/gunicorn finv.wsgi:application -b {bind} -w 4\n'
                            'Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n')
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'enable', 'finv'])
        print('Starting finv and checking HTTP readiness.', flush=True)
        run(['systemctl', 'start', 'finv'])
        readiness(config)
        print(f'Deployment ready ({config["FINV_TRANSPORT"]}); existing update unit preserved.')
    except BaseException:
        if stopped:
            try:
                run(['systemctl', 'stop', 'finv'])
            except (DeployError, OSError):
                print('WARNING: service stop failed; operator must ensure finv is stopped.', file=sys.stderr)
            print(f'FAILURE: leave finv stopped. No automatic DB rollback. Backup: {snapshot or "see backup directory printed above (may be incomplete)"}. '
                  'See DEPLOY.md for recovery.', file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description='Safe finv PostgreSQL deployment; see DEPLOY.md.', allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group()
    for mode in ('install', 'update', 'check'):
        modes.add_argument('--' + mode, action='store_const', const=mode, dest='mode')
    modes.add_argument('--uninstall', action='store_const', const='uninstall', dest='mode', help='refused: never deletes databases')
    args = parser.parse_args()
    if args.mode == 'uninstall':
        parser.error('Uninstall removed. This tool never deletes an existing database or installation.')
    if sys.version_info < (3, 10):
        parser.error('Python >= 3.10 required.')
    os.umask(0o077)
    def interrupted(signum, frame):
        raise DeployError('Deployment interrupted; inspect the recovery instructions.')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        deploy(args.mode or 'install')
    except (DeployError, OSError, KeyError) as error:
        print(f'Deployment refused/failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
