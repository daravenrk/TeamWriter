import json
import os
from pathlib import Path
from threading import Lock

PROGRESS_FILE = Path(__file__).parent / "agent_progress.json"
PROGRESS_LOCK = Lock()

class AgentMotivation:
    def __init__(self, agent_name):
        self.agent_name = agent_name
        self._load()

    def _load(self):
        with PROGRESS_LOCK:
            if PROGRESS_FILE.exists():
                with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                    self.progress = json.load(f)
            else:
                self.progress = {}
            if self.agent_name not in self.progress:
                self.progress[self.agent_name] = {"bits": 0, "bytes": 0, "rejections": 0, "review_agreements": 0}

    def _save(self):
        with PROGRESS_LOCK:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.progress, f, indent=2)

    def add_bit(self):
        self.progress[self.agent_name]["bits"] += 1
        if self.progress[self.agent_name]["bits"] >= 8:
            self.progress[self.agent_name]["bits"] = 0
            self.progress[self.agent_name]["bytes"] += 1
        self._save()

    def remove_bit(self):
        if self.progress[self.agent_name]["bits"] > 0:
            self.progress[self.agent_name]["bits"] -= 1
        elif self.progress[self.agent_name]["bytes"] > 0:
            self.progress[self.agent_name]["bytes"] -= 1
            self.progress[self.agent_name]["bits"] = 7
        self._save()

    def add_rejection(self):
        self.progress[self.agent_name]["rejections"] += 1
        self._save()

    def add_review_agreement(self):
        self.progress[self.agent_name]["review_agreements"] += 1
        self._save()

    def get_progress(self):
        return self.progress[self.agent_name]

# Example usage:
if __name__ == "__main__":
    agent = AgentMotivation("book-writer")
    agent.add_bit()
    agent.remove_bit()
    agent.add_rejection()
    agent.add_review_agreement()
    print(agent.get_progress())
