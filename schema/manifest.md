# Manifest schema

Every test is described by a `manifest.toml` file inside `catalog/`. The
harness walks the catalog tree and runs each manifest it finds.

```toml
schema = 1
name = "464-boot-banner"
description = "CPC 464 boots to the BASIC 1.0 prompt within 200 frames."

[setup]
model = "Cpc464"           # required: Cpc464 | Cpc664 | Cpc6128
crtc  = "Type1"            # required: Type0 | Type1 | Type2 | Type4
frames = 200               # required: frames after any input script
disks = []                 # optional: list of paths relative to the manifest
rom = "fixtures/zex.rom"   # optional: substitute lower ROM for direct-boot
input = "input.txt"        # optional: input script path relative to manifest

[[verdict]]
kind = "screen_image"
golden = "goldens/boot.png"
tolerance = 0.0            # 0..=1 fraction of pixels allowed to differ

[[verdict]]
kind = "ram_byte"
address = 0x7FFF
value = 0x01

[[verdict]]
kind = "screen_text_contains"
needle = "ALL TESTS PASSED"

[[verdict]]
kind = "ram_hash"
range = [0x4000, 0x8000]
sha256 = "deadbeef..."
```

## Verdict kinds

A manifest may declare any number of verdicts. **All** verdicts must pass for
the test to pass. Verdicts are evaluated independently after the runner has
finished.

| `kind`                   | Reads               | Passes when                                                  |
|--------------------------|---------------------|--------------------------------------------------------------|
| `ram_byte`               | `ram.bin[address]`  | byte equals `value`                                          |
| `ram_hash`               | `ram.bin[range]`    | sha256 equals `sha256`                                       |
| `screen_image`           | `screen.png`        | pixel diff against `golden` within `tolerance`               |
| `screen_text_contains`   | `ram.bin` + `screen_mode` (via OCR) | OCR'd grid contains `needle`                  |
| `screen_text_regex`      | `ram.bin` + `screen_mode` (via OCR) | OCR'd grid matches `pattern`                  |

Paths inside a manifest are resolved relative to the manifest file.

## Bless mode

`screen_image` verdicts may be (re)blessed with `cpc-validation run --bless`.
This overwrites the `golden` PNG with the runner's actual output. Use after
intentional behaviour changes.

## Per-runner goldens

Different emulators render at different native resolutions, so a single
`golden` image can't be shared across runners. The harness derives a
runner name from the `--runner` binary's filename (stripping a
`cpc-runner-` prefix if present, e.g. `cpc-runner-amspirit` -> `amspirit`)
and looks for `<golden's dir>/<runner_name>/<golden's filename>` first,
falling back to the plain `golden` path if no such file exists yet.
`--bless` always writes to the per-runner path, never to the shared
default, so blessing one runner's goldens never clobbers another's.

Example: a manifest declaring `golden = "goldens/boot.png"` resolves, for
the `amspirit` runner, to `goldens/amspirit/boot.png` if present.
