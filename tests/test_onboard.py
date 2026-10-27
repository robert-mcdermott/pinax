# SPDX-License-Identifier: Apache-2.0
"""Bulk onboarding from CSV (A5): a dry run that lists every change, all-or-nothing apply, refused rows, audit."""

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from pinax.auth import identity_for
from pinax.db import session_scope
from pinax.models import AuditEvent, Lab, LabFolder, LabMembership, User
from pinax.onboard import apply_import, plan_import

EXAMPLES = Path(__file__).parents[1] / "examples" / "import"


def _files(tmp_path: Path, **contents: str) -> dict[str, Path]:
    out = {}
    for name, text in contents.items():
        out[name] = tmp_path / f"{name}.csv"
        out[name].write_text(text.lstrip())
    return out


def _plan(**files):
    with session_scope() as s:
        return plan_import(s, **files)


def _apply(**files):
    with session_scope() as s:
        plan = plan_import(s, **files)
        assert plan.ok, plan.text()
        return apply_import(s, plan)


def _state() -> dict:
    with session_scope() as s:
        return {
            "labs": {lab.slug: (lab.name, lab.kind, lab.active) for lab in s.scalars(select(Lab))},
            "people": {u.username: (u.display_name, u.email, u.title, u.is_admin, u.active) for u in s.scalars(select(User))},
            "members": {(m.lab.slug, m.user.username): m.role for m in s.scalars(select(LabMembership))},
            "folders": {f.path: f.lab.slug for f in s.scalars(select(LabFolder))},
        }


def test_dry_run_lists_every_change_and_changes_nothing(catalog, tmp_path):
    before = _state()
    files = _files(tmp_path,
                   labs="slug,name,kind\ngamma,Gamma Lab,lab\nalpha,Alpha Laboratory,lab\nbeta,Beta Lab,lab\n",
                   people="username,display_name,email\ncara,Cara Diaz,cara@example.org\n",
                   memberships="lab,username,role\ngamma,cara,pi\nbeta,ben,pi\nalpha,ada,none\n",
                   folders="filesystem,folder,lab\nfast,gamma_lab,gamma\nfast,beta_lab,alpha\nfast,alpha_lab,none\n")
    plan = _plan(**files)
    assert plan.ok, plan.text()
    got = {(c.kind, c.op, c.key): c.fields for c in plan.changes}
    assert got == {
        ("lab", "create", "gamma"): {"name": (None, "Gamma Lab"), "kind": (None, "lab"), "division": (None, ""),
                                     "research_area": (None, ""), "active": (None, True)},
        ("lab", "update", "alpha"): {"name": ("Alpha Lab", "Alpha Laboratory")},
        ("person", "create", "cara"): {"display_name": (None, "Cara Diaz"), "email": (None, "cara@example.org"),
                                       "title": (None, ""), "is_admin": (None, False), "active": (None, True)},
        ("membership", "create", "cara in gamma"): {"role": (None, "pi")},
        ("membership", "update", "ben in beta"): {"role": ("delegate", "pi")},
        ("membership", "remove", "ada in alpha"): {"role": ("pi", None)},
        ("folder", "create", "fast:gamma_lab"): {"owner": (None, "gamma")},
        ("folder", "update", "fast:beta_lab"): {"owner": ("beta", "alpha")},
        ("folder", "remove", "fast:alpha_lab"): {"owner": ("alpha", None)},
    }
    assert plan.unchanged == {"labs": 1, "people": 0, "memberships": 0, "folders": 0}
    text = plan.text()
    assert "~ folder fast:beta_lab: owner 'beta' -> 'alpha'" in text and "- membership ada in alpha" in text
    assert _state() == before


def test_apply_makes_the_changes_audits_each_and_is_idempotent(catalog, tmp_path):
    files = _files(tmp_path,
                   labs="slug,name,kind\ngamma,Gamma Lab,department\n",
                   people="username,display_name,email,title\ncara,Cara Diaz,cara@example.org,Data Manager\n",
                   memberships="lab,username,role\ngamma,cara,pi\ngamma,nobody,delegate\n",
                   folders="filesystem,folder,lab\nfast,gamma_lab,gamma\nfast,beta_lab,gamma\n")
    assert _apply(**files) == 6
    state = _state()
    assert state["labs"]["gamma"] == ("Gamma Lab", "department", True)
    assert state["people"]["cara"] == ("Cara Diaz", "cara@example.org", "Data Manager", False, True)
    assert state["members"][("gamma", "nobody")] == "delegate"
    assert state["folders"]["beta_lab"] == "gamma" and state["folders"]["gamma_lab"] == "gamma"
    with session_scope() as s:
        actions = [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.detail["source"].as_string() == "import"))]
        assert sorted(actions) == sorted(["lab.created", "person.created", "lab.member", "lab.member", "folder.mapped",
                                          "folder.mapped"])
        summary = s.scalar(select(AuditEvent).where(AuditEvent.action == "import.applied"))
        assert summary.detail["changes"] == 6 and set(summary.detail["files"]) == set(files)
        # Access follows: the new delegate now sees the folder moved to gamma.
        nobody = s.scalar(select(User).where(User.username == "nobody"))
        assert {name for (_, name) in identity_for(nobody).folders} == {"gamma_lab", "beta_lab"}
    again = _plan(**files)
    assert again.ok and not again.changes


