"""
Rust type introspection utilities for GDB Python API.

Provides helpers to unwrap Arc, Mutex, Option, Vec, BTreeMap, SlotVec
using DWARF debug info — never hardcoded offsets.

When Rust GDB pretty-printers are loaded (via rust-gdb or auto-detection
in the entry point script), most functions use the pretty-printer API as a fast
path and fall back to manual DWARF traversal when printers are unavailable.
"""

import gdb


class IntrospectionError(Exception):
    """Raised when a type introspection operation fails."""
    pass


def _first_success(*readers):
    """Returns the first successful reader result."""
    last_error = None
    for reader in readers:
        try:
            return reader()
        except (gdb.error, KeyError, TypeError, ValueError) as error:
            last_error = error
    if last_error is None:
        raise IntrospectionError("No reader candidates provided")
    raise last_error


# --- Pretty-Printer Integration ---
#
# gdb.default_visualizer() returns the Rust pretty-printer for stdlib types
# (Arc, Vec, BTreeMap, Option, String, …).  Each printer exposes children()
# and to_string() that already know the internal layout, so we use them as
# a fast path before falling back to manual DWARF traversal.


def _get_pp(val):
    """Get the GDB pretty-printer (visualizer) for a value, or None."""
    try:
        return gdb.default_visualizer(val)
    except Exception:
        return None


def _pp_children(pp):
    """Safely collect children of a pretty-printer.  Returns list or None."""
    try:
        return list(pp.children())
    except Exception:
        return None


def _pp_to_string(pp):
    """Safely get string representation from a pretty-printer."""
    try:
        result = pp.to_string()
        if result is None:
            return None
        if isinstance(result, str):
            return result
        # gdb.LazyString — used by String / &str printers
        if hasattr(result, 'value'):
            length = result.length
            if length == 0:
                return ""
            encoding = result.encoding or "utf-8"
            return result.value().string(encoding, errors="replace", length=length)
        return str(result)
    except Exception:
        return None


# --- Arc<T> ---

def unwrap_arc(arc_val):
    """
    Dereference an alloc::sync::Arc<T> to its inner T value.

    Fast path: Rust pretty-printer yields ("value", inner_T).
    Fallback: manual traversal of Arc { ptr: NonNull<ArcInner<T>> }.
    """
    pp = _get_pp(arc_val)
    if pp is not None:
        children = _pp_children(pp)
        if children is not None:
            for name, child in children:
                if name == "value":
                    return child

    try:
        ptr = arc_val['ptr']['pointer']
        arc_inner = ptr.dereference()
        return arc_inner['data']
    except gdb.error as e:
        raise IntrospectionError(f"Failed to unwrap Arc: {e}")


# --- Mutex<T> (ostd custom) ---

def unwrap_mutex(mutex_val):
    """
    Read the inner value of an ostd Mutex<T>.

    ostd Mutex layout: { lock: AtomicBool, queue: WaitQueue, val: UnsafeCell<T> }
    Returns (is_locked: bool, inner_value: gdb.Value).
    """
    try:
        lock_val = mutex_val['lock']
        # AtomicBool -> inner value
        try:
            locked = bool(int(lock_val['v']['value']))
        except gdb.error:
            try:
                locked = bool(int(lock_val['v']['v']['value']))
            except gdb.error:
                locked = None

        inner = mutex_val['val']['value']
        return (locked, inner)
    except gdb.error as e:
        raise IntrospectionError(f"Failed to unwrap Mutex: {e}")


def unwrap_mutex_value(mutex_val):
    """Convenience: just get the inner value, ignoring lock state."""
    _, inner = unwrap_mutex(mutex_val)
    return inner


# --- RwMutex<T> ---

def unwrap_rwmutex_value(rwmutex_val):
    """Read the inner value of an ostd RwMutex<T>."""
    try:
        return rwmutex_val['val']['value']
    except gdb.error as e:
        raise IntrospectionError(f"Failed to unwrap RwMutex: {e}")


# --- Option<T> ---

