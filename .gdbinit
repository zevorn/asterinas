# >>> asterinas-gdb-auto-load >>>
# Asterinas GDB Debug Helper Scripts
python
import os
_gdbinit_dir = os.path.dirname(os.path.abspath(".gdbinit"))
_gdb_script = os.path.join(_gdbinit_dir, "scripts", "gdb", "asterinas-gdb.py")
if os.path.exists(_gdb_script):
    gdb.execute(f"source {_gdb_script}")
else:
    print(f"Warning: {_gdb_script} not found. Asterinas GDB helpers not loaded.")
end
# <<< asterinas-gdb-auto-load <<<
