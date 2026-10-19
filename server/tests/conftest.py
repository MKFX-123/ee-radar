"""测试默认关闭定时网络采集；同步测试使用显式注入的替身。"""
import os
from tempfile import TemporaryDirectory

import pytest


_test_data = TemporaryDirectory(prefix='ee-radar-tests-')
_previous_db = os.environ.get('EE_RADAR_DB_PATH')
# 测试模块导入 app 时也会构造默认 Store，须提前隔离默认路径。
os.environ['EE_RADAR_DB_PATH'] = os.path.join(_test_data.name, 'default.sqlite3')


def pytest_unconfigure(config):
    if _previous_db is None:
        os.environ.pop('EE_RADAR_DB_PATH', None)
    else:
        os.environ['EE_RADAR_DB_PATH'] = _previous_db
    _test_data.cleanup()


@pytest.fixture(autouse=True)
def disable_scheduled_network(monkeypatch):
    monkeypatch.setenv('EE_RADAR_SYNC_ENABLED', '0')
