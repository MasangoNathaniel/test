"""FreeMask — Magic-Mask-style object tracking for DaVinci Resolve (free version)."""

import os

# Let OpenCV read EXR image sequences (must be set before cv2 is imported).
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

__version__ = "1.0.0"
