"""Artifact ownership policy and normalization utilities.

Policy: All runtime artifacts in book_project/ should be user-owned with group-write
permissions to enable both CLI and containerized operations to read/write without
privilege escalation.

- Directories: mode 0o775 (rwxrwxr-x) with user:group ownership
- Files: mode 0o664 (rw-rw-r--) with user:group ownership
- User: current user running the CLI (typically 'daravenrk')
- Group: 'docker' or current user's group (for container-to-host coordination)

This avoids the operational blocker of CLI runs failing due to root-owned artifacts
created by containerized services.
"""

import os
import pwd
import grp
import stat
from pathlib import Path
from typing import List, Tuple, Dict


# Runtime artifacts that are typically created by containerized services
CORE_RUNTIME_ARTIFACTS = [
    "agent_hibernation_state.json",
    "agent_reward_events.jsonl",
    "agent_reward_ledger.json",
    "cli_runtime_activity.json",
    "diagnostic_report.md",
    "ollama_run_ledger.jsonl",
    "quality_gate_failures.jsonl",
    "quarantine_events.jsonl",
    "resource_events.jsonl",
    "resource_tracker.json",
    "task_ledger.json",
    "webui_events.jsonl",
    "webui_state.json",
]

# Ownership policy
TARGET_MODE_DIR = 0o775  # rwxrwxr-x
TARGET_MODE_FILE = 0o664  # rw-rw-r--


def get_current_user_info() -> Tuple[int, int, str]:
    """Get current user's UID, GID, and username.
    
    Returns:
        tuple: (uid, gid, username)
    """
    uid = os.getuid()
    gid = os.getgid()
    try:
        username = pwd.getpwuid(uid).pw_name
    except KeyError:
        username = f"uid_{uid}"
    return uid, gid, username


def get_target_group() -> int:
    """Get target group GID for artifacts.
    
    Prefers 'docker' group if available (for container coordination),
    falls back to current user's group.
    
    Returns:
        int: Target GID
    """
    try:
        return grp.getgrnam("docker").gr_gid
    except KeyError:
        return os.getgid()  # Fallback to current user's group


def check_artifact_ownership(root_dir: Path) -> Dict[str, List[Path]]:
    """Check ownership of runtime artifacts.
    
    Args:
        root_dir: Root directory to scan (typically book_project/)
    
    Returns:
        dict with keys:
        - 'root_owned': list of root-owned files/dirs
        - 'wrong_permissions': list of files/dirs with incorrect permissions
        - 'healthy': list of correctly owned/permissioned artifacts
    """
    root_dir = Path(root_dir)
    result = {
        "root_owned": [],
        "wrong_permissions": [],
        "healthy": [],
    }
    
    uid, gid, _ = get_current_user_info()
    target_gid = get_target_group()
    
    # Check core runtime artifacts at root level
    for artifact_name in CORE_RUNTIME_ARTIFACTS:
        artifact_path = root_dir / artifact_name
        if not artifact_path.exists():
            continue
        
        try:
            stat_info = artifact_path.stat()
            artifact_uid = stat_info.st_uid
            artifact_gid = stat_info.st_gid
            artifact_mode = stat.S_IMODE(stat_info.st_mode)
            
            # Check for root ownership
            if artifact_uid == 0:
                result["root_owned"].append(artifact_path)
                continue
            
            # Check for correct permissions
            is_dir = stat.S_ISDIR(stat_info.st_mode)
            expected_mode = TARGET_MODE_DIR if is_dir else TARGET_MODE_FILE
            
            if artifact_mode != expected_mode or (artifact_gid != gid and artifact_gid != target_gid):
                result["wrong_permissions"].append(artifact_path)
            else:
                result["healthy"].append(artifact_path)
        except (OSError, PermissionError):
            result["wrong_permissions"].append(artifact_path)
    
    # Check for root-owned directories in book_project/
    try:
        for item in root_dir.iterdir():
            if not item.is_dir():
                continue
            try:
                stat_info = item.stat()
                if stat_info.st_uid == 0:
                    result["root_owned"].append(item)
            except (OSError, PermissionError):
                pass
    except (OSError, PermissionError):
        pass
    
    return result