def unwrap_option(option_val):
    """
    Unwrap an Option<T>. Returns (is_some: bool, value_or_none).

    Fast path: Rust pretty-printer discriminates variants directly.
    Fallback: multiple DWARF strategies for variant representation.
    """
    # Pretty-printer fast path
    pp = _get_pp(option_val)
    if pp is not None:
        try:
            s = _pp_to_string(pp)
            children = _pp_children(pp)
            if s is not None and "None" in s and not children:
                return (False, None)
            if children:
                return (True, children[0][1])
        except Exception:
            pass  # Fall through to manual strategies

    def unwrap_some_payload(some_val):
        for field_name in ('__0', '0'):
            try:
                return some_val[field_name]
            except (gdb.error, KeyError):
                continue
        return some_val

    try:
        variant = option_val.dynamic_type
        tag = str(variant)
        if 'None' in tag:
            return (False, None)
        if 'Some' in tag:
            for field_name in ('Some', '__0', '0'):
                try:
                    return (True, unwrap_some_payload(option_val[field_name]))
                except (gdb.error, KeyError):
                    continue
    except (gdb.error, KeyError):
        pass

    try:
        field_names = [field.name for field in option_val.type.strip_typedefs().fields()]
        if 'Some' in field_names:
            try:
                some_value = option_val['Some']
                return (True, unwrap_some_payload(some_value))
            except (gdb.error, KeyError):
                pass
        if 'None' in field_names and all(name in (None, 'None', 'Some') for name in field_names):
            try:
                option_val['None']
                return (False, None)
            except (gdb.error, KeyError):
                pass
    except gdb.error:
        pass

    # Fallback: try discriminant-based approach
    try:
        disc = int(option_val['DISCRIMINANT'])
        if disc == 0:
            return (False, None)
        for field_name in ('Some', '__0', '0'):
            try:
                return (True, unwrap_some_payload(option_val[field_name]))
            except (gdb.error, KeyError):
                continue
    except (gdb.error, KeyError):
        pass

    # Fallback: pointer-based Option (None = 0)
    try:
        for field_name in ('Some', '__0', '0'):
            try:
                val = unwrap_some_payload(option_val[field_name])
                ptr_val = int(val)
                if ptr_val == 0:
                    return (False, None)
                return (True, val)
            except (gdb.error, KeyError, ValueError, TypeError):
                continue
    except (gdb.error, KeyError, ValueError):
        pass

    raise IntrospectionError("Cannot determine Option variant")


def read_scalar(value):
    """Reads an integer-like scalar wrapped by tuple structs or atomics."""
    try:
        return int(value)
    except (gdb.error, TypeError, ValueError):
        pass

    readers = [
        lambda: int(value['value']),
        lambda: int(value['v']),
        lambda: int(value['v']['value']),
        lambda: int(value['v']['v']),
        lambda: int(value['v']['v']['value']),
        lambda: int(value['inner']),
        lambda: int(value['inner']['value']),
        lambda: int(value['inner']['v']),
        lambda: int(value['inner']['v']['value']),
        lambda: int(value['__0']),
        lambda: int(value['__0']['value']),
        lambda: int(value['__0']['v']),
        lambda: int(value['__0']['v']['value']),
        lambda: int(value['__0']['__0']),
        lambda: int(value['__0']['__0']),
        lambda: int(value['0']),
        lambda: int(value['0']['value']),
        lambda: int(value['0']['0']),
        lambda: int(value['0']['v']),
        lambda: int(value['0']['v']['value']),
    ]
    try:
        return _first_success(*readers)
    except (gdb.error, KeyError, TypeError, ValueError) as error:
        raise IntrospectionError(f"Failed to read scalar: {error}")


# --- Vec<T> ---

def _get_vec_raw_ptr(vec_val):
    """
    Extract the raw data pointer from a Vec<T>.

    Handles multiple nightly layouts:
    - Old: Vec { buf: RawVec { ptr: Unique<T> { pointer: *T } }, len }
    - New: Vec { buf: RawVec { inner: RawVecInner { ptr: Unique<u8> { .. } } }, len }

    For the new layout, the pointer is u8*; callers must cast to *T.
    Returns (ptr: gdb.Value, is_byte_ptr: bool).
    """
    buf = vec_val['buf']

    # New layout: buf.inner.ptr.pointer (Unique<u8>)
    for path in [
        lambda: buf['inner']['ptr']['pointer']['pointer'],
        lambda: buf['inner']['ptr']['pointer'],
    ]:
        try:
            ptr = path()
            return (ptr, True)
        except gdb.error:
            continue

    # Old layout: buf.ptr.pointer (Unique<T>)
    for path in [
        lambda: buf['ptr']['pointer']['pointer'],
        lambda: buf['ptr']['pointer'],
    ]:
        try:
            ptr = path()
            return (ptr, False)
        except gdb.error:
            continue

    raise IntrospectionError("Cannot locate Vec data pointer in any known layout")


