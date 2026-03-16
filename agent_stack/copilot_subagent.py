# agent_stack/copilot_subagent.py

class CopilotSubagent:
    """
    Handles requests to the Copilot Codes tool/endpoint.
    """
    def __init__(self, endpoint="http://127.0.0.1:11600"):
        self.endpoint = endpoint

    def run(self, prompt, model="default", stream=False):
        # TODO: Implement API call to Copilot Codes
        return f"[CopilotSubagent] Would call {self.endpoint} with model={model}, stream={stream}"
