# Procedural Memory: Atomic Components & Task Templates

Status: implemented (`memory/` package + CLI). The recorder (`docs/RECORDING.md`)
solves "remember a whole task"; this layer solves "remember the building
blocks, so a new task can be assembled from what was done before".

## Why components instead of whole tasks

Task-level memory only fires when the new task is almost identical to a
learned one. But workflows share small reusable units - `open the text panel`,
`click the font-size control`, `type a number`, `press Enter` - and a library
of a few dozen units can cover a large task space. A component is the atomic
unit; a task is just an ordered sequence of component ids.

## Three layers

| Layer | What lives there | File |
|---|---|---|
| Episodic | raw recordings, skill text, success/failure | `recordings/`, `skills/recorded/` |
| Procedural | atomic components + task templates | `memory/components.json` |
| Semantic | fingerprints, aliases, popularity, staleness | same file (indexed at load) |

## Component identity

A component is identified by a **semantic fingerprint**, not a task name:

```text
<app>::<action>::<role>::<normalized target name>
```

- `jianying::click::button::font-size`
- `jianying::type_text` (values are parameterized to `{value}` so "12" and
  "18" share one component)

Fingerprints are stable across recordings: re-recording a similar workflow
updates hits instead of duplicating entries. Aliases (added via
`components add-alias`) extend matching for synonyms ("caption-size" ->
"Font size").

## Lifecycle

```sh
# 1. Learn: compile a recording into a skill AND extract components
lean-computer-use compile --in recordings/font-size.json \
    --out-dir skills/recorded/subtitle-font-size \
    --library memory/components.json

# 2. Inspect / teach the library
lean-computer-use components --library memory/components.json list
lean-computer-use components --library memory/components.json search font
lean-computer-use components --library memory/components.json add-alias \
    jianying::click::edit::font-size caption-size

# 3. Recall: map an intent onto memory (template reuse or component chain)
lean-computer-use recall --intent "reduce the subtitle font size" \
    --app JianYing --library memory/components.json --dry-run
#    Execute with only the varying value asked: --run prompts for the value,
#    or pass it directly: --run --yes --value 12

# 4. Execute: replay feeds every step back into the library
lean-computer-use replay --in skills/recorded/subtitle-font-size/recording.json \
    --run --library memory/components.json

# 5. Curate: let the model suggest semantic equivalences, review, then apply
lean-computer-use refine --library memory/components.json
lean-computer-use refine --library memory/components.json \
    --apply-file memory/refine-suggestions.json
```

## Retrieval & composition

`memory/retrieve.py` is local and deterministic - the model never pays tokens
to re-discover a learned unit:

- **Signal filter**: a component must have a semantic hook beyond the app
  (target name/alias overlap, belonging to a matching template, or an action
  word such as "press" in the intent). App context alone is not enough.
- **Scoring**: app match +1, name/alias overlap 0..2, role match +0.3,
  template-name overlap +0.5x, popularity up to +0.75, staleness penalty -1x.
- **Composition**: when the intent confidently matches a template
  (score >= 1.5) the template is reused; otherwise the intent is projected
  onto the top components as a tentative chain for the user to confirm.
- **Values**: a recalled template never types a concrete value from memory.
  Parameterized steps carry a placeholder; `recall --run` asks only for the
  varying values (`--value 12` skips the prompt) and fills them in.
- **Honesty**: when the intent shares no tokens with memory, recall returns an
  empty plan instead of hallucinating - that is the moment to record or teach
  an alias.

## Learning feedback loop

Every replayed step updates the library (`memory/library.py`):

- success -> `hits += 1`, effect names (elements added/changed after the
  action, from the facade delta) are merged into the component;
- failure -> `misses += 1`, which raises staleness and lowers retrieval rank
  for next time;
- a step with no component yet creates one on the fly.

This makes memory **alive**: frequently used units rise to the top, broken
units decay, and effects accumulate from real executions.

## LLM-assisted curation

Deterministic extraction handles the exact, scriptable parts (fingerprint
dedupe, value parameterization). The semantic parts - "Font size" vs "??",
two components that are really one, two templates that differ only in a value
- need a model. `refine` sends a compact text-only digest of the library to an
OpenAI-compatible endpoint and returns structured suggestions:

```sh
lean-computer-use refine --library memory/components.json
# uses LEAN_CU_VISION_API_BASE / _KEY / _MODEL; or pass --api-base/--api-key/--model
```

Suggestions are saved to a JSON file and never applied in the same step.
Review the file, then apply:

```sh
lean-computer-use refine --library memory/components.json \
    --apply-file memory/refine-suggestions.json
```

Only id-valid entries are applied: aliases extend matching, merges fold hits
and aliases into the surviving component, descriptions document when a
component applies, and generalizations collapse near-identical templates into
one named template. The API key never touches the library file; it flows
through the same `LEAN_CU_VISION_*` environment variables as the vision
engine.

## Cost model

- Recall is a local JSON search: ~1 KB of file I/O, zero image bytes.
- A replayed task reuses learned targets, so each step still costs one
  `cu_observe` (control preset, no screenshot) + one `cu_act` (delta), but the
  model does not pay discovery/vision rounds for known steps.
- `--metrics-path` records every call; `components stats` shows library size.

## Limits & roadmap

- v1: Latin-keyboard recordings; composition is deterministic (no LLM
  re-ranking); per-step effect alignment is coarse (global first/last
  snapshot).
- Done: LLM-assisted curation (aliases / merges / descriptions / template
  generalizations) via `refine`, with a human-reviewed apply step.
- Done: `recall --run` asks only for the varying values (prompt or
  `--value`), then executes the rest of the learned plan.
- Next: retrieval-time alias suggestion on misses and per-step
  precondition/effect alignment from facade deltas.