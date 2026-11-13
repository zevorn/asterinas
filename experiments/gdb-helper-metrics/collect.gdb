# SPDX-License-Identifier: MPL-2.0
#
# Collect raw and helper-mediated GDB output from a running Asterinas kernel.

source scripts/gdb/asterinas-gdb.py

set pagination off
set confirm off
set print elements 50
set print repeats 3
set print pretty off
set print max-depth 10
set max-value-size unlimited

target remote .osdk-gdb-socket

hbreak __ostd_main
continue
delete

break aster_core::syscall::handle_syscall
continue
delete

python
import sys
sys.path.insert(0, "experiments/gdb-helper-metrics")
import collect
collect.run()
end

quit
