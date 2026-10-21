@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"
set "BENV=.venv-build"
set "PYB=%BENV%\Scripts\python.exe"

echo.
echo  ===== PDF 目录书签助手 打包工具 =====
echo.

rem ---------- 准备专用的精简打包环境（只装打包需要的库，控制体积） ----------
if exist "%BENV%\build-v2.ok" goto choose
echo  首次打包，正在准备精简的打包环境，只需一次，约需 3-8 分钟...
echo.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo  没有找到 Python，请先安装 Python 3.9 或更新版本：https://www.python.org/downloads/
    pause
    exit /b 1
)
if not exist "%PYB%" %PY% -m venv %BENV%
if not exist "%PYB%" (
    echo  创建打包环境失败。
    pause
    exit /b 1
)
"%PYB%" -m pip install --upgrade pip %MIRROR%
"%PYB%" -m pip install -r build-requirements.txt %MIRROR%
if errorlevel 1 goto envfail
rem RapidOCR 不带依赖安装，避免它装入带图形界面的完整版 OpenCV
"%PYB%" -m pip install --no-deps rapidocr_onnxruntime %MIRROR%
if errorlevel 1 goto envfail
echo ok> "%BENV%\build-v2.ok"

rem ---------- 选择打包方式 ----------
:choose
set "SEL=%~1"
if defined SEL goto build
echo  请选择打包方式：
echo.
echo    1  文件夹版   dist\PdfBookmarker\PdfBookmarker.exe
echo                  启动最快，分发时需要复制整个文件夹
echo.
echo    2  单文件版   dist\PdfBookmarker-Portable.exe
echo                  只有一个 exe，方便发给别人；每次启动要先解压，会慢几秒
echo.
echo    3  两种都打包
echo.
choice /c 123 /n /m "  请输入 1、2 或 3："
set "SEL=%errorlevel%"

:build
set "FAILED="
if "%SEL%"=="1" call :onedir
if "%SEL%"=="2" call :onefile
if "%SEL%"=="3" call :onedir
if "%SEL%"=="3" call :onefile

echo.
if defined FAILED (
    echo  打包失败，请查看上面的错误信息。
    pause
    exit /b 1
)
echo  ===== 打包完成 =====
echo.
if exist "dist\PdfBookmarker\PdfBookmarker.exe" for /f %%S in ('powershell -NoProfile -Command "[math]::Round((Get-ChildItem -LiteralPath 'dist\PdfBookmarker' -Recurse -File | Measure-Object Length -Sum).Sum/1MB,1)"') do echo    文件夹版  dist\PdfBookmarker\              共 %%S MB
if exist "dist\PdfBookmarker-Portable.exe" for /f %%S in ('powershell -NoProfile -Command "[math]::Round((Get-Item -LiteralPath 'dist\PdfBookmarker-Portable.exe').Length/1MB,1)"') do echo    单文件版  dist\PdfBookmarker-Portable.exe   共 %%S MB
echo.
echo  文件夹版要把整个 PdfBookmarker 文件夹一起复制；单文件版只需复制那一个 exe。
echo  两种都不需要对方电脑安装 Python。
explorer "dist"
pause
endlocal
exit /b 0

rem ---------- 文件夹版 ----------
:onedir
echo.
echo  [文件夹版] 正在打包，大约需要 1-3 分钟...
set "PDFBM_MODE=onedir"
"%PYB%" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build\onedir PdfBookmarker.spec
if errorlevel 1 set "FAILED=1"
goto :eof

rem ---------- 单文件版（优先带启动画面，不支持时自动改为不带） ----------
:onefile
echo.
echo  [单文件版] 正在打包，大约需要 2-5 分钟...
set "PDFBM_MODE=onefile"
set "PDFBM_SPLASH=1"
"%PYB%" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build\onefile PdfBookmarker.spec
if not errorlevel 1 goto :eof
echo.
echo  当前 Python 没有可用的 Tcl/Tk，无法添加启动画面，改为不带启动画面重新打包...
echo  如需启动画面：用 python.org 的安装程序安装 Python，安装时保持勾选 "tcl/tk and IDLE"。
set "PDFBM_SPLASH=0"
"%PYB%" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build\onefile PdfBookmarker.spec
if errorlevel 1 set "FAILED=1"
goto :eof

:envfail
echo.
echo  打包环境安装失败，请检查网络后重新运行本脚本。
if exist "%BENV%\build-v2.ok" del "%BENV%\build-v2.ok"
pause
exit /b 1
