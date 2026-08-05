from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from lean_computer_use_mcp.config import Settings
from lean_computer_use_mcp.memory.refine import (
    LlmRefiner,
    RefineSuggestions,
    apply_suggestions,
)
from lean_computer_use_mcp.metrics.logger import MetricsLogger
from lean_computer_use_mcp.record.compile import write_skill
from lean_computer_use_mcp.record.model import Recording
from lean_computer_use_mcp.record.recorder import Recorder
from lean_computer_use_mcp.record.replay import ReplayOutcome, ReplayRunner
from lean_computer_use_mcp.server import LeanComputerUse
from lean_computer_use_mcp.upstream.cli_client import CliUpstreamClient
from lean_computer_use_mcp.upstream.fake_client import FakeUpstreamClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lean-computer-use", description="Low-context Computer Use MCP facade"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the MCP server on stdio")
    serve.add_argument(
        "--fake", action="store_true", help="Use a fake upstream client for demos/tests"
    )
    serve.add_argument(
        "--upstream-binary",
        default=None,
        help="Override the open-computer-use binary name",
    )
    serve.add_argument(
        "--metrics-path", default=None, help="Write JSONL metrics to this file"
    )
    serve.add_argument(
        "--state-ttl-seconds", type=int, default=None, help="State freshness TTL"
    )

    doctor = subparsers.add_parser("doctor", help="Check upstream prerequisites")
    doctor.add_argument(
        "--upstream-binary",
        default=None,
        help="Override the open-computer-use binary name",
    )

    record = subparsers.add_parser(
        "record", help="Record a workflow demonstration into a replayable skill"
    )
    record.add_argument(
        "--app", required=True, help="App name (process name or window title)"
    )
    record.add_argument(
        "--out",
        default=None,
        help="Recording JSON path (default: recordings/<app>-<ts>.json)",
    )
    record.add_argument(
        "--seconds",
        type=float,
        default=0.0,
        help="Auto-stop after N seconds (0 = wait for Ctrl+Shift+R)",
    )
    record.add_argument(
        "--snapshot-interval",
        type=float,
        default=3.0,
        help="Element snapshot interval in seconds",
    )
    record.add_argument(
        "--description",
        default="",
        help="Short purpose of the workflow (used in the compiled skill)",
    )
    record.add_argument(
        "--fake", action="store_true", help="Use a fake upstream client for tests"
    )
    record.add_argument(
        "--upstream-binary",
        default=None,
        help="Override the open-computer-use binary name",
    )
    record.add_argument(
        "--metrics-path", default=None, help="Write JSONL metrics to this file"
    )

    compile_ = subparsers.add_parser(
        "compile", help="Compile a recording into an editable SKILL.md"
    )
    compile_.add_argument(
        "--in",
        dest="in_path",
        required=True,
        help="Recording JSON produced by `record`",
    )
    compile_.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: skills/recorded/<name>)",
    )
    compile_.add_argument(
        "--name", default=None, help="Skill name (default: recording name)"
    )
    compile_.add_argument(
        "--description", default=None, help="Override the recorded description"
    )
    compile_.add_argument(
        "--metrics-path", default=None, help="Write JSONL metrics to this file"
    )
    compile_.add_argument(
        "--library",
        default=None,
        help="Procedural memory file; learned components are added here",
    )

    replay = subparsers.add_parser(
        "replay", help="Preview or execute a recorded workflow"
    )
    replay.add_argument(
        "--in",
        dest="in_path",
        required=True,
        help="Recording JSON produced by `record`",
    )
    replay.add_argument("--app", default=None, help="Override the recorded app name")
    replay.add_argument(
        "--dry-run", action="store_true", help="Print the plan only (default)"
    )
    replay.add_argument(
        "--run",
        action="store_true",
        help="Execute steps, confirming each content-level action",
    )
    replay.add_argument(
        "--yes",
        action="store_true",
        help="Pre-confirm all content-level steps in --run mode",
    )
    replay.add_argument(
        "--fake", action="store_true", help="Use a fake upstream client for tests"
    )
    replay.add_argument(
        "--upstream-binary",
        default=None,
        help="Override the open-computer-use binary name",
    )
    replay.add_argument(
        "--metrics-path", default=None, help="Write JSONL metrics to this file"
    )
    replay.add_argument(
        "--library",
        default=None,
        help="Procedural memory file; replay results feed back into it",
    )

    components = subparsers.add_parser(
        "components", help="Inspect the procedural memory (atomic components)"
    )
    components.add_argument(
        "--library", default="memory/components.json", help="Memory file path"
    )
    components.add_argument(
        "action", choices=["list", "search", "stats", "add-alias"], help="Operation"
    )
    components.add_argument("query", nargs="*", help="search query or id/alias pair")

    recall = subparsers.add_parser(
        "recall",
        help="Map an intent onto learned components/templates and replay them",
    )
    recall.add_argument("--intent", required=True, help="What the user wants to do")
    recall.add_argument("--app", default=None, help="Target app (filters memory)")
    recall.add_argument(
        "--library", default="memory/components.json", help="Memory file path"
    )
    recall.add_argument(
        "--dry-run", action="store_true", help="Print the composed plan only (default)"
    )
    recall.add_argument(
        "--run", action="store_true", help="Execute the plan with per-step confirmation"
    )
    recall.add_argument(
        "--yes", action="store_true", help="Pre-confirm all content-level steps"
    )
    recall.add_argument(
        "--fake", action="store_true", help="Use a fake upstream client for tests"
    )
    recall.add_argument(
        "--upstream-binary",
        default=None,
        help="Override the open-computer-use binary name",
    )
    recall.add_argument(
        "--metrics-path", default=None, help="Write JSONL metrics to this file"
    )
    recall.add_argument(
        "--value",
        action="append",
        default=None,
        help="Fill a recalled value placeholder (repeat per placeholder; "
        "otherwise you are prompted for each)",
    )

    refine = subparsers.add_parser(
        "refine",
        help="LLM-assisted memory curation (aliases, merges, descriptions, generalizations)",
    )
    refine.add_argument(
        "--library", default="memory/components.json", help="Memory file path"
    )
    refine.add_argument(
        "--apply-file",
        default=None,
        help="Apply reviewed suggestions saved by a previous `refine` run",
    )
    refine.add_argument(
        "--out",
        default="memory/refine-suggestions.json",
        help="Where to save fetched suggestions for review",
    )
    refine.add_argument(
        "--api-base",
        default=None,
        help="OpenAI-compatible endpoint (default: LEAN_CU_VISION_API_BASE)",
    )
    refine.add_argument(
        "--api-key", default=None, help="API key (default: LEAN_CU_VISION_API_KEY)"
    )
    refine.add_argument(
        "--model", default=None, help="Model name (default: LEAN_CU_VISION_MODEL)"
    )

    args = parser.parse_args(argv)

    if args.command == "doctor":
        binary = args.upstream_binary or Settings.from_env().upstream_binary
        resolved = shutil.which(binary)
        if resolved:
            print(f"OK: {binary} -> {resolved}")
            return 0
        print(f"MISSING: {binary} not found on PATH")
        return 1
    if args.command == "record":
        return _cmd_record(args)
    if args.command == "compile":
        return _cmd_compile(args)
    if args.command == "replay":
        return _cmd_replay(args)
    if args.command == "components":
        return _cmd_components(args)
    if args.command == "recall":
        return _cmd_recall(args)
    if args.command == "refine":
        return _cmd_refine(args)

    settings = _settings_from_args(args)
    upstream = (
        FakeUpstreamClient()
        if args.fake
        else CliUpstreamClient(
            settings.upstream_binary, settings.upstream_timeout_seconds
        )
    )
    server = LeanComputerUse(upstream=upstream, settings=settings)
    server.to_mcp().run(transport="stdio")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    settings = _settings_from_args(args)
    upstream = (
        FakeUpstreamClient()
        if args.fake
        else CliUpstreamClient(
            settings.upstream_binary, settings.upstream_timeout_seconds
        )
    )
    out_path = args.out or f"recordings/{args.app}-{int(time.time())}.json"
    hook = None
    foreground = None
    if args.fake:
        from lean_computer_use_mcp.record.recorder import NoopForeground, NoopHook

        hook, foreground = NoopHook(), NoopForeground()
    recorder = Recorder(
        upstream=upstream,
        app=args.app,
        snapshot_interval=args.snapshot_interval,
        hook=hook,
        foreground=foreground,
        metrics=MetricsLogger(settings.metrics_path) if settings.metrics_path else None,
        description=args.description,
    )
    recorder.start()
    print(f"Recording {args.app!r}. Demonstrate the workflow now.")
    print(f"Stop with Ctrl+Shift+R or wait {args.seconds or 'indefinitely'} seconds.")
    try:
        if args.seconds > 0:
            deadline = time.time() + args.seconds
            while time.time() < deadline and not recorder.stop_event.is_set():
                time.sleep(0.2)
        else:
            while not recorder.stop_event.is_set():
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        recording = recorder.stop()
    recording.save(out_path)
    print(
        f"Recorded {len(recording.steps)} steps ({recording.metrics.duration_ms} ms) -> {out_path}"
    )
    print(f"Next: lean-computer-use compile --in {out_path}")
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    recording = Recording.load(args.in_path)
    out_dir = args.out_dir or f"skills/recorded/{recording.name}"
    skill_path, recording_path = write_skill(
        recording, out_dir, name=args.name, description=args.description
    )
    metrics = MetricsLogger(args.metrics_path) if args.metrics_path else None
    if metrics is not None:
        metrics.record(
            tool="cu_skill_compile",
            app=recording.app,
            text_chars=len(skill_path.read_text(encoding="utf-8")),
            image_bytes=0,
            image_payloads=0,
            nodes=len(recording.steps),
            truncated=False,
            latency_ms=0,
            error=None,
        )
    print(f"Skill written -> {skill_path}")
    print(f"Recording copy -> {recording_path}")
    if args.library:
        from lean_computer_use_mcp.memory.library import Memory

        memory = Memory(args.library)
        learned = memory.learn(recording)
        print(
            f"Memory: {learned['components_added']} new components, "
            f"{learned['templates']} templates -> {args.library}"
        )
    print(f"Next: lean-computer-use replay --in {recording_path} --run")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    recording = Recording.load(args.in_path)
    if args.app:
        recording.app = args.app
    settings = _settings_from_args(args)
    upstream = (
        FakeUpstreamClient()
        if args.fake
        else CliUpstreamClient(
            settings.upstream_binary, settings.upstream_timeout_seconds
        )
    )
    server = LeanComputerUse(upstream=upstream, settings=settings)
    memory = None
    if args.library:
        from lean_computer_use_mcp.memory.library import Memory

        memory = Memory(args.library)
    runner = ReplayRunner(server, yes=args.yes, memory=memory)
    dry_run = not args.run
    if dry_run:
        print(
            f"Replay plan for {recording.name!r} ({recording.app}, {len(recording.steps)} steps)"
        )
    result = runner.run(recording, dry_run=dry_run, on_step=_print_outcome)
    _print_result(result)
    if memory is not None and not dry_run:
        stats = memory.stats()
        print(
            f"Memory: {stats['components']} components, {stats['templates']} templates, "
            f"{stats['total_hits']} hits, {stats['total_misses']} misses"
        )
    return 0 if result.ok else 1


