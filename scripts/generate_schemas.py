"""Generate deterministic editor schemas; --check detects contract drift in CI."""

import argparse
import json
from pathlib import Path

from pyintegrationtests.config import Config
from pyintegrationtests.registry import default_registry
from pyintegrationtests.schema import Suite

ROOT = Path(__file__).resolve().parents[1]


def schemas():
    result = {
        "suite": Suite.model_json_schema(by_alias=True),
        "config": Config.model_json_schema(by_alias=True),
    }
    for name, schema in result.items():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = (
            f"https://raw.githubusercontent.com/jeremygovi/PyIntegrationTests/master/schemas/{name}.schema.json"
        )
    actions = {}
    for name, action in sorted(default_registry(Config()).actions.items()):
        actions[name] = action.parameters.model_json_schema(by_alias=True)
    result["actions"] = actions
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    failures = []
    for name, schema in schemas().items():
        path = ROOT / "schemas" / f"{name}.schema.json"
        text = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != text:
                failures.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
    if failures:
        raise SystemExit("Stale schemas; run make schema: " + ", ".join(failures))


if __name__ == "__main__":
    main()
