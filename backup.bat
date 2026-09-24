@echo off
setlocal
cd /d "%~dp0"
title IT 资产管理系统 - 备份数据库

rem ==================================================================
rem  IT 资产管理系统 —— 一键备份数据库
rem
rem  双击即可，生成 backups\it_assets_20260924_153012.db 这样的文件。
rem
rem  数据库文件：backend\data\it_assets.db
rem  备份目录  ：backups\           可以改，见下面 IT_ASSET_BACKUP_DIR
rem
rem  优先用电脑上的 Python 跑 backend\backup_db.py 做备份 —— 它走 SQLite
rem  的「在线备份」接口，能在服务正在运行、有人正在写的时候也拿到完整
rem  一致的数据，比直接复制文件安全得多。找不到 Python 时才退化成复制。
rem
rem  默认只保留最近 30 份，更早的自动清理。想改份数就直接跑：
rem     python backend\backup_db.py --keep 100
rem ==================================================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "DATADIR=%BACKEND%\data"
set "DBFILE=%DATADIR%\it_assets.db"

set "BACKUPDIR=%ROOT%backups"
if not "%IT_ASSET_BACKUP_DIR%"=="" set "BACKUPDIR=%IT_ASSET_BACKUP_DIR%"

echo ============================================================
echo    IT 资产管理系统 - 备份数据库
echo ============================================================
echo    数据库文件：%DBFILE%
echo    备份目录  ：%BACKUPDIR%
echo.

if not exist "%DBFILE%" (
  echo    [错误] 没找到数据库文件：
  echo           %DBFILE%
  echo.
  echo    这套系统还没启动过吧？数据库是第一次启动时自动创建的，
  echo    先双击 start.bat 让它跑起来，再回来备份。
  echo.
  pause
  exit /b 1
)

rem backup_db.py 只用 Python 标准库，所以这里不检查依赖，只要有个 Python 就行
set "PYCMD="
set "PYARG="
call :any_py py -3
if not defined PYCMD call :any_py python
if not defined PYCMD call :any_py python3
if not defined PYCMD goto :plain_copy

%PYCMD% %PYARG% "%BACKEND%\backup_db.py" --keep 30
if errorlevel 1 (
  echo.
  echo    [错误] 备份失败，请把上面的信息截图给维护人员。
  echo.
  pause
  exit /b 1
)
goto :done

:plain_copy
echo    [提示] 这台电脑上没有找到 Python，改用直接复制文件的方式。
echo           这种方式在服务正在写入时有小概率拿到不完整的数据，
echo           建议先双击 stop.bat 停掉服务再备份。
echo.
set "STAMP="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set "STAMP=%%D"
if not defined STAMP set "STAMP=backup"
if not exist "%BACKUPDIR%" mkdir "%BACKUPDIR%" >nul 2>nul
copy /y "%DBFILE%" "%BACKUPDIR%\it_assets_%STAMP%.db" >nul
if errorlevel 1 (
  echo    [错误] 复制失败，请检查备份目录是否可写：%BACKUPDIR%
  echo.
  pause
  exit /b 1
)
echo.
echo    [完成] 已生成备份：%BACKUPDIR%\it_assets_%STAMP%.db

:done
echo.
pause
exit /b 0

rem ------------------------------------------------------------------
rem  子过程：只要有个能跑的 Python 就行（不检查依赖）
rem ------------------------------------------------------------------
:any_py
if defined PYCMD exit /b 0
%~1 %~2 --version >nul 2>nul
if errorlevel 1 exit /b 0
set "PYCMD=%~1"
set "PYARG=%~2"
exit /b 0
