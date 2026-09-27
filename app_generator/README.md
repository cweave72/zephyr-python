# app_generator

Generates a new Zephyr application from `common/templates/app`, wiring up
networking, RPC, tracing and `common/modules` so a new app builds on the first
try.

> **app_gen needs the full workspace.** It reads the module and board data
> from the `common` repository. It writes the new application to the
> `applications` repository. Thus app_gen does not operate in a standalone
> `zephyr-python` checkout. The other tools in this repository do.

```bash
app_gen                       # TUI (default)
app_gen new --name my_app --net wifi --board esp32s3_matrix --rpc
app_gen new --name fs_app --net eth --ip static --board w55rp20_evb_pico \
    --rpc --module FsApi --fs-size 256
app_gen new ... --dry-run     # print the manifest, write nothing
app_gen boards                # discoverable boards + net-type hints
app_gen modules               # selectable modules + their dependency closure
app_gen update applications/my_app
```

## How it fits together

| Piece | Role |
|---|---|
| `common/templates/app` | Copier template: every file whose *presence* is a fixed function of the answers |
| `boards.py` | Board discovery from `common/boards/*/*/board.yml` + the upstream boards this workspace uses; FsApi flash layouts (`FS_LAYOUTS`) |
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

## The FsApi module and the fs size

Selecting `FsApi` adds a littlefs file system (`common/modules/FsApi`):

- `conf/fs.conf`: flash, the littlefs cache size and, with RPC, `FSAPIRPC`.
- `src/FsApi.c`: `app_FsApi_init()` mounts the file system. `main.c` calls it
  before the network, so no remote call reaches an unmounted file system.
- `src/rpc.c`: registers the `FsApiRpc` callset as id 2 (RPC apps only).
- `boards/<board>.overlay`: the fs partition and the `/flash` fstab node.

`--fs-size` (TUI: "FsApi size (KiB)") sets the partition size in KiB. The
default is 128. The size is stored in `.copier-answers.yml` as `fs_size_kb`, so
`app_gen update` writes the same partition again. The overlay puts the
partition at the top of flash and ends the code partition where it starts. The
overlay also defines `APP_LFS_SIZE`, so one build can use another size without
an edit (see the generated README).

app_gen writes a partition only for a board in `boards.FS_LAYOUTS`. Each entry
gives the flash size, the erase block, the code partition and the smallest code
size to keep. app_gen rejects a size that is not a multiple of the erase block,
is smaller than 8 blocks, or leaves less than the smallest code size. Any other
board gets a commented template in its overlay, and FsApi stops the build
until you fill it in. Add a board to `FS_LAYOUTS` only after you verify its
flash layout on hardware. For example, the esp32s3 devicetrees declare 8 MiB of
flash, and some of those modules have 4 MiB.

| Board              | Flash | Code partition   | fs size range     |
|--------------------|-------|------------------|-------------------|
| `w55rp20_evb_pico` | 2 MiB | `code_partition` | 32 KiB – 1532 KiB |

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
