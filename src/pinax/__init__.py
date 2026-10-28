# SPDX-License-Identifier: Apache-2.0
"""Pinax: explore your data. File system reporting for research storage."""

__version__ = "0.5.0"


def main() -> None:
    from .cli import main as cli_main

    cli_main()
