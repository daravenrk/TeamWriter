#!/bin/bash
# Trigger the agent orchestrator to check for and start the next job if active

cd "$(dirname "$0")"
PYTHONPATH=.. python3 orchestrator.py --check-and-trigger-next
