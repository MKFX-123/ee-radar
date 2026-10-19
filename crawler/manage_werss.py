"""仅监听本机的 WeRSS 管理入口；配置与凭据只保存在本地。"""
import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor/we-mp-rss'
DATA = ROOT / 'data'
LOGS = ROOT / 'logs'
CONFIG = DATA / 'werss.yaml'
CREDENTIALS = DATA / 'werss-login.json'
PIDFILE = DATA / 'werss.pid'
BASE = 'http://127.0.0.1:8001'


def configure():
    os.umask(0o077)
    for directory in (DATA, LOGS):
        directory.mkdir(exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    if not CREDENTIALS.exists():
        credentials = {'username': 'admin', 'password': secrets.token_urlsafe(24)}
        CREDENTIALS.write_text(json.dumps(credentials, ensure_ascii=False, indent=2))
    CREDENTIALS.chmod(0o600)
    if not CONFIG.exists():
        raw = (VENDOR / 'config.example.yaml').read_text()
        raw = re.sub(r'${([^}:]+)(?::-([^}]*))?}', lambda m: m.group(2) or '', raw)
        config = yaml.safe_load(raw)
        config['server'].update(enable_job=False, auto_reload=False, threads=1, auth_web=True, send_code=False, article_stats_refresh_enabled=False)
        config['db'] = 'sqlite:///' + str(DATA / 'werss.sqlite3')
        config['redis']['url'] = ''
        config['redis']['server']['enabled'] = False
        config['secret'] = secrets.token_urlsafe(48)
        config['port'] = 8001
        config['debug'] = False
        config['interval'] = 60
        config['max_page'] = 1
        config['notice'] = dict.fromkeys(('dingding', 'wechat', 'feishu', 'custom'), '')
        config['gather'].update(content=True, model='web', content_auto_check=False, browser_type='firefox')
        config['rss'].update(base_url=BASE, full_context=True, page_size=30)
        config['weread']['auto_add_to_shelf'] = False
        config['cascade']['enabled'] = False
        CONFIG.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    CONFIG.chmod(0o600)


def serve():
    configure()
    credentials = json.loads(CREDENTIALS.read_text())
    os.environ['USERNAME'] = credentials['username']
    os.environ['PASSWORD'] = credentials['password']
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(DATA / 'browsers')
    os.chdir(VENDOR)
    sys.path.insert(0, str(VENDOR))
    sys.argv = ['ee-radar-werss', '-config', str(CONFIG)]
    # 上游导入时检查版本，限制这一次请求的等待时间。
    original_get = requests.get
    def bounded_get(url, **kwargs):
        kwargs.setdefault('timeout', 15)
        return original_get(url, **kwargs)
    requests.get = bounded_get
    from init_sys import init
    init()
    import uvicorn
    from web import app
    uvicorn.run(app, host='127.0.0.1', port=8001, access_log=False)


def healthy():
    try:
        response = requests.get(BASE + '/api/openapi.json', timeout=3)
        return response.status_code == 200 and response.json().get('info', {}).get('title') == 'WeRSS API'
    except (requests.RequestException, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description='本地 WeRSS：配置、启动、状态和停止')
    parser.add_argument('command', choices=['setup', 'start', 'status', 'stop', 'serve'])
    command = parser.parse_args().command
    if command == 'serve':
        serve()
        return
    if command == 'status':
        print(json.dumps({'url': BASE, 'healthy': healthy()}, ensure_ascii=False))
        return
    if command == 'stop':
        if PIDFILE.exists():
            pid = int(PIDFILE.read_text())
            process = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
            if str(Path(__file__).resolve()) in process.stdout and 'serve' in process.stdout:
                os.kill(pid, signal.SIGTERM)
                print('已向本地 WeRSS 发送停止信号')
            PIDFILE.unlink(missing_ok=True)
        return
    configure()
    if command == 'setup':
        print('配置完成；管理员凭据保存在 crawler/data/werss-login.json')
        return
    if healthy():
        print('WeRSS 已在 ' + BASE + ' 运行')
        return
    # 只转交必要环境，不携带现有终端中的其他服务密钥。
    env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
    env['PYTHONUNBUFFERED'] = '1'
    with (LOGS / 'werss.log').open('a') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'serve'], cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
    PIDFILE.write_text(str(process.pid))
    for _ in range(45):
        if process.poll() is not None:
            raise SystemExit('启动失败，详见 crawler/logs/werss.log')
        if healthy():
            print('WeRSS 已启动：' + BASE)
            return
        time.sleep(1)
    raise SystemExit('启动尚未就绪，请检查状态和本地日志')


if __name__ == '__main__':
    main()