def test_absent_columns_leave_fields_alone(catalog, tmp_path):
    _apply(**_files(tmp_path, people="username,display_name,email,title,is_admin\nada,Ada,ada@example.org,PI,true\n"))
    _apply(**_files(tmp_path, people="username,display_name,email\nada,Ada Lovelace,ada@example.org\n"))
    assert _state()["people"]["ada"] == ("Ada Lovelace", "ada@example.org", "PI", True, True)
    # A present column sets the field, an empty text value included.
    _apply(**_files(tmp_path, people="username,display_name,email,title\nada,Ada Lovelace,ada@example.org,\n"))
    assert _state()["people"]["ada"][2] == ""


@pytest.mark.parametrize(("name", "text", "problem"), [
    ("labs", "slug,name\nalpha,A\nalpha,B\n", "lab alpha is listed more than once (labs.csv:2, labs.csv:3)"),
    ("labs", "slug,name,kind\ndelta,Delta,team\n", "kind must be lab or department"),
    ("labs", "slug,name\nNot A Slug,X\n", "is not a usable slug"),
    ("labs", "slug,name\nnone,Nobody's\n", "is not a usable slug"),
    ("labs", "slug,name,active\ndelta,Delta,maybe\n", "active must be true or false"),
    ("labs", "slug,name,colour\ndelta,Delta,red\n", "has unknown column colour"),
    ("labs", "slug\ndelta\n", "is missing column name"),
    ("labs", "slug,name\ndelta,\n", "labs.csv:2: name is empty"),
    ("labs", "slug,name\ndelta,Delta,extra\n", "3 values for 2 columns"),
    ("people", "username,display_name,email\nAda,Ada,ada@example.org\n", "differs only in case from 'ada'"),
    ("people", "username,display_name,email\nal ice,Alice,al@example.org\n", "contains a space"),
    ("people", "username,display_name,email\ncara,Cara,not-an-email\n", "is not an email address"),
    ("memberships", "lab,username,role\ngamma,ada,pi\n", "no lab or department 'gamma'"),
    ("memberships", "lab,username,role\nalpha,zed,pi\n", "no person 'zed'"),
    ("memberships", "lab,username,role\nalpha,ben,owner\n", "role must be pi, delegate, or none"),
    ("memberships", "lab,username,role\nalpha,ben,pi\nalpha,ben,delegate\n", "membership alpha:ben is listed more than once"),
    ("folders", "filesystem,folder,lab\nslow,x,alpha\n", "no filesystem 'slow'"),
    ("folders", "filesystem,folder,lab\nfast,a/b,alpha\n", "is not a top-level folder name"),
    ("folders", "filesystem,folder,lab\nfast,x,gamma\n", "no lab or department 'gamma'"),
    ("folders", "filesystem,folder,lab\nfast,x,alpha\nfast,x,beta\n", "folder fast:x is listed more than once"),
])
def test_ambiguous_or_invalid_rows_refuse_the_whole_import(catalog, tmp_path, name, text, problem):
    files = _files(tmp_path, **{name: text},
                   **({} if name == "labs" else {"labs": "slug,name\nepsilon,Epsilon Lab\n"}))
    before = _state()
    plan = _plan(**files)
    assert not plan.ok and any(problem in p for p in plan.problems), plan.problems
    with session_scope() as s, pytest.raises(ValueError):
        apply_import(s, plan)
    assert _state() == before


def test_command_line_dry_run_and_apply(catalog, tmp_path, monkeypatch, capsys):
    from pinax import cli

    folder = tmp_path / "onboarding"
    folder.mkdir()
    (folder / "labs.csv").write_text("slug,name\ngamma,Gamma Lab\n")
    for argv, code in ((["pinax", "import", str(folder)], 0), (["pinax", "import", str(folder), "--apply"], 0)):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as done:
            cli.main()
        assert done.value.code == code
    out = capsys.readouterr().out
    assert "Dry run: nothing changed" in out and "Applied 1 changes" in out
    (folder / "labs.csv").write_text("slug,name\ngamma,Gamma\ngamma,Gamma Lab\n")
    monkeypatch.setattr(sys, "argv", ["pinax", "import", str(folder), "--apply"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 1 and "Refused" in capsys.readouterr().out


def test_example_files_import_into_the_demo_shape(env, tmp_path):
    """The example files in examples/import apply cleanly to a catalog with the demo's filesystems and people."""
    from pinax.models import Filesystem

    with session_scope() as s:
        s.add(Filesystem(slug="fast", name="Fast", root="/fh/fast"))
        s.add(Filesystem(slug="secure", name="Secure", root="/fh/secure"))
        s.add(User(username="cmorgan", display_name="Casey Morgan", email="cmorgan@example.org"))
    files = {n: EXAMPLES / f"{n}.csv" for n in ("labs", "people", "memberships", "folders")}
    assert _apply(**files) == 14
    assert _state()["folders"] == {"shared_reference": "research_computing", "scratch_legacy": "research_computing",
                                   "irb_archive": "irb_office", "ortiz_v": "ortiz_v"}
