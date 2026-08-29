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
  grid-template-columns: 1fr 90px;
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

class TastyR2Modal {
  constructor() {
    this.overlay = null;
    this.body = null;
    this.errorEl = null;
    this.downloading = new Set();
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
          <h2>Tasty Models</h2>
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
    if (this.overlay) {
      this.overlay.remove();
      this.overlay = null;
      this.openSections.clear();
    }
  }

  setError(msg) {
    this.errorEl.textContent = msg || "";
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

    const btn = document.createElement("button");
    btn.className = "tasty-r2-btn";
    btn.type = "button";
    btn.textContent = "Download";
    btn.disabled = this.downloading.has(item.filename);
    btn.onclick = () => this.download(item.filename, btn);

    row.append(info, btn);
    return row;
  }

  async download(filename, btn) {
    if (this.downloading.has(filename)) return;
    this.downloading.add(filename);
    btn.disabled = true;
    btn.textContent = "Downloading...";
    this.setError("");

    try {
      const resp = await api.fetchApi("/tasty-r2/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || "Download failed");
      }
      btn.textContent = "Download";
      btn.disabled = false;
    } catch (err) {
      this.setError(`${filename}: ${err.message || err}`);
      btn.disabled = false;
      btn.textContent = "Download";
    } finally {
      this.downloading.delete(filename);
    }
  }
}

const modal = new TastyR2Modal();

function openTastyModels() {
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
      label: "Open Tasty Models",
      menubarLabel: "Tasty Models",
      icon: "pi pi-download",
      function: openTastyModels,
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
      label: "Tasty Models",
      tooltip: "Download models from Tasty R2 registry",
      onClick: openTastyModels,
    },
  ],
});
