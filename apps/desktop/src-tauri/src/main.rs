//! G23: Kopifund in a Mac window. The shell starts `serve.py --app` from the
//! checkout it was built from, shows the starting page (desktop/ui) until the
//! server prints its ready line, then loads the app from the server. The
//! server stops when its standard input closes, which this process holds open
//! for as long as it runs (SPEC.md, "Desktop app (G23)").

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[cfg(target_os = "macos")]
use objc2::{AnyThread, MainThreadMarker};
#[cfg(target_os = "macos")]
use objc2_app_kit::{NSApplication, NSImage};
#[cfg(target_os = "macos")]
use objc2_foundation::NSString;
use serde::Serialize;
use tauri::webview::DownloadEvent;
use tauri::{AppHandle, Manager, RunEvent, State, Url, WebviewWindowBuilder};

/// The line serve.py prints once it is listening, then its address.
const READY: &str = "KOPIFUND_READY";
/// The line serve.py prints with the app icon set picked in Settings, as it
/// starts and after every pick.
const ICON: &str = "KOPIFUND_ICON";
/// How many of the server's last lines the failure page shows.
const KEEP_LINES: usize = 40;

/// The checkout this app was built from: apps/desktop/src-tauri/../../..
fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("..")
}

/// One icon set's Dock icon, which app-icons/apply.py writes on macOS's grid;
/// None for a name that is not a set.
fn icon_path(name: &str) -> Option<PathBuf> {
    let plain = !name.is_empty()
        && name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("icons")
        .join("sets")
        .join(format!("{name}.png"));
    (plain && path.is_file()).then_some(path)
}

/// The Dock shows this image while the app runs; quit, it shows the bundle's
/// icon, the default set, again.
#[cfg(target_os = "macos")]
fn set_dock_icon(app: &AppHandle, path: PathBuf) {
    let _ = app.run_on_main_thread(move || {
        let Some(mtm) = MainThreadMarker::new() else { return };
        let file = NSString::from_str(&path.to_string_lossy());
        if let Some(image) = NSImage::initWithContentsOfFile(NSImage::alloc(), &file) {
            // SAFETY: the setter takes an image; it is never handed None here
            unsafe { NSApplication::sharedApplication(mtm).setApplicationIconImage(Some(&image)) };
        }
    });
}

#[cfg(not(target_os = "macos"))]
fn set_dock_icon(_app: &AppHandle, _path: PathBuf) {}

#[derive(Default)]
struct Sidecar {
    child: Option<Child>,
    /// Held open while the server should run; dropping it stops the server.
    stdin: Option<ChildStdin>,
    log: VecDeque<String>,
    failed: bool,
    /// Bumped on each start, so a reader thread of an old server cannot mark
    /// the new one failed.
    generation: u64,
    quitting: bool,
}

type Shared = Arc<Mutex<Sidecar>>;

#[derive(Serialize)]
struct Status {
    failed: bool,
    log: Vec<String>,
}

fn remember(state: &Shared, line: String) {
    let mut s = state.lock().unwrap();
    s.log.push_back(line);
    while s.log.len() > KEEP_LINES {
        s.log.pop_front();
    }
}

/// The server's environment comes from the owner's login shell, so python3
/// and pdftotext from Homebrew, and any key the shell profile exports, are
/// found as they are in a terminal. `exec` hands the shell's process to
/// python3, so the child's standard input is the server's.
fn command() -> Command {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into());
    let mut cmd = Command::new(shell);
    cmd.args(["-l", "-i", "-c", "exec python3 server/serve.py --app"])
        .current_dir(repo())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    cmd
}

/// Mark this start failed. False when a newer start or quitting has taken
/// over, so the caller leaves the window alone.
fn fail(state: &Shared, generation: u64, line: Option<String>) -> bool {
    let mut s = state.lock().unwrap();
    if s.generation != generation || s.quitting {
        return false;
    }
    if let Some(line) = line {
        s.log.push_back(line);
    }
    s.failed = true;
    s.stdin = None;
    true
}

fn show_start_page(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        if let Ok(url) = window.url() {
            if url.host_str() == Some("127.0.0.1") {
                // the start page's address on macOS
                let _ = window.navigate("tauri://localhost/index.html".parse().unwrap());
            }
        }
    }
}

