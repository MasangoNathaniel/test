@echo off
REM One-time setup for FreeMask on Windows. Needs Python 3.10+ from python.org
REM (tick "Add python.exe to PATH" when installing it).
cd /d "%~dp0"

python -c "import sys; assert sys.version_info >= (3, 10)" || (
  echo Python 3.10 or newer is required: https://www.python.org/downloads/
  pause & exit /b 1
)

python -m venv .venv || (pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel

where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  echo NVIDIA GPU found - installing PyTorch with CUDA support...
  python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
) else (
  echo No NVIDIA GPU found - installing CPU PyTorch ^(works, just slower^)...
  python -m pip install torch torchvision
)
if errorlevel 1 (pause & exit /b 1)

set SAM2_BUILD_CUDA=0
python -m pip install --no-build-isolation "sam2>=1.1.0" || (pause & exit /b 1)
python -m pip install "huggingface_hub>=0.23" "numpy>=1.24" "opencv-python>=4.8" || (pause & exit /b 1)

echo.
echo Done. Drag a video file onto freemask.bat, or run:  freemask.bat path\to\clip.mp4
pause
