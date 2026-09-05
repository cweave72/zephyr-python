# app_generator

Generates a new Zephyr application from `common/templates/app`, wiring up
networking, RPC, tracing and `common/modules` so a new app builds on the first
try.

```bash
app_gen                       # TUI (default)
app_gen new --name my_app --net wifi --board esp32s3_matrix --rpc
app_gen new ... --dry-run     # print the manifest, write nothing
app_gen boards                # discoverable boards + net-type hints
app_gen modules               # selectable modules + their dependency closure
app_gen update applications/my_app
```

## How it fits together

| Piece | Role |
|---|---|
| `common/templates/app` | Copier template: every file whose *presence* is a fixed function of the answers |
| `boards.py` | Board discovery from `common/boards/*/*/board.yml` + the upstream boards this workspace uses |
| `modules.py` | Parses `depends on` out of `common/modules/*/Kconfig` and resolves the transitive closure |
| `generate.py` | Answer assembly, Copier invocation, per-board files, and `plan_files()` |
| `cli.py` / `tui.py` | The two front ends |

The per-board files are written by `generate.py` rather than the template,
because the board set is discovered and so cannot be enumerated in `copier.yml`.
Everything else is template-owned.

`plan_files()` is the single description of what a set of answers produces; both
`--dry-run` and the TUI manifest render it, so the preview cannot drift from what
lands on disk.

## Things that bite

**Board file naming is not the board name.** Zephyr looks for the *short build
string* — the board plus its qualifiers minus the SoC segment:

```
esp32s3_matrix/esp32s3/procpu  ->  boards/esp32s3_matrix_procpu.conf
w55rp20_evb_pico/rp2040        ->  boards/w55rp20_evb_pico.conf
```

`boards.conf_basename()` computes this. Getting it wrong is silent: the file is
simply never applied and the build succeeds with the board's settings missing.

**Kconfig `depends on` does not auto-enable.** A symbol whose dependency is unmet
is dropped with only a warning, and because each module gates its
`zephyr_include_directories()` on its own CONFIG, a consumer then fails with a
bare `No such file or directory`. This is why the closure is written out in full
into `conf/modules.conf`, and why the declarations in `common/modules/*/Kconfig`
have to be correct — see the Task 0 audit.

**`copier update` needs more than generation does**: a clean git working tree at
the destination, and a *versioned* template. Copier records a `_commit` in
`.copier-answers.yml` only when it can `git describe` the template source, so
`common/templates/app` must be committed and the `common` repo tagged
(`app-template-v1`, ...). Until then `app_gen update` reports what to do and
exits; plain generation is unaffected.

**`applications/` is west-managed.** `west update` resets it to `manifest-rev`;
be on `main` before committing generated apps.

## Adding a module to the selector

1. Add the module name to `modules.SELECTABLE`.
2. Add a snippet at
   `common/templates/app/template/src/{% if '<Name>' in modules %}<Name>.c{% endif %}.jinja`,
   built from a real call site in an existing app or module — not from the
   header, since the signatures need instances, buffers and callbacks that
   cannot be inferred.
3. Keep the example inert behind `#if 0` so a generated app always builds.

Verify with the guard-stripping test: generate an app with every module, delete
the `#if 0`/`#endif` lines, and build. That is what catches a snippet drifting
from its module's API.
