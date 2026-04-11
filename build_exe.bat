@echo off
setlocal
set ROOT=%~dp0
set PY_VENV=%ROOT%.venv\Scripts\python.exe

echo ===== Stocks Web App EXE ビルド =====
echo.

REM PyInstaller がなければインストール
echo PyInstaller を確認中...
%PY_VENV% -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller をインストール中...
    %PY_VENV% -m pip install pyinstaller
)

echo.
echo EXE をビルド中...
%PY_VENV% -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "StocksApp" ^
    --distpath "%ROOT%dist" ^
    --workpath "%ROOT%build_tmp" ^
    --specpath "%ROOT%build_tmp" ^
    "%ROOT%launcher.py"

if errorlevel 1 (
    echo.
    echo [ERROR] ビルドに失敗しました
    pause
    exit /b 1
)

echo.
echo ===== 完了 =====
echo EXE の場所: %ROOT%dist\StocksApp.exe
echo.
echo デスクトップにショートカットを作成しますか? (Y/N)
set /p SHORTCUT=
if /i "%SHORTCUT%"=="Y" (
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([System.Environment]::GetFolderPath('Desktop') + '\StocksApp.lnk'); $s.TargetPath = '%ROOT%dist\StocksApp.exe'; $s.WorkingDirectory = '%ROOT%'; $s.Save()"
    echo デスクトップにショートカットを作成しました。
)

pause
endlocal
