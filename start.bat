@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title IT 资产管理系统

rem ==================================================================
rem  IT 资产管理系统 —— 一键启动
rem
rem  双击本文件即可完成：
rem     找 Python -> 检查依赖 -> 构建前端页面(仅首次) -> 启动服务 -> 打开浏览器
rem
rem  ★ 本脚本使用「电脑上已经装好的 Python」，不会创建虚拟环境，
rem    也不会自动往你的 Python 里装东西。
rem    如果提示缺少依赖，按屏幕上的命令手动装一次就行 —— 只需一次，
rem    以后双击本脚本不会再问。
rem
rem  可选参数：
rem     start.bat --rebuild   强制重新构建前端页面
rem
rem  可选环境变量：
rem     set IT_ASSET_PORT=18080   指定端口（指定了就不再自动顺延）
rem ==================================================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "DATADIR=%BACKEND%\data"
set "DBFILE=%DATADIR%\it_assets.db"
set "CHECKENV=%BACKEND%\check_env.py"
set "RUNDIR=%ROOT%.tmp"
set "PORTFILE=%RUNDIR%\last-port.txt"

set "PORT=8080"
set "PORT_FIXED=0"
set "FORCE_BUILD=0"
if not "%IT_ASSET_PORT%"=="" set "PORT=%IT_ASSET_PORT%"
if not "%IT_ASSET_PORT%"=="" set "PORT_FIXED=1"
if /i "%~1"=="--rebuild" set "FORCE_BUILD=1"
if /i "%~2"=="--rebuild" set "FORCE_BUILD=1"

echo ============================================================
echo    IT 资产管理系统 - 启动
echo ============================================================
echo.

rem ------------------------------------------------------------------
rem  0. 已经在跑就别再起一个
rem     上次启动会把端口写进 .tmp\last-port.txt。如果那个端口上还能
rem     访问到本系统的接口，说明服务还活着，直接开浏览器最省事。
rem ------------------------------------------------------------------
if not exist "%PORTFILE%" goto :port_scan
set "LASTPORT="
set /p LASTPORT=<"%PORTFILE%"
if not defined LASTPORT goto :port_scan
where curl >nul 2>nul
if errorlevel 1 goto :port_scan
curl -s -m 2 "http://127.0.0.1:%LASTPORT%/api/system/info" 2>nul | findstr /c:"version" >nul 2>nul
if errorlevel 1 goto :port_scan
echo   [提示] 服务已经在运行，端口 %LASTPORT%，直接给你打开浏览器。
echo          想停掉它请双击 stop.bat。
echo.
start "" "http://127.0.0.1:%LASTPORT%"
echo.
pause
exit /b 0

:port_scan
rem ------------------------------------------------------------------
rem  1. 找一个能用的端口
rem     8000 常被打印控件之类的程序占用，所以默认从 8080 开始，
rem     被占用就往后顺延，免得出现「启动了却打不开」。
rem ------------------------------------------------------------------
if "%PORT_FIXED%"=="1" goto :port_ok
set /a "PORT_TRIES=0"

:find_port
netstat -an | findstr /c:":%PORT% " | findstr /i "LISTENING" >nul 2>nul
if errorlevel 1 goto :port_ok
set /a "PORT_TRIES+=1"
if %PORT_TRIES% GTR 10 (
  echo   [错误] %PORT% 往后 10 个端口都被占用了。
  echo   请手动指定一个端口，例如先执行：set IT_ASSET_PORT=18080 再运行本脚本。
  echo.
  pause
  exit /b 1
)
set /a "PORT=%PORT%+1"
goto :find_port

:port_ok

rem ------------------------------------------------------------------
rem  2. 找一个「装了依赖、真的能跑起来」的 Python
rem
rem     顺序：py -3  ->  python  ->  python3
rem
rem     为什么要真的跑一下 check_env.py 来判断，而不是 where python 取第一个？
rem     因为「能找到 python」和「这个 python 能跑本系统」是两回事：
rem     电脑上可能有好几个 Python，PATH 里排第一的那个未必是装了依赖的那个。
rem     光看文件名会被坑，实测才是可靠的。
rem
rem     py -3 优先：它是 Windows 官方的启动器，指向系统里正式安装的 Python，
rem     不会受 PATH 顺序影响。
rem ------------------------------------------------------------------
set "PYCMD="
set "PYARG="
set "ANY_PY="
call :probe py -3
if not defined PYCMD call :probe python
if not defined PYCMD call :probe python3
if not defined PYCMD goto :env_problem

echo [1/3] Python 环境就绪
echo         %PYCMD% %PYARG%   ^(依赖齐全^)

