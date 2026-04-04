"""
Symbol and type resolution helpers for Asterinas kernel.

Provides cached lookups for global symbols and Rust type names
via DWARF debug info.  Compatible with GDB 14+ and GDB 15+
(where single-quoted identifiers are character literals).
"""

import re

import gdb


_type_cache = {}
_symbol_cache = {}


def _resolve_symbol_value(symbol_name):
    """Resolve a Rust symbol by trying multiple GDB access methods.

    GDB 15 changed single-quote semantics in the expression parser
    (they are now character literals), so the traditional
    ``parse_and_eval("&'crate::path'")`` trick no longer works.
    We fall back through several strategies:

    1. ``gdb.lookup_static_symbol`` (most robust, available GDB 14+)
    2. ``parse_and_eval`` *without* quoting (works when the language is Rust)
    3. ``info address`` + ``ptype`` to reconstruct a typed pointer manually
    """
    # Strategy 1: lookup_static_symbol (doesn't need expression quoting)
    try:
        sym = gdb.lookup_static_symbol(symbol_name)
        if sym is not None:
            return sym.value()
    except (gdb.error, AttributeError):
        pass

    # Strategy 2: parse_and_eval without quotes (Rust language mode)
    try:
        return gdb.parse_and_eval(f"&{symbol_name}").dereference()
    except gdb.error:
        pass

    # Strategy 3: info address → ptype → manual cast
    try:
        addr_out = gdb.execute(f"info address {symbol_name}", to_string=True)
        m = re.search(r"(0x[0-9a-fA-F]+)", addr_out)
        if m:
            addr = m.group(1)
            type_out = gdb.execute(f"ptype {symbol_name}", to_string=True)
            tm = re.match(r"type\s*=\s*(.*)", type_out.strip(), re.DOTALL)
            if tm:
                type_str = tm.group(1).strip().rstrip(";")
                return gdb.parse_and_eval(f"*({type_str} *){addr}")
    except gdb.error:
        pass

    return None


def lookup_type(type_name):
    """Look up a GDB type by its (possibly mangled) Rust name, with caching."""
    if type_name in _type_cache:
        return _type_cache[type_name]
    try:
        t = gdb.lookup_type(type_name)
        _type_cache[type_name] = t
        return t
    except gdb.error:
        return None


def lookup_global(symbol_name):
    """Look up a global symbol by name, with caching."""
    if symbol_name in _symbol_cache:
        return _symbol_cache[symbol_name]
    val = _resolve_symbol_value(symbol_name)
    if val is not None:
        _symbol_cache[symbol_name] = val
    return val


def read_global(symbol_name):
    """Read the value of a global symbol."""
    return _resolve_symbol_value(symbol_name)


def clear_caches():
    """Clear all caches. Call when the inferior is restarted."""
    _type_cache.clear()
    _symbol_cache.clear()
