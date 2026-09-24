@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title IT 资产管理系统 - 停止服务

rem ==================================================================
rem  IT 资产管理系统 —— 停止服务
rem
rem  双击即可。它会按端口找到正在监听的 python.exe 并结束掉。
rem  端口来源：优先读 .tmp\last-port.txt（start.bat 启动时写的），
rem  读不到就在 8080-8090 这一小段里逐个找。
rem  只结束 python.exe —— 万一端口被别的程序占着，不会误伤。
rem ==================================================================

set "ROOT=%~dp0"
set "RUNDIR=%ROOT%.tmp"
set "PORTFILE=%RUNDIR%\last-port.txt"

echo ============================================================
echo    IT 资产管理系统 - 停止服务
echo ============================================================
echo.

set "KILLED=0"
set "LASTPORT="
if exist "%PORTFILE%" set /p LASTPORT=<"%PORTFILE%"

if defined LASTPORT (
  echo   正在检查端口 %LASTPORT% ...
  call :kill_port %LASTPORT%
)

for %%P in (8080 8081 8082 8083 8084 8085 8086 8087 8088 8089 8090) do call :kill_port %%P

echo.
if "%KILLED%"=="0" (
  echo   没有找到正在运行的服务，它可能已经停了。
) else (
  echo   已停止 %KILLED% 个服务进程。
)

if exist "%PORTFILE%" del /q "%PORTFILE%" >nul 2>nul

echo.
pause
exit /b 0

rem ------------------------------------------------------------------
rem  子过程：结束占用指定端口的 python 进程（没有就什么都不做）
rem ------------------------------------------------------------------
:kill_port
set "P=%~1"
set "FOUND="
for /f "tokens=5" %%A in ('netstat -ano ^| findstr /c:":%P% " ^| findstr /i "LISTENING"') do set "FOUND=%%A"
if not defined FOUND exit /b 0

set "PNAME="
for /f "tokens=1 delims=," %%N in ('tasklist /fi "PID eq %FOUND%" /fo csv /nh 2^>nul') do set "PNAME=%%~N"
if /i not "%PNAME%"=="python.exe" (
  echo   端口 %P% 被 %PNAME%（PID %FOUND%）占着，不是本系统，跳过。
  exit /b 0
)

echo   正在停止端口 %P% 上的服务（PID %FOUND%）...
taskkill /f /t /pid %FOUND% >nul 2>nul
if not errorlevel 1 set /a KILLED+=1
exit /b 0
