const { contextBridge, ipcRenderer } = require("electron/renderer");

contextBridge.exposeInMainWorld("benchmark", Object.freeze({
  load: () => ipcRenderer.invoke("benchmark:load"),
  complete: (trace) => ipcRenderer.send("benchmark:complete", trace),
}));

