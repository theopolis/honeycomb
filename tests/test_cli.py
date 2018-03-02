# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import json
import signal
import tempfile
import requests
import subprocess
from multiprocessing import Process
from requests.adapters import HTTPAdapter

import pytest
from click.testing import CliRunner

from honeycomb import cli
from honeycomb.utils.wait import wait_until
from honeycomb.utils.defs import DEBUG_LOG_FILE
from .utils.syslog import runSyslogServer

DEMO_SERVICE = 'simple_http'
DEMO_SERVICE_PORT = '8888/TCP'
DEMO_SERVICE_ALERT = 'simple_http'
RUN_HONEYCOMB = 'coverage run --parallel-mode --module --source=honeycomb honeycomb'
JSON_LOG_FILE = tempfile.mkstemp()[1]
SYSLOG_HOST = '127.0.0.1'
SYSLOG_PORT = 5514
rsession = requests.Session()
rsession.mount('https://', HTTPAdapter(max_retries=3))


@pytest.fixture
def service_installed(tmpdir):
    """prepared honeycomb home path with service installed"""
    CliRunner().invoke(cli.main, args=['--iamroot', '--home', str(tmpdir),
                       'install', 'sample_services/{}'.format(DEMO_SERVICE)])
    yield str(tmpdir)
    CliRunner().invoke(cli.main, args=['--iamroot', '--home', str(tmpdir), 'uninstall', '-y', DEMO_SERVICE])


@pytest.fixture
def running_service(service_installed, request):
    cmd = [RUN_HONEYCOMB, '--iamroot', '--home', service_installed] + request.param
    p = subprocess.Popen(' '.join(cmd), shell=True, env=os.environ.copy())
    yield service_installed
    p.send_signal(signal.SIGINT)
    p.wait()


@pytest.fixture
def running_daemon(service_installed):
    cmd = [RUN_HONEYCOMB, '--iamroot', '--home', service_installed, 'run', '-d', DEMO_SERVICE, 'port=8888']
    p = subprocess.Popen(' '.join(cmd), shell=True, env=os.environ.copy())
    p.wait()
    assert p.returncode == 0
    assert wait_until(search_json_log, filepath=os.path.join(service_installed, DEBUG_LOG_FILE), total_timeout=10,
                      key='message', value='Starting Simple HTTP service on port: 8888')

    yield service_installed

    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', service_installed, 'stop', DEMO_SERVICE])
    assert result.exit_code == 0
    assert not result.exception

    try:
        rsession.get('http://localhost:8888')
        assert False, 'Service is still available (make sure to properly kill it before repeating test)'
    except requests.exceptions.ConnectionError:
        assert True


@pytest.fixture
def syslog(tmpdir):
    logfile = tmpdir.join('syslog.log')
    p = Process(target=runSyslogServer, args=(SYSLOG_HOST, SYSLOG_PORT, logfile))
    p.start()
    yield str(logfile)
    p.terminate()


def json_log_is_valid(path):
    with open(os.path.join(str(path), 'honeycomb.debug.log'), 'r') as fh:
        for line in fh.readlines():
                try:
                    json.loads(line)
                except json.decoder.JSONDecodeError:
                    return False
    return True


def search_file_log(filepath, method, args):
    with open(filepath, 'r') as fh:
        for line in fh.readlines():
                cmd = getattr(line, method)
                if cmd(args):
                    return line
        return False


def search_json_log(filepath, key, value):
    with open(filepath, 'r') as fh:
        for line in fh.readlines():
                log = json.loads(line)
                if key in log and log[key] == value:
                    return log
        return False


def test_cli_help():
    result = CliRunner().invoke(cli.main, args=['--help'])
    assert result.exit_code == 0
    assert not result.exception


