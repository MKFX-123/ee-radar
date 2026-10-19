"""本地模型配置：完整快照、原子替换、密钥永不进入响应。"""
from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit, urlunsplit

from .ai import AIConfig


class ConfigError(Exception):
    pass


class ConfigStorageError(ConfigError):
    pass


def validate_base_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 2048 or any(char.isspace() or ord(char) < 32 for char in value) or any(char in value for char in ('\\', '?', '#')):
        raise ConfigError('模型地址必须为不含查询参数或片段的有效 HTTP(S) 地址。')
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
        if parts.scheme not in ('http', 'https') or not host or parts.username is not None or parts.password is not None:
            raise ValueError()
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        loopback = host.lower() == 'localhost'
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parts.scheme == 'http' and not loopback:
            raise ConfigError('外部模型服务必须使用 HTTPS；HTTP 仅允许本机回环地址。')
        host.encode('idna')
    except (ValueError, UnicodeError):
        raise ConfigError('模型地址无效；不允许账号密码或无效端口。') from None
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))


def validate_config(base_url: str, model: str, api_key: str) -> AIConfig:
    base_url = validate_base_url(base_url)
    model, api_key = model.strip(), api_key.strip()
    if not model or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise ConfigError('请填写有效的模型名称。')
    if not api_key or len(api_key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise ConfigError('请填写有效的 API Key；首次配置不能留空。')
    return AIConfig(base_url, api_key, model)


class ModelSettings:
    def __init__(self, path: Path, environment: AIConfig):
        self.path = Path(path)
        self._lock = RLock()
        try:
            safe_url = validate_base_url(environment.base_url) if environment.base_url else ''
        except ConfigError:
            safe_url = ''
        self._environment = AIConfig(safe_url, environment.api_key, environment.model)
        self._local = None
        if self.path.exists():
            try:
                if self.path.is_symlink() or self.path.stat().st_size > 16384:
                    raise ValueError()
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                local = validate_config(saved['base_url'], saved['model'], saved['api_key'])
                os.chmod(self.path, 0o600)
                self._local = local
            except (OSError, ValueError, TypeError, KeyError, ConfigError):
                # 无效配置不破坏环境回退，也不在日志中打印配置正文。
                self._local = None

    def snapshot(self) -> AIConfig:
        with self._lock:
            return self._local or self._environment

    def public(self) -> dict:
        with self._lock:
            config = self._local or self._environment
            return {'configured': config.configured, 'base_url': config.base_url, 'model': config.model, 'has_api_key': bool(config.api_key), 'source': 'local' if self._local else 'environment' if any((config.base_url, config.model, config.api_key)) else 'none'}

    def save(self, base_url: str, model: str, api_key=None) -> dict:
        with self._lock:
            previous = self._local or self._environment
            if not (api_key or '').strip() and previous.base_url:
                old_url, new_url = urlsplit(previous.base_url), urlsplit(validate_base_url(base_url))
                if (old_url.hostname, old_url.port or (443 if old_url.scheme == 'https' else 80)) != (new_url.hostname, new_url.port or (443 if new_url.scheme == 'https' else 80)):
                    raise ConfigError('更换模型服务主机或端口时，请重新填写 API Key。')
            candidate = validate_config(base_url, model, (api_key or '').strip() or previous.api_key)
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor, temporary = tempfile.mkstemp(prefix='.model-config-', suffix='.tmp', dir=self.path.parent)
                with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
                    os.fchmod(file.fileno(), 0o600)
                    json.dump({'base_url': candidate.base_url, 'model': candidate.model, 'api_key': candidate.api_key}, file, ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
                temporary = None
            except OSError:
                raise ConfigStorageError('模型配置未能保存，原配置保持不变。') from None
            finally:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
            self._local = candidate
            return self.public()

    def reset(self) -> dict:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                raise ConfigStorageError('本地配置未能清除，原配置保持不变。') from None
            self._local = None
            return self.public()