def diagnose_ownership(book_project_dir: Path = None) -> str:
    """Generate a diagnostic report about artifact ownership.
    
    Args:
        book_project_dir: Path to book_project directory (default: auto-detect)
    
    Returns:
        str: Diagnostic report
    """
    if book_project_dir is None:
        book_project_dir = Path(__file__).parent.parent / "book_project"
    
    book_project_dir = Path(book_project_dir)
    
    if not book_project_dir.exists():
        return f"book_project directory not found at {book_project_dir}"
    
    ownership = check_artifact_ownership(book_project_dir)
    uid, gid, username = get_current_user_info()
    target_gid = get_target_group()
    
    report_lines = [
        "Artifact Ownership Diagnostic Report",
        "=" * 50,
        f"Current user: {username} (UID {uid}, GID {gid})",
        f"Target group: {target_gid}",
        f"Book project dir: {book_project_dir}",
        "",
    ]
    
    if ownership["root_owned"]:
        report_lines.append(f"⚠ Root-owned artifacts ({len(ownership['root_owned'])}):")
        for path in ownership["root_owned"][:10]:  # Limit to first 10
            report_lines.append(f"  - {path.relative_to(book_project_dir.parent)}")
        if len(ownership["root_owned"]) > 10:
            report_lines.append(f"  ... and {len(ownership['root_owned']) - 10} more")
        report_lines.append("")
    
    if ownership["wrong_permissions"]:
        report_lines.append(f"⚠ Wrong permissions ({len(ownership['wrong_permissions'])}):")
        for path in ownership["wrong_permissions"][:10]:  # Limit to first 10
            report_lines.append(f"  - {path.relative_to(book_project_dir.parent)}")
        if len(ownership["wrong_permissions"]) > 10:
            report_lines.append(f"  ... and {len(ownership['wrong_permissions']) - 10} more")
        report_lines.append("")
    
    if ownership["healthy"]:
        report_lines.append(f"✓ Healthy artifacts ({len(ownership['healthy'])})")
    
    if ownership["root_owned"] or ownership["wrong_permissions"]:
        report_lines.append("")
        report_lines.append("To fix ownership issues, run:")
        report_lines.append("  python3 -m agent_stack.artifact_ownership --repair")
        report_lines.append("")
        report_lines.append("To see more details:")
        report_lines.append("  python3 -m agent_stack.artifact_ownership --diagnose --verbose")
    
    return "\n".join(report_lines)


def repair_ownership(book_project_dir: Path = None, dry_run: bool = False) -> Tuple[bool, str]:
    """Repair artifact ownership issues.
    
    Attempts to fix root-owned and misaligned permission artifacts by:
    1. Changing ownership to current user
    2. Setting correct permissions per policy
    
    Args:
        book_project_dir: Path to book_project directory (default: auto-detect)
        dry_run: If True, report what would be done without making changes
    
    Returns:
        tuple: (success: bool, report: str)
    """
    if book_project_dir is None:
        book_project_dir = Path(__file__).parent.parent / "book_project"
    
    book_project_dir = Path(book_project_dir)
    
    if not book_project_dir.exists():
        return False, f"book_project directory not found at {book_project_dir}"
    
    ownership = check_artifact_ownership(book_project_dir)
    uid, gid, username = get_current_user_info()
    target_gid = get_target_group()
    
    to_fix = ownership["root_owned"] + ownership["wrong_permissions"]
    
    if not to_fix:
        return True, "No ownership issues found."
    
    report_lines = [
        f"Ownership repair report ({'DRY RUN' if dry_run else 'ACTUAL'})",
        "=" * 50,
    ]
    
    fixed_count = 0
    failed_count = 0
    
    for path in to_fix:
        try:
            is_dir = path.is_dir()
            expected_mode = TARGET_MODE_DIR if is_dir else TARGET_MODE_FILE
            
            mode_str = "rwxrwxr-x" if is_dir else "rw-rw-r--"
            rel_path = path.relative_to(book_project_dir.parent)
            
            if not dry_run:
                # Change ownership
                os.chown(path, uid, target_gid)
                # Change permissions
                os.chmod(path, expected_mode)
            
            report_lines.append(f"✓ {rel_path} → {username}:{target_gid} ({mode_str})")
            fixed_count += 1
        except Exception as exc:
            rel_path = path.relative_to(book_project_dir.parent)
            report_lines.append(f"✗ {rel_path}: {exc}")
            failed_count += 1
    
    report_lines.append("")
    report_lines.append(f"Summary: {fixed_count} fixed, {failed_count} failed")
    
    success = failed_count == 0
    return success, "\n".join(report_lines)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage artifact ownership and permissions")
    parser.add_argument(
        "--book-project",
        type=Path,
        help="Path to book_project directory (auto-detect if not provided)",
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnose", action="store_true", help="Run ownership diagnostic")
    group.add_argument("--check", action="store_true", help="Check ownership status (exit code indicates issues)")
    group.add_argument("--repair", action="store_true", help="Repair ownership issues")
    group.add_argument("--repair-dry-run", action="store_true", help="Show what repairs would do without applying")
    
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    if args.diagnose:
        report = diagnose_ownership(args.book_project)
        print(report)
    elif args.check:
        ownership = check_artifact_ownership(args.book_project or Path("book_project"))
        has_issues = bool(ownership["root_owned"] or ownership["wrong_permissions"])
        if has_issues:
            print(diagnose_ownership(args.book_project))
            return 1
        else:
            print("✓ All artifacts have correct ownership")
            return 0
    elif args.repair or args.repair_dry_run:
        success, report = repair_ownership(args.book_project, dry_run=args.repair_dry_run)
        print(report)
        return 0 if success else 1
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
