#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
#
# Boot Asterinas with a GDB server and collect raw and helper-mediated output.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"
SOCKET_PATH="$PROJECT_ROOT/.osdk-gdb-socket"
QEMU_PID=""

die() {
    echo "Error: $1" >&2
    exit 1
}

cleanup() {
    if [ -n "$QEMU_PID" ]; then
        kill -- -"$QEMU_PID" 2>/dev/null || kill "$QEMU_PID" 2>/dev/null || true
        wait "$QEMU_PID" 2>/dev/null || true
    fi
    rm -f "$SOCKET_PATH"
}
trap cleanup EXIT

target_triple() {
    case "$1" in
        x86_64)
            echo "x86_64-unknown-none"
            ;;
        riscv64)
            echo "riscv64imac-unknown-none-elf"
            ;;
        loongarch64)
            echo "loongarch64-unknown-none-softfloat"
            ;;
        aarch64)
            echo "aarch64-unknown-none-softfloat"
            ;;
        *)
            die "unsupported target architecture: $1"
            ;;
    esac
}

kernel_bin_name() {
    local package_name

    package_name="$(
        cd "$PROJECT_ROOT"
        cargo metadata --no-deps --format-version 1 \
            | jq -r '.packages[]
                | select(.manifest_path | endswith("/kernel/Cargo.toml"))
                | .name'
    )"

    if [ -z "$package_name" ] || [ "$package_name" = "null" ]; then
        die "cannot determine the kernel crate name"
    fi
    echo "${package_name}-osdk-bin"
}

ensure_osdk() {
    if command -v cargo-osdk >/dev/null 2>&1; then
        return
    fi

    echo "[metrics] Installing cargo-osdk"
    (
        cd "$PROJECT_ROOT"
        OSDK_LOCAL_DEV=1 cargo install cargo-osdk --path osdk
    )
}

main() {
    local arch="${TARGET_ARCH:-x86_64}"
    local target
    local elf

    target="$(target_triple "$arch")"
    mkdir -p "$OUTPUT_DIR"
    rm -f "$SOCKET_PATH"
    ensure_osdk

    echo "[metrics] Building initramfs images"
    make -C "$PROJECT_ROOT" initramfs

    echo "[metrics] Starting kernel with GDB server"
    (
        cd "$PROJECT_ROOT"
        setsid cargo osdk run \
            --initramfs="$PROJECT_ROOT/test/initramfs/build/initramfs.cpio.gz" \
            --gdb-server wait-client
    ) &
    QEMU_PID=$!

    echo "[metrics] Waiting for GDB socket"
    for _ in $(seq 1 900); do
        [ -S "$SOCKET_PATH" ] && break
        sleep 1
    done
    if [ ! -S "$SOCKET_PATH" ]; then
        die "GDB socket did not appear"
    fi

    elf="$PROJECT_ROOT/target/$target/debug/$(kernel_bin_name)"
    if [ ! -f "$elf" ]; then
        die "kernel ELF with debug symbols not found: $elf"
    fi

    echo "[metrics] Kernel ELF: $elf"
    timeout 900 rust-gdb --batch \
        --command="$SCRIPT_DIR/collect.gdb" \
        "$elf" > "$OUTPUT_DIR/gdb.log" 2>&1 || true

    if [ ! -s "$OUTPUT_DIR/gdb.log" ]; then
        die "GDB produced no output"
    fi
    cp "$PROJECT_ROOT/qemu.log" "$OUTPUT_DIR/qemu.log" 2>/dev/null || true
    cp "$PROJECT_ROOT/qemu-serial.log" "$OUTPUT_DIR/qemu-serial.log" 2>/dev/null || true
    echo "[metrics] Wrote $OUTPUT_DIR/gdb.log"
}

main "$@"
