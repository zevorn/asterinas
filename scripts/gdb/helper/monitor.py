"""
QEMU monitor commands for Guest physical memory and address translation.

Commands:
    ast-xp          — Read Guest Physical Address (GPA)
    ast-phys-dump   — Dump GPA region to host file
    ast-gpa2hva     — GPA to QEMU Host Virtual Address
    ast-gva2gpa     — GVA to GPA (current CPU page table)
    ast-mm          — Show page table mappings (current CPU)
    ast-phys-write  — Write bytes to GPA
    ast-monitor     — Generic QEMU monitor passthrough
"""

import gdb
import os
import re
import tempfile

from helper.constants import LINEAR_MAPPING_BASE_VADDR


def _qemu_monitor(cmd):
    """Execute a QEMU monitor command and return its output."""
    try:
        return gdb.execute(f"monitor {cmd}", to_string=True)
    except gdb.error as e:
        return f"Error: {e}"


def _quote_hmp_string(value):
    """Quote a string argument for QEMU HMP commands."""
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


class AstXp(gdb.Command):
    """Read Guest Physical Address via QEMU monitor.

Usage: ast-xp /FMT <GPA>
    FMT is a QEMU xp format string, e.g.: 16xb, 4xw, 8xg
    GPA is a Guest Physical Address in hex.

Examples:
    ast-xp /16xb 0x1000
    ast-xp /4xg 0x100000
    """

    def __init__(self):
        super().__init__("ast-xp", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        arg = arg.strip()
        if not arg:
            gdb.write("Usage: ast-xp /FMT <GPA>\n"
                       "  FMT: count + format + size\n"
                       "    format: x(hex), d(dec), u(unsigned), o(oct), c(char)\n"
                       "    size: b(8-bit), h(16-bit), w(32-bit), g(64-bit)\n"
                       "  Example: ast-xp /16xb 0x1000\n")
            return

        # Parse /FMT and address
        m = re.match(r'^/(\S+)\s+(0x[0-9a-fA-F]+|\d+)$', arg)
        if not m:
            gdb.write("Error: expected format: ast-xp /FMT <GPA>\n")
            return

        fmt = m.group(1)
        addr = m.group(2)
        output = _qemu_monitor(f"xp /{fmt} {addr}")
        gdb.write(output)


class AstPhysDump(gdb.Command):
    """Dump Guest physical memory region to a host file.

Usage: ast-phys-dump <GPA> <size> <file>
    GPA is the start Guest Physical Address.
    size is the number of bytes to dump.
    file is the host file path.

Example:
    ast-phys-dump 0x1000 4096 /tmp/mem.bin
    """

    def __init__(self):
        super().__init__("ast-phys-dump", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        parts = arg.strip().split()
        if len(parts) != 3:
            gdb.write("Usage: ast-phys-dump <GPA> <size> <file>\n")
            return

        addr, size_str, filepath = parts
        try:
            size = int(size_str, 0)
        except ValueError:
            gdb.write(f"Error: invalid size '{size_str}'\n")
            return

        if size <= 0:
            gdb.write("Error: size must be positive\n")
            return

        output = _qemu_monitor(f"pmemsave {addr} {size} {_quote_hmp_string(filepath)}")
        if output.strip():
            gdb.write(output)
        else:
            gdb.write(f"Saved {size} bytes from GPA {addr} to {filepath}\n")


class AstGpa2Hva(gdb.Command):
    """Translate Guest Physical Address to QEMU Host Virtual Address.

Usage: ast-gpa2hva <GPA>

Example:
    ast-gpa2hva 0x1000
    """

    def __init__(self):
        super().__init__("ast-gpa2hva", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        addr = arg.strip()
        if not addr:
            gdb.write("Usage: ast-gpa2hva <GPA>\n")
            return

        output = _qemu_monitor(f"gpa2hva {addr}")
        gdb.write(output)


class AstGva2Gpa(gdb.Command):
    """Translate Guest Virtual Address to Guest Physical Address.

Uses the current CPU's page table.

Usage: ast-gva2gpa <GVA>

Example:
    ast-gva2gpa 0xffffffff80000000
    """

    def __init__(self):
        super().__init__("ast-gva2gpa", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        addr = arg.strip()
        if not addr:
            gdb.write("Usage: ast-gva2gpa <GVA>\n")
            return

        output = _qemu_monitor(f"gva2gpa {addr}")
        gdb.write(output)


class AstMm(gdb.Command):
    """Show current address space page table mappings.

Displays the virtual memory map of the active CPU, including
address ranges and permissions (via QEMU 'info mem').

Usage: ast-mm
    """

    def __init__(self):
        super().__init__("ast-mm", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        output = _qemu_monitor("info mem")
        gdb.write(output)


class AstPhysWrite(gdb.Command):
    """Write bytes to Guest Physical Address.

Usage:
    ast-phys-write <GPA> <hex-bytes>
    ast-phys-write <GPA> <file> --from-file

QEMU has no native monitor command to write physical memory.
This command uses an indirect strategy:
  1. Translate GPA to GVA via kernel identity mapping offset
  2. Write through GDB's virtual memory write

Examples:
    ast-phys-write 0x1000 deadbeef
    ast-phys-write 0x1000 /tmp/patch.bin --from-file
    """

    def __init__(self):
        super().__init__("ast-phys-write", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        parts = arg.strip().split()
        if len(parts) < 2:
            gdb.write("Usage: ast-phys-write <GPA> <hex-bytes>\n"
                       "       ast-phys-write <GPA> <file> --from-file\n")
            return

        addr_str = parts[0]
        try:
            gpa = int(addr_str, 0)
        except ValueError:
            gdb.write(f"Error: invalid address '{addr_str}'\n")
            return

        from_file = "--from-file" in parts

        if from_file:
            filepath = parts[1]
            if not os.path.exists(filepath):
                gdb.write(f"Error: file not found: {filepath}\n")
                return
            with open(filepath, "rb") as f:
                data = f.read()
        else:
            hex_str = parts[1]
            try:
                data = bytes.fromhex(hex_str)
            except ValueError:
                gdb.write(f"Error: invalid hex string '{hex_str}'\n")
                return

        if not data:
            gdb.write("Error: no data to write\n")
            return

        # Strategy: use pmemsave to read current content, then try to find
        # the GPA's GVA mapping and write through GDB.
        # For kernel physical memory, Asterinas typically uses a linear mapping
        # at a known offset. Try the common identity mapping first.

        # Try to resolve GPA -> GVA using gva2gpa probing
        # First attempt: Asterinas kernel maps physical memory at a fixed offset
        # (typically phys_addr + KERNEL_OFFSET). Try common offsets.
        kernel_phys_offsets = [
            LINEAR_MAPPING_BASE_VADDR,
            0,
        ]

        written = False
        for offset in kernel_phys_offsets:
            gva = gpa + offset
            verify = _qemu_monitor(f"gva2gpa {hex(gva)}")
            if "error" in verify.lower() or "not mapped" in verify.lower():
                continue

            # Check if the resolved GPA matches
            try:
                match = re.search(r'(?:gpa|GPA)[:\s]+0x([0-9a-fA-F]+)', verify)
                if match:
                    resolved_gpa = int(match.group(1), 16)
                    if resolved_gpa != gpa:
                        continue
            except (ValueError, AttributeError):
                continue

            # Write via GDB virtual memory
            try:
                inferior = gdb.selected_inferior()
                inferior.write_memory(gva, data)
                gdb.write(f"Wrote {len(data)} bytes to GPA {hex(gpa)} "
                          f"(via GVA {hex(gva)})\n")
                written = True
                break
            except gdb.error as e:
                continue

        if not written:
            gdb.write(f"Error: could not find writable GVA mapping for GPA {hex(gpa)}.\n"
                       "The GPA may not be linearly mapped in the current address space.\n"
                       "This helper only supports the kernel linear mapping fallback.\n")


class AstMonitor(gdb.Command):
    """Pass an arbitrary command to the QEMU monitor.

Usage: ast-monitor <command>

Examples:
    ast-monitor info registers
    ast-monitor info cpus
    ast-monitor info mtree
    ast-monitor system_reset
    """

    def __init__(self):
        super().__init__("ast-monitor", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        if not arg.strip():
            gdb.write("Usage: ast-monitor <command>\n")
            return

        output = _qemu_monitor(arg.strip())
        gdb.write(output)


AstXp()
AstPhysDump()
AstGpa2Hva()
AstGva2Gpa()
AstMm()
AstPhysWrite()
AstMonitor()
