@echo off
for %%P in (3000 8000 8010) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr :%%P ^| findstr LISTENING') do (
    echo Stopping PID %%A on port %%P
    taskkill /PID %%A /F >nul 2>nul
  )
)
echo Done.
