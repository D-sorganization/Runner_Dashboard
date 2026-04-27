import yaml
import sys

try:
    with open(".github/workflows/Jules-Control-Tower.yml", "r") as f:
        yaml.safe_load(f)
    print("YAML is valid")
except Exception as e:
    print(f"YAML is invalid: {e}")
    sys.exit(1)
