"""
Constants for Asterinas GDB scripts.

All struct field access goes through DWARF debug info (gdb.Value['field']),
NOT hardcoded offsets. This file only stores symbolic names.
"""

# Crate name: kernel/Cargo.toml has name = "aster-kernel",
# Rust converts hyphens to underscores for symbol names.
_CRATE = "aster_kernel"
_OSTD = "ostd"

# Unified PID table: Mutex<PidTable>
# PidTable.entries: BTreeMap<u32, Arc<Mutex<PidEntry>>>
# PidEntry { thread: Weak<Thread>, process: Weak<Process>, ... }
PID_TABLE_SYMBOL = f"{_CRATE}::process::pid_table::PID_TABLE"

# Context switch counter
CONTEXT_SWITCH_COUNTER_SYMBOL = f"{_CRATE}::thread::stats::CONTEXT_SWITCH_COUNTER"

# Process creation counter
PROCESS_CREATION_COUNTER_SYMBOL = f"{_CRATE}::process::stats::PROCESS_CREATION_COUNTER"

# Elapsed jiffies since boot (AtomicU64, TIMER_FREQ=1000 Hz)
ELAPSED_JIFFIES_SYMBOL = f"{_OSTD}::timer::jiffies::ELAPSED"
TIMER_FREQ = 1000

# Per-CPU current task pointer
CURRENT_TASK_PTR_SYMBOL = f"{_OSTD}::task::processor::CURRENT_TASK_PTR"

# x86_64 linear mapping base: 0xffff_ffc0_0000_0000 << (ADDRESS_WIDTH - 39)
# For ADDRESS_WIDTH=48: shift=9, result = 0xffff_8000_0000_0000
LINEAR_MAPPING_BASE_VADDR = 0xFFFF_8000_0000_0000
