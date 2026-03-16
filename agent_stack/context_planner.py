import argparse
import math
from pathlib import Path

from .profile_loader import load_agent_profiles


def estimate_tokens(text):
    # Fast heuristic for English/code mixed prompts.
    return max(1, math.ceil(len(text) / 4.0))


def build_system_prompt(profile):
    sections = profile.get("sections", {})
    purpose = sections.get("purpose", "")
    behavior = sections.get("system_behavior", "")
    actions = sections.get("actions", "")

    blocks = [
        f"Agent Profile: {profile.get('name', 'default')}",
        "",
        "Purpose:",
        purpose,
        "",
        "System Behavior:",
        behavior,
        "",
        "Actions:",
        actions,
    ]
    return "\n".join(blocks).strip()


def choose_profile(profiles, profile_name, prompt_text):
    if profile_name:
        for p in profiles:
            if p.get("name") == profile_name:
                return p
        raise ValueError(f"Profile not found: {profile_name}")

    lowered = prompt_text.lower()
    for profile in profiles:
        for keyword in profile.get("intent_keywords", []):
            if keyword in lowered:
                return profile
    if not profiles:
        raise ValueError("No profiles found")
    return profiles[0]


def recommend_num_ctx(total_tokens):
    # Context ladder aligned with your environment defaults.
    ladders = [8192, 12288, 16384, 24576, 32768, 49152, 65536, 81920]
    for value in ladders:
        if total_tokens <= value:
            return value
    return 98304


def main():
    parser = argparse.ArgumentParser(description="Estimate required Ollama num_ctx before full generation")
    parser.add_argument("--profile-dir", default=str(Path(__file__).parent / "agent_profiles"))
    parser.add_argument("--profile", default=None, help="Exact profile name, e.g. amd-coder")
    parser.add_argument("--prompt", default="", help="Prompt text")
    parser.add_argument("--prompt-file", default=None, help="Path to prompt file")
    parser.add_argument("--expected-output", type=int, default=512, help="Expected output tokens")
    parser.add_argument("--history-tokens", type=int, default=0, help="Existing conversation/history tokens")
    parser.add_argument("--safety-ratio", type=float, default=1.25, help="Safety multiplier")
    args = parser.parse_args()

    prompt_text = args.prompt
    if args.prompt_file:
        prompt_text = Path(args.prompt_file).read_text(encoding="utf-8")

    if not prompt_text.strip():
        raise ValueError("Provide --prompt or --prompt-file")

    profiles = load_agent_profiles(args.profile_dir)
    profile = choose_profile(profiles, args.profile, prompt_text)

    system_prompt = build_system_prompt(profile)
    system_tokens = estimate_tokens(system_prompt)
    user_tokens = estimate_tokens(prompt_text)

    raw_total = system_tokens + user_tokens + int(args.expected_output) + int(args.history_tokens)
    safe_total = math.ceil(raw_total * float(args.safety_ratio))
    suggested = recommend_num_ctx(safe_total)

    print(f"PROFILE={profile.get('name')}")
    print(f"ROUTE={profile.get('route')}")
    print(f"MODEL={profile.get('model')}")
    print(f"SYSTEM_TOKENS_EST={system_tokens}")
    print(f"USER_TOKENS_EST={user_tokens}")
    print(f"EXPECTED_OUTPUT_TOKENS={args.expected_output}")
    print(f"HISTORY_TOKENS={args.history_tokens}")
    print(f"RAW_TOTAL_TOKENS={raw_total}")
    print(f"SAFETY_RATIO={args.safety_ratio}")
    print(f"SAFE_TOTAL_TOKENS={safe_total}")
    print(f"SUGGESTED_NUM_CTX={suggested}")


if __name__ == "__main__":
    main()
