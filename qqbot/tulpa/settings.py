"""Application extension settings, separate from provider/model configuration."""
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


def load_settings():
    path = Path(os.environ.get('TULPA_CONFIG', 'tulpa.toml'))
    result = tomllib.loads(path.read_text()) if path.exists() else {}
    if set(result)-{'conversations', 'mcp_servers', 'performance'}:
        raise ValueError('Unknown Tulpa application setting')
    conversations = result.get('conversations', {})
    if set(conversations)-{'friends', 'max_sessions', 'idle_seconds'}:
        raise ValueError('Unknown conversation setting')
    if conversations.get('friends', 'all') not in ('all', 'owner'):
        raise ValueError('friends must be all or owner')
    max_sessions = conversations.get('max_sessions', 8)
    idle = conversations.get('idle_seconds', 300)
    if type(max_sessions) is not int or not 1 <= max_sessions <= 8:
        raise ValueError('Tulpa supports 1–8 active sessions')
    if type(idle) is not int or not 60 <= idle <= 86400:
        raise ValueError('idle_seconds must be 60–86400')
    performance = result.get('performance', {})
    flags = {'compact_context', 'single_bubble_short_replies'}
    if (not isinstance(performance, dict) or set(performance)-flags
            or any(type(value) is not bool for value in performance.values())):
        raise ValueError('Performance settings must be known boolean flags')
    servers = result.get('mcp_servers', {})
    for name, server in servers.items():
        if not re.fullmatch('[a-z][a-z0-9_]{0,15}', name):
            raise ValueError('Invalid MCP server alias')
        if set(server)-{'url', 'token_env', 'tools'}:
            raise ValueError('Unknown MCP server setting')
        parsed = urlsplit(server.get('url', ''))
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.fragment:
            raise ValueError('Invalid MCP URL')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Non-loopback MCP servers require HTTPS')
        if not isinstance(server.get('token_env',''),str):
            raise ValueError('Invalid MCP token environment variable')
        if 'tools' in server and (not isinstance(server['tools'],list) or any(not isinstance(x,str) for x in server['tools'])):
            raise ValueError('MCP tools must be a list')
    return dict(friends=conversations.get('friends','all'), max_sessions=max_sessions,
                idle_seconds=idle, mcp_servers=servers,
                performance={name: performance.get(name, False) for name in flags})
