const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld(
    "racpClient",
    Object.freeze({
        settings: () => ipcRenderer.invoke("client:settings"),
        updateSettings: (value) =>
            ipcRenderer.invoke("client:update_settings", value),
        info: () => ipcRenderer.invoke("client:info"),
        overview: () => ipcRenderer.invoke("client:overview"),
        refresh: () => ipcRenderer.invoke("client:refresh"),
        exit: () => ipcRenderer.invoke("client:exit"),
        folder: () => ipcRenderer.invoke("client:folder"),
        ca: () => ipcRenderer.invoke("client:ca"),
        connection: () => ipcRenderer.invoke("client:connection-file"),
        enrollConnection: (value) =>
            ipcRenderer.invoke("client:enroll-connection", value),
        enroll: (value) => ipcRenderer.invoke("client:enroll", value),
        start: () => ipcRenderer.invoke("client:start"),
        status: () => ipcRenderer.invoke("client:status"),
        stop: () => ipcRenderer.invoke("client:stop"),
        loginSettings: () => ipcRenderer.invoke("client:login-settings"),
        setLogin: (enabled) => ipcRenderer.invoke("client:set-login", enabled),
    }),
);
