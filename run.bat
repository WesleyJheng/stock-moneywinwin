@echo off
chcp 65001 > nul
echo ========================================================
echo   啟動台股全自動監控與 AI 分析戰情室 (Streamlit)
echo ========================================================

set PYTHON_EXE="%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if not exist %PYTHON_EXE% (
    set PYTHON_EXE=python
)

%PYTHON_EXE% -m streamlit run app.py
pause
