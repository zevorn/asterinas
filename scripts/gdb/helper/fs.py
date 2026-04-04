"""
File descriptor inspection commands.

Commands:
    ast-fds     — List open file descriptors for a process
"""

import gdb

from helper.utils import (
    IntrospectionError,
    read_scalar,
    read_slot_vec,
    unwrap_arc,
    unwrap_mutex,
    unwrap_option,
    read_vec,
)
from helper.proc import (
    _find_process,
    _get_posix_thread_from_task,
)


def _get_file_table(process_val):
    """
    Navigate: Process -> tasks -> first Task -> PosixThread -> file_table.

    PosixThread.file_table: Mutex<Option<RoArc<FileTable>>>
    FileTable.table: SlotVec<FileTableEntry>
    """
    try:
        _, task_set = unwrap_mutex(process_val['tasks'])
        tasks = read_vec(task_set['tasks'])
        if not tasks:
            return None

        posix_thread = _get_posix_thread_from_task(unwrap_arc(tasks[0]))
        if posix_thread is None:
            return None

        _, ft_option = unwrap_mutex(posix_thread['file_table'])
        is_some, roarc = unwrap_option(ft_option)
        if not is_some:
            return None

        # RoArc<T> is a newtype: RoArc(Arc<Inner<T>>)
        # Inner<T> { data: RwLock<T>, num_rw: AtomicUsize }
        try:
            inner_arc = roarc['__0']
        except gdb.error:
            inner_arc = roarc['0']
        inner = unwrap_arc(inner_arc)
        # RwLock<T> -> read inner value
        try:
            return inner['data']['val']['value']
        except gdb.error:
            return inner['data']['value']
    except (gdb.error, IntrospectionError):
        return None


def _read_fd_type(entry_val):
    """Try to identify the file type from a FileTableEntry."""
    try:
        # entry.file is Arc<dyn FileLike> — a fat pointer (data_ptr, vtable_ptr)
        file_field = entry_val['file']

        # Read vtable pointer to identify the concrete type
        vtable_ptr = file_field['vtable']['pointer']
        vtable_addr = int(vtable_ptr)

        # Use GDB's info symbol to resolve the vtable address
        output = gdb.execute(f"info symbol {vtable_addr}", to_string=True)
        # Typical output: "vtable for Foo::Bar + 0 in section .rodata"
        if "No symbol" not in output:
            name = output.strip()
            for marker in ("InodeFile", "Socket", "EventFile", "EpollFile",
                           "TimerFile", "PipeReader", "PipeWriter", "Pipe",
                           "UnixStream", "TcpSocket", "UdpSocket", "DevPts"):
                if marker in name:
                    return marker
            if "vtable" in name:
                parts = name.split("::")
                for part in reversed(parts):
                    cleaned = part.split("+")[0].strip().rstrip(">").rstrip(",")
                    if cleaned and len(cleaned) > 2 and cleaned[0].isupper():
                        return cleaned
            return name[:40]
        return "?"
    except (gdb.error, IntrospectionError):
        return "?"


class AstFds(gdb.Command):
    """List open file descriptors for a process.

Usage: ast-fds <PID>
    """

    def __init__(self):
        super().__init__("ast-fds", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        if not arg.strip():
            gdb.write("Usage: ast-fds <PID>\n")
            return

        try:
            target_pid = int(arg.strip())
        except ValueError:
            gdb.write(f"Error: invalid PID '{arg.strip()}'\n")
            return

        try:
            proc = _find_process(target_pid)
        except (gdb.error, IntrospectionError) as e:
            gdb.write(f"Error: cannot read process table: {e}\n")
            return

        if proc is None:
            gdb.write(f"No process with PID {target_pid}\n")
            return

        file_table = _get_file_table(proc)
        if file_table is None:
            gdb.write(f"Cannot read file table for PID {target_pid}\n")
            return

        gdb.write(f"{'FD':>4}  {'FLAGS':>5}  TYPE\n")

        try:
            slot_vec = file_table['table']
            for fd, entry in read_slot_vec(slot_vec):
                flags = read_scalar(entry['flags'])
                fd_type = _read_fd_type(entry)
                flag_str = ""
                if flags & 1:
                    flag_str += "O_CLOEXEC"
                gdb.write(f"{fd:>4}  {flags:>5}  {fd_type}  {flag_str}\n")
        except (gdb.error, IntrospectionError) as e:
            gdb.write(f"Error reading file entries: {e}\n")


AstFds()
