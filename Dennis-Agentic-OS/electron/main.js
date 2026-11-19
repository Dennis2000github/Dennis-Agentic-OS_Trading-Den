const path = require("node:path");
const { spawn } = require("node:child_process");
const {
  app, BrowserWindow, dialog, shell,
} = require("electron");

const ROOT = path.resolve(__dirname, "..");
const PORT = Number(process.env.DENNIS_PORT || 8080);
const BASE_URL = "http://127.0.0.1:" + PORT + "/";

let mainWindow = null;
let serverProcess = null;
let startedServer = false;

function delay(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function backendReady(timeoutMs) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    try {
      const response = await fetch(BASE_URL, { signal: AbortSignal.timeout(1200) });
      if (response.ok && (await response.text()).includes("DENNIS Agentic OS")) return true;
    } catch (_) {}
    await delay(300);
  }
  return false;
}

async function startBackend() {
  if (await backendReady(500)) return;
  const python = process.env.PYTHON_BIN || "python3";
  serverProcess = spawn(python, [path.join(ROOT, "app.py"), String(PORT)], {
    cwd: ROOT,
    env: { ...process.env, DENNIS_DESKTOP: "1" },
    stdio: "inherit",
  });
  startedServer = true;
  serverProcess.on("error", (error) => {
    dialog.showErrorBox("DENNIS backend failed to start", error.message);
  });
  if (!(await backendReady(45000))) {
    throw new Error("DENNIS did not start at " + BASE_URL +
      ". Check Python and whether port " + PORT + " is available.");
  }
}

function externalHttpsOnly(contents) {
  contents.on("will-navigate", (event, target) => {
    const localAppPage = target.startsWith(BASE_URL) || target.startsWith("file://");
    if (!localAppPage && !target.startsWith("https://")) event.preventDefault();
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) shell.openExternal(url);
    return { action: "deny" };
  });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1500, height: 980, minWidth: 960, minHeight: 640,
    title: "DENNIS Agentic OS", backgroundColor: "#060913",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  externalHttpsOnly(mainWindow.webContents);
  mainWindow.loadURL(BASE_URL);
  mainWindow.on("closed", () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  try {
    await startBackend();
    createMainWindow();
  } catch (error) {
    dialog.showErrorBox("Could not launch DENNIS", error.message);
    app.quit();
  }
});
app.on("before-quit", () => {
  if (startedServer && serverProcess && !serverProcess.killed) serverProcess.kill("SIGTERM");
});
app.on("window-all-closed", () => app.quit());
