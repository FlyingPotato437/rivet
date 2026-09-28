from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.api.main import app

Path("docs/openapi.json").write_text(json.dumps(app.openapi(), indent=2))
