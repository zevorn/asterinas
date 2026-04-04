// SPDX-License-Identifier: MPL-2.0

use std::path::PathBuf;
use std::process::Command;

use crate::{
    cli::DebugArgs,
    commands::util::bin_file_name,
    util::{get_cargo_metadata, get_kernel_crate, get_target_directory, new_command_checked_exists},
};

pub fn execute_debug_command(_profile: &str, args: &DebugArgs) {
    let remote = &args.remote;

    let file_path = get_target_directory()
        .join("osdk")
        .join(get_kernel_crate().name)
        .join(bin_file_name());
    println!("Debugging {}", file_path.display());

    // Prefer rust-gdb for Rust pretty-printer support, fall back to gdb.
    let mut gdb = if which::which("rust-gdb").is_ok() {
        Command::new("rust-gdb")
    } else {
        new_command_checked_exists("gdb")
    };
    gdb.args([
        format!("{}", file_path.display()).as_str(),
        "-ex",
        format!("target remote {}", remote).as_str(),
    ]);

    // Auto-source the Asterinas GDB helper scripts if available.
    let workspace_root = get_cargo_metadata(None::<&str>, None::<&[&str]>)
        .and_then(|m| m.get("workspace_root").and_then(|v| v.as_str()).map(PathBuf::from));
    if let Some(root) = workspace_root {
        let helper_script = root.join("scripts/gdb/asterinas-gdb.py");
        if helper_script.exists() {
            gdb.args(["-ex", &format!("source {}", helper_script.display())]);
        }
    }

    gdb.status().unwrap();
}

#[test]
fn have_gdb_installed() {
    let output = new_command_checked_exists("gdb").arg("--version").output();
    assert!(output.is_ok(), "Failed to run gdb");
    let stdout = String::from_utf8_lossy(&output.unwrap().stdout).to_string();
    assert!(stdout.contains("GNU gdb"));
}
