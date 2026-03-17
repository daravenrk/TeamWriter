# Model context test prompt for Fibonacci sequence
PROMPT = """
Write a Python program that prints the Fibonacci sequence up to 1000.
"""

# List of AMD coder models to test
MODELS = [
    "qwen3.5:9b",
    "qwen2.5-coder:14b",
    "deepseek-coder-v2:16b",
    "starcoder2:15b",
    "codellama:13b"
]

# Example: How to use this prompt with your orchestrator or agent stack
# (Replace with actual API or CLI call as needed)
# for model in MODELS:
#     result = run_agent(model=model, prompt=PROMPT)
#     print(f"Model: {model}\n{result}\n{'-'*40}")

# This file is a template for manual or automated testing.