fn start(app: &AppHandle, state: &Shared) {
    let generation = {
        let mut s = state.lock().unwrap();
        stop_child(&mut s);
        s.generation += 1;
        s.failed = false;
        s.log.clear();
        s.generation
    };
    let mut child = match command().spawn() {
        Ok(child) => child,
        Err(e) => {
            fail(state, generation, Some(format!("Could not start the shell: {e}")));
            return;
        }
    };
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    {
        let mut s = state.lock().unwrap();
        s.stdin = child.stdin.take();
        s.child = Some(child);
    }

    let errs = state.clone();
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            remember(&errs, line);
        }
    });

    let outs = state.clone();
    let app = app.clone();
    thread::spawn(move || {
        let mut ready = false;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(address) = line.strip_prefix(READY) {
                if let (Ok(url), Some(window)) =
                    (address.trim().parse::<Url>(), app.get_webview_window("main"))
                {
                    ready = true;
                    let _ = window.navigate(url);
                }
            }
            if let Some(path) = line.strip_prefix(ICON).and_then(|name| icon_path(name.trim())) {
                set_dock_icon(&app, path);
            }
            remember(&outs, line);
        }
        // standard output closed: the server has stopped
        if !ready {
            // give stderr's reader a moment to catch the last lines
            thread::sleep(Duration::from_millis(200));
        }
        if fail(&outs, generation, Some("The server stopped.".into())) {
            show_start_page(&app);
        }
    });
}

