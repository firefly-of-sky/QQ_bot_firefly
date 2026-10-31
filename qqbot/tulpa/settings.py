"""Application extension settings, separate from provider/model configuration."""
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit


def load_settings():
    path = Path(os.environ.get('TULPA_CONFIG', 'tulpa.toml'))
    result = tomllib.loads(path.read_text()) if path.exists() else {}
    if set(result)-{'conversations', 'mcp_servers', 'performance', 'autonomy'}:
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
    autonomy=result.get('autonomy',{})
    if (not isinstance(autonomy,dict) or set(autonomy)-{'enabled','authorization_id','capabilities_file','background_friend_requests','writes_per_minute','background_per_hour'}
            or any(type(autonomy.get(k,False)) is not bool for k in ('enabled','background_friend_requests'))):
        raise ValueError('Invalid autonomy settings')
    authorization=autonomy.get('authorization_id','')
    if not isinstance(authorization,str) or (autonomy.get('enabled') and not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',authorization)):
        raise ValueError('Enabling autonomy requires an explicit authorization_id')
    if not isinstance(autonomy.get('capabilities_file',''),str):raise ValueError('Invalid capabilities manifest')
    for key,default,maximum in [('writes_per_minute',10,60),('background_per_hour',12,120)]:
        value=autonomy.get(key,default)
        if type(value) is not int or not 1<=value<=maximum:raise ValueError('Invalid autonomy rate limit')
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
                performance={name: performance.get(name, False) for name in flags},
                autonomy=dict(enabled=autonomy.get('enabled',False),authorization_id=authorization,
                    capabilities_file=autonomy.get('capabilities_file',''),
                    background_friend_requests=autonomy.get('background_friend_requests',False),
                    writes_per_minute=autonomy.get('writes_per_minute',10),
                    background_per_hour=autonomy.get('background_per_hour',12)))
