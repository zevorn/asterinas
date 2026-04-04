"""
Asterinas GDB Debug Helper Scripts — Entry Point

This file is sourced by GDB (via .gdbinit or manually) to register
all ast-* commands for debugging the Asterinas kernel.

Usage:
    (gdb) source scripts/gdb/asterinas-gdb.py
    (gdb) ast-version
"""

import sys
import os

# Add scripts/gdb/ to Python path so `helper` package is importable
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import gdb

def _try_load_rust_pretty_printers():
    """Auto-detect and load Rust GDB pretty-printers from the toolchain.

    Replicates what ``rust-gdb`` does: locate the sysroot, add the
    pretty-printer directory to sys.path, and register the printers.
    If the user is already running ``rust-gdb``, this is a safe no-op.
    """
    try:
        import gdb_lookup  # noqa: F401 — already loaded (e.g. via rust-gdb)
        return True
    except ImportError:
        pass

    import subprocess
    try:
        sysroot = subprocess.check_output(
            ["rustc", "--print", "sysroot"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

    etc_dir = os.path.join(sysroot, "lib", "rustlib", "etc")
    if not os.path.isdir(etc_dir):
        return False

    if etc_dir not in sys.path:
        sys.path.insert(0, etc_dir)

    try:
        import gdb_lookup
        gdb_lookup.register_printers(gdb.current_objfile())
        return True
    except Exception:
        return False


class AstPrefix(gdb.Command):
    """Asterinas GDB helper commands. Type 'ast-' and press TAB for completion."""

    def __init__(self):
        super().__init__("ast-", gdb.COMMAND_USER, gdb.COMPLETE_COMMAND, True)


class AstVersion(gdb.Command):
    """Print Asterinas kernel version."""

    def __init__(self):
        super().__init__("ast-version", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        # Kernel version from VERSION file
        version_file = os.path.join(_script_dir, "..", "..", "VERSION")
        kernel_version = None
        try:
            with open(version_file) as f:
                kernel_version = f.read().strip()
        except OSError:
            pass

        if kernel_version:
            gdb.write(f"Asterinas {kernel_version}\n")
        else:
            gdb.write("Asterinas (version unknown)\n")


def _require_connection(func):
    """Decorator: check that GDB is connected to a target before running."""
    def wrapper(self, arg, from_tty):
        try:
            inf = gdb.selected_inferior()
            if inf is None or not inf.is_valid():
                raise gdb.error("not connected")
            if inf.pid == 0 and len(inf.threads()) == 0:
                raise gdb.error("not connected")
        except gdb.error:
            gdb.write("Error: not connected to a target. "
                       "Use 'target remote' first.\n")
            return
        return func(self, arg, from_tty)
    return wrapper


def _register_commands():
    """Register all ast-* commands."""
    AstPrefix()
    AstVersion()

    loaded_modules = []
    warnings = []
    for module_name in ("proc", "mem", "fs", "cpu", "monitor", "pretty_printers"):
        try:
            __import__(f"helper.{module_name}")
            loaded_modules.append(module_name)
        except ModuleNotFoundError as error:
            if error.name == f"helper.{module_name}":
                continue
            warnings.append(f"{module_name}: {error}")
        except ImportError as error:
            warnings.append(f"{module_name}: {error}")
        except Exception as error:
            warnings.append(f"{module_name}: {error}")
    return loaded_modules, warnings


_rust_pp = _try_load_rust_pretty_printers()
loaded_modules, warnings = _register_commands()
if warnings:
    for warning in warnings:
        gdb.write(f"Warning: failed to load helper module {warning}\n")

_pp_tag = "rust-pp: active" if _rust_pp else "rust-pp: off, use rust-gdb for better support"
gdb.write(
    f"Asterinas GDB helpers loaded "
    f"({', '.join(loaded_modules) if loaded_modules else 'core only'}). "
    f"[{_pp_tag}]. "
    "Type 'ast-' and TAB for commands.\n"
)
