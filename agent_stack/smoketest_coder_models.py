import time
import subprocess
import re

PROMPT = """
Write a Python program that prints the Fibonacci sequence up to 1000.
"""

MODELS = [
    "qwen3.5:9b",
    "qwen2.5-coder:14b",
    "deepseek-coder-v2:16b",
    "starcoder2:15b",
    "codellama:13b"
]

# Replace this with the actual CLI or API call for your agent stack.
# Example CLI: ./bin/ask-amd --model MODEL --prompt PROMPT
# This script assumes a CLI tool that takes --model and --prompt arguments and prints the result to stdout.

RESULTS = []

for model in MODELS:
    print(f"\n=== Testing model: {model} ===")
    start = time.time()
    try:
        # You may need to adjust the CLI path and arguments for your environment
        proc = subprocess.run([
            "./bin/ask-amd", "--model", model, "--prompt", PROMPT
def get_gpu_stats():
    try:
        # Try nvidia-smi first
        proc = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            usage = proc.stdout.strip().split(',')
            if len(usage) >= 3:
                gpu = int(usage[0].strip())
                vram_used = int(usage[1].strip())
                vram_total = int(usage[2].strip())
                vram_pct = 100 * vram_used / vram_total if vram_total else 0
                return {"gpu_pct": gpu, "vram_pct": vram_pct, "vram_used": vram_used, "vram_total": vram_total}
    except Exception:
        pass
    try:
        # Try rocm-smi for AMD
        proc = subprocess.run(["rocm-smi"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            lines = proc.stdout.splitlines()
            for line in lines:
                if "GPU" in line and "%" in line:
                    m = re.search(r"(\d+)%.*?(\d+)MiB / (\d+)MiB", line)
                    if m:
                        gpu = int(m.group(1))
                        vram_used = int(m.group(2))
                        vram_total = int(m.group(3))
                        vram_pct = 100 * vram_used / vram_total if vram_total else 0
                        return {"gpu_pct": gpu, "vram_pct": vram_pct, "vram_used": vram_used, "vram_total": vram_total}
    except Exception:
        pass
    return {"gpu_pct": None, "vram_pct": None, "vram_used": None, "vram_total": None}

for model in MODELS:
    print(f"\n=== Testing model: {model} ===")
    gpu_stats_before = get_gpu_stats()
    start = time.time()
    try:
        proc = subprocess.run([
            "./bin/ask-amd", "--model", model, "--prompt", PROMPT
        ], capture_output=True, text=True, timeout=120)
        duration = time.time() - start
        gpu_stats_after = get_gpu_stats()
        output = proc.stdout.strip()
        error = proc.stderr.strip()
        print(f"Time: {duration:.2f}s")
        print("Output:\n", output)
        if error:
            print("[stderr]", error)
        code_ok = ("def" in output and "print" in output)
        print(f"Code quality: {'PASS' if code_ok else 'FAIL'}")
        print(f"GPU usage before: {gpu_stats_before}")
        print(f"GPU usage after: {gpu_stats_after}")
        RESULTS.append({"model": model, "time": duration, "code_ok": code_ok, "output": output, "error": error, "gpu_before": gpu_stats_before, "gpu_after": gpu_stats_after})
    except Exception as e:
        print(f"Error running model {model}: {e}")
        RESULTS.append({"model": model, "time": None, "code_ok": False, "output": "", "error": str(e), "gpu_before": gpu_stats_before, "gpu_after": None})
        ], capture_output=True, text=True, timeout=120)
        duration = time.time() - start
        output = proc.stdout.strip()
        error = proc.stderr.strip()
        print(f"Time: {duration:.2f}s")
        print("Output:\n", output)
        if error:
            print("[stderr]", error)
        # Simple code quality check: does it contain 'def' and 'print'?
        code_ok = ("def" in output and "print" in output)
        print(f"Code quality: {'PASS' if code_ok else 'FAIL'}")
        RESULTS.append({"model": model, "time": duration, "code_ok": code_ok, "output": output, "error": error})
    except Exception as e:
        print(f"Error running model {model}: {e}")
        RESULTS.append({"model": model, "time": None, "code_ok": False, "output": "", "error": str(e)})

print("\n=== Summary ===")
for r in RESULTS:
    print(f"Model: {r['model']}, Time: {r['time']}, Code: {'PASS' if r['code_ok'] else 'FAIL'}")
