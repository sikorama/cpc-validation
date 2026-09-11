"""Verdict evaluation against runner artefacts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from cpc_validation import ocr
from cpc_validation.manifest import Manifest, Verdict
from cpc_validation.runner import RunArtefacts


@dataclass
class VerdictOutcome:
    verdict: Verdict
    passed: bool
    reason: str


def evaluate(
    manifest: Manifest,
    artefacts: RunArtefacts,
    bless: bool = False,
    runner_name: str | None = None,
) -> list[VerdictOutcome]:
    return [_evaluate_one(manifest, v, artefacts, bless, runner_name) for v in manifest.verdicts]


def _evaluate_one(
    manifest: Manifest,
    verdict: Verdict,
    artefacts: RunArtefacts,
    bless: bool,
    runner_name: str | None,
) -> VerdictOutcome:
    try:
        match verdict.kind:
            case "ram_byte":
                return _ram_byte(verdict, artefacts)
            case "ram_hash":
                return _ram_hash(verdict, artefacts)
            case "screen_image":
                return _screen_image(manifest, verdict, artefacts, bless, runner_name)
            case "screen_text_contains":
                return _screen_text_contains(verdict, artefacts)
            case "screen_text_regex":
                return _screen_text_regex(verdict, artefacts)
            case _:
                return VerdictOutcome(verdict, False, f"unknown verdict kind {verdict.kind!r}")
    except Exception as e:
        return VerdictOutcome(verdict, False, f"verdict raised: {e}")


def _ram_byte(verdict: Verdict, artefacts: RunArtefacts) -> VerdictOutcome:
    address = _require_int(verdict, "address")
    expected = _require_int(verdict, "value") & 0xFF

    data = artefacts.ram_path.read_bytes()
    if not 0 <= address < len(data):
        return VerdictOutcome(
            verdict, False, f"address 0x{address:X} out of range (ram size {len(data)})"
        )
    actual = data[address]
    if actual == expected:
        return VerdictOutcome(verdict, True, f"ram[0x{address:X}] = 0x{actual:02X}")
    return VerdictOutcome(
        verdict, False, f"ram[0x{address:X}] = 0x{actual:02X}, expected 0x{expected:02X}"
    )


def _ram_hash(verdict: Verdict, artefacts: RunArtefacts) -> VerdictOutcome:
    rng = verdict.params.get("range")
    if not isinstance(rng, list) or len(rng) != 2:
        return VerdictOutcome(verdict, False, "ram_hash.range must be [start, end]")
    expected = verdict.params.get("sha256")
    if not isinstance(expected, str):
        return VerdictOutcome(verdict, False, "ram_hash.sha256 must be a string")
    start, end = int(rng[0]), int(rng[1])

    data = artefacts.ram_path.read_bytes()
    if not 0 <= start < end <= len(data):
        return VerdictOutcome(verdict, False, f"range {start:X}..{end:X} out of bounds")
    actual = hashlib.sha256(data[start:end]).hexdigest()
    if actual == expected:
        return VerdictOutcome(verdict, True, f"sha256 = {actual}")
    return VerdictOutcome(verdict, False, f"sha256 {actual} != expected {expected}")


def _screen_image(
    manifest: Manifest,
    verdict: Verdict,
    artefacts: RunArtefacts,
    bless: bool,
    runner_name: str | None,
) -> VerdictOutcome:
    golden_str = verdict.params.get("golden")
    if not isinstance(golden_str, str):
        return VerdictOutcome(verdict, False, "screen_image.golden is required")
    golden_path: Path = manifest.resolve(golden_str)
    tolerance = float(verdict.params.get("tolerance", 0.0))

    # Different emulators render at different native resolutions, so a single
    # shared golden can't work across runners. When a runner name is known,
    # look for (and always bless to) a per-runner golden alongside the
    # default one: goldens/<file> -> goldens/<runner_name>/<file>. Falls back
    # to the shared default when no per-runner golden exists yet, so existing
    # manifests/goldens need no changes.
    if runner_name:
        per_runner_path = golden_path.parent / runner_name / golden_path.name
        if bless or per_runner_path.exists():
            golden_path = per_runner_path

    if bless or not golden_path.exists():
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        Image.open(artefacts.screen_path).save(golden_path)
        return VerdictOutcome(verdict, True, f"blessed {golden_path} from {artefacts.screen_path}")

    actual = Image.open(artefacts.screen_path).convert("RGBA")
    golden = Image.open(golden_path).convert("RGBA")
    if actual.size != golden.size:
        return VerdictOutcome(verdict, False, f"size {actual.size} != golden {golden.size}")

    a = actual.tobytes()
    g = golden.tobytes()
    # Compare 4 bytes per pixel.
    pixel_count = actual.size[0] * actual.size[1]
    diff = sum(1 for i in range(pixel_count) if a[i * 4 : i * 4 + 4] != g[i * 4 : i * 4 + 4])
    ratio = diff / pixel_count if pixel_count else 0.0
    if ratio <= tolerance:
        return VerdictOutcome(verdict, True, f"diff ratio {ratio:.4f} ≤ {tolerance:.4f}")

    diff_path = artefacts.output_dir / "diff.png"
    _write_diff_png(actual, golden, diff_path)
    return VerdictOutcome(
        verdict,
        False,
        f"diff ratio {ratio:.4f} > {tolerance:.4f}; see {diff_path}",
    )


def _write_diff_png(actual: Image.Image, golden: Image.Image, out_path: Path) -> None:
    w, h = actual.size
    diff_img = Image.new("RGBA", (w, h))
    a_pixels = actual.load()
    g_pixels = golden.load()
    d_pixels = diff_img.load()
    for y in range(h):
        for x in range(w):
            if a_pixels[x, y] == g_pixels[x, y]:
                d_pixels[x, y] = (0, 0, 0, 255)
            else:
                d_pixels[x, y] = (255, 0, 255, 255)
    diff_img.save(out_path)


def _screen_text(verdict: Verdict, artefacts: RunArtefacts) -> str | None:
    mode = int(artefacts.meta.get("screen_mode", -1))
    ram = artefacts.ram_path.read_bytes()
    text = ocr.decode_screen_text(ram, mode)
    if text == "":
        return None
    return text


def _screen_text_contains(verdict: Verdict, artefacts: RunArtefacts) -> VerdictOutcome:
    needle = verdict.params.get("needle")
    if not isinstance(needle, str):
        return VerdictOutcome(verdict, False, "screen_text_contains.needle is required")
    text = _screen_text(verdict, artefacts)
    if text is None:
        return VerdictOutcome(
            verdict, False, f"OCR unavailable for screen_mode {artefacts.meta.get('screen_mode')}"
        )
    if needle in text:
        return VerdictOutcome(verdict, True, f"found {needle!r}")
    preview = " | ".join(line for line in text.splitlines() if line)[:120]
    return VerdictOutcome(verdict, False, f"{needle!r} not on screen (saw: {preview!r})")


def _screen_text_regex(verdict: Verdict, artefacts: RunArtefacts) -> VerdictOutcome:
    pattern = verdict.params.get("pattern")
    if not isinstance(pattern, str):
        return VerdictOutcome(verdict, False, "screen_text_regex.pattern is required")
    text = _screen_text(verdict, artefacts)
    if text is None:
        return VerdictOutcome(
            verdict, False, f"OCR unavailable for screen_mode {artefacts.meta.get('screen_mode')}"
        )
    flags = re.MULTILINE | re.DOTALL
    m = re.search(pattern, text, flags=flags)
    if m:
        return VerdictOutcome(verdict, True, f"matched {m.group(0)!r}")
    preview = " | ".join(line for line in text.splitlines() if line)[:120]
    return VerdictOutcome(verdict, False, f"pattern {pattern!r} not found (saw: {preview!r})")


def _require_int(verdict: Verdict, key: str) -> int:
    v = verdict.params.get(key)
    if isinstance(v, int):
        return v
    raise ValueError(f"verdict {verdict.kind} requires integer {key!r}")
