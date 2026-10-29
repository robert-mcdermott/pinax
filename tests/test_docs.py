# SPDX-License-Identifier: Apache-2.0
"""The documentation keeps up: every command is in the command-line guide, every guide is on the website, and every
screenshot a guide shows exists in both themes."""

import re
import sys
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _choices(monkeypatch, capsys, *argv) -> list[str]:
    from pinax import cli

    monkeypatch.setattr(sys, "argv", ["pinax", *argv, "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    found = re.search(r"\{([a-z,-]+)\}", capsys.readouterr().out)
    return found.group(1).split(",") if found else []


def test_every_command_is_in_the_command_line_guide(monkeypatch, capsys):
    guide = (DOCS / "CLI.md").read_text()
    commands = _choices(monkeypatch, capsys)
    assert "serve" in commands and "db" in commands
    for command in commands:
        assert f"\n## pinax {command}\n" in guide, f"docs/CLI.md has no section for pinax {command}"
        for sub in _choices(monkeypatch, capsys, command):
            assert f"\n### pinax {command} {sub}\n" in guide, f"docs/CLI.md has no section for pinax {command} {sub}"


def test_every_guide_is_on_the_website():
    build = (DOCS / "site" / "build.mjs").read_text()
    on_site = set(re.findall(r"file: '([\w.-]+\.md)'", build))
    assert on_site == {p.name for p in DOCS.glob("*.md")}


def test_every_screenshot_exists_in_both_themes():
    shown = {m for p in DOCS.glob("*.md") for m in re.findall(r"\(images/([\w-]+)-light\.webp", p.read_text())}
    shown |= set(re.findall(r"\{\{shot ([\w-]+)", (DOCS / "site" / "pages" / "index.html").read_text()))
    assert shown, "no screenshots found"
    missing = [f"{n}-{t}.webp" for n in sorted(shown) for t in ("light", "dark") if not (DOCS / "images" / f"{n}-{t}.webp").exists()]
    assert missing == []
