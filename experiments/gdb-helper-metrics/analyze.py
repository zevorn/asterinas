# SPDX-License-Identifier: MPL-2.0

import json
import re
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
LOG_PATH = OUTPUT / "gdb.log"
REPORT_PATH = OUTPUT / "report.md"
METRICS_PATH = OUTPUT / "metrics.json"
AGENT_DIR = OUTPUT / "agent"

ENCODING = tiktoken.get_encoding("o200k_base")

PAIRS = [
    ("pid_table", "pid_table_raw", "pid_table_helper"),
    ("process_pid", "process_pid_raw", "process_pid_helper"),
    ("process_status", "process_status_raw", "process_status_helper"),
    ("parent_pid", "parent_pid_raw", "parent_pid_helper"),
    ("tasks", "tasks_raw", "tasks_helper"),
    ("thread_name", "name_raw", "name_helper"),
    ("is_exited", "is_exited_raw", "is_exited_helper"),
    ("timer_slack", "timer_slack_raw", "timer_slack_helper"),
    ("file_slots", "files_raw", "files_helper"),
    ("elapsed", "elapsed_raw", "elapsed_helper"),
]

COMMANDS = [
    ("ast-ps", "ast_ps"),
    ("ast-threads", "ast_threads"),
    ("ast-pstree", "ast_pstree"),
    ("ast-fds 1", "ast_fds"),
    ("ast-uptime", "ast_uptime"),
    ("info threads", "info_threads"),
]

NOISE_PATTERNS = {
    "UnsafeCell": r"UnsafeCell",
    "PhantomData": r"PhantomData",
    "__0": r"__0",
    "Atomic": r"Atomic",
    "Mutex": r"Mutex",
    "Arc": r"\bArc\b",
    "Weak": r"\bWeak\b",
    "BTreeMap": r"BTreeMap",
    "Vec": r"\bVec\b",
    "Option": r"\bOption\b",
    "hex addresses": r"0x[0-9a-fA-F]+",
    "type separators": r"::",
}

TASKS = [
    {
        "id": "process_summary",
        "question": (
            "Report the PID, PPID, state, thread count, and name of PID 1. "
            "Use only the GDB output below."
        ),
        "raw_cases": [
            "pid_table_raw",
            "process_pid_raw",
            "process_status_raw",
            "parent_pid_raw",
            "tasks_raw",
            "name_raw",
        ],
        "helper_cases": ["ast_ps"],
    },
    {
        "id": "thread_list",
        "question": (
            "List every live thread with its TID, owning PID, and name. "
            "Use only the GDB output below."
        ),
        "raw_cases": ["pid_table_raw", "tasks_raw", "name_raw"],
        "helper_cases": ["ast_threads"],
    },
    {
        "id": "file_descriptors",
        "question": (
            "List the file descriptors of PID 1 with their flags and object types. "
            "Use only the GDB output below."
        ),
        "raw_cases": ["files_raw"],
        "helper_cases": ["ast_fds"],
    },
    {
        "id": "uptime",
        "question": (
            "Report the kernel uptime in jiffies, the timer frequency, and the "
            "formatted hours, minutes, seconds, and milliseconds. "
            "Use only the GDB output below."
        ),
        "raw_cases": ["elapsed_raw"],
        "helper_cases": ["ast_uptime"],
    },
    {
        "id": "thread_details",
        "question": (
            "For TID 1, report the thread name, whether the thread has exited, "
            "and the timer slack in nanoseconds. Use only the GDB output below."
        ),
        "raw_cases": ["name_raw", "is_exited_raw", "timer_slack_raw"],
        "helper_cases": ["name_helper", "is_exited_helper", "timer_slack_helper"],
    },
    {
        "id": "process_tree",
        "question": (
            "Show the process tree with each PID, its parent PID, and its state. "
            "Use only the GDB output below."
        ),
        "raw_cases": [
            "pid_table_raw",
            "process_pid_raw",
            "process_status_raw",
            "parent_pid_raw",
        ],
        "helper_cases": ["ast_pstree"],
    },
]


