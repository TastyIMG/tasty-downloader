import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

const styles = `
.tasty-r2-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}
.tasty-r2-modal {
  background: #353535;
  border: 1px solid #555;
  border-radius: 8px;
  min-width: 520px;
  max-width: 90vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  color: #ddd;
  font-family: sans-serif;
}
.tasty-r2-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #555;
}
.tasty-r2-header h2 {
  margin: 0;
  font-size: 16px;
}
.tasty-r2-close {
  background: none;
  border: none;
  color: #aaa;
  font-size: 20px;
  cursor: pointer;
}
.tasty-r2-body {
  overflow-y: auto;
  padding: 4px 0 8px;
}
.tasty-r2-section {
  border-bottom: 1px solid #444;
}
.tasty-r2-section:last-child {
  border-bottom: none;
}
.tasty-r2-section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 16px;
  background: #3a3a3a;
  border: none;
  color: #eee;
  font-size: 13px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}
.tasty-r2-section-header:hover {
  background: #404040;
}
.tasty-r2-chevron {
  color: #999;
  font-size: 11px;
  width: 12px;
}
.tasty-r2-section-title {
  flex: 1;
  text-transform: capitalize;
}
.tasty-r2-section-meta {
  color: #999;
  font-size: 12px;
  font-weight: normal;
}
.tasty-r2-section-body {
  display: none;
}
.tasty-r2-section.open .tasty-r2-section-body {
  display: block;
}
.tasty-r2-row {
  display: grid;
  grid-template-columns: 1fr 110px;
  gap: 8px;
  align-items: center;
  padding: 8px 16px 8px 36px;
  border-bottom: 1px solid #3a3a3a;
  font-size: 13px;
}
.tasty-r2-row:last-child {
  border-bottom: none;
}
.tasty-r2-row:hover {
  background: #3a3a3a;
}
.tasty-r2-info {
  min-width: 0;
}
.tasty-r2-for {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tasty-r2-filename {
  color: #999;
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tasty-r2-btn {
  background: #444;
  border: 1px solid #666;
  color: #eee;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.tasty-r2-btn:hover:not(:disabled) {
  background: #555;
}
.tasty-r2-btn:disabled {
  opacity: 0.5;
  cursor: wait;
}
.tasty-r2-btn.done {
  opacity: 1;
  cursor: default;
  background: #2f4f3a;
  border-color: #4a7;
  color: #cfc;
}
.tasty-r2-action {
  min-width: 110px;
}
.tasty-r2-progress {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tasty-r2-progress-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.tasty-r2-progress-bar {
  flex: 1;
  height: 6px;
  background: #222;
  border-radius: 3px;
  overflow: hidden;
}
.tasty-r2-progress-fill {
  height: 100%;
  background: #6a9;
  width: 0%;
  transition: width 0.15s ease;
}
.tasty-r2-progress-label {
  font-size: 10px;
  color: #aaa;
  text-align: center;
  line-height: 1.2;
}
.tasty-r2-cancel {
  background: #533;
  border: 1px solid #855;
  color: #fcc;
  padding: 2px 6px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 10px;
  flex-shrink: 0;
}
.tasty-r2-cancel:hover {
  background: #644;
}
.tasty-r2-empty {
  padding: 24px 16px;
  text-align: center;
  color: #888;
}
.tasty-r2-error {
  padding: 8px 16px;
  color: #f88;
  font-size: 12px;
}
`;

function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

class TastyR2Modal {
  constructor() {
    this.overlay = null;
    this.body = null;
    this.errorEl = null;
    this.downloading = new Set();
    this.downloaded = new Set();
    this.abortControllers = new Map();
    this.openSections = new Set();
  }

  ensureStyles() {
    if (document.getElementById("tasty-r2-styles")) return;
    const el = document.createElement("style");
    el.id = "tasty-r2-styles";
    el.textContent = styles;
    document.head.appendChild(el);
  }

  open() {
    this.ensureStyles();
    this.overlay = document.createElement("div");
    this.overlay.className = "tasty-r2-overlay";
    this.overlay.innerHTML = `
      <div class="tasty-r2-modal">
        <div class="tasty-r2-header">
          <h2>Tasty Downloader</h2>
          <button class="tasty-r2-close" type="button">&times;</button>
        </div>
        <div class="tasty-r2-error"></div>
        <div class="tasty-r2-body">Loading...</div>
      </div>
    `;
    document.body.appendChild(this.overlay);
    this.body = this.overlay.querySelector(".tasty-r2-body");
    this.errorEl = this.overlay.querySelector(".tasty-r2-error");
    this.overlay.querySelector(".tasty-r2-close").onclick = () => this.close();
    this.overlay.addEventListener("click", (e) => {
      if (e.target === this.overlay) this.close();
    });
    this.refresh();
  }

  close() {
    for (const controller of this.abortControllers.values()) {
      controller.abort();
    }
    this.abortControllers.clear();
    if (this.overlay) {
      this.overlay.remove();
      this.overlay = null;
      this.openSections.clear();
    }
  }

