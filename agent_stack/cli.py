import argparse
import json
import os
import sys
import time
from urllib import request

# Additional imports for output saving and logging
import datetime
import pathlib
import re
import traceback

# Helper for slugifying prompt text for directory names
def slugify(text):
    lowered = text.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered or "code-run"

from .orchestrator import OrchestratorAgent
from .validate_agent_profiles import DEFAULT_MAX_SYSTEM_PROMPT_CHARS, lint_profiles
from .artifact_ownership import diagnose_ownership, repair_ownership, check_artifact_ownership


def _print_stream(token, _chunk):
    sys.stdout.write(token)
    sys.stdout.flush()


def _api_get(api_url, path):
    with request.urlopen(f"{api_url.rstrip('/')}{path}", timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_post(api_url, path, payload):
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{api_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_profiles(orchestrator):
    print("Available profiles:")
    for profile in orchestrator.profiles:
        print(
            f"- {profile.get('name')} route={profile.get('route')} model={profile.get('model')} "
            f"priority={profile.get('priority')}"
        )


def cmd_profile_lint(profile_dir, max_system_prompt_chars, json_output):
    report = lint_profiles(
        profile_dir=pathlib.Path(profile_dir),
        max_system_prompt_chars=max(1, int(max_system_prompt_chars)),
    )
    if json_output:
        print(json.dumps(report, indent=2))
    else:
        for profile in report["profiles"]:
            label = profile.get("name") or pathlib.Path(profile["path"]).name
            if profile["errors"]:
                print(f"[FAIL] {label}")
                for error in profile["errors"]:
                    print(f"  - error: {error}")
            elif profile["warnings"]:
                print(f"[WARN] {label}")
                for warning in profile["warnings"]:
                    print(f"  - warning: {warning}")
            else:
                print(f"[OK]   {label}")
            print(f"  - system_prompt_chars={profile['stats']['system_prompt_chars']}")
        print(
            f"Profile lint {'passed' if report['valid'] else 'failed'}: "
            f"{report['profile_count']} profiles, {report['error_count']} errors, {report['warning_count']} warnings"
        )
    if not report["valid"]:
        raise SystemExit(1)


def cmd_check_ownership(book_project_dir):
    """Check artifact ownership and report status."""
    book_project_path = pathlib.Path(book_project_dir)
    ownership = check_artifact_ownership(book_project_path)
    
    has_issues = bool(ownership["root_owned"] or ownership["wrong_permissions"])
    
    print(diagnose_ownership(book_project_path))
    
    if has_issues:
        raise SystemExit(1)
    else:
        raise SystemExit(0)


def cmd_fix_ownership(book_project_dir, dry_run):
    """Fix artifact ownership issues."""
    book_project_path = pathlib.Path(book_project_dir)
    success, report = repair_ownership(book_project_path, dry_run=dry_run)
    print(report)
    
    if not success:
        raise SystemExit(1)


def cmd_health(orchestrator):
    report = orchestrator.get_agent_health_report()
    print(json.dumps(report, indent=2))


def cmd_plan(orchestrator, prompt, profile, stream):
    plan = orchestrator.plan_request(prompt, profile_name=profile, stream_override=stream)
    out = {
        "profile": (plan.get("profile") or {}).get("name"),
        "route": plan.get("route"),
        "model": plan.get("model"),
        "stream": plan.get("stream"),
        "options": plan.get("options"),
        "timeout_seconds": plan.get("timeout_seconds"),
        "retry_limit": plan.get("retry_limit"),
        "system_prompt_chars": len(plan.get("system_prompt") or ""),
    }
    print(json.dumps(out, indent=2))


def cmd_once(orchestrator, prompt, profile, stream):
    # Generate slug for prompt
    start_time = datetime.datetime.utcnow()
    timestamp = start_time.strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = slugify(prompt)[:32]
    run_id = f"{timestamp}-{slug}"
    out_dir = pathlib.Path(__file__).resolve().parent / "code_runs" / run_id
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[ERROR] Could not create output directory: {out_dir}\n{e}")
        traceback.print_exc()
        return

    try:
        response = orchestrator.handle_request_with_overrides(
            prompt,
            profile_name=profile,
            stream_override=stream,
            on_stream=_print_stream if stream else None,
        )
    except Exception as e:
        print(f"[ERROR] Orchestrator failed: {e}")
        traceback.print_exc()
        response = f"[ERROR] Orchestrator failed: {e}"

    # Save main output
    try:
        result_file = out_dir / "result.txt"
        if isinstance(response, str):
            result_file.write_text(response)
            result_preview = response[:200]
            output_size_bytes = len(response.encode("utf-8"))
        else:
            # fallback for non-str outputs
            result_file.write_text(str(response))
            result_preview = str(response)[:200]
            output_size_bytes = len(str(response).encode("utf-8"))
        print(f"[INFO] Saved result to {result_file}")
    except Exception as e:
        print(f"[ERROR] Could not write result.txt: {e}")
        traceback.print_exc()
        result_preview = str(response)[:200]
        output_size_bytes = 0

    # Save metadata
    try:
        meta = {
            "timestamp": timestamp,
            "agent": profile or "amd-coder",
            "profile": profile or "amd-coder",
            "request": {
                "prompt": prompt,
                "parameters": {}
            },
            "result_file": "result.txt",
            "result_preview": result_preview,
            "status": "completed" if not str(response).startswith("[ERROR]") else "error",
            "error": None if not str(response).startswith("[ERROR]") else str(response),
            "run_id": run_id,
            "parent_task": None,
            "extra": {
                "duration_seconds": (datetime.datetime.utcnow() - start_time).total_seconds(),
                "output_size_bytes": output_size_bytes
            }
        }
        meta_file = out_dir / "metadata.json"
        meta_file.write_text(json.dumps(meta, indent=2))
        print(f"[INFO] Saved metadata to {meta_file}")
    except Exception as e:
        print(f"[ERROR] Could not write metadata.json: {e}")
        traceback.print_exc()

    # Save collaborative Markdown log
    try:
        md_file = out_dir / "collab_log.md"
        md_file.write_text(f"""# Code Run Log: {run_id}\n\n- **Timestamp:** {timestamp}\n- **Agent/Profile:** {profile or 'amd-coder'}\n- **Prompt:** {prompt[:120]}{'...' if len(prompt) > 120 else ''}\n- **Status:** {'completed' if not str(response).startswith('[ERROR]') else 'error'}\n- **Output Preview:**\n\n```
{result_preview}
```
\n---\n\n*This log is auto-generated for audit and collaboration. See metadata.json for full details.*\n""")
        print(f"[INFO] Saved collaborative log to {md_file}")
    except Exception as e:
        print(f"[ERROR] Could not write collab_log.md: {e}")
        traceback.print_exc()

    if stream:
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        print(response)


def cmd_chat(orchestrator, profile, stream):
    print("Entering chat mode. Type 'exit' or 'quit' to leave.")
    while True:
        try:
            prompt = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting chat mode.")
            break
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            print("Exiting chat mode.")
            break
        response = orchestrator.handle_request_with_overrides(
            prompt,
            profile_name=profile,
            stream_override=stream,
            on_stream=_print_stream if stream else None,
        )
        if stream:
            sys.stdout.write("\n")
            sys.stdout.flush()
        else:
            print(response)

def cmd_server_status(api_url):
    data = _api_get(api_url, "/api/status")
    print(json.dumps(data, indent=2))

def cmd_server_watch(api_url, interval):
    while True:
        data = _api_get(api_url, "/api/status")
        health = data.get("health", {})
        agents = (health.get("agents") or {})
        counts = data.get("task_counts", {})

        print("=" * 80)
        print("Dragonlair Agent Server Watch")
        print(f"counts queued={counts.get('queued',0)} running={counts.get('running',0)} completed={counts.get('completed',0)} failed={counts.get('failed',0)}")
        print("subagents:")
        for name, info in agents.items():
            state = info.get("state")
            succ = info.get("success_count")
            fail = info.get("failed_count")
            hung = info.get("hung_count")
            print(f"- {name:14s} state={state:8s} success={succ} failed={fail} hung={hung}")

        print("recent tasks:")
        for task in data.get("tasks", [])[:10]:
            print(
                f"- {task.get('id','')[:8]} status={task.get('status')} profile={task.get('profile')} "
                f"route={task.get('route')} model={task.get('model')}"
            )

        time.sleep(interval)


def cmd_server_submit(api_url, prompt, direction, profile):
    payload = {"prompt": prompt, "direction": direction, "profile": profile}
    out = _api_post(api_url, "/api/tasks", payload)
    print(json.dumps(out, indent=2))


def cmd_cancel(api_url, task_id):
    resp = _api_post(api_url, f"/api/tasks/{task_id}/cancel", {})
    print(f"Cancelled task {task_id}: status={resp.get('status')}")

def main():
    parser = argparse.ArgumentParser(description="Dragonlair agent CLI")
    parser.add_argument("--profile", default=None, help="Profile override, e.g. amd-coder")
    parser.add_argument("--stream", action="store_true", help="Enable streamed output")
    parser.add_argument("--no-stream", action="store_true", help="Disable streamed output")
    parser.add_argument("--api-url", default=os.environ.get("DRAGONLAIR_AGENT_API", "http://127.0.0.1:11888"))

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("profiles", help="List loaded profiles")
    lint_p = sub.add_parser("profile-lint", help="Validate markdown agent profiles")
    lint_p.add_argument("--profile-dir", default=str(pathlib.Path(__file__).parent / "agent_profiles"))
    lint_p.add_argument("--max-system-prompt-chars", type=int, default=DEFAULT_MAX_SYSTEM_PROMPT_CHARS)
    lint_p.add_argument("--json", action="store_true", help="Emit full JSON report")
    
    own_p = sub.add_parser("check-ownership", help="Check artifact ownership status")
    own_p.add_argument("--book-project", default="book_project", help="Path to book_project directory")
    
    fix_p = sub.add_parser("fix-ownership", help="Fix artifact ownership issues")
    fix_p.add_argument("--book-project", default="book_project", help="Path to book_project directory")
    fix_p.add_argument("--dry-run", action="store_true", help="Show what would be fixed without applying")
    
    sub.add_parser("health", help="Show orchestrator health report")

    plan_p = sub.add_parser("plan", help="Show route/model plan for a prompt")
    plan_p.add_argument("prompt", help="Prompt text")

    once_p = sub.add_parser("once", help="Run one request")
    once_p.add_argument("prompt", help="Prompt text")

    sub.add_parser("chat", help="Interactive chat mode")

    sub.add_parser("server-status", help="Show dockerized agent server status")

    watch_p = sub.add_parser("server-watch", help="Continuously watch subagent and task status")
    watch_p.add_argument("--interval", type=float, default=2.0)

    submit_p = sub.add_parser("server-submit", help="Submit a steered task to dockerized agent server")
    submit_p.add_argument("prompt", help="Task prompt")
    submit_p.add_argument("--direction", default=None, help="Steering direction text")
    submit_p.add_argument("--profile", dest="submit_profile", default=None, help="Profile override")

    cancel_parser = sub.add_parser("cancel", help="Cancel a running or queued task by task_id")
    cancel_parser.add_argument("task_id", help="Task ID to cancel")

    args = parser.parse_args()

    if args.stream and args.no_stream:
        raise SystemExit("Use either --stream or --no-stream, not both")

    stream = True if args.stream else None
    if args.no_stream:
        stream = False
    if args.command == "cancel":
        cmd_cancel(args.api_url, args.task_id)
        return
    elif args.command == "once":
        # Import OrchestratorAgent here to avoid circular import if needed
        orchestrator = OrchestratorAgent()
        cmd_once(orchestrator, args.prompt, args.profile, stream)
        return
    elif args.command == "profiles":
        orchestrator = OrchestratorAgent()
        cmd_profiles(orchestrator)
        return
    elif args.command == "profile-lint":
        cmd_profile_lint(args.profile_dir, args.max_system_prompt_chars, args.json)
        return
    elif args.command == "check-ownership":
        cmd_check_ownership(args.book_project)
        return
    elif args.command == "fix-ownership":
        cmd_fix_ownership(args.book_project, args.dry_run)
        return
    elif args.command == "health":
        orchestrator = OrchestratorAgent()
        cmd_health(orchestrator)
        return
    elif args.command == "plan":
        orchestrator = OrchestratorAgent()
        cmd_plan(orchestrator, args.prompt, args.profile, stream)
        return
    elif args.command == "server-status":
        cmd_server_status(args.api_url)
        return
    elif args.command == "server-watch":
        cmd_server_watch(args.api_url, args.interval)
        return
    elif args.command == "server-submit":
        profile = args.submit_profile if getattr(args, "submit_profile", None) else args.profile
        cmd_server_submit(args.api_url, args.prompt, args.direction, profile)
        return
    elif args.command == "chat":
        orchestrator = OrchestratorAgent()
        cmd_chat(orchestrator, args.profile, stream)
        return
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