/// Close the server's input, give it two seconds to stop, then kill it.
fn stop_child(s: &mut Sidecar) {
    s.stdin = None;
    if let Some(mut child) = s.child.take() {
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// What the window does with a navigation.
#[derive(Debug, PartialEq)]
enum Nav {
    /// load it in the window
    Load,
    /// open it in the default browser, keep the app
    Browser,
    /// stay where it is
    Refuse,
}

/// Where the window may go: the start page (tauri://) and the server's own
/// pages. Drops reach the page (tauri.conf.json, `dragDropEnabled`), so a PDF
/// dropped beside Upload's drop area would otherwise have WebKit open the file
/// in place of the app, with no Back to return; that is refused. A link to
/// another site opens in the default browser.
fn navigation(url: &Url) -> Nav {
    match url.scheme() {
        "tauri" | "about" => Nav::Load,
        "http" if url.host_str() == Some("127.0.0.1") => Nav::Load,
        "http" | "https" => Nav::Browser,
        _ => Nav::Refuse,
    }
}

fn open_in_browser(url: &Url) {
    let url = url.to_string();
    // waited on in its own thread, so the window never stalls and no zombie is left
    thread::spawn(move || {
        let _ = Command::new("/usr/bin/open").arg(url).status();
    });
}

/// Downloads the window has started, by URL, so a finished one can be shown:
/// on macOS WebKit's finish event carries no path (tauri `DownloadEvent`).
type Downloads = Arc<Mutex<Vec<(Url, PathBuf)>>>;

/// Whether the window may save the file at `url`: the server's own files only
/// (Settings' exports), never a file another site hands the window.
fn may_download(url: &Url) -> bool {
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1")
}

/// A download the window asked for (Settings > Export's links). WebKit cancels
/// every download when no handler is installed, so before this a click did
/// nothing. The file goes where Safari puts it, the Downloads folder (wry picks
/// a free name there), and Finder shows it once it is saved.
fn on_download(downloads: &Downloads, event: DownloadEvent<'_>) -> bool {
    match event {
        DownloadEvent::Requested { url, destination } => {
            if !may_download(&url) {
                return false;
            }
            let mut list = downloads.lock().unwrap();
            // a second click while the same file still downloads is refused: the
            // finish event names only the URL, so two at once could not be told
            // apart and Finder could show the other one's file
            if list.iter().any(|(u, _)| *u == url) {
                return false;
            }
            list.push((url, destination.clone()));
            true
        }
        DownloadEvent::Finished { url, path, success } => {
            let saved = {
                let mut list = downloads.lock().unwrap();
                list.iter().position(|(u, _)| *u == url).map(|i| list.remove(i).1)
            };
            if let (true, Some(file)) = (success, path.or(saved)) {
                thread::spawn(move || {
                    let _ = Command::new("/usr/bin/open").arg("-R").arg(file).status();
                });
            }
            true
        }
        _ => true,
    }
}

#[tauri::command]
fn status(state: State<'_, Shared>) -> Status {
    let s = state.lock().unwrap();
    Status { failed: s.failed, log: s.log.iter().cloned().collect() }
}

#[tauri::command]
fn retry(app: AppHandle, state: State<'_, Shared>) {
    let state = state.inner().clone();
    start(&app, &state);
}

fn main() {
    let state: Shared = Arc::default();
    let app = tauri::Builder::default()
        // a second launch brings the open window forward
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(state.clone())
        .invoke_handler(tauri::generate_handler![status, retry])
        .setup(move |app| {
            // tauri.conf.json describes the window with `create: false`, so it is
            // built here, before the server starts, with the navigation guard
            let config = app
                .config()
                .app
                .windows
                .iter()
                .find(|w| w.label == "main")
                .cloned()
                .expect("tauri.conf.json describes the main window");
            let downloads = Downloads::default();
            WebviewWindowBuilder::from_config(app.handle(), &config)?
                .on_download(move |_, event| on_download(&downloads, event))
                .on_navigation(|url| match navigation(url) {
                    Nav::Load => true,
                    Nav::Browser => {
                        open_in_browser(url);
                        false
                    }
                    Nav::Refuse => false,
                })
                .build()?;
            start(app.handle(), &state);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Kopifund's window could not be built");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            let state = app.state::<Shared>();
            let mut s = state.lock().unwrap();
            s.quitting = true;
            stop_child(&mut s);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nav(url: &str) -> Nav {
        navigation(&url.parse().unwrap())
    }

    #[test]
    fn the_start_page_and_the_server_load_in_the_window() {
        assert_eq!(nav("tauri://localhost/index.html"), Nav::Load);
        assert_eq!(nav("http://127.0.0.1:8771/upload"), Nav::Load);
        assert_eq!(nav("about:blank"), Nav::Load);
    }

    #[test]
    fn a_dropped_file_never_replaces_the_app() {
        assert_eq!(nav("file:///Users/me/Downloads/statement.pdf"), Nav::Refuse);
        assert_eq!(nav("data:text/html,hi"), Nav::Refuse);
    }

    #[test]
    fn a_dock_icon_is_found_for_a_set_and_nothing_else() {
        assert!(icon_path("sticker").is_some_and(|p| p.ends_with("icons/sets/sticker.png")));
        assert_eq!(icon_path("nope"), None);
        assert_eq!(icon_path("../icons/icon"), None);
        assert_eq!(icon_path(""), None);
    }

    #[test]
    fn another_site_opens_in_the_browser() {
        assert_eq!(nav("https://example.com/"), Nav::Browser);
        assert_eq!(nav("http://localhost:8771/"), Nav::Browser);
        assert_eq!(nav("https://127.0.0.1:8771/"), Nav::Browser);
    }
}

#[cfg(test)]
mod download_tests {
    use super::*;

    #[test]
    fn the_servers_own_files_download_and_another_sites_do_not() {
        let downloads = Downloads::default();
        let mut to = PathBuf::from("/Users/me/Downloads/transactions.csv");
        let ours: Url = "http://127.0.0.1:8771/api/exports/2026-10-01/transactions.csv".parse().unwrap();
        assert!(on_download(&downloads, DownloadEvent::Requested { url: ours.clone(), destination: &mut to }));
        assert_eq!(downloads.lock().unwrap().len(), 1);

        let theirs: Url = "https://example.com/file.pdf".parse().unwrap();
        let mut elsewhere = PathBuf::from("/Users/me/Downloads/file.pdf");
        assert!(!on_download(&downloads, DownloadEvent::Requested { url: theirs, destination: &mut elsewhere }));
        assert_eq!(downloads.lock().unwrap().len(), 1);
    }

    #[test]
    fn a_second_click_while_the_same_file_downloads_is_refused() {
        let downloads = Downloads::default();
        let url: Url = "http://127.0.0.1:8771/api/exports/x/a.csv".parse().unwrap();
        let mut to = PathBuf::from("/nonexistent/a.csv");
        assert!(on_download(&downloads, DownloadEvent::Requested { url: url.clone(), destination: &mut to }));
        let mut again = PathBuf::from("/nonexistent/a-2.csv");
        assert!(!on_download(&downloads, DownloadEvent::Requested { url: url.clone(), destination: &mut again }));
        on_download(&downloads, DownloadEvent::Finished { url: url.clone(), path: None, success: false });
        // once the first has finished, the file can be downloaded again
        assert!(on_download(&downloads, DownloadEvent::Requested { url, destination: &mut again }));
    }

    #[test]
    fn a_finished_download_is_forgotten() {
        let downloads = Downloads::default();
        let url: Url = "http://127.0.0.1:8771/api/exports/x/a.csv".parse().unwrap();
        let mut to = PathBuf::from("/nonexistent/a.csv");
        on_download(&downloads, DownloadEvent::Requested { url: url.clone(), destination: &mut to });
        // a failed download reveals nothing, and is still taken off the list
        on_download(&downloads, DownloadEvent::Finished { url, path: None, success: false });
        assert!(downloads.lock().unwrap().is_empty());
    }
}
