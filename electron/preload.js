const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("dennisDesktop", {
  openWebModel: (id) => ipcRenderer.invoke("web-model:open", id),
  selectWebModel: (id) => ipcRenderer.invoke("web-model:select", id),
  webModelAction: (action) => ipcRenderer.invoke("web-model:action", action),
  onWebModelSelected: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("web-model:selected", listener);
    return () => ipcRenderer.removeListener("web-model:selected", listener);
  },
  onWebModelNavigated: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("web-model:navigated", listener);
    return () => ipcRenderer.removeListener("web-model:navigated", listener);
  },
  onWebModelLoadError: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on("web-model:load-error", listener);
    return () => ipcRenderer.removeListener("web-model:load-error", listener);
  }
});
