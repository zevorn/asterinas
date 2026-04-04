"""
Thread listing and process tree commands.

Commands:
    ast-threads  — List all threads from the global PID table
    ast-pstree   — Show process parent-child tree
"""

import gdb

from helper.utils import (
    IntrospectionError,
    read_scalar,
    unwrap_weak,
)
from helper.proc import (
    _iter_processes,
    _iter_threads,
    _read_pid,
    _read_ppid,
    _read_status,
    _read_process_name,
    _read_tid_from_posix,
    _read_thread_name_from_posix,
    _get_posix_thread_from_thread,
)


class AstThreads(gdb.Command):
    """List all threads from the global PID table.

Usage: ast-threads
    """

    def __init__(self):
        super().__init__("ast-threads", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        try:
            threads = list(_iter_threads())
        except (gdb.error, IntrospectionError) as e:
            gdb.write(f"Error: cannot read PID table: {e}\n")
            return

        gdb.write(f"{'TID':>6}  {'PID':>6}  NAME\n")

        for tid_key, thread in threads:
            posix_thread = _get_posix_thread_from_thread(thread)
            tid = _read_tid_from_posix(posix_thread) if posix_thread else tid_key
            name = _read_thread_name_from_posix(posix_thread)

            # Get the owning process PID
            pid = 0
            if posix_thread is not None:
                try:
                    proc = unwrap_weak(posix_thread['process'])
                    if proc is not None:
                        pid = read_scalar(proc['pid'])
                except (gdb.error, IntrospectionError):
                    pass

            gdb.write(f"{tid:>6}  {pid:>6}  {name}\n")


class AstPstree(gdb.Command):
    """Show process tree (parent-child relationships).

Usage: ast-pstree
    """

    def __init__(self):
        super().__init__("ast-pstree", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        try:
            processes = list(_iter_processes())
        except (gdb.error, IntrospectionError) as e:
            gdb.write(f"Error: cannot read process table: {e}\n")
            return

        # Collect all processes
        proc_info = {}
        children_map = {}
        for pid, proc in processes:
            ppid = _read_ppid(proc)
            name = _read_process_name(proc)
            state = _read_status(proc)
            proc_info[pid] = (ppid, name, state)
            children_map.setdefault(ppid, []).append(pid)

        if not proc_info:
            gdb.write("No processes found\n")
            return

        # Find roots (PIDs whose PPID is not in the table)
        roots = [pid for pid, (ppid, _, _) in proc_info.items()
                 if ppid not in proc_info]
        if not roots:
            roots = sorted(proc_info.keys())[:1]

        def print_tree(pid, prefix=""):
            _, name, state = proc_info[pid]
            gdb.write(f"{prefix}{name}({pid}) [{state}]\n")

            kids = sorted(children_map.get(pid, []))
            for i, child in enumerate(kids):
                is_last = (i == len(kids) - 1)
                connector = "`-- " if is_last else "|-- "
                gdb.write(f"{prefix}{connector}")
                child_prefix = prefix + ("    " if is_last else "|   ")
                _, cname, cstate = proc_info[child]
                gdb.write(f"{cname}({child}) [{cstate}]\n")

                grandkids = sorted(children_map.get(child, []))
                for j, grandchild in enumerate(grandkids):
                    print_tree(grandchild, child_prefix)

        for root in sorted(roots):
            print_tree(root)


AstThreads()
AstPstree()
