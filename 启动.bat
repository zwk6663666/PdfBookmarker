@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"

if exist ".venv\installed.ok" goto run

echo.
echo  首次运行，正在准备运行环境（只需一次，约需 2-5 分钟）...
echo.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo  没有找到 Python。请先安装 Python 3.9 或更新版本：
    echo  https://www.python.org/downloads/
    echo  安装时请勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  创建虚拟环境失败。
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip %MIRROR%
".venv\Scripts\python.exe" -m pip install -r requirements.txt %MIRROR%
if errorlevel 1 (
    echo.
    echo  依赖安装失败，请检查网络后重新运行本脚本。
    pause
    exit /b 1
)
echo ok> ".venv\installed.ok"

:run
start "" ".venv\Scripts\pythonw.exe" app.py %*
endlocal
