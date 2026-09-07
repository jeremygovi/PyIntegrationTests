import copy
import json

import pytest
import yaml

from pyintegrationtests.cli import main
from pyintegrationtests.errors import ValidationError
from pyintegrationtests.pytest_plugin import load_suite


def suite():
    return {
        "apiVersion": "pyintegrationtests/v1alpha1",
        "id": "example",
        "labels": {"team": "platform"},
        "tests": [
            {
                "id": "one",
                "steps": [
                    {
                        "kind": "action",
                        "id": "value",
                        "action": "data.value",
                        "with": {"value": {"replicas": 2, "newField": True}},
                        "assertions": [{"path": "$.replicas", "op": "equal", "value": 2}],
                    }
                ],
            },
            {
                "id": "two",
                "steps": [
                    {"kind": "action", "id": "value", "action": "data.value", "with": {"value": 1}}
                ],
            },
        ],
    }


def write_suite(root, payload=None):
    path = root / "example.integ.yaml"
    path.write_text(yaml.safe_dump(payload or suite(), sort_keys=False))
    return path


def configure(pytester):
    pytester.makeini("[pytest]\nfilterwarnings =\n    ignore::pytest.PytestAssertRewriteWarning\n")


def test_plugin_collects_only_reserved_suffixes_without_io(pytester):
    configure(pytester)
    write_suite(pytester.path)
    (pytester.path / "values.yaml").write_text("unsafe: not a suite")
    result = pytester.runpytest("--collect-only", "-q")
    result.assert_outcomes()
    result.stdout.fnmatch_lines(
        ["example.integ.yaml::one", "example.integ.yaml::two", "*2 tests collected*"]
    )
    assert not (pytester.path / ".pyintegrationtests").exists()


def test_plugin_pytest_selection_json_junit_and_yaml_only_extension(pytester):
    configure(pytester)
    data = suite()
    # Adding a newly exposed field check requires no adapter or Python assertion changes.
    data["tests"][0]["steps"][0]["assertions"].append(
        {"path": "$.newField", "op": "equal", "value": True}
    )
    write_suite(pytester.path, data)
    report = pytester.path / "report.json"
    junit = pytester.path / "junit.xml"
    result = pytester.runpytest(
        "-q", "-k", "one", "--pit-json", str(report), "--junitxml", str(junit)
    )
    result.assert_outcomes(passed=1, deselected=1)
    assert json.loads(report.read_text())["cases"][0]["steps"][0]["status"] == "passed"
    assert '<testcase classname="example.integ.yaml" name="one"' in junit.read_text()


def test_live_suites_are_opt_in_and_collect_offline(pytester):
    configure(pytester)
    data = suite()
    data["config"] = "config.yaml"
    (pytester.path / "config.yaml").write_text(
        "providers:\n"
        "  cluster:\n"
        "    kind: kubernetes\n"
        "    context: explicit-sandbox\n"
        "    namespace: fixtures\n"
    )
    write_suite(pytester.path, data)
    pytester.runpytest("-q").assert_outcomes(skipped=2)
    assert not (pytester.path / ".pyintegrationtests").exists()


def test_validation_failure_precedes_any_action(pytester):
    configure(pytester)
    data = suite()
    data["tests"][1]["steps"][0]["action"] = "unknown.action"
    write_suite(pytester.path, data)
    result = pytester.runpytest("-q")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*test=two step=value*Unknown action*"])
    assert not (pytester.path / ".pyintegrationtests").exists()


def test_secrets_are_masked_in_failure_json_and_junit(pytester):
    configure(pytester)
    data = suite()
    data["inputs"] = {"secret": "DO-NOT-LEAK-THIS"}
    data["sensitiveInputs"] = ["secret"]
    data["tests"] = [
        {
            "id": "secret",
            "steps": [
                {
                    "kind": "action",
                    "id": "read",
                    "action": "data.value",
                    "sensitive": True,
                    "with": {"value": {"$ref": {"source": "inputs", "path": "$.secret"}}},
                    "assertions": [{"op": "equal", "value": "wrong"}],
                }
            ],
        }
    ]
    write_suite(pytester.path, data)
    result = pytester.runpytest(
        "-q", "--showlocals", "--pit-json", "report.json", "--junitxml", "junit.xml"
    )
    result.assert_outcomes(failed=1)
    assert "DO-NOT-LEAK-THIS" not in result.stdout.str()
    for path in [
        pytester.path / "report.json",
        pytester.path / "junit.xml",
        *pytester.path.glob(".pyintegrationtests/**/*.json"),
    ]:
        assert "DO-NOT-LEAK-THIS" not in path.read_text()


def test_label_selection_and_no_tests_exit_code(pytester):
    configure(pytester)
    write_suite(pytester.path)
    result = pytester.runpytest("-q", "--pit-label", "team=other")
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED
    result.assert_outcomes(deselected=2)


def test_cli_validate_list_and_invalid_inputs(tmp_path, capsys):
    path = write_suite(tmp_path)
    assert main(["validate", str(tmp_path)]) == 0
    assert "Validated 2 cases (offline)" in capsys.readouterr().out
    assert main(["list", str(path)]) == 0
    assert "::two" in capsys.readouterr().out
    assert main(["validate", str(tmp_path / "missing")]) == 2
    assert "does not exist" in capsys.readouterr().err
    path.unlink()
    assert main(["list", str(tmp_path)]) == 5


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (
            lambda s: s["tests"][0]["steps"].append(copy.deepcopy(s["tests"][0]["steps"][0])),
            "Duplicate step",
        ),
        (lambda s: s["tests"].append(copy.deepcopy(s["tests"][0])), "duplicate test"),
        (
            lambda s: s["tests"][0]["steps"][0]["with"].update(
                value={"$ref": {"source": "steps.future.data"}}
            ),
            "future reference",
        ),
        (
            lambda s: s["tests"][0]["steps"][0]["assertions"][0].update(path="$..name"),
            "Unsupported JSONPath",
        ),
        (
            lambda s: s["tests"][0]["steps"][0].update(provider="unknown"),
            "does not accept a provider",
        ),
    ],
)
def test_semantic_validation_has_source_case_step(tmp_path, mutation, expected):
    data = suite()
    mutation(data)
    path = write_suite(tmp_path, data)
    with pytest.raises(ValidationError, match=expected) as exc:
        load_suite(path)
    assert str(path) in str(exc.value)


def test_schema_rejects_polling_a_capture(tmp_path):
    data = suite()
    data["tests"][0]["steps"].append(
        {
            "kind": "assert",
            "id": "stale",
            "source": "steps.value.data",
            "mode": "eventually",
            "assertions": [{"op": "exists"}],
        }
    )
    with pytest.raises(ValidationError, match="value_error"):
        load_suite(write_suite(tmp_path, data))
