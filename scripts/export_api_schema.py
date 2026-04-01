from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def main() -> None:
    app = import_module("app.main").app
    schema_path = REPO_ROOT / "apps" / "web" / "src" / "generated" / "api-schema.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(schema_path)


if __name__ == "__main__":
    main()
