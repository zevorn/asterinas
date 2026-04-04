"""
Process and thread inspection commands for Asterinas.

Commands:
    ast-ps      — List all processes
    ast-task    — Show detailed info for a single process
"""

import gdb

from helper.symbols import lookup_type, read_global
from helper.utils import (
    IntrospectionError,
    read_btree_map,
    read_scalar,
    read_vec,
    unwrap_arc,
    unwrap_mutex,
    unwrap_option,
    unwrap_weak,
)
from helper.constants import PID_TABLE_SYMBOL


# --- PID table navigation ---
#
# Kernel layout (pid_table.rs):
#   PID_TABLE: Mutex<PidTable>
#   PidTable { entries: BTreeMap<u32, Arc<Mutex<PidEntry>>>, ... }
#   PidEntry { thread: Weak<Thread>, process: Weak<Process>,
#              process_group: Weak<ProcessGroup>, session: Weak<Session> }


def _get_pid_table_entries():
    """Return the BTreeMap<u32, Arc<Mutex<PidEntry>>> from the global PID table."""
    table_mutex = read_global(PID_TABLE_SYMBOL)
    if table_mutex is None:
        raise IntrospectionError(f"Cannot resolve symbol {PID_TABLE_SYMBOL}")
    _, pid_table = unwrap_mutex(table_mutex)
    return pid_table['entries']


def _unwrap_pid_entry(arc_mutex_val):
    """Unwrap Arc<Mutex<PidEntry>> -> PidEntry."""
    inner_mutex = unwrap_arc(arc_mutex_val)
    _, entry = unwrap_mutex(inner_mutex)
    return entry


def _iter_processes():
    """Iterate live processes from the unified PID table.

    Yields (pid: int, process: gdb.Value) pairs.
    """
    entries = _get_pid_table_entries()
    for key, arc_mutex in read_btree_map(entries):
        try:
            entry = _unwrap_pid_entry(arc_mutex)
            process = unwrap_weak(entry['process'])
            if process is not None:
                yield (int(key), process)
        except (gdb.error, IntrospectionError):
            continue


def _iter_threads():
    """Iterate live threads from the unified PID table.

    Yields (tid: int, thread: gdb.Value) pairs.
    """
    entries = _get_pid_table_entries()
    for key, arc_mutex in read_btree_map(entries):
        try:
            entry = _unwrap_pid_entry(arc_mutex)
            thread = unwrap_weak(entry['thread'])
            if thread is not None:
                yield (int(key), thread)
        except (gdb.error, IntrospectionError):
            continue


def _find_process(target_pid):
    """Find a process by PID. Returns gdb.Value or None."""
    for pid, process in _iter_processes():
        if pid == target_pid:
            return process
    return None


# --- Per-process field accessors ---


def _read_pid(process_val):
    return read_scalar(process_val['pid'])


def _read_ppid(process_val):
    """Read parent PID from ParentProcess."""
    try:
        parent = process_val['parent']
        return read_scalar(parent['pid'])
    except (gdb.error, IntrospectionError):
        return 0


def _read_status(process_val):
    """Read process status (Running/Zombie/Stopped)."""
    try:
        status = process_val['status']
        is_zombie = bool(read_scalar(status['is_zombie']))
        if is_zombie:
            return "Zombie"

        try:
            stop_status = status['stop_status']
            is_stopped = bool(read_scalar(stop_status['is_stopped']))
            if is_stopped:
                return "Stopped"
        except (gdb.error, IntrospectionError):
            pass

        return "Running"
    except (gdb.error, IntrospectionError):
        return "?"


def _read_thread_count(process_val):
    """Read number of threads from TaskSet."""
    try:
        _, task_set = unwrap_mutex(process_val['tasks'])
        tasks_vec = task_set['tasks']
        return int(tasks_vec['len'])
    except (gdb.error, IntrospectionError):
        return 0


def _read_nice(process_val):
    """Read nice value."""
    try:
        return read_scalar(process_val['nice'])
    except (gdb.error, IntrospectionError):
        return 0


def _unwrap_boxed_value(box_val):
    """Dereference a Rust `Box<T>` or `Box<dyn Trait>` value."""
    return box_val['pointer'].dereference()


