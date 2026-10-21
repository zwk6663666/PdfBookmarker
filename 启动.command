#!/bin/bash
# macOS / Linux 启动脚本：首次运行自动安装依赖
cd "$(dirname "$0")"
MIRROR="-i https://pypi.tuna.tsinghua.edu.cn/simple"
if [ ! -f .venv/installed.ok ]; then
  echo "首次运行，正在准备运行环境（只需一次）..."
  python3 -m venv .venv || { echo "需要 Python 3.9 或更新版本"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip $MIRROR
  .venv/bin/python -m pip install -r requirements.txt $MIRROR || { echo "依赖安装失败"; exit 1; }
  echo ok > .venv/installed.ok
fi
exec .venv/bin/python app.py "$@"
