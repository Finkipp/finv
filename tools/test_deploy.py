"""Isolated deployment tests: no real database, root operations or services."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location('deploy_safe', Path(__file__).with_name('deploy_safe.py'))
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)
ROOT = Path(__file__).resolve().parent.parent
OLD = dict(FINV_DB_ENGINE='postgresql', FINV_DB_NAME='legacy_inventory',
           FINV_DB_USER='legacy_user', FINV_DB_PASSWORD='finv_password',
           FINV_DB_HOST='db.internal', FINV_DB_PORT='5433',
           PGSSLMODE='require', PGOPTIONS='-c statement_timeout=90000', CUSTOM_OPTION='keep me')


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='deploy-test-', dir='/tmp/opencode')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / 'app'
        self.app.mkdir()
        self.source = self.base / 'release'
        self.source.mkdir()
        self.unit = self.base / 'finv.service'
        self.unit.write_text('old unit')
        self.envfile = self.app / '.env'
        self.envfile.write_text(''.join(f'{k}="{v}"\n' for k, v in OLD.items()))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [('APP', self.app), ('SOURCE', self.source),
                            ('UNIT', self.unit), ('BACKUPS', self.base / 'backups')]:
            self.stack.enter_context(patch.object(deploy, name, value))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def test_shell_syntax_and_arguments(self):
        subprocess.run(['bash', '-n', str(ROOT / 'deploy.sh')], check=True)
        for args in [('--wat',), ('--up',), ('--update',), ('--update', '--install'),
                      ('--check', '--update'), ('--uninstall',)]:
            result = subprocess.run(['bash', str(ROOT / 'deploy.sh'), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
        result = subprocess.run(['bash', str(ROOT / 'deploy.sh'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)

    def test_public_installer_is_clean_install_only(self):
        script = (ROOT / 'deploy.sh').read_text()
        self.assertNotIn('--delete', script)
        self.assertNotIn('DROP DATABASE', script)
        self.assertNotIn('DROP ROLE', script)
        self.assertIn('proxy_pass http://127.0.0.1:8001', script)
        self.assertIn('semanage port -a -t http_port_t -p tcp 8000', script)
        self.assertNotIn('0.0.0.0:8000', (ROOT / 'tools/deploy_safe.py').read_text())
        result = subprocess.run(['bash', str(ROOT / 'deploy.sh'), '--update'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('never updates or imports', result.stderr)

    def test_config_preserves_database_and_old_password(self):
        caller = {key: 'ATTACKER' for key in OLD}
        caller['FINV_ALLOWED_HOSTS'] = 'inventory.example.org,192.0.2.8'
        config = deploy.production_config(deploy.read_env(self.envfile), caller)
        for key, value in OLD.items():
            self.assertEqual(config[key], value)
        self.assertEqual(config['FINV_DEBUG'], 'false')
        for key in ('FINV_COOKIE_SECURE', 'FINV_SECURE_SSL_REDIRECT', 'FINV_TRUST_PROXY_HEADERS'):
            self.assertEqual(config[key], 'false')
        self.assertEqual(config['FINV_HSTS_SECONDS'], '0')
        self.assertEqual(deploy.production_config(config, caller)['FINV_SECRET_KEY'], config['FINV_SECRET_KEY'])
        with patch.dict(os.environ, caller):
            self.assertEqual(deploy.environment(config)['FINV_DB_PASSWORD'], 'finv_password')

    def test_missing_hosts_and_invalid_engines_refused(self):
        with self.assertRaises(deploy.DeployError):
            deploy.production_config(OLD, {})
        for engine in ('', 'sqlite', 'postgres', 'typo'):
            with self.assertRaises(deploy.DeployError):
                deploy.validate_db(dict(OLD, FINV_DB_ENGINE=engine))
        incomplete = dict(OLD)
        del incomplete['FINV_DB_PASSWORD']
        with self.assertRaises(deploy.DeployError):
            deploy.validate_db(incomplete)

    def test_https_requires_explicit_choice(self):
        config = deploy.production_config(OLD, {'FINV_ALLOWED_HOSTS': 'example.org', 'FINV_TRANSPORT': 'https-proxy'})
        self.assertEqual(config['FINV_COOKIE_SECURE'], 'true')
        self.assertEqual(config['FINV_TRUST_PROXY_HEADERS'], 'true')

    def test_literal_env_roundtrip_without_shell(self):
        config = dict(OLD, FINV_DB_PASSWORD='finv#pass $HOME $(touch NEVER) `id` " \\ end')
        account = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
        with patch.object(deploy.os, 'fchown'):
            deploy.write_env(self.envfile, config, account)
        self.assertEqual(deploy.read_env(self.envfile), config)
        self.assertEqual(self.envfile.stat().st_mode & 0o777, 0o600)
        self.envfile.write_text('FINV_DB_PASSWORD=finv#password\n')
        self.assertEqual(deploy.read_env(self.envfile)['FINV_DB_PASSWORD'], 'finv#password')

    def test_same_source_refused_before_commands(self):
        with patch.object(deploy, 'SOURCE', self.app), patch.object(deploy, 'run') as run:
            with self.assertRaisesRegex(deploy.DeployError, 'separate'):
                deploy.preflight('update', OLD, self.app / 'venv/bin/python')
            run.assert_not_called()

    def test_install_refuses_existing_destination_before_db_probe(self):
        for item in deploy.MANAGED:
            (self.source / item).touch()
        with patch.object(deploy.os, 'geteuid', return_value=0), \
                patch.object(deploy.shutil, 'which', return_value='/mock/tool'), \
                patch.object(deploy.pwd, 'getpwnam'), patch.object(deploy, 'run') as run:
            with self.assertRaisesRegex(deploy.DeployError, 'existing destination'):
                deploy.preflight('install', OLD, Path(sys.executable))
            run.assert_not_called()

    def test_readiness_failure_stops_after_start(self):
        self.mock_update()
        with patch.object(deploy, 'backup', return_value=self.base / 'snapshot'), \
                patch.object(deploy, 'run', return_value='') as run, \
                patch.object(deploy.os, 'fchown'), \
                patch.object(deploy, 'readiness', side_effect=deploy.DeployError('not ready')):
            with self.assertRaises(deploy.DeployError):
                deploy.deploy('update')
        self.assertEqual(run.call_args_list[-2].args[0], ['systemctl', 'start', 'finv'])
        self.assertEqual(run.call_args_list[-1].args[0], ['systemctl', 'stop', 'finv'])

    def mock_update(self):
        self.stack.enter_context(patch.object(deploy, 'preflight'))
        self.stack.enter_context(patch.object(deploy.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())))
        self.stack.enter_context(patch.dict(os.environ, {'FINV_ALLOWED_HOSTS': 'example.org', 'FINV_DB_PASSWORD': 'wrong'}, clear=True))

    def test_backup_failure_prevents_all_mutations(self):
        self.mock_update()
        original = self.envfile.read_bytes()
        with patch.object(deploy, 'backup', side_effect=deploy.DeployError('dump failed')), \
                patch.object(deploy, 'run') as run, patch.object(deploy, 'write_env') as write:
            with self.assertRaises(deploy.DeployError):
                deploy.deploy('update')
            write.assert_not_called()
            self.assertEqual([c.args[0] for c in run.call_args_list],
                             [['systemctl', 'stop', 'finv'], ['systemctl', 'stop', 'finv']])
        self.assertEqual(self.envfile.read_bytes(), original)
        self.assertEqual(self.unit.read_text(), 'old unit')

    def test_pg_dump_failure_has_no_complete_marker(self):
        with patch.object(deploy, 'run', side_effect=deploy.DeployError('dump failed')) as run:
            with self.assertRaises(deploy.DeployError):
                deploy.backup(OLD)
            args, config = run.call_args.args
            self.assertEqual(args[0], 'pg_dump')
            self.assertIn('--format=custom', args)
            self.assertEqual(config['PGPASSWORD'], 'finv_password')
            self.assertEqual(config['PGOPTIONS'], OLD['PGOPTIONS'])
        self.assertFalse(list((self.base / 'backups').rglob('COMPLETE')))

    def test_update_order_environment_and_preserved_unit(self):
        self.mock_update()
        events = []
        def fake_run(args, config=None, **kwargs):
            events.append(('run', [str(a) for a in args], config))
            return ''
        def fake_backup(config):
            events.append(('backup', config))
            return self.base / 'snapshot'
        with patch.object(deploy, 'backup', side_effect=fake_backup), \
                patch.object(deploy, 'run', side_effect=fake_run), \
                patch.object(deploy.os, 'fchown'), patch.object(deploy, 'readiness') as ready:
            deploy.deploy('update')
        self.assertEqual(events[0][1], ['systemctl', 'stop', 'finv'])
        self.assertEqual(events[1][0], 'backup')
        self.assertEqual(events[2][1][0], 'rsync')
        self.assertNotIn('--delete', events[2][1])
        self.assertEqual(self.unit.read_text(), 'old unit')
        python_calls = [event for event in events if event[0] == 'run' and event[1][0].endswith('/venv/bin/python')]
        self.assertEqual(len(python_calls), 7)
        for _, args, config in python_calls:
            for key, value in OLD.items():
                self.assertEqual(config[key], value)
            self.assertIn('-B', args)
        ready.assert_called_once()
        self.assertEqual(events[-1][1], ['systemctl', 'start', 'finv'])

    def test_failed_migration_never_starts(self):
        self.mock_update()
        def fail_migration(args, *unused, **kwargs):
            if '--noinput' in args:
                raise deploy.DeployError('migration failed')
            return ''
        with patch.object(deploy, 'backup', return_value=self.base / 'snapshot'), \
                patch.object(deploy, 'run', side_effect=fail_migration) as run, \
                patch.object(deploy.os, 'fchown'):
            with self.assertRaises(deploy.DeployError):
                deploy.deploy('update')
        self.assertFalse(any(call.args[0] == ['systemctl', 'start', 'finv'] for call in run.call_args_list))
        self.assertEqual(run.call_args.args[0], ['systemctl', 'stop', 'finv'])

    def test_check_only_read_commands_and_readonly_database(self):
        with patch.object(deploy, 'preflight'), patch.object(deploy, 'run', return_value='OK') as run:
            deploy.deploy('check')
        self.assertEqual(len(run.call_args_list), 5)
        for call in run.call_args_list:
            args, config = call.args
            self.assertIn('default_transaction_read_only=on', config['PGOPTIONS'])
            self.assertEqual(config['FINV_DB_PASSWORD'], OLD['FINV_DB_PASSWORD'])
            self.assertNotIn('--noinput', args)
            self.assertNotIn('systemctl', args)

    def test_subprocess_environment_does_not_inherit_caller(self):
        with patch.dict(os.environ, {'FINV_DB_ENGINE': 'sqlite', 'PGPASSWORD': 'wrong', 'PYTHONPATH': '/evil'}), \
                patch.object(deploy.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='')) as run:
            deploy.run([sys.executable, '-B', '-c', 'pass'], OLD)
        env = run.call_args.kwargs['env']
        self.assertEqual(env['FINV_DB_ENGINE'], 'postgresql')
        self.assertNotIn('PGPASSWORD', env)
        self.assertNotIn('PYTHONPATH', env)

    def test_schema_introspection_reports_missing_table_and_column(self):
        meta = types.SimpleNamespace(managed=True, proxy=False, swapped=False,
                                     db_table='inventory_userprofile',
                                     local_fields=[types.SimpleNamespace(column='user_id')])
        connection = MagicMock()
        connection.settings_dict = {'ENGINE': 'django.db.backends.postgresql',
                                    **{key: OLD['FINV_DB_' + key] for key in deploy.DB_KEYS if key != 'ENGINE'}}
        modules = {'django': types.SimpleNamespace(setup=lambda: None),
                   'django.db': types.SimpleNamespace(connection=connection),
                   'django.apps': types.SimpleNamespace(apps=types.SimpleNamespace(
                       get_models=lambda **kwargs: [types.SimpleNamespace(_meta=meta)]))}
        for tables, expected in [([], 'inventory_userprofile'),
                                 (['inventory_userprofile'], 'inventory_userprofile.user_id')]:
            connection.introspection.table_names.return_value = tables
            connection.introspection.get_table_description.return_value = []
            output = io.StringIO()
            with patch.dict(sys.modules, modules), patch.dict(os.environ, OLD, clear=True), \
                    contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                exec(deploy.SCHEMA_CHECK, {})
            self.assertIn('Missing tables/columns: ' + expected, output.getvalue())


if __name__ == '__main__':
    unittest.main()
