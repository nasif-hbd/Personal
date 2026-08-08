/**
 * Mindora desktop shell (Windows, macOS, Linux).
 *
 * The app is served over http://127.0.0.1 on a random free port rather than
 * loaded from file://. That is not incidental: a file:// page has the origin
 * "null", and the YouTube IFrame Player API refuses the postMessage handshake
 * from a null origin — every video would still play but watch progress and
 * auto-complete would silently stop working. A loopback origin also counts as
 * a secure context, so the service worker and localStorage behave exactly as
 * they do on the website.
 *
 * Security posture: no node integration in the renderer, context isolation on,
 * navigation locked to the local origin, and every external link handed to the
 * user's real browser.
 */
const { app, BrowserWindow, shell, Menu, nativeTheme } = require("electron");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const APP_DIR = path.join(__dirname, "app");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".webmanifest": "application/manifest+json",
};

let origin = null;
let mainWindow = null;

function startServer() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      let rel = decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname);
      if (rel === "/") rel = "/index.html";

      // Resolve, then confirm the result is still inside APP_DIR. Without this
      // check a request for /../../etc/passwd would escape the bundle.
      const file = path.normalize(path.join(APP_DIR, rel));
      if (!file.startsWith(APP_DIR + path.sep) && file !== APP_DIR) {
        res.writeHead(403).end("forbidden");
        return;
      }
      fs.readFile(file, (err, body) => {
        if (err) {
          res.writeHead(404).end("not found");
          return;
        }
        res.writeHead(200, {
          "Content-Type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream",
          "Cache-Control": "no-cache",
        });
        res.end(body);
      });
    });
    server.on("error", reject);
    // Port 0 asks the OS for any free port, so two copies of Mindora — or
    // anything else already sitting on a fixed port — can never collide.
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve(`http://127.0.0.1:${port}`);
    });
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 380,
    minHeight: 560,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#08060f" : "#f7f7fb",
    title: "Mindora",
    // Keeps the traffic lights on macOS but drops the heavy title bar, so the
    // app's own header reads as the top of the window.
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    autoHideMenuBar: process.platform !== "darwin",
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.loadURL(`${origin}/index.html`);

  // target="_blank" and window.open go to the real browser. Anything that
  // opened here would be a chromeless window with no back button.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  // Top-level navigation stays on the bundled app. YouTube still loads —
  // that happens in an iframe, which this does not touch.
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(origin)) {
      event.preventDefault();
      if (/^https?:/.test(url)) shell.openExternal(url);
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      label: "File",
      submenu: [isMac ? { role: "close" } : { role: "quit" }],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        {
          label: "Mindora on the web",
          click: () => shell.openExternal("https://nasif-hbd.github.io/Personal/"),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// Second launches focus the running window instead of starting a rival copy
// that would fight over the same localStorage.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    origin = await startServer();
    buildMenu();
    createWindow();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
