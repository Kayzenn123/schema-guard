#!/usr/bin/env python3
"""schema-guard: validate JSON or .env configs against a simple JSON schema.

Reads a config from a file or stdin, checks every value against the contract
in --schema, and reports violations with JSON paths. Pipe-friendly by design:

    cat config.json | schema-guard --schema schema.json
    schema-guard --schema schema.json < config.json
    cat app.env | schema-guard --schema schema.json --format env --coerce

Exit codes: 0 = valid, 1 = config violates schema, 2 = usage/schema error.
"""
import argparse
import json
import os
import re
import sys

VERSION = "1.0.0"

KNOWN_TYPES = ("string", "integer", "number", "boolean", "null",
               "array", "object", "any")
SCHEMA_KEYS = ("type", "required", "properties", "items", "enum",
               "min", "max", "minLength", "maxLength", "pattern",
               "minItems", "maxItems", "noExtra", "rules", "description")


class SchemaError(Exception):
    """The schema file itself is malformed (usage error -> exit 2)."""


class InputError(Exception):
    """The config input cannot be read or parsed (usage error -> exit 2)."""


# ---------------------------------------------------------------- schema check

def check_schema(node, path="$schema"):
    """Meta-validate the schema document. Raises SchemaError on problems."""
    problems = []

    def err(msg):
        problems.append("%s: %s" % (path, msg))

    def walk(n, p):
        if not isinstance(n, dict):
            problems.append("%s: schema node must be an object" % p)
            return
        for k in n:
            if k not in SCHEMA_KEYS:
                problems.append("%s: unknown keyword %r (typo?)" % (p, k))
        t = n.get("type", "any")
        if t not in KNOWN_TYPES:
            problems.append("%s: unknown type %r" % (p, t))
        req = n.get("required", [])
        if not isinstance(req, list) or any(not isinstance(x, str) for x in req):
            problems.append("%s: 'required' must be a list of strings" % p)
        props = n.get("properties", {})
        if not isinstance(props, dict):
            problems.append("%s: 'properties' must be an object" % p)
        else:
            for k, sub in props.items():
                walk(sub, "%s.properties.%s" % (p, k))
        if "items" in n:
            walk(n["items"], "%s.items" % p)
        enum = n.get("enum")
        if enum is not None and (not isinstance(enum, list) or not enum):
            problems.append("%s: 'enum' must be a non-empty list" % p)
        for k in ("min", "max"):
            if k in n and not isinstance(n[k], (int, float)):
                problems.append("%s: %r must be a number" % (p, k))
        for k in ("minLength", "maxLength", "minItems", "maxItems"):
            if k in n and (not isinstance(n[k], int) or n[k] < 0):
                problems.append("%s: %r must be a non-negative integer" % (p, k))
        if "pattern" in n:
            try:
                re.compile(n["pattern"])
            except re.error as e:
                problems.append("%s: invalid regex in 'pattern': %s" % (p, e))
        if "noExtra" in n and not isinstance(n["noExtra"], bool):
            problems.append("%s: 'noExtra' must be true or false" % p)
        rules = n.get("rules", [])
        if not isinstance(rules, list):
            problems.append("%s: 'rules' must be a list" % p)
        else:
            for i, r in enumerate(rules):
                rp = "%s.rules[%d]" % (p, i)
                if not isinstance(r, dict):
                    problems.append("%s: rule must be an object" % rp)
                    continue
                if not isinstance(r.get("if"), dict):
                    problems.append("%s: rule 'if' must be an object" % rp)
                rq = r.get("require", [])
                if not isinstance(rq, list) or any(not isinstance(x, str) for x in rq):
                    problems.append("%s: rule 'require' must be a list of strings" % rp)

    walk(node, path)
    if problems:
        raise SchemaError("; ".join(problems))


# ---------------------------------------------------------------- input parsing

def parse_env(text):
    """Parse dotenv-style text into a dict. Raises InputError."""
    data = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise InputError("line %d: not a KEY=VALUE pair: %r" % (lineno, raw.strip()))
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise InputError("line %d: invalid key name %r" % (lineno, key))
        if key in data:
            raise InputError("line %d: duplicate key %r" % (lineno, key))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
    return data


def parse_input(text, fmt):
    """Parse config text. Returns dict. Raises InputError."""
    if not text.strip():
        raise InputError("empty input: nothing to validate")
    if fmt == "auto":
        stripped = text.lstrip()
        fmt = "json" if stripped[:1] in ("{", "[") else "env"
    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise InputError("invalid JSON: %s" % e)
        if not isinstance(data, dict):
            raise InputError("top-level JSON must be an object, got %s"
                             % type(data).__name__)
        return data
    if fmt == "env":
        return parse_env(text)
    raise InputError("unknown format %r (choose json, env, or auto)" % fmt)


# ---------------------------------------------------------------- coercion

def coerce_scalar(value, want):
    """Try to convert a string to the schema type. Returns (ok, new_value)."""
    if not isinstance(value, str):
        return True, value
    s = value.strip()
    try:
        if want == "integer":
            if re.fullmatch(r"[+-]?\d+", s):
                return True, int(s)
        elif want == "number":
            if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", s):
                num = float(s)
                return True, int(num) if num.is_integer() and "." not in s and "e" not in s.lower() else num
        elif want == "boolean":
            if s.lower() in ("true", "1", "yes", "on"):
                return True, True
            if s.lower() in ("false", "0", "no", "off"):
                return True, False
        elif want == "array":
            return True, [p.strip() for p in s.split(",")] if s else []
        elif want == "null":
            if s.lower() in ("null", "none", ""):
                return True, None
    except (ValueError, OverflowError):
        pass
    return False, value