def _cast_boxed_dyn_data(box_val, type_name):
    """Cast the data pointer of a boxed trait object to a concrete type."""
    concrete_type = lookup_type(type_name)
    if concrete_type is None:
        raise IntrospectionError(f"Cannot resolve type {type_name}")
    data_addr = int(box_val['pointer'])
    return gdb.Value(data_addr).cast(concrete_type.pointer()).dereference()


def _get_posix_thread_from_task(task_val):
    """
    Navigate the real object chain:
      Task.data (Box<dyn Any>) -> Arc<Thread>
      Thread.data (Box<dyn Any>) -> PosixThread

    Box<dyn Any> is a fat pointer: (data_ptr, vtable_ptr).
    We dereference data_ptr to get the concrete type.
    """
    try:
        thread_arc = _cast_boxed_dyn_data(
            task_val['data'],
            'alloc::sync::Arc<aster_kernel::thread::Thread, alloc::alloc::Global>',
        )
        thread = unwrap_arc(thread_arc)
    except (gdb.error, IntrospectionError):
        return None

    try:
        return _cast_boxed_dyn_data(
            thread['data'],
            'aster_kernel::process::posix_thread::PosixThread',
        )
    except gdb.error:
        return None


def _get_posix_thread_from_thread(thread_val):
    """Navigate: Thread.data (Box<dyn Any>) -> PosixThread."""
    try:
        return _cast_boxed_dyn_data(
            thread_val['data'],
            'aster_kernel::process::posix_thread::PosixThread',
        )
    except (gdb.error, IntrospectionError):
        return None


def _read_thread_name_from_posix(posix_thread):
    """
    Read thread name from PosixThread.name (Mutex<ThreadName>).
    ThreadName is ([u8; 16]) — a tuple struct wrapping a byte array.
    """
    if posix_thread is None:
        return "<unknown>"
    try:
        name_mutex = posix_thread['name']
        _, name_val = unwrap_mutex(name_mutex)
        try:
            buf = name_val['__0']
        except gdb.error:
            buf = name_val['0']

        name_bytes = bytearray()
        for index in range(16):
            byte = read_scalar(buf[index])
            if byte == 0:
                break
            name_bytes.append(byte)
        return name_bytes.decode('utf-8', errors='replace') if name_bytes else "<unnamed>"
    except (gdb.error, IntrospectionError):
        return "<unknown>"


def _read_tid_from_posix(posix_thread):
    """Read TID from PosixThread.tid (AtomicU32)."""
    if posix_thread is None:
        return 0
    try:
        return read_scalar(posix_thread['tid'])
    except (gdb.error, IntrospectionError):
        return 0


def _read_thread_name(task_val):
    """Read thread name from a Task via Task -> Thread -> PosixThread chain."""
    pt = _get_posix_thread_from_task(task_val)
    return _read_thread_name_from_posix(pt)


def _read_process_name(process_val):
    """Read the process name from the first thread's name."""
    try:
        _, task_set = unwrap_mutex(process_val['tasks'])
        tasks = read_vec(task_set['tasks'])
        if tasks:
            first_task = unwrap_arc(tasks[0])
            return _read_thread_name(first_task)
    except (gdb.error, IntrospectionError):
        pass
    return "<unknown>"


def _read_sig_mask(posix_thread):
    """Read the blocked signal mask as a hex bitset."""
    if posix_thread is None:
        return "?"
    try:
        return f"0x{read_scalar(posix_thread['sig_mask']):016x}"
    except (gdb.error, IntrospectionError):
        return "?"


def _read_credentials(posix_thread):
    """Read real/effective UID/GID from `Credentials`."""
    if posix_thread is None:
        return None
    try:
        credentials_arc = posix_thread['credentials']['__0']
    except (gdb.error, KeyError):
        try:
            credentials_arc = posix_thread['credentials']['0']
        except (gdb.error, KeyError):
            return None

    try:
        credentials_inner = unwrap_arc(credentials_arc)
        return {
            'ruid': read_scalar(credentials_inner['ruid']),
            'euid': read_scalar(credentials_inner['euid']),
            'rgid': read_scalar(credentials_inner['rgid']),
            'egid': read_scalar(credentials_inner['egid']),
        }
    except (gdb.error, IntrospectionError, KeyError):
        return None


