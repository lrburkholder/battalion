#[derive(serde::Serialize)]
struct BoundaryContract {
    state_authority: &'static str,
    provider_mode: &'static str,
    renderer_filesystem: bool,
    renderer_shell: bool,
    renderer_network: bool,
}

#[tauri::command]
fn boundary_contract() -> BoundaryContract {
    BoundaryContract {
        state_authority: "fixture-adapter-only",
        provider_mode: "disabled",
        renderer_filesystem: false,
        renderer_shell: false,
        renderer_network: false,
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![boundary_contract])
        .run(tauri::generate_context!())
        .expect("failed to run disposable BTN-38 Tauri spike");
}

