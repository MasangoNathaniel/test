@echo off
REM One-time setup for FreeMask on Windows. Double-click me.
REM Needs Python 3.10+ from python.org (tick "Add python.exe to PATH" when installing it).
setlocal
cd /d "%~dp0"

set "PY="
for %%P in (py python) do (
  if not defined PY (
    %%P -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=%%P"
  )
)
if not defined PY (
  echo FreeMask needs Python 3.10 or newer: https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" in the installer, then run this again.
  pause & exit /b 1
)

echo.
echo === Creating the FreeMask environment
%PY% -m venv .venv || (pause & exit /b 1)
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -m pip install --upgrade --quiet pip setuptools wheel

echo.
where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  echo === NVIDIA GPU found - installing PyTorch with CUDA ^(big download^)
  "%VPY%" -m pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cu124
) else (
  echo === No NVIDIA GPU found - installing CPU PyTorch ^(works, just slower^)
  "%VPY%" -m pip install --quiet torch torchvision
)
if errorlevel 1 (pause & exit /b 1)

echo.
echo === Installing SAM 2 and friends
set SAM2_BUILD_CUDA=0
"%VPY%" -m pip install --quiet --no-build-isolation "sam2>=1.1.0" || (pause & exit /b 1)
"%VPY%" -m pip install --quiet "huggingface_hub>=0.23" "numpy>=1.24" "opencv-python-headless>=4.8" "imageio-ffmpeg>=0.5" || (pause & exit /b 1)

echo.
echo === Downloading the SAM 2.1 model (~180 MB)
"%VPY%" -c "from sam2.build_sam import _hf_download; _hf_download('facebook/sam2.1-hiera-small')" || echo   (will download on first use)

echo.
echo === Adding FreeMask to DaVinci Resolve
"%VPY%" -m freemask.install_resolve || (pause & exit /b 1)

echo.
echo All set!
echo  - In DaVinci Resolve: put the playhead on a clip, then Workspace ^> Scripts ^> FreeMask
echo  - Or double-click freemask.bat to open the app directly.
pause
