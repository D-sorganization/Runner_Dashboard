import sys

import yaml

try:
    with open(".github/workflows/Jules-Control-Tower.yml") as f:
        yaml.safe_load(f)
    print("YAML is valid")
except Exception as e:
    print(f"YAML is invalid: {e}")
    sys.exit(1)
