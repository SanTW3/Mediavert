const { contextBridge, ipcRenderer, webUtils } = require('electron');
const fs = require('fs');
const path = require('path');
const { FORMAT_LIST, EXT, TARGETS, KIND_LABEL } = require('../lib/formats');

const EVENTS = ['job:update', 'files:add', 'shelf:pointer-left', 'settings:changed', 'guide:open', 'update:status', 'morph:play', 'morph:fade', 'window:enter', 'window:prepare', 'shelf:morph-started'];

contextBridge.exposeInMainWorld('api', {
  formats: { FORMAT_LIST, EXT, TARGETS, KIND_LABEL },
  platform: { mica: process.argv.includes('--mediavert-mica') },

  pathForFile: (file) => webUtils.getPathForFile(file),
  stat: (paths) =>
    paths
      .map((p) => {
        try {
          const s = fs.statSync(p);
          return s.isFile() ? { path: p, name: path.basename(p), size: s.size } : null;
        } catch {
          return null;
        }
      })
      .filter(Boolean),

  convert: (req) => ipcRenderer.invoke('jobs:add', req),
  listJobs: () => ipcRenderer.invoke('jobs:list'),
  clearJobs: (sources) => ipcRenderer.invoke('jobs:clear', sources),

  reveal: (p) => ipcRenderer.send('file:reveal', p),
  openFile: (p) => ipcRenderer.send('file:open', p),
  openOutput: () => ipcRenderer.send('output:open'),
  startDrag: (p) => ipcRenderer.send('file:drag-out', p),

  getSettings: () => ipcRenderer.invoke('settings:get'),
  setSettings: (patch) => ipcRenderer.invoke('settings:set', patch),
  pickFolder: () => ipcRenderer.invoke('dialog:pick-folder'),

  openMain: (paths, rect) => ipcRenderer.send('main:open', paths || [], rect || null),
  morphLanded: () => ipcRenderer.send('morph:landed'),
  morphAlmost: () => ipcRenderer.send('morph:almost'),
  setShelfRegion: (rect) => ipcRenderer.send('shelf:region', rect),

  getVersion: () => ipcRenderer.invoke('app:version'),
  updateStatus: () => ipcRenderer.invoke('update:status'),
  checkUpdates: () => ipcRenderer.invoke('update:check'),
  installUpdate: () => ipcRenderer.invoke('update:install'),

  on: (channel, cb) => {
    if (EVENTS.includes(channel)) ipcRenderer.on(channel, (_e, data) => cb(data));
  },
});
