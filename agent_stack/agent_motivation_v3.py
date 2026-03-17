import json
import os
from pathlib import Path
from threading import Lock

PROGRESS_FILE = Path(__file__).parent / "agent_progress.json"
PROGRESS_LOCK = Lock()
STAR_FILE = Path(__file__).parent / "agent_stars.md"

BYTE_LEVELS = [8, 16, 32, 64]

class AgentMotivationV3:
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
                self.progress[self.agent_name] = {"bits": 0, "byte_level": 0, "stars": 0, "failures": 0, "successes": 0}

    def _save(self):
        with PROGRESS_LOCK:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.progress, f, indent=2)

    def add_bit(self):
        self.progress[self.agent_name]["bits"] += 1
        self.progress[self.agent_name]["successes"] += 1
        self._check_byte_level()
        self._save()

    def remove_bit(self):
        if self.progress[self.agent_name]["bits"] > 0:
            self.progress[self.agent_name]["bits"] -= 1
        elif self.progress[self.agent_name]["byte_level"] > 0:
            self.progress[self.agent_name]["byte_level"] -= 1
            self.progress[self.agent_name]["bits"] = BYTE_LEVELS[self.progress[self.agent_name]["byte_level"]] - 1
        self.progress[self.agent_name]["failures"] += 1
        self._save()
        self._check_flagged()

    def _check_byte_level(self):
        level = self.progress[self.agent_name]["byte_level"]
        if level < len(BYTE_LEVELS):
            if self.progress[self.agent_name]["bits"] >= BYTE_LEVELS[level]:
                self.progress[self.agent_name]["bits"] = 0
                self.progress[self.agent_name]["byte_level"] += 1
                if self.progress[self.agent_name]["byte_level"] == len(BYTE_LEVELS):
                    self._award_star()
                    self.progress[self.agent_name]["byte_level"] = 0

    def _award_star(self):
        self.progress[self.agent_name]["stars"] += 1
        self._save()
        with open(STAR_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n⭐ Agent {self.agent_name} achieved a star at {self.progress[self.agent_name]['successes']} successes!\n")

    def remove_star(self):
        if self.progress[self.agent_name]["stars"] > 0:
            self.progress[self.agent_name]["stars"] -= 1
            # When a star is removed, refill all points (bits/bytes) to max (64)
            self.progress[self.agent_name]["bits"] = 0
            self.progress[self.agent_name]["byte_level"] = len(BYTE_LEVELS)
            self._save()
            with open(STAR_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n❌ Agent {self.agent_name} lost a star and was refilled to 64 points.\n")

    def _check_flagged(self):
        if self.progress[self.agent_name]["stars"] <= 0 and self.progress[self.agent_name]["bits"] <= 0 and self.progress[self.agent_name]["byte_level"] <= 0:
            with open(STAR_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n⚠️ Agent {self.agent_name} flagged for review: all stars and points lost.\n")

    def get_progress(self):
        return self.progress[self.agent_name]

# Example usage:
if __name__ == "__main__":
    agent = AgentMotivationV3("book-writer")
    for _ in range(8*4):
        agent.add_bit()
    print(agent.get_progress())
    agent.remove_star()
    print(agent.get_progress())
