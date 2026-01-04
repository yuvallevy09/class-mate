from __future__ import annotations

import sys
from pathlib import Path

# Ensure `import app...` works when running pytest from the backend directory.
BACKEND_ROOT = Path(__file__).resolve().parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))




