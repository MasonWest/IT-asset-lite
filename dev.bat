@echo off
setlocal
cd /d "%~dp0"
title IT 资产管理系统 - 开发模式

rem ==================================================================
rem  IT 资产管理系统 —— 开发模式
rem
rem  后端带热重载 + 前端 Vite 开发服务器（改代码即时生效）。
rem  和 start.bat 一个口径：用电脑上已经装好的 Python，不建虚拟环境。
rem ==================================================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "PORT=8080"
if not "%IT_ASSET_PORT%"=="" set "PORT=%IT_ASSET_PORT%"

set "PYCMD="
set "PYARG="
call :probe py -3
if not defined PYCMD call :probe python
if not defined PYCMD call :probe python3
if not defined PYCMD (
  echo.
  echo   没找到可用的 Python（或依赖不全）。
  echo   请先双击 start.bat，按屏幕提示把环境准备好，再回来用本脚本。
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo    开发模式
echo      后端  http://127.0.0.1:%PORT%          （带热重载）
echo      前端  http://127.0.0.1:5173        （改代码即时生效）
echo    Python  %PYCMD% %PYARG%
echo ============================================================
echo.

rem 先切到 backend 再 start —— 让新窗口继承正确的工作目录，
rem 免得在 start 里写嵌套引号（cmd 对嵌套引号的解析很容易出错）
cd /d "%BACKEND%"
start "IT资产-后端" cmd /k "%PYCMD% %PYARG% run.py --port %PORT% --reload"

cd /d "%FRONTEND%"
if not exist "node_modules" (
  echo   正在安装前端依赖...
  call npm install --no-fund --no-audit
)
call npm run dev

echo.
pause
exit /b 0

rem ------------------------------------------------------------------
rem  子过程：找一个依赖齐全的 Python（同 start.bat）
rem ------------------------------------------------------------------
:probe
if defined PYCMD exit /b 0
%~1 %~2 --version >nul 2>nul
if errorlevel 1 exit /b 0
%~1 %~2 "%BACKEND%\check_env.py" --quiet >nul 2>nul
if errorlevel 1 exit /b 0
set "PYCMD=%~1"
set "PYARG=%~2"
exit /b 0
