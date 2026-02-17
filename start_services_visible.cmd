@echo off
setlocal

set ROOT=C:\Users\o_van\Desktop\stocks_web_app
set FRONTEND=%ROOT%\frontend
set BACKEND=%ROOT%\backend
set PY_SYS=C:\Users\o_van\AppData\Local\Programs\Python\Python312\python.exe
set PY_VENV=%ROOT%\.venv\Scripts\python.exe

echo Stopping old listeners on 3000/8000/8010...
for %%P in (3000 8000 8010) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr :%%P ^| findstr LISTENING') do (
    taskkill /PID %%A /F >nul 2>nul
  )
)

echo.
echo Starting FRONTEND-3000 (window will stay open)...
start "FRONTEND-3000" cmd /k "cd /d %FRONTEND% && echo [FRONTEND] python -m http.server 3000 && %PY_SYS% -m http.server 3000 && echo. && echo [FRONTEND] EXITED with errorlevel !errorlevel! && pause"

echo Starting BACKEND-8000 (window will stay open)...
start "BACKEND-8000" cmd /k "cd /d %BACKEND% && echo [BACKEND] uvicorn :8000 && %PY_VENV% -m uvicorn app.main:app --host 0.0.0.0 --port 8000 && echo. && echo [BACKEND] EXITED with errorlevel !errorlevel! && pause"

echo Starting API-8010 (window will stay open)...
start "API-8010" cmd /k "cd /d %BACKEND% && echo [API] uvicorn :8010 && %PY_VENV% -m uvicorn app.main:app --host 0.0.0.0 --port 8010 && echo. && echo [API] EXITED with errorlevel !errorlevel! && pause"

timeout /t 3 >nul
echo.
echo ===== PORT CHECK =====
netstat -ano | findstr :3000
netstat -ano | findstr :8000
netstat -ano | findstr :8010
echo.
echo Open: http://127.0.0.1:3000/screener.html

endlocal
