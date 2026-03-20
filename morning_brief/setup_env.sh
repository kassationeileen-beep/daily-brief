#!/bin/bash
# 环境初始化脚本 — 修复 akshare 的 jsonpath 依赖问题
# 用法: bash setup_env.sh

set -e

# 兼容不同系统：优先用 pip3，其次 python3 -m pip，最后 pip
if command -v pip3 &>/dev/null; then
    PIP="pip3"
elif command -v python3 &>/dev/null; then
    PIP="python3 -m pip"
else
    PIP="pip"
fi

PYTHON=$(command -v python3 || command -v python)
echo "使用 Python: $PYTHON  Pip: $PIP"

echo "=== 安装 Python 依赖 ==="
$PIP install -r requirements.txt

echo "=== 安装 jsonpath 兼容层 ==="
# akshare 内部 import jsonpath，但该包无法通过 pip 正常构建
# 使用 jsonpath-ng 创建兼容 shim
SITE_PKG=$($PYTHON -c "import site; print(site.getsitepackages()[0])")
cat > "${SITE_PKG}/jsonpath.py" << 'EOF'
"""Compatibility shim: maps 'jsonpath' -> 'jsonpath_ng'"""
from jsonpath_ng import parse as _parse

def jsonpath(obj, expr):
    try:
        matches = [m.value for m in _parse(expr).find(obj)]
        return matches if matches else False
    except Exception:
        return False
EOF
echo "jsonpath shim 写入: ${SITE_PKG}/jsonpath.py"

echo "=== 验证 ==="
$PYTHON -c "import akshare as ak; print('akshare', ak.__version__, 'OK')"
$PYTHON -c "import httpx; print('httpx', httpx.__version__, 'OK')"

echo "=== 完成 ==="
