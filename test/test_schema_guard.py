"""Tests for schema-guard. Run: python3 -m unittest discover -s test"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import schema_guard as sg

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "schema_guard.py")
SCHEMA = {
    "type": "object",
    "required": ["host", "port"],
    "noExtra": True,
    "properties": {
        "host": {"type": "string", "pattern": r"^[a-z0-9.-]+$"},
        "port": {"type": "integer", "min": 1, "max": 65535},
        "mode": {"type": "string", "enum": ["dev", "staging", "prod"]},
        "debug": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "db": {"type": "object", "required": ["name"],
               "properties": {"name": {"type": "string"},
                              "pool": {"type": "integer", "min": 1}}},
    },
    "rules": [{"if": {"mode": "prod"}, "require": ["db"]}],
}


class TestValidate(unittest.TestCase):
    def test_valid_config_passes(self):
        data = {"host": "api.example.com", "port": 8080, "mode": "dev",
                "debug": True, "tags": ["web"],
                "db": {"name": "app", "pool": 5}}
        self.assertEqual(sg.validate(data, SCHEMA), [])

    def test_missing_required(self):
        errs = sg.validate({"host": "x"}, SCHEMA)
        self.assertTrue(any("missing required key 'port'" in e for e in errs))

    def test_wrong_type(self):
        errs = sg.validate({"host": "x", "port": "8080"}, SCHEMA)
        self.assertTrue(any("$.port" in e and "integer" in e for e in errs))

    def test_bool_is_not_integer(self):
        errs = sg.validate({"host": "x", "port": True}, SCHEMA)
        self.assertTrue(any("$.port" in e for e in errs))

    def test_enum_violation(self):
        errs = sg.validate({"host": "x", "port": 1, "mode": "production"}, SCHEMA)
        self.assertTrue(any("one of" in e and "$.mode" in e for e in errs))

    def test_range_violation(self):
        errs = sg.validate({"host": "x", "port": 99999}, SCHEMA)
        self.assertTrue(any("$.port" in e for e in errs))

    def test_pattern_violation(self):
        errs = sg.validate({"host": "BAD_HOST!", "port": 1}, SCHEMA)
        self.assertTrue(any("$.host" in e and "pattern" in e for e in errs))

    def test_no_extra_unknown_key(self):
        errs = sg.validate({"host": "x", "port": 1, "surprise": 1}, SCHEMA)
        self.assertTrue(any("unknown key" in e for e in errs))

    def test_nested_object(self):
        errs = sg.validate({"host": "x", "port": 1, "db": {"pool": 0}}, SCHEMA)
        self.assertTrue(any("missing required key 'name'" in e for e in errs))
        self.assertTrue(any("$.db.pool" in e for e in errs))

    def test_array_items(self):
        errs = sg.validate({"host": "x", "port": 1, "tags": ["ok", 42]}, SCHEMA)
        self.assertTrue(any("$.tags[1]" in e for e in errs))
        errs = sg.validate({"host": "x", "port": 1, "tags": []}, SCHEMA)
        self.assertTrue(any("at least 1 item" in e for e in errs))


class TestRules(unittest.TestCase):
    def test_conditional_rule_fires(self):
        errs = sg.apply_rules({"mode": "prod"}, SCHEMA)
        self.assertTrue(any("'db' is required" in e for e in errs))

    def test_conditional_rule_not_fired(self):
        self.assertEqual(sg.apply_rules({"mode": "dev"}, SCHEMA), [])

    def test_conditional_rule_satisfied(self):
        self.assertEqual(
            sg.apply_rules({"mode": "prod", "db": {"name": "x"}}, SCHEMA), [])


class TestInput(unittest.TestCase):
    def test_parse_env(self):
        data = sg.parse_env('HOST=x\nPORT=8080\nexport DEBUG="true"\n# comment\n\n')
        self.assertEqual(data, {"HOST": "x", "PORT": "8080", "DEBUG": "true"})

    def test_parse_env_duplicate_key(self):
        with self.assertRaises(sg.InputError):
            sg.parse_env("A=1\nA=2\n")

    def test_parse_env_bad_line(self):
        with self.assertRaises(sg.InputError):
            sg.parse_env("just some text\n")

    def test_parse_input_empty(self):
        with self.assertRaises(sg.InputError):
            sg.parse_input("   \n", "auto")

    def test_parse_input_malformed_json(self):
        with self.assertRaises(sg.InputError):
            sg.parse_input("{oops", "json")

    def test_parse_input_json_not_object(self):
        with self.assertRaises(sg.InputError):
            sg.parse_input("[1,2]", "json")

    def test_auto_detect(self):
        self.assertEqual(sg.parse_input('{"a": 1}', "auto"), {"a": 1})
        self.assertEqual(sg.parse_input("A=1\n", "auto"), {"A": "1"})


class TestCoerce(unittest.TestCase):
    def test_coerce_env_values(self):
        schema = {"type": "object",
                  "properties": {"PORT": {"type": "integer", "min": 1},
                                 "DEBUG": {"type": "boolean"},
                                 "RATIO": {"type": "number"}}}
        data = {"PORT": "8080", "DEBUG": "true", "RATIO": "0.5"}
        self.assertEqual(sg.validate(data, schema, coerce_on=True), [])

    def test_coerce_failure_reports(self):
        schema = {"type": "object",
                  "properties": {"PORT": {"type": "integer"}}}
        errs = sg.validate({"PORT": "abc"}, schema, coerce_on=True)
        self.assertTrue(any("cannot coerce" in e for e in errs))

    def test_no_coerce_by_default(self):
        schema = {"type": "object",
                  "properties": {"PORT": {"type": "integer"}}}
        errs = sg.validate({"PORT": "8080"}, schema)
        self.assertTrue(any("expected integer" in e for e in errs))


class TestSchemaCheck(unittest.TestCase):
    def test_unknown_keyword_rejected(self):
        with self.assertRaises(sg.SchemaError):
            sg.check_schema({"type": "object", "requierd": []})

    def test_unknown_type_rejected(self):
        with self.assertRaises(sg.SchemaError):
            sg.check_schema({"type": "objekt"})

    def test_bad_pattern_rejected(self):
        with self.assertRaises(sg.SchemaError):
            sg.check_schema({"type": "string", "pattern": "([unclosed"})

    def test_good_schema_accepted(self):
        sg.check_schema(SCHEMA)  # must not raise


class TestReports(unittest.TestCase):
    def test_json_report_shape(self):
        out = json.loads(sg.json_report(["$.x: boom"]))
        self.assertFalse(out["valid"])
        self.assertEqual(out["error_count"], 1)
        self.assertEqual(len(out["errors"]), 1)
        out = json.loads(sg.json_report([]))
        self.assertTrue(out["valid"])
        self.assertEqual(out["error_count"], 0)

    def test_no_color_respected(self):
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertNotIn("\033[", sg.human_report(["$.x: boom"]))
        finally:
            del os.environ["NO_COLOR"]


def run_cli(args, stdin_text):
    return subprocess.run([sys.executable, CLI] + args, input=stdin_text,
                          capture_output=True, text=True, timeout=30,
                          env={**os.environ, "NO_COLOR": "1"})


def write_schema_file(tmp_name="t-schema.json"):
    import tempfile
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(SCHEMA, f)
    f.close()
    return f.name


class TestEndToEnd(unittest.TestCase):
    def test_e2e_valid_stdin(self):
        schema_file = write_schema_file()
        try:
            cfg = {"host": "api.example.com", "port": 8080, "mode": "staging"}
            r = run_cli(["--schema", schema_file], json.dumps(cfg))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("VALID", r.stdout)
        finally:
            os.unlink(schema_file)

    def test_e2e_invalid_exit1(self):
        schema_file = write_schema_file()
        try:
            cfg = {"host": "x", "mode": "prod"}  # missing port, missing db
            r = run_cli(["--schema", schema_file], json.dumps(cfg))
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("INVALID", r.stdout)
            self.assertIn("missing required key 'port'", r.stdout)
            self.assertIn("rules[0]", r.stdout)
        finally:
            os.unlink(schema_file)

    def test_e2e_broken_schema_exit2_no_traceback(self):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write('{"type": "nope"}')
        f.close()
        try:
            r = run_cli(["--schema", f.name], '{"a": 1}')
            self.assertEqual(r.returncode, 2)
            self.assertIn("error:", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            os.unlink(f.name)

    def test_e2e_version_flag(self):
        r = run_cli(["--version"], "")
        self.assertEqual(r.returncode, 0)
        self.assertIn("schema-guard", r.stdout)

    def test_e2e_json_flag(self):
        schema_file = write_schema_file()
        try:
            r = run_cli(["--schema", schema_file, "--json"], '{"host": "x"}')
            self.assertEqual(r.returncode, 1)
            out = json.loads(r.stdout)
            self.assertFalse(out["valid"])
        finally:
            os.unlink(schema_file)


if __name__ == "__main__":
    unittest.main()