@pytest.mark.dependency(name='install_uninstall')
@pytest.mark.parametrize("service", [
    DEMO_SERVICE,  # install from online repo
    'sample_services/{}'.format(DEMO_SERVICE),  # install from local folder
    'sample_services/{}.zip'.format(DEMO_SERVICE),  # install from local zip
])
def test_install_uninstall(tmpdir, service):
    # install
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', str(tmpdir), 'install', service])
    assert result.exit_code == 0
    assert not result.exception

    # uninstall
    result = CliRunner().invoke(cli.main, input='y', args=['--iamroot', '--home', str(tmpdir), 'uninstall', service])
    assert result.exit_code == 0
    assert not result.exception

    assert json_log_is_valid(tmpdir)


def test_list_nothing_installed(tmpdir):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', str(tmpdir), 'list'])
    assert result.exit_code == 0
    assert json_log_is_valid(str(tmpdir))


def test_list_remote(tmpdir):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', str(tmpdir), 'list', '--remote'])
    assert DEMO_SERVICE in result.output
    assert result.exit_code == 0
    assert not result.exception
    assert json_log_is_valid(tmpdir)


@pytest.mark.dependency(depends=['install_uninstall'])
def test_list_local(service_installed):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', service_installed, 'list'])
    assert '{} ({}) [Alerts: {}]'.format(DEMO_SERVICE, DEMO_SERVICE_PORT, DEMO_SERVICE_ALERT) in result.output
    assert result.exit_code == 0
    assert not result.exception
    assert json_log_is_valid(service_installed)


def test_show_remote_not_installed(tmpdir):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', str(tmpdir), 'show', DEMO_SERVICE])
    assert 'Installed: False' in result.output
    assert 'Name: {}'.format(DEMO_SERVICE) in result.output
    assert result.exit_code == 0
    assert not result.exception
    assert json_log_is_valid(tmpdir)


@pytest.mark.dependency(depends=['install_uninstall'])
def test_show_local_installed(service_installed):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', service_installed, 'show', DEMO_SERVICE])
    assert 'Installed: True' in result.output
    assert 'Name: {}'.format(DEMO_SERVICE) in result.output
    assert result.exit_code == 0
    assert not result.exception
    assert json_log_is_valid(service_installed)


def test_show_nonexistent(tmpdir):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', str(tmpdir), 'show', 'this_should_never_exist'])
    assert result.exit_code != 0
    assert result.exception
    assert json_log_is_valid(str(tmpdir))


@pytest.mark.dependency(name='arg_missing', depends=['install_uninstall'])
def test_missing_arg(service_installed):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', service_installed, 'run', DEMO_SERVICE])
    assert result.exit_code != 0
    assert result.exception
    assert "'port' is missing" in result.output
    assert json_log_is_valid(service_installed)


@pytest.mark.dependency(name='arg_bad_int', depends=['install_uninstall'])
def test_arg_bad_int(service_installed):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', service_installed,
                                                'run', DEMO_SERVICE, 'port=notint'])
    assert result.exit_code != 0
    assert result.exception
    assert 'Bad value for port=notint (must be integer)' in result.output
    assert json_log_is_valid(service_installed)


@pytest.mark.dependency(name='arg_bad_bool', depends=['install_uninstall'])
def test_arg_bad_bool(service_installed):
    result = CliRunner().invoke(cli.main, args=['--iamroot', '--home', service_installed,
                                                'run', DEMO_SERVICE, 'port=8888', 'threading=notbool'])
    assert result.exit_code != 0
    assert result.exception
    assert 'Bad value for threading=notbool (must be boolean)' in result.output
    assert json_log_is_valid(service_installed)


@pytest.mark.dependency(name='run', depends=['arg_missing', 'arg_bad_int', 'arg_bad_bool'])
@pytest.mark.parametrize('running_service', [['run', DEMO_SERVICE, 'port=8888']], indirect=['running_service'])
def test_run(running_service):
    assert wait_until(search_json_log, filepath=os.path.join(running_service, DEBUG_LOG_FILE), total_timeout=10,
                      key='message', value='Starting Simple HTTP service on port: 8888')

    r = rsession.get('http://localhost:8888')
    assert 'Welcome to nginx!' in r.text


