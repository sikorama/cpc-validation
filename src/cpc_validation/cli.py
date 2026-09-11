"""Console-script entry point."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import click

from cpc_validation import manifest as manifest_mod
from cpc_validation.report import TestResult, emit, emit_junit
from cpc_validation.runner import RunnerError, invoke
from cpc_validation.verdict import evaluate


@click.group()
def main() -> None:
    """cpc-validation: drive a CPC emulator through a catalog of tests."""


@main.command()
@click.option(
    "--runner",
    "runner_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to a runner binary implementing the schema/runner-protocol contract.",
)
@click.option(
    "--catalog",
    "catalog_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Catalog root directory containing manifest.toml files.",
)
@click.option(
    "--filter",
    "name_filter",
    type=str,
    default=None,
    help="Only run tests whose name contains this substring.",
)
@click.option(
    "--out-dir",
    "out_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Where runner artefacts go. Defaults to a temp dir cleaned up on exit.",
)
@click.option(
    "--keep-artefacts",
    is_flag=True,
    help="Keep runner artefacts even if --out-dir was auto-created.",
)
@click.option(
    "--bless",
    is_flag=True,
    help="Update screen_image goldens from the runner's output.",
)
@click.option(
    "--junit",
    "junit_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Write a JUnit XML report to this path.",
)
def run(
    runner_path: Path,
    catalog_path: Path,
    name_filter: str | None,
    out_dir: Path | None,
    keep_artefacts: bool,
    bless: bool,
    junit_path: Path | None,
) -> None:
    """Run every manifest in CATALOG against RUNNER and print the results."""
    manifests = manifest_mod.discover(catalog_path)
    if name_filter:
        manifests = [m for m in manifests if name_filter in m.name]

    # Used to pick a per-runner screen_image golden (goldens/<runner_name>/<file>)
    # when the shared default golden doesn't match this runner's native
    # resolution/rendering. E.g. "cpc-runner-amspirit" -> "amspirit".
    runner_name = runner_path.stem.removeprefix("cpc-runner-")

    if not manifests:
        click.echo("no manifests found", err=True)
        sys.exit(2)

    tmp_root: Path | None = None
    if out_dir is None:
        tmp_root = Path(tempfile.mkdtemp(prefix="cpc-validation-"))
        out_dir = tmp_root

    try:
        results: list[TestResult] = []
        for m in manifests:
            slug = m.name.replace("/", "_")
            artefact_dir = out_dir / slug
            try:
                artefacts = invoke(runner_path, m, artefact_dir)
                outcomes = evaluate(m, artefacts, bless=bless, runner_name=runner_name)
                results.append(TestResult(m, None, outcomes))
            except RunnerError as e:
                results.append(TestResult(m, str(e), []))

        ok = emit(results)
        if junit_path is not None:
            emit_junit(results, junit_path)
        sys.exit(0 if ok else 1)
    finally:
        if tmp_root is not None and not keep_artefacts:
            shutil.rmtree(tmp_root, ignore_errors=True)


@main.command(name="list")
@click.option(
    "--catalog",
    "catalog_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
def list_cmd(catalog_path: Path) -> None:
    """List every manifest in CATALOG."""
    for m in manifest_mod.discover(catalog_path):
        click.echo(f"{m.name}  ({m.setup.model}, {m.setup.crtc})  {m.path}")


if __name__ == "__main__":
    main()
