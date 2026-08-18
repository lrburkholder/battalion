#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::time::Duration;

#[derive(serde::Serialize)]
struct BoundaryContract {
    state_authority: &'static str,
    provider_mode: &'static str,
    renderer_filesystem: bool,
    renderer_shell: bool,
    renderer_network: bool,
    permission_probe: bool,
}

#[derive(serde::Serialize)]
struct BenchmarkCompletion {
    measurement_mode: bool,
}

#[derive(serde::Serialize)]
struct BenchmarkError {
    code: &'static str,
    message: String,
}

struct BenchmarkMode {
    ready_file: Option<PathBuf>,
    permission_probe: bool,
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct PermissionProbe {
    filesystem_denied: bool,
    shell_denied: bool,
    network_denied: bool,
}

fn parse_benchmark_ready_file(args: &[String]) -> Option<PathBuf> {
    args.iter().find_map(|argument| {
        argument
            .strip_prefix("--benchmark-ready-file=")
            .map(PathBuf::from)
    })
}

fn permission_probe_requested(args: &[String]) -> bool {
    args.iter()
        .any(|argument| argument == "--benchmark-permission-probe")
}

#[tauri::command]
fn boundary_contract(mode: tauri::State<'_, BenchmarkMode>) -> BoundaryContract {
    BoundaryContract {
        state_authority: "fixture-adapter-only",
        provider_mode: "disabled",
        renderer_filesystem: false,
        renderer_shell: false,
        renderer_network: false,
        permission_probe: mode.permission_probe,
    }
}

#[tauri::command]
fn benchmark_complete(
    app: tauri::AppHandle,
    mode: tauri::State<'_, BenchmarkMode>,
    probes: Option<PermissionProbe>,
) -> Result<BenchmarkCompletion, BenchmarkError> {
    let Some(ready_file) = &mode.ready_file else {
        return Ok(BenchmarkCompletion {
            measurement_mode: false,
        });
    };

    if mode.permission_probe
        && !matches!(
            probes,
            Some(PermissionProbe {
                filesystem_denied: true,
                shell_denied: true,
                network_denied: true,
            })
        )
    {
        return Err(BenchmarkError {
            code: "permission-probe-failed",
            message: "an undeclared renderer capability was not denied".to_owned(),
        });
    }

    let marker = if mode.permission_probe {
        b"filesystem_denied=true\nshell_denied=true\nnetwork_denied=true\n".as_slice()
    } else {
        b"ready\n".as_slice()
    };
    std::fs::write(ready_file, marker).map_err(|error| BenchmarkError {
        code: "benchmark-ready-write-failed",
        message: format!("failed to write benchmark readiness marker: {error}"),
    })?;

    std::thread::spawn(move || {
        // Leave a bounded collection window after readiness so the external
        // harness can sample the complete WebView process tree.
        std::thread::sleep(Duration::from_millis(500));
        app.exit(0);
    });

    Ok(BenchmarkCompletion {
        measurement_mode: true,
    })
}

fn main() {
    let args = std::env::args().collect::<Vec<_>>();
    let ready_file = parse_benchmark_ready_file(&args);
    let permission_probe = permission_probe_requested(&args);
    tauri::Builder::default()
        .manage(BenchmarkMode {
            ready_file,
            permission_probe,
        })
        .invoke_handler(tauri::generate_handler![
            boundary_contract,
            benchmark_complete
        ])
        .run(tauri::generate_context!())
        .expect("failed to run disposable BTN-38 Tauri spike");
}

#[cfg(test)]
mod tests {
    use super::{parse_benchmark_ready_file, permission_probe_requested};
    use std::path::PathBuf;

    #[test]
    fn parses_explicit_benchmark_ready_file() {
        let parsed = parse_benchmark_ready_file(&[
            "battalion-tauri-spike".to_owned(),
            "--benchmark-ready-file=C:\\temp\\ready.txt".to_owned(),
        ]);

        assert_eq!(parsed, Some(PathBuf::from("C:\\temp\\ready.txt")));
    }

    #[test]
    fn normal_launch_has_no_benchmark_output() {
        let parsed = parse_benchmark_ready_file(&["battalion-tauri-spike".to_owned()]);

        assert_eq!(parsed, None);
    }

    #[test]
    fn permission_probe_is_explicitly_opt_in() {
        assert!(permission_probe_requested(&[
            "battalion-tauri-spike".to_owned(),
            "--benchmark-permission-probe".to_owned(),
        ]));
        assert!(!permission_probe_requested(&[
            "battalion-tauri-spike".to_owned()
        ]));
    }
}
