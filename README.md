# Autograder

A multi-mode autograder for course assignments. Supports Python and C++ submissions with three grading modes:

1. **Output Compare** — Run student code, check stdin/stdout/return code against expected values
2. **Pytest** — Run Python test functions against student code (Python only)
3. **LLM Review** — Use Claude Code to grade implementation approach, correctness, and code quality against a rubric

Includes a web interface for creating assignments, uploading submissions, triggering grading, and viewing grades + per-student feedback.

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — `pip install uv` or `winget install astral-sh.uv` (Windows)
- **[Claude Code](https://claude.com/claude-code)** — `npm install -g @anthropic-ai/claude-code` (required for Mode 3)
- **g++** — only needed for grading C++ assignments (MinGW on Windows, build-essential on Linux, Xcode tools on macOS)

## Setup

```bash
git clone https://github.com/horatioxu1122/auto-grader.git
cd auto-grader
uv sync
```

Then authenticate Claude Code (one-time):

```bash
claude
```

Follow the prompts to log in with your Anthropic account.

## Running

Start the web interface:

```bash
uv run autograder serve
```

Open http://127.0.0.1:8000 in your browser.

### Other CLI commands

```bash
uv run autograder grade <assignment>       # Grade all submissions from the terminal
uv run autograder validate <assignment>    # Check assignment config
uv run autograder status                   # Check Claude authentication
uv run autograder serve --port 3000        # Custom port
```

## How it Works

### Directory Layout

```
auto-grader/
├── assignments/              # One subfolder per assignment (config.yaml + supporting files)
├── submissions/              # Student submissions (gitignored)
├── results/                  # Generated grade CSVs and feedback (gitignored)
├── src/autograder/           # The application code
└── templates/                # Web UI templates
```

### Creating an Assignment

Easiest way: click **+ Create Assignment** on the dashboard and fill out the form. This generates a folder under `assignments/<name>/` with a `config.yaml`.

For Mode 3 (LLM Review), you can upload the instructions document (PDF/MD/TXT) during creation. The rubric you define becomes what Claude grades against.

For Mode 1 (Output Compare), you define test cases directly in the form — each can independently check:
- stdin input (none/text/file)
- return code (don't check/zero/nonzero)
- stdout (don't check/text/file)
- diff options (ignore case, whitespace, blank lines)

### Uploading Submissions

Two ways from the assignment page:

**Bulk upload (ZIP)** — handles two common LMS export formats:
- **Flat files**: `jdoe_solution.py`, `asmith_solution.py` (ID parsed from filename)
- **Folders**: `jdoe/solution.py`, `asmith/solution.py` (folder name = student ID)

Format is auto-detected; separator for flat files is configurable.

**Single student** — upload files for one student at a time, manually specify their ID.

### Grading Modes in Detail

**Mode 1 (Output Compare)** — Each test case runs the student code with optional stdin, then checks the return code and/or stdout against expected values. Points awarded separately for return code and stdout. Supports diff options for lenient matching.

**Mode 2 (Pytest)** — Copies student files into a temp dir alongside test files from `assignments/<name>/tests/`, runs pytest with JSON reporting, and maps test results to rubric items. Partial credit scaled proportionally within each rubric group.

**Mode 3 (LLM Review)** — Compiles and runs student code (captures execution output), then sends the source code + execution results + rubric to Claude Code. Claude grades each rubric item against the provided execution evidence. Grading strictness (strict/moderate/lenient) controls how generously partial credit is awarded, especially for crashed code.

### Grading Strictness

Configurable per-assignment. Affects Mode 3 most directly:
- **Strict** — points only for clearly met criteria; crashed code → minimal partial credit
- **Moderate** — fair partial credit; crashed code with correct approach → up to 50% on methodology items
- **Lenient** — generous; crashed code with correct approach → up to 75% on methodology items

In all cases, correctness items are 0 if the code doesn't run (that's objective).

## Example Assignments

The repo ships with four example assignments showing the config format for each mode:

- `assignments/hw1_output/` — Mode 1, Python
- `assignments/hw2_pytest/` — Mode 2, Python
- `assignments/hw3_llm/` — Mode 3, LLM Review
- `assignments/hw4_cpp/` — Mode 1, C++

Run `uv run autograder validate hw1_output` to check the config.

## Troubleshooting

**Dashboard shows "Claude Code not ready"**
Run `claude` in your terminal to log in. The dashboard "Re-check" button will refresh the status.

**Grading runs forever / times out**
Adjust `timeout_seconds` in the assignment's `config.yaml`. Default is 30s.

**C++ compilation fails**
Verify `g++` is on PATH: `g++ --version`. The compiler and flags are configurable in `config.yaml` (`cpp_compiler`, `cpp_flags`).

**Mode 3 grading produces inconsistent scores**
The LLM is non-deterministic. For grading audit trails, the full response is saved to `results/<assignment>/feedback/<student>.md`. Use `--regrade` from the web UI to re-run a single student.