# ---------------------------------------------------------------- validation

def lookup(data, dotted):
    """Fetch data['a']['b'] for dotted='a.b'. Returns (found, value)."""
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def fmt_value(v):
    if isinstance(v, str):
        return repr(v)
    return json.dumps(v) if not isinstance(v, bool) else str(v).lower()


def check_type(value, want, path, coerce_on):
    """Returns (value_to_use, error_message_or_None)."""
    if want == "any":
        return value, None
    v = value
    if coerce_on and isinstance(value, str) and want != "string":
        ok, conv = coerce_scalar(value, want)
        if not ok:
            return value, "%s: cannot coerce %r to %s" % (path, value, want)
        v = conv
    table = {
        "string": isinstance(v, str),
        "integer": isinstance(v, int) and not isinstance(v, bool),
        "number": isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": isinstance(v, bool),
        "null": v is None,
        "array": isinstance(v, list),
        "object": isinstance(v, dict),
    }
    if not table[want]:
        return value, "%s: expected %s, got %s" % (path, want, fmt_value(value))
    return v, None


def validate(value, schema, path="$", coerce_on=False):
    """Validate value against schema node. Returns list of error strings."""
    errors = []
    value, terr = check_type(value, schema.get("type", "any"), path, coerce_on)
    if terr:
        return [terr]
    t = schema.get("type", "any")

    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: expected one of %s, got %s"
                      % (path, json.dumps(schema["enum"]), fmt_value(value)))

    if t in ("integer", "number"):
        if "min" in schema and value < schema["min"]:
            errors.append("%s: expected >= %s, got %s" % (path, schema["min"], value))
        if "max" in schema and value > schema["max"]:
            errors.append("%s: expected <= %s, got %s" % (path, schema["max"], value))
    if t == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: too short (min %d chars)" % (path, schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append("%s: too long (max %d chars)" % (path, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append("%s: does not match pattern %r" % (path, schema["pattern"]))
    if t == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: need at least %d item(s), got %d"
                          % (path, schema["minItems"], len(value)))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: need at most %d item(s), got %d"
                          % (path, schema["maxItems"], len(value)))
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(validate(item, schema["items"],
                                       "%s[%d]" % (path, i), coerce_on))
    if t == "object":
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing required key %r" % (path, key))
        props = schema.get("properties", {})
        for key in value:
            if key in props:
                errors.extend(validate(value[key], props[key],
                                       "%s.%s" % (path, key), coerce_on))
            elif schema.get("noExtra"):
                errors.append("%s: unknown key %r (noExtra is on)" % (path, key))
    return errors


def apply_rules(data, schema, coerce_on=False):
    """Conditional rules: [{'if': {'mode': 'prod'}, 'require': ['db']}]."""
    errors = []
    for i, rule in enumerate(schema.get("rules", [])):
        cond = rule.get("if", {})
        matched = True
        for dotted, expected in cond.items():
            found, actual = lookup(data, dotted)
            if coerce_on and isinstance(actual, str) and not isinstance(expected, str):
                want = {bool: "boolean", int: "integer",
                        float: "number"}.get(type(expected))
                if want:
                    ok, conv = coerce_scalar(actual, want)
                    actual = conv if ok else actual
            if not found or actual != expected:
                matched = False
                break
        if matched:
            for dotted in rule.get("require", []):
                found, val = lookup(data, dotted)
                if not found or val is None:
                    errors.append("$.rules[%d]: when %s, %r is required"
                                  % (i, json.dumps(cond), dotted))
    return errors


# ---------------------------------------------------------------- reporting

def use_color():
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if use_color() else text


def human_report(errors):
    if not errors:
        return paint("VALID: config conforms to schema", "32")
    lines = [paint("INVALID: %d error(s) found" % len(errors), "31")]
    lines.extend("  " + e for e in errors)
    return "\n".join(lines)


def json_report(errors):
    return json.dumps({
        "valid": not errors,
        "error_count": len(errors),
        "errors": [{"message": e} for e in errors],
    }, indent=2)


# ---------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog="schema-guard",
        description="Validate a JSON or .env config (file or stdin) "
                    "against a JSON schema. Exit 0=valid, 1=invalid, 2=usage error.")
    p.add_argument("config", nargs="?",
                   help="config file to validate (default: read stdin)")
    p.add_argument("--schema", required=True,
                   help="JSON schema file describing the expected config")
    p.add_argument("--format", choices=("auto", "json", "env"), default="auto",
                   help="config format (default: auto-detect)")
    p.add_argument("--coerce", action="store_true",
                   help="coerce string values to the schema type "
                        "(useful for .env input where everything is a string)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output instead of the human report")
    p.add_argument("--version", action="version", version="schema-guard " + VERSION)
    return p


def read_text(path, what):
    try:
        if path:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        if sys.stdin.isatty():
            raise InputError("no input: pipe the config via stdin or pass a file")
        return sys.stdin.read()
    except OSError as e:
        raise InputError("cannot read %s %r: %s" % (what, path or "<stdin>", e))
    except UnicodeDecodeError as e:
        raise InputError("cannot decode %s as UTF-8: %s" % (what, e))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        try:
            schema = json.loads(read_text(args.schema, "schema file"))
        except json.JSONDecodeError as e:
            raise SchemaError("schema file is not valid JSON: %s" % e)
        check_schema(schema)
        data = parse_input(read_text(args.config, "config"), args.format)
        errors = validate(data, schema, coerce_on=args.coerce)
        errors += apply_rules(data, schema, coerce_on=args.coerce)
    except (SchemaError, InputError) as e:
        print("schema-guard: error: %s" % e, file=sys.stderr)
        return 2
    print(json_report(errors) if args.json else human_report(errors))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