def _cmd_components(args: argparse.Namespace) -> int:
    from lean_computer_use_mcp.memory.library import Memory

    memory = Memory(args.library)
    if args.action == "stats":
        stats = memory.stats()
        print(f"Components: {stats['components']}")
        print(f"Templates: {stats['templates']}")
        print(f"Hits: {stats['total_hits']}  Misses: {stats['total_misses']}")
        return 0
    if args.action == "list":
        for component in sorted(
            memory.library.components.values(),
            key=lambda item: item.hits - item.misses,
            reverse=True,
        ):
            _print_component(component)
        return 0
    if args.action == "search":
        query = " ".join(args.query or [])
        for component, score in memory.search(query, limit=12):
            print(
                f"{score:5.2f}  {component.id}  (hits={component.hits}, misses={component.misses})"
            )
        return 0
    if args.action == "add-alias":
        if len(args.query) < 2:
            print("usage: components add-alias <component-id> <alias>")
            return 2
        ok = memory.add_alias(args.query[0], " ".join(args.query[1:]))
        print("alias added" if ok else f"component not found: {args.query[0]}")
        return 0 if ok else 1
    print(f"unknown action: {args.action}")
    return 2


def _print_component(component) -> None:
    target = component.name or f"<{component.action}>"
    extra = ""
    if component.value_template:
        extra = f" value={component.value_template}"
    print(
        f"{component.id}  {target!r}{extra}  "
        f"(hits={component.hits}, misses={component.misses}, effect={len(component.effect)})"
    )


