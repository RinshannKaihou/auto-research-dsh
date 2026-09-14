#!/bin/bash
# Double-click in Finder. No package installation or model call is needed to open the GUI.
set -e
launcher_root="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:$HOME/.hermes/node/bin:/opt/homebrew/bin:$PATH"
launcher_python=""
for candidate in "$launcher_root/.venv/bin/python" /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 /opt/homebrew/bin/python3 python3; do
  if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    launcher_python="$candidate"
    break
  fi
done
if [ -z "$launcher_python" ]; then
  echo '需要 Python 3.11 或更新版本才能打开研究工作台。'
  read -r -p '按回车关闭窗口。' launcher_reply
  exit 1
fi
export PYTHONPATH="$launcher_root/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec "$launcher_python" -m auto_research gui "$@"
