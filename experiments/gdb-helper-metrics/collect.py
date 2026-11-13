# SPDX-License-Identifier: MPL-2.0

import gdb

CASES = [
    ("pid_table_raw", "p aster_core::process::pid_table::PID_TABLE", True),
    ("pid_table_helper", "p aster_core::process::pid_table::PID_TABLE", False),
    ("process_pid_raw", "p $ast_process(1)->pid", True),
    ("process_pid_helper", "p $ast_process(1)->pid", False),
    ("process_status_raw", "p (*$ast_process(1)).status", True),
    ("process_status_helper", "p (*$ast_process(1)).status", False),
    ("parent_pid_raw", "p (*$ast_process(1)).parent.pid", True),
    ("parent_pid_helper", "p (*$ast_process(1)).parent.pid", False),
    ("tasks_raw", "p (*$ast_process(1)).tasks", True),
    ("tasks_helper", "p (*$ast_process(1)).tasks", False),
    ("name_raw", "p (*$ast_thread(1)).name", True),
    ("name_helper", "p (*$ast_thread(1)).name", False),
    ("is_exited_raw", "p (*$ast_thread(1)).is_exited", True),
    ("is_exited_helper", "p (*$ast_thread(1)).is_exited", False),
    ("timer_slack_raw", "p (*$ast_thread(1)).timer_slack_ns", True),
    ("timer_slack_helper", "p (*$ast_thread(1)).timer_slack_ns", False),
    ("files_raw", "p (*$ast_file_table(1)).table", True),
    ("files_helper", "p (*$ast_file_table(1)).table", False),
    ("elapsed_raw", "p ostd::timer::jiffies::ELAPSED", True),
    ("elapsed_helper", "p ostd::timer::jiffies::ELAPSED", False),
    ("ast_ps", "ast-ps", False),
    ("ast_threads", "ast-threads", False),
    ("ast_pstree", "ast-pstree", False),
    ("ast_fds", "ast-fds 1", False),
    ("ast_uptime", "ast-uptime", False),
    ("info_threads", "info threads", False),
]


def _set_asterinas_printers(enabled):
    command = (
        "enable pretty-printer global asterinas"
        if enabled
        else "disable pretty-printer global asterinas"
    )
    try:
        gdb.execute(command, to_string=True)
    except gdb.error:
        pass


def _emit(name, command):
    gdb.write(f"\n[collect] {name}\n")
    gdb.write(f"\n@@@BEGIN {name}\n")
    try:
        output = gdb.execute(command, to_string=True)
    except gdb.error as error:
        output = f"ERROR: {error}\n"
    gdb.write(output)
    if output and not output.endswith("\n"):
        gdb.write("\n")
    gdb.write(f"@@@END {name}\n")


def run():
    for name, command, raw in CASES:
        _set_asterinas_printers(not raw)
        _emit(name, command)
    _set_asterinas_printers(True)
