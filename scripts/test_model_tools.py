from __future__ import annotations

import argparse
import json
import re
import statistics
import time

import httpx

NATIVE_CALL = re.compile(
    r"<tool_call>\s*<function=report_number>\s*"
    r"<parameter=number>\s*(\d+)\s*</parameter>\s*"
    r"</function>\s*</tool_call>"
)


def parsed_call(message: dict[str, object]) -> tuple[str, int]:
    calls = message.get("tool_calls") or []
    if isinstance(calls, list) and calls:
        function = calls[0]["function"]
        arguments = function["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return "openai", int(arguments["number"])

    match = NATIVE_CALL.fullmatch(str(message.get("reasoning_content", "")).strip())
    if not match:
        raise ValueError(f"No valid report_number call in response: {message}")
    return "native_xml", int(match.group(1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Stress-test local Qwen tool-call generation.")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        raise SystemExit("--count must be between 1 and 100")

    tool = {
        "type": "function",
        "function": {
            "name": "report_number",
            "description": "Report the requested integer.",
            "parameters": {
                "type": "object",
                "properties": {"number": {"type": "integer"}},
                "required": ["number"],
            },
        },
    }
    formats = {"openai": 0, "native_xml": 0}
    timings: list[float] = []
    with httpx.Client(timeout=120) as client:
        for number in range(1, args.count + 1):
            started = time.perf_counter()
            response = client.post(
                "http://127.0.0.1:1234/v1/chat/completions",
                json={
                    "model": "local-agent-vlm",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Call report_number with number {number}. Do not answer normally."
                            ),
                        }
                    ],
                    "tools": [tool],
                    "tool_choice": "required",
                    "temperature": 0,
                    "max_tokens": 512,
                    "reasoning_effort": "none",
                },
            )
            response.raise_for_status()
            envelope, actual = parsed_call(response.json()["choices"][0]["message"])
            if actual != number:
                raise RuntimeError(f"Call {number} returned argument {actual}")
            formats[envelope] += 1
            timings.append(time.perf_counter() - started)
            print(f"[{number}/{args.count}] correct ({envelope}, {timings[-1]:.3f}s)")

    print(
        json.dumps(
            {
                "correct": args.count,
                "formats": formats,
                "mean_seconds": round(statistics.mean(timings), 3),
            }
        )
    )
    if formats["native_xml"]:
        print(
            "WARNING: LM Studio exposed native tool XML in reasoning_content; "
            "validate the real Bionic Chat/MCP host separately."
        )


if __name__ == "__main__":
    main()
