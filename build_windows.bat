REM Build Windows executable using PyInstaller
python -m pip install --upgrade pip
pip install pyinstaller
pyinstaller --onefile --add-data "printer_agent.db;." agent.py
echo Build finished. Check dist\agent.exe
pause
