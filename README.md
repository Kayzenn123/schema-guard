# schema-guard

> Validate JSON and .env configs against a simple schema, from stdin or a file. Daily Tools #25.

## Why

Most config bugs are not syntax errors — they are wrong values: a port as a
string, a missing key in prod, an unknown key sneaking in. `cfg-lint` (my
day-7 tool) checks that a config file is *well-formed*; schema-guard checks
that its *values* honor a contract you define.

The closest alternatives are `ajv-cli` (Node, needs npm, full JSON Schema
draft complexity) and `check-jsonschema` (needs pip install). schema-guard
is different: a single zero-dependency Python file (stdlib only), genuinely
pipe-friendly (`cat config.json | schema-guard --schema schema.json`),
reads `.env` files too (with `--coerce` for string→type conversion), and
speaks CI with meaningful exit codes (0 valid / 1 invalid / 2 usage error).

## Install

```bash
git clone https://github.com/Kayzenn123/schema-guard.git
cd schema-guard
# no dependencies — Python 3.9+ stdlib only
chmod +x schema_guard.py
```

Requires: Python >= 3.9

## Usage

```bash
cat config.json | python3 schema_guard.py --schema schema.json
python3 schema_guard.py config.json --schema schema.json
cat app.env | python3 schema_guard.py --schema schema.json --format env --coerce
python3 schema_guard.py --schema schema.json --json < config.json
```

| Flag | Function | Default |
| --- | --- | --- |
| `--schema FILE` | JSON schema describing the expected config (required) | — |
| `config` | config file to validate (else stdin) | stdin |
| `--format` | `auto`, `json`, or `env` | `auto` |
| `--coerce` | coerce string values to the schema type (for `.env` input) | off |
| `--json` | machine-readable report instead of the human one | off |
| `--version` | print version | — |

Exit codes: `0` valid, `1` config violates schema, `2` usage/schema error.

A schema is plain JSON. Supported keywords: `type` (`string`, `integer`,
`number`, `boolean`, `null`, `array`, `object`, `any`), `required`,
`properties`, `items`, `enum`, `min`/`max`, `minLength`/`maxLength`,
`pattern` (regex), `minItems`/`maxItems`, `noExtra` (reject unknown keys),
and `rules` for conditional requirements:

```json
{
  "type": "object",
  "required": ["host", "port"],
  "noExtra": true,
  "properties": {
    "host": {"type": "string", "pattern": "^[a-z0-9.-]+$"},
    "port": {"type": "integer", "min": 1, "max": 65535},
    "mode": {"type": "string", "enum": ["dev", "staging", "prod"]}
  },
  "rules": [{"if": {"mode": "prod"}, "require": ["db"]}]
}
```

The schema file itself is meta-validated: typos in keywords, unknown types,
or bad regexes are reported as usage errors (exit 2), never tracebacks.

## Example output

Command:

```bash
cat examples/app-bad.json | python3 schema_guard.py --schema examples/app-schema.json
```

Output (real, copied from an actual run):

```text
INVALID: 8 error(s) found
  $.allowed_origins[0]: does not match pattern '^https?://'
  $.allowed_origins[1]: expected string, got 42
  $: unknown key 'cache_size' (noExtra is on)
  $.debug: expected boolean, got 'yes-please'
  $.host: does not match pattern '^[a-z0-9.-]+$'
  $.port: expected integer, got 'eighty'
  $.workers: expected >= 1, got 0
  $.rules[0]: when {"mode": "prod"}, 'db' is required
```

And a valid config through the same pipe:

```bash
cat examples/app-good.json | python3 schema_guard.py --schema examples/app-schema.json
```

```text
VALID: config conforms to schema
```

`.env` input with type coercion:

```bash
cat examples/app.env | python3 schema_guard.py --schema examples/app-schema.json --format env --coerce
```

```text
VALID: config conforms to schema
```

## How it works

1. Reads the config from a file argument or stdin, and the schema from
   `--schema`. Format is auto-detected (`{`/`[` → JSON, otherwise `.env`).
2. Meta-validates the schema document first (`check_schema`), so a broken
   schema fails fast with a clear message instead of silent mis-validation.
3. `.env` input is parsed into a dict (quotes, `export` prefix, duplicate-key
   detection); with `--coerce`, string values are converted to the schema's
   expected types with strict, reported failures.
4. `validate()` walks the schema recursively as pure functions — required
   keys, types (booleans are never accepted as integers), enums, ranges,
   regex patterns, nested objects/arrays, and `noExtra` unknown-key
   rejection. `apply_rules()` then evaluates conditional `if`/`require`
   rules. Every violation is reported with a JSON path (`$.db.pool`).
5. Prints the human report (colors only on TTY, honors `NO_COLOR`) or
   `--json` for machines, and exits 0/1/2.

Structure: one file (`schema_guard.py`, ~330 lines), tests in
`test/test_schema_guard.py`, realistic fixtures in `examples/`.

## Limitations

- The schema language is intentionally small: no `$ref`, no `allOf`/`oneOf`,
  no remote schema loading — if you need full JSON Schema, use `ajv-cli`
  or `check-jsonschema`.
- Top-level config must be a JSON object (arrays at the root are rejected).
- `--coerce` array conversion splits on commas; quoted commas are not
  supported.
- `pattern` uses Python `re` syntax, not ECMA-262; behavior may differ from
  JS-based validators on exotic patterns.
- Conditional `rules` only test equality on existing keys (no comparisons
  like "port > 1024 implies ...").
- Inline `#` comments inside `.env` values are kept as part of the value
  (standard dotenv behavior for unquoted values).

## Development

```bash
python3 -m unittest discover -s test
```

34 tests: unit tests for every keyword, `.env` parsing, coercion,
schema meta-validation, report shapes, `NO_COLOR`, plus end-to-end tests
that run the CLI as a real subprocess and assert output and exit codes.

## License

MIT

---
Ringkasan (Bahasa Indonesia): schema-guard memvalidasi file config JSON atau
.env terhadap schema JSON buatanmu — cocok untuk gate config di CI atau
pre-commit. Cara pakai: `cat config.json | python3 schema_guard.py --schema
schema.json`; hasilnya daftar pelanggaran ber-path (`$.db.pool`) dengan exit
code 0 = valid, 1 = melanggar, 2 = salah pakai. Tanpa dependensi (stdlib
Python saja), satu file. Batasan utama: bahasa schemanya sengaja sederhana
(tanpa $ref/allOf), dan config JSON harus berupa object di level teratas.
