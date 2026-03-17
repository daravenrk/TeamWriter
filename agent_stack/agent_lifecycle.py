import datetime
import json
from pathlib import Path

LIFECYCLE_LOG = Path(__file__).parent / "agent_lifecycle.log"

class AgentLifecycle:
    def __init__(self, agent_name):
        self.agent_name = agent_name

    def log_event(self, event, details=None):
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "agent": self.agent_name,
            "event": event,
            "details": details or {},
        }
        with open(LIFECYCLE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def activate(self, purpose, objective):
        self.log_event("activate", {"purpose": purpose, "objective": objective})

    def handoff(self, to_agent):
        self.log_event("handoff", {"to": to_agent})

    def terminate(self, reason=None):
        self.log_event("terminate", {"reason": reason})

# Example usage:
if __name__ == "__main__":
    agent = AgentLifecycle("book-writer")
    agent.activate("Draft book sections", "Produce section draft")
    agent.handoff("book-editor")
    agent.terminate("stage complete")
