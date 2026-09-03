#!/usr/bin/env python3
"""A fake `opencode` binary for offline tests.

Supports two subcommands used by university.ai:

    opencode models
        -> prints a small provider/model listing on stdout.

    opencode run <prompt> --model <id> --format json
        -> prints a newline-delimited JSON event stream whose final assistant
           text is a deterministic, schema-correct JSON answer derived from the
           prompt (so ai.summarize_item / explain / extract_concepts all parse).

No network, no real model — purely deterministic.
"""

import json
import sys


def _emit(text):
    # Mimic opencode's --format json event stream.
    print(json.dumps({"type": "step_start", "part": {"type": "step-start"}}))
    print(json.dumps({"type": "text", "part": {"type": "text", "text": text}}))
    print(json.dumps({"type": "step_finish", "part": {"type": "step-finish"}}))


def main():
    args = sys.argv[1:]
    if not args:
        return 1
    if args[0] == "models":
        print("openai/gpt-fake-mini")
        print("openai/gpt-fake")
        print("anthropic/claude-fake")
        return 0
    if args[0] == "run":
        prompt = args[1] if len(args) > 1 else ""
        low = prompt.lower()
        if '"summary"' in low and '"terms"' in low:
            payload = {
                "summary": ["First plain point.", "Second plain point.", "Third plain point."],
                "terms": ["mixture-of-experts", "router", "context window"],
            }
            _emit(json.dumps(payload))
        elif '"concepts"' in low and '"question"' in low:
            # extract_concepts: pull the selected text back out of the prompt and
            # derive a deterministic concept list. A selection containing the
            # sentinel word "vague" (or an empty span) is treated as too unclear
            # to name, so we return a clarifying question instead of guessing.
            span = ""
            marker = 'selected text: "'
            if marker in low:
                start = low.index(marker) + len(marker)
                end = low.index('"', start)
                if end != -1:
                    span = prompt[start:end]
            if "vague" in span.lower() or not span.strip():
                payload = {
                    "concepts": [],
                    "question": "Which idea do you mean — can you name the specific term?",
                }
            else:
                payload = {"concepts": [span.strip()], "question": None}
            _emit(json.dumps(payload))
        elif "article_chat_mode" in low:
            # Article chat: echo the injected grounding back so a test can prove
            # the KB notes / concept map actually reached the model.
            ground = ""
            if "begin_grounding" in low and "end_grounding" in low:
                start = low.index("begin_grounding") + len("begin_grounding")
                end = low.index("end_grounding")
                ground = prompt[start:end].strip()
            payload = {
                "lead": "About this article",
                "body": "Drawing on your notes. " + ground,
            }
            _emit(json.dumps(payload))
        else:
            # explain / summarize / ask
            payload = {
                "lead": "In plain words",
                "body": "This is a clear explanation produced by the fake model.",
                "analogy": "Like a librarian who knows exactly which shelf to check.",
            }
            _emit(json.dumps(payload))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