  setError(msg) {
    if (this.errorEl) this.errorEl.textContent = msg || "";
  }

  async refresh() {
    this.setError("");
    try {
      const resp = await api.fetchApi("/tasty-r2/list");
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || "Failed to load registry");
      }
      if (!Array.isArray(data)) {
        throw new Error("Invalid registry response");
      }
      for (const item of data) {
        if (item.exists) this.downloaded.add(item.filename);
      }
      this.render(data);
    } catch (err) {
      this.body.innerHTML = `<div class="tasty-r2-empty">Failed to load list</div>`;
      this.setError(String(err));
    }
  }

  render(items) {
    if (!items.length) {
      this.body.innerHTML = `<div class="tasty-r2-empty">No models in registry.json</div>`;
      return;
    }

    const order = [
      "checkpoints", "unet", "diffusion_models", "loras", "vae", "clip",
      "clip_vision", "controlnet", "upscale_models", "embeddings", "hypernetworks",
    ];
    const grouped = new Map();
    for (const item of items) {
      const key = item.save_path || "other";
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(item);
    }

    const categories = [...grouped.keys()].sort((a, b) => {
      const ai = order.indexOf(a);
      const bi = order.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });

    if (!this.openSections.size) {
      for (const cat of categories) {
        this.openSections.add(cat);
      }
    }

    this.body.innerHTML = "";
    for (const category of categories) {
      const sectionItems = grouped.get(category);
      const isOpen = this.openSections.has(category);

      const section = document.createElement("div");
      section.className = `tasty-r2-section${isOpen ? " open" : ""}`;

      const header = document.createElement("button");
      header.type = "button";
      header.className = "tasty-r2-section-header";
      header.innerHTML = `
        <span class="tasty-r2-chevron">${isOpen ? "▼" : "▶"}</span>
        <span class="tasty-r2-section-title">${category}</span>
        <span class="tasty-r2-section-meta">${sectionItems.length}</span>
      `;
      header.onclick = () => {
        if (this.openSections.has(category)) {
          this.openSections.delete(category);
        } else {
          this.openSections.add(category);
        }
        section.classList.toggle("open");
        header.querySelector(".tasty-r2-chevron").textContent = section.classList.contains("open") ? "▼" : "▶";
      };

      const body = document.createElement("div");
      body.className = "tasty-r2-section-body";
      for (const item of sectionItems) {
        body.appendChild(this.renderRow(item));
      }

      section.append(header, body);
      this.body.appendChild(section);
    }
  }

  renderRow(item) {
    const row = document.createElement("div");
    row.className = "tasty-r2-row";

    const info = document.createElement("div");
    info.className = "tasty-r2-info";

    const forModel = document.createElement("div");
    forModel.className = "tasty-r2-for";
    forModel.textContent = item.for_model || item.filename;
    forModel.title = item.for_model || item.filename;

    const filename = document.createElement("div");
    filename.className = "tasty-r2-filename";
    filename.textContent = item.filename;
    filename.title = item.filename;

    info.append(forModel, filename);

    const action = document.createElement("div");
    action.className = "tasty-r2-action";
    action.appendChild(this.createActionButton(item.filename));
    row.append(info, action);
    return row;
  }

  createActionButton(filename) {
    const btn = document.createElement("button");
    btn.className = "tasty-r2-btn";
    btn.type = "button";

    if (this.downloaded.has(filename)) {
      btn.textContent = "Downloaded";
      btn.classList.add("done");
      btn.disabled = true;
      return btn;
    }

    btn.textContent = "Download";
    btn.disabled = this.downloading.has(filename);
    btn.onclick = () => {
      const action = btn.parentElement;
      if (action) this.download(filename, action);
    };
    return btn;
  }

  createProgress(actionEl, onCancel) {
    actionEl.innerHTML = `
      <div class="tasty-r2-progress">
        <div class="tasty-r2-progress-top">
          <div class="tasty-r2-progress-bar">
            <div class="tasty-r2-progress-fill"></div>
          </div>
          <button class="tasty-r2-cancel" type="button">Cancel</button>
        </div>
        <div class="tasty-r2-progress-label">Starting...</div>
      </div>
    `;
    const cancelBtn = actionEl.querySelector(".tasty-r2-cancel");
    cancelBtn.onclick = (e) => {
      e.stopPropagation();
      onCancel?.();
    };
    return {
      fill: actionEl.querySelector(".tasty-r2-progress-fill"),
      label: actionEl.querySelector(".tasty-r2-progress-label"),
      cancelBtn,
    };
  }

  updateProgress(progressEl, event) {
    if (!progressEl?.fill || !progressEl?.label) return;
    const downloaded = Number(event.downloaded) || 0;
    const total = Number(event.total) || 0;
    const percent = event.percent != null
      ? Math.max(0, Math.min(100, Number(event.percent)))
      : (total ? Math.min(100, Math.round((downloaded * 100) / total)) : null);

    if (percent != null) {
      progressEl.fill.style.width = `${percent}%`;
      progressEl.label.textContent = total
        ? `${percent}% · ${formatBytes(downloaded)} / ${formatBytes(total)}`
        : `${percent}%`;
      return;
    }

    progressEl.fill.style.width = "100%";
    progressEl.label.textContent = formatBytes(downloaded);
  }

  restoreDownloadButton(actionEl, filename) {
    if (!actionEl?.isConnected) return;
    actionEl.innerHTML = "";
    actionEl.appendChild(this.createActionButton(filename));
  }

  parseDownloadEvent(line) {
    if (!line.trim()) return null;
    try {
      return JSON.parse(line);
    } catch {
      return null;
    }
  }

  handleDownloadEvent(event, progressEl) {
    if (!event) return false;
    if (event.type === "progress") {
      this.updateProgress(progressEl, event);
      return false;
    }
    if (event.type === "done") {
      if (progressEl?.fill) progressEl.fill.style.width = "100%";
      if (progressEl?.label) progressEl.label.textContent = "Done";
      return true;
    }
    if (event.type === "error") {
      throw new Error(event.error || "Download failed");
    }
    return false;
  }

  async download(filename, actionEl) {
    if (this.downloading.has(filename)) return;
    this.downloading.add(filename);

    const abortController = new AbortController();
    this.abortControllers.set(filename, abortController);

    const progressEl = this.createProgress(actionEl, () => {
      abortController.abort();
      if (progressEl.label) progressEl.label.textContent = "Cancelling...";
      if (progressEl.cancelBtn) progressEl.cancelBtn.disabled = true;
    });
    this.setError("");

    let succeeded = false;
    let cancelled = false;
    try {
      // Same path helper as /tasty-r2/list — do not call api.apiURL() bare.
      const resp = await api.fetchApi("/tasty-r2/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename }),
        signal: abortController.signal,
      });

      if (!resp.ok) {
        const text = await resp.text();
        let message = text || "Download failed";
        try {
          const data = JSON.parse(text);
          if (data.error) message = data.error;
        } catch {
          // use raw response text
        }
        throw new Error(message);
      }

      if (!resp.body) {
        throw new Error("Download failed: empty response");
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let doneReceived = false;

      try {
        while (true) {
          if (abortController.signal.aborted) {
            await reader.cancel();
            cancelled = true;
            break;
          }

          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (this.handleDownloadEvent(this.parseDownloadEvent(line), progressEl)) {
              doneReceived = true;
            }
          }
        }
      } catch (err) {
        if (err?.name === "AbortError" || abortController.signal.aborted) {
          cancelled = true;
        } else {
          throw err;
        }
      }

      if (!cancelled) {
        buffer += decoder.decode();
        for (const line of buffer.split("\n")) {
          if (this.handleDownloadEvent(this.parseDownloadEvent(line), progressEl)) {
            doneReceived = true;
          }
        }

        if (!doneReceived) {
          throw new Error("Download ended unexpectedly");
        }
        this.downloaded.add(filename);
        succeeded = true;
      }
    } catch (err) {
      if (err?.name === "AbortError" || abortController.signal.aborted) {
        cancelled = true;
      } else {
        this.setError(`${filename}: ${err.message || err}`);
      }
    } finally {
      this.downloading.delete(filename);
      this.abortControllers.delete(filename);
      if (succeeded) {
        await new Promise((r) => setTimeout(r, 800));
      } else if (cancelled && progressEl.label) {
        progressEl.label.textContent = "Cancelled";
        await new Promise((r) => setTimeout(r, 500));
      }
      this.restoreDownloadButton(actionEl, filename);
    }
  }
}

const modal = new TastyR2Modal();

function openTastyDownloader() {
  modal.open();
}

app.registerExtension({
  name: "Comfy.TastyR2Downloader",
  init() {
    if (document.getElementById("tasty-r2-styles")) return;
    const el = document.createElement("style");
    el.id = "tasty-r2-styles";
    el.textContent = styles;
    document.head.appendChild(el);
  },
  commands: [
    {
      id: "Comfy.TastyR2Downloader.open",
      label: "Open Tasty Downloader",
      menubarLabel: "Tasty Downloader",
      icon: "pi pi-download",
      function: openTastyDownloader,
    },
  ],
  menuCommands: [
    {
      path: ["Tasty"],
      commands: ["Comfy.TastyR2Downloader.open"],
    },
    {
      path: ["Extensions", "Tasty R2 Downloader"],
      commands: ["Comfy.TastyR2Downloader.open"],
    },
  ],
  actionBarButtons: [
    {
      icon: "icon-[lucide--download]",
      label: "Tasty Downloader",
      tooltip: "Download models from Tasty R2 registry",
      onClick: openTastyDownloader,
    },
  ],
});