def _read_process_group_info(proc):
    """Read process-group and session identifiers."""
    try:
        _, group_opt = unwrap_mutex(proc['process_group'])
        is_some, group_arc = unwrap_option(group_opt)
        if not is_some or group_arc is None:
            return None
        group = unwrap_arc(group_arc)
        session = unwrap_weak(group['session'])
        return {
            'pgid': read_scalar(group['pgid']),
            'sid': read_scalar(session['sid']) if session is not None else None,
        }
    except (gdb.error, IntrospectionError):
        return None


class AstPs(gdb.Command):
    """List all processes.

Usage: ast-ps [PID]
    Without arguments, lists all processes.
    With PID, shows only the matching process.
    """

    def __init__(self):
        super().__init__("ast-ps", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        filter_pid = None
        if arg.strip():
            try:
                filter_pid = int(arg.strip())
            except ValueError:
                gdb.write(f"Error: invalid PID '{arg.strip()}'\n")
                return

        try:
            processes = list(_iter_processes())
        except (gdb.error, IntrospectionError) as e:
            gdb.write(f"Error: cannot read process table: {e}\n")
            return

        header = f"{'PID':>6}  {'PPID':>6}  {'STATE':<10}  {'#THR':>4}  {'NICE':>4}  NAME\n"
        gdb.write(header)

        found = False
        for pid, proc in processes:
            if filter_pid is not None and pid != filter_pid:
                continue

            found = True
            ppid = _read_ppid(proc)
            state = _read_status(proc)
            nthreads = _read_thread_count(proc)
            nice = _read_nice(proc)
            name = _read_process_name(proc)

            gdb.write(f"{pid:>6}  {ppid:>6}  {state:<10}  {nthreads:>4}  {nice:>4}  {name}\n")

        if filter_pid is not None and not found:
            gdb.write(f"No process with PID {filter_pid}\n")


class AstTask(gdb.Command):
    """Show detailed information for a process.

Usage: ast-task <PID>
    """

    def __init__(self):
        super().__init__("ast-task", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        if not arg.strip():
            gdb.write("Usage: ast-task <PID>\n")
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

        pid = _read_pid(proc)
        ppid = _read_ppid(proc)
        state = _read_status(proc)
        nice = _read_nice(proc)
        nthreads = _read_thread_count(proc)

        gdb.write(f"Process {pid}:\n")
        gdb.write(f"  PPID:    {ppid}\n")
        gdb.write(f"  State:   {state}\n")
        gdb.write(f"  Nice:    {nice}\n")
        gdb.write(f"  Threads: {nthreads}\n")

        # Process group
        group_info = _read_process_group_info(proc)
        if group_info is not None:
            gdb.write(f"  PGID:    {group_info['pgid']}\n")
            if group_info['sid'] is not None:
                gdb.write(f"  SID:     {group_info['sid']}\n")

        # Credentials
        tasks = []
        try:
            _, task_set = unwrap_mutex(proc['tasks'])
            tasks = read_vec(task_set['tasks'])
            main_posix_thread = _get_posix_thread_from_task(unwrap_arc(tasks[0])) if tasks else None
        except (gdb.error, IntrospectionError):
            main_posix_thread = None

        credentials = _read_credentials(main_posix_thread)
        if credentials is not None:
            gdb.write(
                f"  UIDs:    r={credentials['ruid']} e={credentials['euid']}\n"
            )
            gdb.write(
                f"  GIDs:    r={credentials['rgid']} e={credentials['egid']}\n"
            )

        # Thread list
        gdb.write(f"\n  {'TID':>6}  {'SIGMASK':<18}  NAME\n")
        try:
            for task_arc in tasks:
                task = unwrap_arc(task_arc)
                posix_thread = _get_posix_thread_from_task(task)
                tid = _read_tid_from_posix(posix_thread)
                name = _read_thread_name_from_posix(posix_thread)
                sig_mask = _read_sig_mask(posix_thread)
                gdb.write(f"  {tid:>6}  {sig_mask:<18}  {name}\n")
        except (gdb.error, IntrospectionError) as e:
            gdb.write(f"  (error reading threads: {e})\n")


AstPs()
AstTask()