def parse_blocks(text):
    blocks = {}
    pattern = re.compile(
        r"@@@BEGIN (?P<name>\S+)\n(?P<body>.*?)@@@END (?P=name)\n",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        blocks[match.group("name")] = match.group("body")
    return blocks


def token_count(text):
    return len(ENCODING.encode(text))


def noise_counts(text):
    return {
        name: len(re.findall(pattern, text))
        for name, pattern in NOISE_PATTERNS.items()
    }


def angle_depth(text):
    depth = 0
    maximum = 0
    for char in text:
        if char == "<":
            depth += 1
            maximum = max(maximum, depth)
        elif char == ">" and depth > 0:
            depth -= 1
    return maximum


def text_metrics(text):
    lines = text.splitlines()
    return {
        "tokens": token_count(text),
        "characters": len(text),
        "lines": len(lines),
        "non_empty_lines": sum(1 for line in lines if line.strip()),
        "max_line_length": max((len(line) for line in lines), default=0),
        "max_angle_depth": angle_depth(text),
        "noise": noise_counts(text),
    }


def reduction(raw, helper):
    if raw == 0:
        return 0.0
    return 100.0 * (raw - helper) / raw


def build_pair_rows(blocks):
    rows = []
    for label, raw_name, helper_name in PAIRS:
        raw_text = blocks.get(raw_name, "")
        helper_text = blocks.get(helper_name, "")
        raw_metrics = text_metrics(raw_text)
        helper_metrics = text_metrics(helper_text)
        rows.append(
            {
                "case": label,
                "raw": raw_metrics,
                "helper": helper_metrics,
                "token_reduction_percent": round(
                    reduction(raw_metrics["tokens"], helper_metrics["tokens"]), 1
                ),
                "character_reduction_percent": round(
                    reduction(raw_metrics["characters"], helper_metrics["characters"]), 1
                ),
                "line_reduction_percent": round(
                    reduction(raw_metrics["lines"], helper_metrics["lines"]), 1
                ),
            }
        )
    return rows


def build_command_rows(blocks):
    rows = []
    for label, block_name in COMMANDS:
        metrics = text_metrics(blocks.get(block_name, ""))
        rows.append({"command": label, "metrics": metrics})
    return rows


def build_task_contexts(blocks):
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    contexts = []
    for task in TASKS:
        entry = {"id": task["id"], "question": task["question"]}
        for condition, case_key in (
            ("raw", "raw_cases"),
            ("helper", "helper_cases"),
        ):
            text = "\n".join(
                f"@ {name}\n{blocks.get(name, '')}" for name in task[case_key]
            )
            path = AGENT_DIR / f"{task['id']}_{condition}.txt"
            path.write_text(text)
            entry[condition] = {
                "cases": task[case_key],
                "tokens": token_count(text),
                "characters": len(text),
                "lines": len(text.splitlines()),
                "file": str(path.relative_to(ROOT)),
            }
        entry["token_reduction_percent"] = round(
            reduction(entry["raw"]["tokens"], entry["helper"]["tokens"]), 1
        )
        contexts.append(entry)
    return contexts


def write_report(pair_rows, command_rows, task_contexts):
    lines = []
    lines.append("# GDB helper symbol simplification metrics")
    lines.append("")
    lines.append("Raw output uses rust-gdb with the Asterinas pretty-printers disabled.")
    lines.append("Helper output uses the same expressions with the printers enabled.")
    lines.append("Token counts use the `o200k_base` tokenizer as a proxy.")
    lines.append("")
    lines.append("## Expression-level comparison")
    lines.append("")
    lines.append(
        "| Case | Raw tokens | Helper tokens | Token reduction | "
        "Raw lines | Helper lines | Line reduction |"
    )
    lines.append("|------|-----------:|--------------:|----------------:|"
                 "----------:|-------------:|---------------:|")
    for row in pair_rows:
        lines.append(
            f"| {row['case']} | {row['raw']['tokens']} | {row['helper']['tokens']} | "
            f"{row['token_reduction_percent']}% | {row['raw']['lines']} | "
            f"{row['helper']['lines']} | {row['line_reduction_percent']}% |"
        )
    lines.append("")
    lines.append("## Wrapper noise removal")
    lines.append("")
    lines.append("| Case | UnsafeCell | PhantomData | __0 | Atomic | Mutex | Arc |")
    lines.append("|------|-----------:|------------:|----:|-------:|------:|----:|")
    for row in pair_rows:
        raw = row["raw"]["noise"]
        helper = row["helper"]["noise"]
        lines.append(
            f"| {row['case']} | {raw['UnsafeCell']} -> {helper['UnsafeCell']} | "
            f"{raw['PhantomData']} -> {helper['PhantomData']} | "
            f"{raw['__0']} -> {helper['__0']} | "
            f"{raw['Atomic']} -> {helper['Atomic']} | "
            f"{raw['Mutex']} -> {helper['Mutex']} | "
            f"{raw['Arc']} -> {helper['Arc']} |"
        )
    lines.append("")
    lines.append("## Helper command output")
    lines.append("")
    lines.append("| Command | Tokens | Characters | Lines | Max angle depth |")
    lines.append("|---------|-------:|-----------:|------:|----------------:|")
    for row in command_rows:
        metrics = row["metrics"]
        lines.append(
            f"| `{row['command']}` | {metrics['tokens']} | "
            f"{metrics['characters']} | {metrics['lines']} | "
            f"{metrics['max_angle_depth']} |"
        )
    lines.append("")
    lines.append("## Agent task contexts")
    lines.append("")
    lines.append("| Task | Raw tokens | Helper tokens | Reduction |")
    lines.append("|------|-----------:|--------------:|----------:|")
    for task in task_contexts:
        lines.append(
            f"| {task['id']} | {task['raw']['tokens']} | "
            f"{task['helper']['tokens']} | "
            f"{task['token_reduction_percent']}% |"
        )
    lines.append("")
    lines.append("## Example output")
    lines.append("")
    lines.append("### Raw `p (*$ast_thread(1)).name`")
    lines.append("")
    lines.append("```text")
    lines.append(blocks_example("name_raw"))
    lines.append("```")
    lines.append("")
    lines.append("### Helper `p (*$ast_thread(1)).name`")
    lines.append("")
    lines.append("```text")
    lines.append(blocks_example("name_helper"))
    lines.append("```")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines))


def blocks_example(name):
    text = BLOCKS.get(name, "")
    lines = text.splitlines()
    if len(lines) > 40:
        lines = lines[:40] + ["..."]
    return "\n".join(lines)


def main():
    text = LOG_PATH.read_text()
    global BLOCKS
    BLOCKS = parse_blocks(text)
    if not BLOCKS:
        raise SystemExit(f"no GDB blocks found in {LOG_PATH}")

    pair_rows = build_pair_rows(BLOCKS)
    command_rows = build_command_rows(BLOCKS)
    task_contexts = build_task_contexts(BLOCKS)
    write_report(pair_rows, command_rows, task_contexts)

    metrics = {
        "blocks": sorted(BLOCKS),
        "pairs": pair_rows,
        "commands": command_rows,
        "agent_tasks": task_contexts,
    }
    METRICS_PATH.write_text(json.dumps(metrics, indent=2))
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {METRICS_PATH}")


BLOCKS = {}
main()