rem ------------------------------------------------------------------
rem  3. 前端页面
rem     dist 已存在就跳过构建。改了前端代码想重新构建：start.bat --rebuild
rem ------------------------------------------------------------------
if "%FORCE_BUILD%"=="1" goto :build_frontend
if exist "%FRONTEND%\dist\index.html" goto :front_ready

:build_frontend
echo [2/3] 正在构建前端页面，第一次会慢一些，请耐心等待...

where npm >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [错误] 没找到 Node.js / npm，首次构建前端需要它。
  echo   请安装 Node.js 18 或更高版本（装 LTS 版就行）：
  echo     https://nodejs.org/zh-cn/download
  echo.
  echo   提示：如果只是把这套系统搬到另一台电脑上跑，把 frontend\dist
  echo         文件夹一起拷过去，那台电脑不装 Node 也能用。
  echo.
  pause
  exit /b 1
)

pushd "%FRONTEND%"
if not exist "node_modules" (
  echo       正在安装前端依赖...
  call npm install --no-fund --no-audit
  if errorlevel 1 (
    echo   [错误] 前端依赖安装失败，请检查网络后重试。
    popd
    pause
    exit /b 1
  )
)
call npm run build
if errorlevel 1 (
  echo   [错误] 前端构建失败，上面是 npm 打印的报错信息。
  popd
  pause
  exit /b 1
)
popd

:front_ready
echo [2/3] 前端页面就绪

rem ------------------------------------------------------------------
rem  4. 启动服务
rem ------------------------------------------------------------------
if not exist "%RUNDIR%" mkdir "%RUNDIR%" >nul 2>nul
>"%PORTFILE%" echo %PORT%

echo [3/3] 正在启动服务...
echo.
echo ============================================================
echo    启动成功后：
echo      电脑访问   http://127.0.0.1:%PORT%
echo      手机扫码   见下面控制台里「手机扫码」那一行
echo                 （手机要和这台电脑连同一个 WiFi）
echo      接口文档   http://127.0.0.1:%PORT%/docs
echo.
echo    数据库文件  %DBFILE%
echo    备份目录    %ROOT%backups    （双击 backup.bat 生成带日期的备份）
echo.
echo    停止服务：在本窗口按 Ctrl+C，或另开一个窗口双击 stop.bat
echo ============================================================
echo.
echo    5 秒后自动打开浏览器...
echo.

start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://127.0.0.1:%PORT%"

pushd "%BACKEND%"
%PYCMD% %PYARG% run.py --port %PORT%
popd

if exist "%PORTFILE%" del /q "%PORTFILE%" >nul 2>nul

echo.
echo 服务已停止。
pause
exit /b 0

rem ------------------------------------------------------------------
rem  环境有问题时的两条出路
rem ------------------------------------------------------------------

:env_problem
if not defined ANY_PY goto :no_python

echo   [错误] 这台电脑上有 Python，但缺了运行本系统需要的依赖。
echo.
%ANY_PY% "%CHECKENV%"
echo.
echo ============================================================
echo    解决办法：装一次依赖（只需一次，十几秒）
echo ============================================================
echo.
echo    复制下面这一整行，粘到这个窗口里按回车：
echo.
echo      %ANY_PY% -m pip install -r "%BACKEND%\requirements.txt"
echo.
echo    国内网络慢的话，换成清华镜像：
echo      %ANY_PY% -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r "%BACKEND%\requirements.txt"
echo.
echo    装完重新双击本脚本即可。以后不会再要求装依赖。
echo.
pause
exit /b 1

:no_python
echo   [错误] 这台电脑上没有找到 Python。
echo.
echo   本系统需要 Python 3.10 或更高版本，装一次就行：
echo     1. 打开 https://www.python.org/downloads/windows/
echo     2. 下载 "Windows installer (64-bit)"
echo     3. 安装时务必勾选最下面的 "Add python.exe to PATH"
echo     4. 装完重新双击本脚本
echo.
pause
exit /b 1

rem ------------------------------------------------------------------
rem  子过程：试一个 Python 能不能用
rem    用法：call :probe py -3      或      call :probe python
rem    能跑 且 依赖齐全 -> 记进 PYCMD / PYARG
rem    只是能跑          -> 记进 ANY_PY（环境有问题时报错要用它打印详情）
rem ------------------------------------------------------------------
:probe
if defined PYCMD exit /b 0
%~1 %~2 --version >nul 2>nul
if errorlevel 1 exit /b 0
if not defined ANY_PY set "ANY_PY=%~1 %~2"
%~1 %~2 "%CHECKENV%" --quiet >nul 2>nul
if errorlevel 1 exit /b 0
set "PYCMD=%~1"
set "PYARG=%~2"
exit /b 0