def _cmd_refine(args: argparse.Namespace) -> int:
    from lean_computer_use_mcp.memory.library import Memory

    memory = Memory(args.library)
    if args.apply_file:
        reviewed = RefineSuggestions.from_dict(
            json.loads(Path(args.apply_file).read_text(encoding="utf-8"))
        )
        applied = apply_suggestions(memory, reviewed)
        print(
            "Applied: "
            + ", ".join(f"{kind}={count}" for kind, count in applied.items())
        )
        return 0
    if not memory.library.components:
        print(
            "Library is empty; nothing to refine. Learn components first: "
            "lean-computer-use compile --in <recording.json> --library <path>"
        )
        return 0
    settings = Settings.from_env()
    refiner = LlmRefiner(
        api_base=args.api_base or settings.vision_api_base,
        api_key=args.api_key or settings.vision_api_key,
        model=args.model or settings.vision_model,
    )
    try:
        suggestions = refiner.refine(memory.library)
    except ValueError as exc:
        print(f"Refine failed: {exc}", file=sys.stderr)
        return 1
    if suggestions.empty:
        print("No suggestions (the library already looks consistent).")
    else:
        print("Suggestions: " + suggestions.summary())
        print(json.dumps(suggestions.to_dict(), ensure_ascii=False, indent=2))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(suggestions.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved -> {out_path}")
    print(
        f"Review the file, then apply: lean-computer-use refine "
        f"--library {args.library} --apply-file {out_path}"
    )
    return 0


def _cmd_recall(args: argparse.Namespace) -> int:
    from lean_computer_use_mcp.memory.library import Memory

    memory = Memory(args.library)
    plan = memory.recall(args.intent, app=args.app)
    print(
        f"Recall {plan.kind} for {args.intent!r} "
        f"(score={plan.score}, tentative={plan.tentative})"
    )
    for index, step in enumerate(plan.steps, start=1):
        print(f"  {index}. {step.describe()}")
    if args.dry_run or not args.run:
        print("Dry-run: nothing executed. Use --run to execute.")
        return 0
    from lean_computer_use_mcp.memory.planner import fill_plan_values, placeholder_indices

    indices = placeholder_indices(plan)
    if indices:
        if args.value:
            values = list(args.value)
        else:
            values = []
            for index in indices:
                step = plan.steps[index - 1]
                values.append(
                    input(f"  Value for step {index} ({step.describe()}): ").strip()
                )
        try:
            plan = fill_plan_values(plan, values)
        except ValueError as exc:
            print(f"Recall aborted: {exc}", file=sys.stderr)
            return 1
        print(f"Filled {len(indices)} value placeholder(s):")
        for index, step in enumerate(plan.steps, start=1):
            print(f"  {index}. {step.describe()}")
    settings = _settings_from_args(args)
    upstream = (
        FakeUpstreamClient()
        if args.fake
        else CliUpstreamClient(
            settings.upstream_binary, settings.upstream_timeout_seconds
        )
    )
    server = LeanComputerUse(upstream=upstream, settings=settings)
    recording = Recording(
        name=f"recall-{int(time.time())}",
        app=plan.app or args.app or "",
        description=args.intent,
        started_at=time.time(),
        steps=plan.steps,
    )
    runner = ReplayRunner(server, yes=args.yes, memory=memory)
    result = runner.run(recording, dry_run=False, on_step=_print_outcome)
    _print_result(result)
    return 0 if result.ok else 1


def _print_outcome(index: int, outcome: ReplayOutcome) -> None:
    status = "ok" if outcome.ok else f"FAILED ({outcome.error})"
    print(f"  {index}. [{outcome.resolution}] {status}")


def _print_result(result) -> None:
    print(f"Completed {result.completed}/{len(result.outcomes)} steps")
    deltas = {
        key: result.metrics_after.get(key, 0) - result.metrics_before.get(key, 0)
        for key in ("calls", "text_chars", "image_bytes", "nodes", "vision_calls")
    }
    print(
        "Metrics delta: " + ", ".join(f"{key}={deltas.get(key, 0)}" for key in deltas)
    )


def _settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    overrides: dict[str, object] = {}
    if getattr(args, "upstream_binary", None):
        overrides["upstream_binary"] = args.upstream_binary
    if getattr(args, "metrics_path", None):
        overrides["metrics_path"] = args.metrics_path
    if getattr(args, "state_ttl_seconds", None):
        overrides["state_ttl_seconds"] = args.state_ttl_seconds
    if not overrides:
        return settings
    return Settings(**{**settings.__dict__, **overrides})
