#!/usr/bin/env python3
"""Digest-only Codex runner for BoW's scheduled jobs (stdlib only)."""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


def resolve_model(value=""):
    """Translate old .env aliases while recording the actual OpenAI model."""
    value = value.strip()
    if not value or value == "opus" or value.startswith("claude-opus"):
        return os.environ.get("BOW_CODEX_MODEL") or "gpt-6.1-sol"
    if value in ("sonnet", "haiku") or value.startswith(("claude-sonnet", "claude-haiku")):
        return os.environ.get("BOW_CODEX_EXTRACT_MODEL") or "gpt-6-luna"
    return value


def ask(prompt, model, *, json_output=False, images_dir=None, timeout=600):
    binary = shutil.which("codex")
    if not binary:
        raise ValueError("codex not installed; see docs/ai.md")
    images = []
    if images_dir:
        directory = Path(images_dir).resolve()
        index = json.loads((directory / "candidates.json").read_text(encoding="utf-8"))
        for name in index:
            path = (directory / name).resolve()
            if path.parent != directory or not path.is_file():
                raise ValueError(f"Missing or invalid image candidate: {name}")
            images.append(path)
        if not images:
            raise ValueError("No image candidates to inspect")
        prompt += "\n\nAttached images, in order (use these exact filenames):\n"
        prompt += "\n".join(f"{i}: {p.name}" for i, p in enumerate(images, 1))

    # No repository, personal instructions, plugins, MCP, shell, or file tools.
    # Photos arrive as image inputs; the model never needs to open local files.
    with tempfile.TemporaryDirectory(prefix="bow-codex-") as work:
        output = Path(work) / "answer.txt"
        command = [binary, "--ask-for-approval", "never", "exec",
                   "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                   "--sandbox", "read-only", "--color", "never", "--cd", work,
                   "--model", model, "--output-last-message", str(output),
                   "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                   "-c", 'model_reasoning_effort="medium"']
        for feature in ("shell_tool", "shell_snapshot", "multi_agent", "apps", "plugins",
                        "remote_plugin", "hooks", "goals", "view_image", "skill_search",
                        "workspace_dependencies", "browser_use", "computer_use",
                        "image_generation", "sleep_tool", "code_mode_host"):
            command += ["--disable", feature]
        command += ["--enable", "skip_host_skill_discovery"]
        for path in images:
            # = keeps the variadic --image option from consuming the stdin marker.
            command.append(f"--image={path}")
        command.append("-")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            process.communicate(prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise ValueError(f"Codex timed out after {timeout}s") from None
        if process.returncode:
            raise ValueError(f"Codex exited with status {process.returncode}")
        answer = output.read_text(encoding="utf-8").strip() if output.exists() else ""
        if not answer:
            raise ValueError("Codex returned an empty answer")
        if json_output:
            # Existing prompts ask for bare JSON; tolerate a surrounding code fence.
            if answer.startswith("```") and answer.endswith("```"):
                answer = answer.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            decoded = json.loads(answer)
            if not isinstance(decoded, (list, dict)):
                raise ValueError("Expected a JSON object or array")
            answer = json.dumps(decoded, ensure_ascii=False)
        return answer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolve-model", nargs="?", const="")
    parser.add_argument("--model", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--images-dir")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("BOW_CODEX_TIMEOUT", 600)))
    args = parser.parse_args()
    if args.resolve_model is not None:
        print(resolve_model(args.resolve_model))
        return
    try:
        prompt = sys.stdin.read()
        if not prompt.strip():
            raise ValueError("Prompt is empty")
        print(ask(prompt, resolve_model(args.model), json_output=args.json,
                  images_dir=args.images_dir, timeout=args.timeout))
    except (ValueError, OSError) as error:
        print(f"codex: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
