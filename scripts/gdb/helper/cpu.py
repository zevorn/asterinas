"""
CPU state and kernel statistics commands.

Commands:
    ast-uptime  — Show kernel uptime from jiffies
"""

import gdb

from helper.symbols import read_global
from helper.utils import IntrospectionError, read_scalar
from helper.constants import ELAPSED_JIFFIES_SYMBOL, TIMER_FREQ


class AstUptime(gdb.Command):
    """Show kernel uptime from jiffies counter.

Usage: ast-uptime
    """

    def __init__(self):
        super().__init__("ast-uptime", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        try:
            elapsed = read_global(ELAPSED_JIFFIES_SYMBOL)
            if elapsed is None:
                gdb.write("Error: cannot read ELAPSED jiffies\n")
                return

            jiffies = read_scalar(elapsed)
            total_secs = jiffies // TIMER_FREQ
            millis = jiffies % TIMER_FREQ
            hours = total_secs // 3600
            mins = (total_secs % 3600) // 60
            secs = total_secs % 60

            gdb.write(f"Uptime: {hours:02d}:{mins:02d}:{secs:02d}.{millis:03d}"
                      f"  ({jiffies} jiffies, {TIMER_FREQ} Hz)\n")
        except (gdb.error, IntrospectionError) as e:
            gdb.write(f"Error: {e}\n")


AstUptime()
