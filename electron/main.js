const path = require("node:path");
const { spawn } = require("node:child_process");
const {
  app, BrowserWindow, WebContentsView, dialog, ipcMain, shell,
} = require("electron");

const ROOT = path.resolve(__dirname, "..");
const PORT = Number(process.env.DENNIS_PORT || 8080);
const BASE_URL = "http://127.0.0.1:" + PORT + "/";
const TOOLBAR_HEIGHT = 64;
const WEB_MODELS = {
  chatgpt: { name: "ChatGPT", url: "https://chatgpt.com/" },
  gemini: { name: "Gemini", url: "https://gemini.google.com/" },
  claude: { name: "Claude", url: "https://claude.ai/" },
  deepseek: { name: "DeepSeek", url: "https://chat.deepseek.com/" },
  copilot: { name: "Microsoft Copilot", url: "https://copilot.microsoft.com/" },
  perplexity: { name: "Perplexity", url: "https://www.perplexity.ai/" },
  grok: { name: "Grok", url: "https://grok.com/" },
  qwen: { name: "Qwen Chat", url: "https://chat.qwen.ai/" },
  kimi: { name: "Kimi", url: "https://www.kimi.com/" },
  mistral: { name: "Le Chat", url: "https://chat.mistral.ai/" },
  poe: { name: "Poe", url: "https://poe.com/" }
};

let mainWindow = null;
let workspaceWindow = null;
let serverProcess = null;
let startedServer = false;
const providerViews = new Map();
let activeProvider = null;
let workspaceReady = Promise.resolve();

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
  mainWindow.on("closed", () => {
    mainWindow = null;
    if (workspaceWindow && !workspaceWindow.isDestroyed()) workspaceWindow.close();
  });
}

function setProviderBounds(view) {
  if (!workspaceWindow || workspaceWindow.isDestroyed()) return;
  const bounds = workspaceWindow.getContentBounds();
  view.setBounds({
    x: 0, y: TOOLBAR_HEIGHT, width: bounds.width,
    height: Math.max(100, bounds.height - TOOLBAR_HEIGHT),
  });
}

function createProviderView(id) {
  const provider = WEB_MODELS[id];
  const view = new WebContentsView({
    webPreferences: {
      partition: "persist:dennis-web-" + id,
      contextIsolation: true, nodeIntegration: false,
      sandbox: true, webSecurity: true,
    },
  });
  view.webContents.setBackgroundThrottling(false);
  view.webContents.on("will-navigate", (event, target) => {
    if (!target.startsWith("https://")) event.preventDefault();
  });
  view.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) {
      setImmediate(() => {
        if (!view.webContents.isDestroyed()) view.webContents.loadURL(url).catch(() => {});
      });
    }
    return { action: "deny" };
  });
  view.webContents.on("did-navigate", (_event, url) => {
    if (workspaceWindow && !workspaceWindow.isDestroyed())
      workspaceWindow.webContents.send("web-model:navigated", { id, url });
  });
  view.webContents.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
    if (isMainFrame && workspaceWindow && !workspaceWindow.isDestroyed())
      workspaceWindow.webContents.send("web-model:load-error", { id, code, description, url });
  });
  view.webContents.loadURL(provider.url).catch(() => {});
  providerViews.set(id, view);
  return view;
}

async function selectProvider(id) {
  const provider = WEB_MODELS[id];
  if (!provider) throw new Error("Unknown Web Model provider.");
  if (!workspaceWindow || workspaceWindow.isDestroyed()) createWorkspaceWindow();
  let view = providerViews.get(id);
  if (!view || view.webContents.isDestroyed()) view = createProviderView(id);
  if (activeProvider && activeProvider !== id) {
    const oldView = providerViews.get(activeProvider);
    if (oldView) workspaceWindow.contentView.removeChildView(oldView);
  }
  activeProvider = id;
  await workspaceReady;
  workspaceWindow.contentView.addChildView(view);
  setProviderBounds(view);
  workspaceWindow.setTitle("DENNIS Web Model Dashboard — " + provider.name);
  workspaceWindow.webContents.send("web-model:selected", { id, name: provider.name });
  workspaceWindow.show();
  workspaceWindow.focus();
  view.webContents.focus();
}

function createWorkspaceWindow() {
  workspaceWindow = new BrowserWindow({
    width: 1360, height: 900, minWidth: 820, minHeight: 600,
    title: "DENNIS Web Model Dashboard",
    parent: mainWindow || undefined,
    backgroundColor: "#080d1b",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
    },
  });
  externalHttpsOnly(workspaceWindow.webContents);
  workspaceReady = new Promise((resolve, reject) => {
    workspaceWindow.webContents.once("did-finish-load", resolve);
    workspaceWindow.webContents.once("did-fail-load", (_event, code, description) =>
      reject(new Error("Web Model workspace failed to load: " + description + " (" + code + ")"))
    );
  });
  workspaceWindow.loadFile(path.join(__dirname, "web-models.html"));
  workspaceWindow.on("resize", () => {
    const view = providerViews.get(activeProvider);
    if (view) setProviderBounds(view);
  });
  workspaceWindow.on("closed", () => {
    for (const view of providerViews.values()) {
      if (!view.webContents.isDestroyed()) view.webContents.close();
    }
    providerViews.clear();
    activeProvider = null;
    workspaceWindow = null;
  });
}

ipcMain.handle("web-model:open", async (_event, id) => { await selectProvider(id); return true; });
ipcMain.handle("web-model:select", async (_event, id) => { await selectProvider(id); return true; });
ipcMain.handle("web-model:action", (_event, action) => {
  const view = providerViews.get(activeProvider);
  if (!view) return false;
  if (action === "back" && view.webContents.navigationHistory.canGoBack())
    view.webContents.navigationHistory.goBack();
  else if (action === "forward" && view.webContents.navigationHistory.canGoForward())
    view.webContents.navigationHistory.goForward();
  else if (action === "reload") view.webContents.reload();
  return true;
});

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
