# agent_stack/chatgpu_subagent.py

class ChatGPUSubagent:
    """
    Handles requests to the ChatGPU tool/endpoint.
    """
    def __init__(self, endpoint="http://127.0.0.1:11500"):
        self.endpoint = endpoint

    def run(self, prompt, model="default", stream=False):
        # TODO: Implement API call to ChatGPU
        return f"[ChatGPUSubagent] Would call {self.endpoint} with model={model}, stream={stream}"
