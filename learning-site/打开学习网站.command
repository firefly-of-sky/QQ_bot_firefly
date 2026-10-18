#!/bin/zsh
# Keep this window open while studying. Control+C closes only the learning site.
cd "$(dirname "$0")" || exit 1
python3 - <<'PY'
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser

handler = partial(SimpleHTTPRequestHandler, directory=str(Path('dist').resolve()))
for port in range(8765, 8785):
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), handler)
        break
    except OSError:
        continue
else:
    raise SystemExit('本机学习网站端口不可用。请先关闭之前的学习网站终端，再试一次。')
url = f'http://127.0.0.1:{port}/'
print(f'服务器小课已打开：{url}', flush=True)
print('学习时保持这个窗口开启。Control + C 只关闭学习网站，不影响云端 QQbot。', flush=True)
webbrowser.open(url)
try:
    server.serve_forever()
except KeyboardInterrupt:
    print('\n学习网站已关闭。进度保存在这个浏览器地址中。')
finally:
    server.server_close()
PY
