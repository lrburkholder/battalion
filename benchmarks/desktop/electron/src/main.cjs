const { app, BrowserWindow, ipcMain, session } = require("electron/main");
const fs = require("node:fs");
const path = require("node:path");

const argument = (name) => {
  const prefix = `--${name}=`;
  const value = process.argv.find((item) => item.startsWith(prefix));
  return value ? path.resolve(value.slice(prefix.length)) : null;
};

const isAutomatedRun = Boolean(argument("trace") || argument("screenshot"));
const automatedProfile = isAutomatedRun
  ? path.join(app.getPath("temp"), `battalion-electron-benchmark-${process.pid}`)
  : null;

if (automatedProfile) {
  app.setPath("userData", automatedProfile);
  app.setPath("sessionData", automatedProfile);
}

const readInputs = () => {
  const root = path.join(__dirname, "..", "benchmark-input");
  return {
    fixture: JSON.parse(fs.readFileSync(path.join(root, "fixture.json"), "utf8")),
    scenario: JSON.parse(fs.readFileSync(path.join(root, "scenario.json"), "utf8")),
  };
};

const createWindow = () => {
  const window = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 760,
    minHeight: 520,
    backgroundColor: "#0d1420",
    icon: path.join(__dirname, "..", "assets", "app-icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  window.removeMenu();
  window.loadFile(path.join(__dirname, "renderer", "index.html"));
  return window;
};

app.whenReady().then(() => {
  session.defaultSession.setPermissionCheckHandler(() => false);
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: /^(https?|wss?):/i.test(details.url) });
  });
  ipcMain.handle("benchmark:load", () => readInputs());

  const window = createWindow();
  ipcMain.once("benchmark:complete", async (_event, trace) => {
    try {
      if (trace?.fixture_id !== "BTN-37-desktop-v1" || trace?.framework !== "electron") {
        app.exit(2);
        return;
      }
      const tracePath = argument("trace");
      if (tracePath) {
        fs.mkdirSync(path.dirname(tracePath), { recursive: true });
        fs.writeFileSync(tracePath, `${JSON.stringify(trace, null, 2)}\n`, "utf8");
      }
      const screenshotPath = argument("screenshot");
      if (screenshotPath) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        const image = await window.webContents.capturePage();
        fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
        fs.writeFileSync(screenshotPath, image.toPNG());
      }
      if (isAutomatedRun) app.quit();
    } catch (error) {
      console.error("Failed to record benchmark evidence:", error);
      app.exit(3);
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", () => {
  if (automatedProfile) {
    try {
      fs.rmSync(automatedProfile, { recursive: true, force: true });
    } catch {
      // Chromium can briefly retain cache handles during shutdown; temp cleanup
      // is best-effort and never changes the benchmark outcome.
    }
  }
});
