import argparse
import json
import os
import sys
import time
from urllib import request

from .orchestrator import OrchestratorAgent


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
        "system_prompt_chars": len(plan.get("system_prompt") or ""),
    }
    print(json.dumps(out, indent=2))


def cmd_once(orchestrator, prompt, profile, stream):
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


def cmd_chat(orchestrator, profile, stream):
    print("Dragonlair Agent CLI chat mode. Type /help for commands, /quit to exit.")
    while True:
        try:
            prompt = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not prompt:
            continue
        if prompt in {"/quit", "/exit"}:
            break
        if prompt == "/help":
            print("/quit /exit /help /health /profiles /plan <text>")
            continue
        if prompt == "/health":
            cmd_health(orchestrator)
            continue
        if prompt == "/profiles":
            cmd_profiles(orchestrator)
            continue
        if prompt.startswith("/plan "):
            cmd_plan(orchestrator, prompt[6:].strip(), profile, stream)
            continue

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

    cancel_parser = subparsers.add_parser("cancel", help="Cancel a running or queued task by task_id")
    cancel_parser.add_argument("task_id", help="Task ID to cancel")

    args = parser.parse_args()

    if args.stream and args.no_stream:
        raise SystemExit("Use either --stream or --no-stream, not both")

    stream = None
    if args.stream:
        stream = True
    elif args.no_stream:
        stream = False

    orchestrator = OrchestratorAgent()

    if args.command == "profiles":
        cmd_profiles(orchestrator)
        return
    if args.command == "health":
        cmd_health(orchestrator)
        return
    if args.command == "plan":
        cmd_plan(orchestrator, args.prompt, args.profile, stream)
        return
    if args.command == "once":
        cmd_once(orchestrator, args.prompt, args.profile, stream)
        return
    if args.command == "chat":
        cmd_chat(orchestrator, args.profile, stream)
        return
    if args.command == "server-status":
        cmd_server_status(args.api_url)
        return
    if args.command == "server-watch":
        cmd_server_watch(args.api_url, args.interval)
        return
    if args.command == "server-submit":
        profile = args.submit_profile if getattr(args, "submit_profile", None) else args.profile
        cmd_server_submit(args.api_url, args.prompt, args.direction, profile)
        return
    if args.command == "cancel":
        cmd_cancel(args.api_url, args.task_id)
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