@pytest.mark.dependency(depends=['run'])
@pytest.mark.parametrize('running_service', [['run', '-j', JSON_LOG_FILE, DEMO_SERVICE, 'port=8888']],
                         indirect=['running_service'])
def test_json_log(running_service):
    assert wait_until(search_json_log, filepath=os.path.join(running_service, DEBUG_LOG_FILE), total_timeout=10,
                      key='message', value='Starting Simple HTTP service on port: 8888')
    r = rsession.get('http://localhost:8888')
    assert 'Welcome to nginx!' in r.text

    json_log = wait_until(search_json_log, filepath=JSON_LOG_FILE, total_timeout=10,
                          key='event_type', value=DEMO_SERVICE)

    assert json_log['request'] == 'GET /'


@pytest.mark.dependency(depends=['run'])
@pytest.mark.parametrize('running_service', [['run', '--syslog', '--syslog-host', SYSLOG_HOST,
                                              '--syslog-port', str(SYSLOG_PORT), DEMO_SERVICE, 'port=8888']],
                         indirect=['running_service'])
def test_syslog(running_service, syslog):
    assert wait_until(search_json_log, filepath=os.path.join(running_service, DEBUG_LOG_FILE), total_timeout=10,
                      key='message', value='Starting Simple HTTP service on port: 8888')
    r = rsession.get('http://localhost:8888')
    assert 'Welcome to nginx!' in r.text

    assert wait_until(search_file_log, filepath=syslog, total_timeout=10,
                      method='find', args='act={}'.format(DEMO_SERVICE_ALERT))

    assert wait_until(search_file_log, filepath=syslog, total_timeout=10,
                      method='find', args='request=GET /')

    assert wait_until(search_file_log, filepath=syslog, total_timeout=10,
                      method='find', args='src=127.0.0.1')


@pytest.mark.dependency(name='daemon', depends=['run'])
def test_daemon(running_daemon):
    r = rsession.get('http://localhost:8888')
    assert 'Welcome to nginx!' in r.text


@pytest.mark.dependency(depends=['daemon'])
def test_status(running_daemon):
    result = CliRunner().invoke(cli.main, args=['--home', running_daemon, 'status', DEMO_SERVICE])
    assert result.exit_code == 0
    assert not result.exception
    assert '{} - running'.format(DEMO_SERVICE) in result.output
    assert json_log_is_valid(running_daemon)


@pytest.mark.dependency(depends=['daemon'])
def test_status_all(running_daemon):
    result = CliRunner().invoke(cli.main, args=['--home', running_daemon, 'status', '--show-all'])
    assert result.exit_code == 0
    assert not result.exception
    assert '{} - running'.format(DEMO_SERVICE) in result.output
    assert json_log_is_valid(running_daemon)


@pytest.mark.dependency(depends=['daemon'])
def test_status_nonexistent(running_daemon):
    result = CliRunner().invoke(cli.main, args=['--home', running_daemon, 'status', 'nosuchservice'])
    assert result.exit_code == 0
    assert not result.exception
    assert 'nosuchservice - no such service' in result.output
    assert json_log_is_valid(running_daemon)


def test_status_no_service(tmpdir):
    result = CliRunner().invoke(cli.main, args=['--home', str(tmpdir), 'status'])
    assert result.exit_code != 0
    assert result.exception
    assert 'You must specify a service name' in result.output
    assert json_log_is_valid(str(tmpdir))


@pytest.mark.dependency(depends=['daemon'])
def test_test(running_daemon):
    result = CliRunner().invoke(cli.main, args=['--home', running_daemon, 'test', DEMO_SERVICE])
    assert result.exit_code == 0
    assert not result.exception
    assert 'alert tested succesfully' in result.output
    assert json_log_is_valid(running_daemon)