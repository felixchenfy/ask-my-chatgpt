"""Correct an authorized strict-Max configuration mistake without contacting Chrome."""

import argparse
import json

from web_content_agent.chatgpt_chrome_bridge import ChatGptChromeBridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--source-task", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Apply after reviewing the dry-run result"
    )
    args = parser.parse_args()
    result = ChatGptChromeBridge().correct_unsent_thinking_mode(
        idempotency_key=args.idempotency_key,
        expected_run_id=args.expected_run_id,
        source_task=args.source_task,
        reason=args.reason,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
