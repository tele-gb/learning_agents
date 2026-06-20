from pathlib import Path

from hello_agent.storage import save_text


class SimpleAgent:
    """A minimal agent: receive a request, decide, respond, save the result."""

    def __init__(self, output_dir: Path | str = "data/outputs") -> None:
        self.output_dir = Path(output_dir)

    def run(self, request: str) -> str:
        decision = self._decide(request)
        response = self._respond(request, decision)
        output_path = save_text(self.output_dir, "latest_response.txt", response)

        return f"{response}\n\nSaved to: {output_path}"

    def _decide(self, request: str) -> str:
        if "short" in request.lower():
            return "write_a_short_answer"

        return "write_a_normal_answer"

    def _respond(self, request: str, decision: str) -> str:
        if decision == "write_a_short_answer":
            return (
                "An AI agent is a program that receives a request, reasons about "
                "what to do, takes action, and returns or stores a result."
            )

        return (
            "An AI agent is a program that connects inputs, reasoning, actions, "
            "and outputs into a repeatable loop."
        )