def read_vec(vec_val):
    """
    Read elements from a Vec<T>.
    Returns a list of gdb.Value items.

    Fast path: Rust pretty-printer yields indexed elements.
    Fallback: manual pointer arithmetic over old/new RawVec layouts.
    """
    # Pretty-printer fast path
    pp = _get_pp(vec_val)
    if pp is not None:
        children = _pp_children(pp)
        if children is not None:
            return [child for _name, child in children]

    # Manual DWARF traversal
    try:
        length = int(vec_val['len'])
        if length == 0:
            return []

        ptr, is_byte_ptr = _get_vec_raw_ptr(vec_val)

        if is_byte_ptr:
            # Derive element type T from Vec<T>'s type
            vec_type = vec_val.type.strip_typedefs()
            # Vec<T> has template_argument(0) = T
            try:
                elem_type = vec_type.template_argument(0)
            except (gdb.error, RuntimeError):
                # Fallback: try to infer from the type name
                raise IntrospectionError(
                    "Cannot determine Vec element type for byte-pointer layout"
                )
            # Cast u8* to *T
            ptr = ptr.cast(elem_type.pointer())

        result = []
        for i in range(length):
            result.append((ptr + i).dereference())
        return result
    except gdb.error as e:
        raise IntrospectionError(f"Failed to read Vec: {e}")


# --- SlotVec<T> ---

def read_slot_vec(slot_vec_val):
    """
    Read occupied entries from a SlotVec<T>.
    SlotVec layout: { slots: Vec<Option<T>>, num_occupied: usize }

    Yields (index, value) tuples for occupied slots.
    """
    try:
        slots_vec = slot_vec_val['slots']
        slots = read_vec(slots_vec)
        for i, slot in enumerate(slots):
            is_some, val = unwrap_option(slot)
            if is_some:
                yield (i, val)
    except gdb.error as e:
        raise IntrospectionError(f"Failed to read SlotVec: {e}")


# --- BTreeMap<K, V> ---

def _read_leaf_kv(node, idx):
    """Read a single (key, value) from a LeafNode at index idx."""
    keys = node['keys']
    vals = node['vals']

    return (_read_maybe_uninit_array_entry(keys, idx),
            _read_maybe_uninit_array_entry(vals, idx))


def _read_maybe_uninit_array_entry(array_val, idx):
    """Reads one initialized element from a `[MaybeUninit<T>; N]`-like array."""
    slot = _first_success(
        lambda: array_val[idx],
        lambda: array_val['__0'][idx],
        lambda: array_val['inner']['value'][idx],
    )
    inner = _first_success(
        lambda: slot['value'],
        lambda: slot['__0'],
        lambda: slot,
    )
    return _first_success(
        lambda: inner['value'],
        lambda: inner,
    )


def _lookup_internal_node_type(leaf_type):
    """Looks up the matching `InternalNode<K, V>` type for a `LeafNode<K, V>`."""
    leaf_name = str(leaf_type.strip_typedefs())
    candidates = []
    if "LeafNode<" in leaf_name:
        candidates.append(leaf_name.replace("LeafNode<", "InternalNode<", 1))
    if "::LeafNode<" in leaf_name:
        candidates.append(leaf_name.replace("::LeafNode<", "::InternalNode<", 1))

    for candidate in candidates:
        try:
            return gdb.lookup_type(candidate)
        except gdb.error:
            continue
    return None


def _read_internal_edge(node_ptr, idx):
    """Read child edge `idx` from an internal B-tree node."""
    leaf_type = node_ptr.type.target().strip_typedefs()
    internal_type = _lookup_internal_node_type(leaf_type)
    if internal_type is not None:
        internal = node_ptr.cast(internal_type.pointer()).dereference()
        edge = _read_maybe_uninit_array_entry(internal['edges'], idx)
        return _first_success(
            lambda: edge['pointer'],
            lambda: edge,
        )

    leaf_size = leaf_type.sizeof
    node_addr = int(node_ptr)
    edge_base = node_addr + leaf_size
    ptr_size = leaf_type.pointer().sizeof
    edge_addr = edge_base + idx * ptr_size
    edge_ptr_type = leaf_type.pointer().pointer()
    return gdb.Value(edge_addr).cast(edge_ptr_type).dereference()


