import os
import re
from pathlib import Path

REQUIRED_SECTIONS = ["# Purpose", "# System Behavior", "# Actions"]
PROFILE_DIR = Path(__file__).parent / "agent_profiles"


def validate_profile(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    missing = []
    for section in REQUIRED_SECTIONS:
        if section not in content:
            missing.append(section)
    # Check for non-empty sections
    for section in REQUIRED_SECTIONS:
        match = re.search(rf"{re.escape(section)}\s*(.*?)(?=\n#|\Z)", content, re.DOTALL)
        if not match or not match.group(1).strip():
            missing.append(f"Non-empty {section}")
    return missing

def main():
    failed = False
    for file in PROFILE_DIR.glob("*.agent.md"):
        missing = validate_profile(file)
        if missing:
            print(f"[FAIL] {file.name}: missing {', '.join(missing)}")
            failed = True
        else:
            print(f"[OK]   {file.name}")
    if failed:
        exit(1)
    else:
        print("All agent profiles validated.")

if __name__ == "__main__":
    main()