def _btree_walk(node_ptr, height):
    """
    Recursively walk a BTreeMap B-tree yielding (key, value) pairs.

    At height 0: node is a LeafNode, directly read keys/vals.
    At height > 0: node is an InternalNode (LeafNode + edges).
    Traversal: edge[0], kv[0], edge[1], kv[1], ..., kv[n-1], edge[n]
    """
    if int(node_ptr) == 0:
        return

    node = node_ptr.dereference()

    try:
        length = _first_success(
            lambda: int(node['len']),
            lambda: int(node['_len']),
        )
    except (gdb.error, KeyError, TypeError, ValueError):
        raise IntrospectionError("Cannot read BTree node length")

    if height == 0:
        for i in range(length):
            yield _read_leaf_kv(node, i)
    else:
        for i in range(length + 1):
            child = _read_internal_edge(node_ptr, i)
            if int(child) != 0:
                yield from _btree_walk(child, height - 1)
            if i < length:
                yield _read_leaf_kv(node, i)


def read_btree_map(btree_map_val):
    """
    Traverse a BTreeMap<K, V> and yield (key, value) pairs.

    Fast path: Rust pretty-printer iterates the B-tree directly.
    Fallback: manual B-tree walk (complex, compiler-version dependent).
    """
    # Pretty-printer fast path — children are alternating key/val pairs
    pp = _get_pp(btree_map_val)
    if pp is not None:
        try:
            children = list(pp.children())
            for i in range(0, len(children) - 1, 2):
                yield (children[i][1], children[i + 1][1])
            return
        except Exception:
            pass  # Fall through to manual traversal

    # Manual B-tree traversal
    try:
        root_field = btree_map_val['root']
        # Option<Root<K, V>>
        is_some, root = unwrap_option(root_field)
        if not is_some:
            return

        # Root contains a NodeRef: { node: NonNull<LeafNode<K, V>>, height: usize }
        try:
            height = int(root['height'])
            node_ptr = root['node']['pointer']
        except gdb.error:
            height = int(root['inner']['height'])
            node_ptr = root['inner']['node']['pointer']

        yield from _btree_walk(node_ptr, height)
    except (gdb.error, IntrospectionError) as e:
        raise IntrospectionError(
            f"Failed to traverse BTreeMap: {e}. "
            "BTreeMap internal layout may have changed with the Rust nightly version."
        )


# --- Weak<T> ---

def unwrap_weak(weak_val):
    """
    Dereference an alloc::sync::Weak<T> to its inner T value.
    Returns None if the weak pointer is dangling.

    Fast path: Rust pretty-printer yields ("value", inner_T) if alive.
    Fallback: manual pointer + strong count check.
    """
    # Pretty-printer fast path
    pp = _get_pp(weak_val)
    if pp is not None:
        children = _pp_children(pp)
        if children is not None:
            for name, child in children:
                if name == "value":
                    return child
            return None  # No value child → dangling or expired

    # Manual fallback
    try:
        ptr = weak_val['ptr']['pointer']
        ptr_int = int(ptr)
        if ptr_int == 0 or ptr_int == usize_max():
            return None
        arc_inner = ptr.dereference()
        if read_scalar(arc_inner['strong']) == 0:
            return None
        return arc_inner['data']
    except (gdb.error, IntrospectionError):
        return None


def usize_max():
    """Return usize::MAX for the target architecture."""
    ptr_size = gdb.lookup_type('usize').sizeof
    return (1 << (ptr_size * 8)) - 1


# --- Helpers ---

def try_field(val, *field_names):
    """Try accessing fields in order, return first success or None."""
    for name in field_names:
        try:
            return val[name]
        except gdb.error:
            continue
    return None


def read_string_from_value(val):
    """Try to read a Rust string (&str or String) from a gdb.Value."""
    # Pretty-printer fast path (Rust String / &str)
    pp = _get_pp(val)
    if pp is not None:
        s = _pp_to_string(pp)
        if s is not None and len(s) <= 1024:
            return s

    # Manual fallback
    try:
        ptr = val['data_ptr']
        length = int(val['length'])
        if length == 0:
            return ""
        if length > 1024:
            return f"<string too long: {length}>"
        inferior = gdb.selected_inferior()
        mem = inferior.read_memory(int(ptr), length)
        return bytes(mem).decode('utf-8', errors='replace')
    except gdb.error:
        pass

    try:
        vec = val['vec']
        items = read_vec(vec)
        return bytes(int(b) for b in items).decode('utf-8', errors='replace')
    except (gdb.error, IntrospectionError):
        pass

    return None
