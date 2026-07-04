/* 外贸助手前端逻辑 */
const App = {
  config: null,
  contacts: [],
  currentTalker: null,
  currentName: "",

  // ─── 工具函数 ───
  async api(method, path, body) {
    const opts = { method, headers: { "Content-Type": "application/json" } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || "请求失败");
    return json.data;
  },

  toast(msg, duration = 3000) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.style.display = "block";
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { el.style.display = "none"; }, duration);
  },

  tsToStr(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  },

  // ─── 初始化 ───
  async init() {
    await this.refreshStatus();
    await this.loadVersion();
  },

  async refreshStatus() {
    try {
      const st = await this.api("GET", "/api/status");
      const left = document.getElementById("status-left");
      if (st.wechat_ready) {
        left.textContent = "微信已连接 · " + (st.db_storage_path || "");
        left.style.color = "var(--success)";
      } else if (st.wechat_error) {
        left.textContent = "微信: " + st.wechat_error.slice(0, 60);
        left.style.color = "var(--danger)";
      } else if (st.db_storage_path) {
        left.textContent = "微信目录已设置，未加载密钥";
        left.style.color = "var(--warning)";
      } else {
        left.textContent = "未检测到微信，请到「设置」配置";
        left.style.color = "var(--text-muted)";
      }
      // ASR 状态
      if (this.config) {
        const asrEngine = this.config.asr?.engine || "未配置";
        document.getElementById("status-right").textContent = "ASR: " + asrEngine;
      }
    } catch (e) {
      document.getElementById("status-left").textContent = "状态获取失败";
    }
  },

  async loadVersion() {
    try {
      const v = await this.api("GET", "/api/version");
      const el = document.getElementById("status-center");
      if (v.found) {
        const tag = v.supports_memory_scan ? "支持内存扫描" : "需加载 all_keys.json";
        el.textContent = "微信 " + v.version + " · " + tag;
      } else {
        el.textContent = "微信版本未检测";
      }
    } catch (e) {
      document.getElementById("status-center").textContent = "";
    }
  },

  // ─── 联系人 ───
  async loadContacts() {
    const list = document.getElementById("contact-list");
    list.innerHTML = '<div class="loading"><span class="spinner"></span> 加载中...</div>';
    try {
      const data = await this.api("GET", "/api/contacts");
      this.contacts = data.contacts || [];
      this.renderContacts("");
      this.toast("已加载 " + this.contacts.length + " 个联系人");
    } catch (e) {
      list.innerHTML = '<div class="empty-hint">加载失败：' + e.message + "</div>";
    }
  },

  filterContacts() {
    const q = document.getElementById("search-input").value.trim().toLowerCase();
    this.renderContacts(q);
  },

  renderContacts(query) {
    const list = document.getElementById("contact-list");
    if (this.contacts.length === 0) {
      list.innerHTML = '<div class="empty-hint">无联系人，点击 ↻ 刷新</div>';
      return;
    }
    const filtered = query
      ? this.contacts.filter(c => (c.name || c.talker || "").toLowerCase().includes(query))
      : this.contacts;
    list.innerHTML = filtered.map(c => {
      const initial = (c.name || c.talker || "?").charAt(0).toUpperCase();
      const active = c.talker === this.currentTalker ? " active" : "";
      return '<div class="contact-item' + active + '" onclick="App.selectContact(\'' + c.talker + '\',\'' + (c.name || c.talker).replace(/'/g, "\\'") + '\')">' +
        '<div class="contact-avatar">' + initial + "</div>" +
        '<div class="contact-info"><div class="contact-name">' + (c.name || c.talker) + "</div>" +
        '<div class="contact-time">' + (c.last_time_str || "") + "</div></div></div>";
    }).join("");
  },

  async selectContact(talker, name) {
    this.currentTalker = talker;
    this.currentName = name;
    document.getElementById("chat-title").textContent = name;
    document.getElementById("analyze-btn").disabled = false;
    this.renderContacts(document.getElementById("search-input").value.trim().toLowerCase());
    await this.reloadChat();
  },

  async reloadChat() {
    if (!this.currentTalker) return;
    const msgs = document.getElementById("chat-messages");
    msgs.innerHTML = '<div class="loading"><span class="spinner"></span> 加载聊天记录...</div>';
    try {
      const filter = document.getElementById("time-filter").value;
      let timeFrom = null, timeTo = null;
      if (filter) {
        const days = parseInt(filter);
        const d = new Date(Date.now() - days * 86400000);
        timeFrom = d.toISOString().slice(0, 10);
      }
      const qs = new URLSearchParams({ limit: "200" });
      if (timeFrom) qs.set("time_from", timeFrom);
      const data = await this.api("GET", "/api/chats/" + encodeURIComponent(this.currentTalker) + "?" + qs);
      this.renderChat(data);
    } catch (e) {
      msgs.innerHTML = '<div class="empty-hint">加载失败：' + e.message + "</div>";
    }
  },

  renderChat(data) {
    const msgs = document.getElementById("chat-messages");
    const list = data.messages || [];
    if (list.length === 0) {
      msgs.innerHTML = '<div class="empty-hint">无聊天记录</div>';
      return;
    }
    msgs.innerHTML = list.map(m => {
      if (m.type === "voice") {
        const text = m.transcription || m.content || "[语音]";
        return this._bubble(m.is_sender, '<span class="msg-voice">[语音] ' + text + "</span>", m.time_str);
      }
      if (m.type === "system") {
        return '<div class="msg-system">' + (m.content || "") + "</div>";
      }
      return this._bubble(m.is_sender, m.content || "", m.time_str);
    }).join("");
    msgs.scrollTop = msgs.scrollHeight;
  },

  _bubble(isSender, content, time) {
    const cls = isSender ? "self" : "other";
    return '<div class="msg-row ' + cls + '"><div><div class="msg-bubble">' + content + "</div>" +
      '<div class="msg-time">' + (time || "") + "</div></div></div>";
  },

  // ─── AI 分析 ───
  async analyzeCurrent() {
    if (!this.currentTalker) return;
    const btn = document.getElementById("analyze-btn");
    const panel = document.getElementById("analysis-content");
    btn.disabled = true;
    panel.innerHTML = '<div class="loading"><span class="spinner"></span> AI 分析中...</div>';
    try {
      const result = await this.api("POST", "/api/analyze", { talker: this.currentTalker, limit: 100 });
      this.renderAnalysis(result);
    } catch (e) {
      panel.innerHTML = '<div class="empty-hint">分析失败：' + e.message + "</div>";
    } finally {
      btn.disabled = false;
    }
  },

  renderAnalysis(r) {
    const panel = document.getElementById("analysis-content");
    let html = "";
    if (r.summary) {
      html += '<div class="analysis-card"><h3>总结</h3><p>' + r.summary + "</p></div>";
    }
    if (r.customer_mood) {
      html += '<div class="analysis-card"><h3>客户情绪</h3><p><span class="mood-tag">' + r.customer_mood + "</span></p></div>";
    }
    if (r.needs && r.needs.length) {
      html += '<div class="analysis-card"><h3>需求</h3>' + r.needs.map(n => '<div class="need-item">' + (n.description || n.text || JSON.stringify(n)) + "</div>").join("") + "</div>";
    }
    if (r.done_items && r.done_items.length) {
      html += '<div class="analysis-card"><h3>已完成</h3>' + r.done_items.map(n => '<div class="done-item">' + (n.description || n.text || JSON.stringify(n)) + "</div>").join("") + "</div>";
    }
    if (r.todo_items && r.todo_items.length) {
      html += '<div class="analysis-card"><h3>待办</h3>' + r.todo_items.map(n => '<div class="todo-item">' + (n.description || n.text || JSON.stringify(n)) + "</div>").join("") + "</div>";
    }
    if (r.provider) {
      html += '<div class="analysis-card"><h3>分析引擎</h3><p>' + r.provider + "</p></div>";
    }
    panel.innerHTML = html || '<div class="empty-hint">无分析结果</div>';
  },

  // ─── 设置弹窗 ───
  async openSettings() {
    if (!this.config) {
      try { this.config = await this.api("GET", "/api/config"); }
      catch (e) { this.toast("加载配置失败: " + e.message); return; }
    }
    this._fillSettingsForm();
    document.getElementById("settings-modal").style.display = "flex";
    // 加载版本信息到密钥 tab
    this._refreshKeyVersion();
  },

  closeSettings() {
    document.getElementById("settings-modal").style.display = "none";
  },

  switchTab(name) {
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
    document.querySelectorAll(".tab-pane").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  },

  _fillSettingsForm() {
    const w = this.config.wechat || {};
    document.getElementById("cfg-db-path").value = w.db_storage_path || "";
    document.getElementById("cfg-process-name").value = w.process_name || "";
    document.getElementById("cfg-keys-json").value = w.all_keys_json_path || "";
    document.getElementById("cfg-raw-key").value = w.raw_key || "";
    // ASR
    const asr = this.config.asr || {};
    document.getElementById("cfg-asr-engine").value = asr.engine || "volcengine";
    this.renderAsrFields();
    // LLM
    this.renderLlmFields();
  },

  async _refreshKeyVersion() {
    try {
      const v = await this.api("GET", "/api/version");
      const el = document.getElementById("key-version-info");
      if (v.found) {
        const tag = v.supports_memory_scan ? "✓ 支持内存扫描" : "⚠ 需加载 all_keys.json（4.1+ 内存扫描失效）";
        el.textContent = "微信版本 " + v.version + " · " + tag;
      } else {
        el.textContent = "未检测到微信版本";
      }
    } catch (e) { /* ignore */ }
  },

  async detectWechat() {
    try {
      const r = await this.api("GET", "/api/detect");
      if (r.found) {
        document.getElementById("cfg-db-path").value = r.db_storage_path;
        document.getElementById("cfg-process-name").value = "微信";
        this.config.wechat = this.config.wechat || {};
        this.config.wechat.db_storage_path = r.db_storage_path;
        this.config.wechat.process_name = "微信";
        this.toast("检测到: " + r.db_storage_path);
        await this.refreshStatus();
      } else {
        this.toast("未找到微信数据目录，请手动填写");
      }
    } catch (e) { this.toast("检测失败: " + e.message); }
  },

  async loadKeysJson() {
    const path = document.getElementById("cfg-keys-json").value.trim();
    if (!path) { this.toast("请填写 all_keys.json 路径"); return; }
    const el = document.getElementById("scan-result");
    el.textContent = "加载中..."; el.className = "result-hint";
    try {
      const r = await this.api("POST", "/api/keys/load_json", { path });
      this.config.wechat = this.config.wechat || {};
      this.config.wechat.all_keys_json_path = path;
      el.textContent = "密钥总数: " + r.total_keys + " 匹配 .db: " + r.matched_dbs;
      el.className = "result-hint ok";
      this.toast("密钥加载成功");
      await this.refreshStatus();
    } catch (e) {
      el.textContent = "失败: " + e.message; el.className = "result-hint err";
    }
  },

  async scanKeys() {
    const el = document.getElementById("scan-result");
    el.textContent = "扫描中（可能需输入密码）..."; el.className = "result-hint";
    try {
      const r = await this.api("POST", "/api/keys/scan");
      if (r.ok === false) throw new Error(r.error);
      el.textContent = "密钥总数: " + r.total_keys + " 匹配 .db: " + r.matched_dbs;
      el.className = "result-hint ok";
      this.toast("扫描完成");
      await this.refreshStatus();
    } catch (e) {
      el.textContent = "扫描失败: " + e.message; el.className = "result-hint err";
    }
  },

  async setRawKey() {
    const key = document.getElementById("cfg-raw-key").value.trim();
    if (!key) { this.toast("请填写 raw key"); return; }
    try {
      await this.api("POST", "/api/keys/raw", { raw_key: key });
      this.config.wechat = this.config.wechat || {};
      this.config.wechat.raw_key = key;
      this.toast("raw key 已保存");
      await this.refreshStatus();
    } catch (e) { this.toast("保存失败: " + e.message); }
  },

  // ─── ASR 字段 ───
  renderAsrFields() {
    const engine = document.getElementById("cfg-asr-engine").value;
    const asr = this.config.asr || {};
    const cfg = asr[engine] || {};
    const container = document.getElementById("asr-fields");
    let html = "";
    if (engine === "volcengine") {
      html = this._field("app_id", "App ID", cfg.app_id) + this._field("access_token", "Access Token", cfg.access_token);
    } else if (engine === "mlx_whisper") {
      html = this._field("model", "模型", cfg.model || "mlx-community/whisper-medium-mlx-8bit")
        + this._field("language", "语言（空=自动）", cfg.language || "")
        + this._field("initial_prompt", "初始 prompt（热词）", cfg.initial_prompt || "", "text");
    } else if (engine === "openai") {
      html = this._field("api_key", "API Key", cfg.api_key) + this._field("model", "模型", cfg.model || "gpt-4o-mini-transcribe");
    }
    container.innerHTML = html;
  },

  _field(key, label, val, type = "text") {
    return '<div class="form-row"><label>' + label + '</label><input type="' + type + '" id="asr-' + key + '" value="' + (val || "") + '"></div>';
  },

  // ─── LLM 多厂商字段 ───
  renderLlmFields() {
    const llm = this.config.llm || {};
    // 兼容旧 schema
    if (!llm.providers && llm.deepseek) {
      llm.providers = { deepseek: llm.deepseek };
      llm.enabled = ["deepseek"];
    }
    const enabled = llm.enabled || ["deepseek"];
    const providers = llm.providers || {};
    const all = ["deepseek", "openai", "claude", "gemini", "qwen"];
    const labels = { deepseek: "DeepSeek", openai: "OpenAI", claude: "Claude", gemini: "Gemini", qwen: "通义千问" };

    // 启用厂商 checkbox
    const cbGroup = document.getElementById("llm-enabled");
    cbGroup.innerHTML = all.map(id =>
      '<label><input type="checkbox" value="' + id + '" ' + (enabled.includes(id) ? "checked" : "") + ' onchange="App.onLlmToggle()"> ' + labels[id] + "</label>"
    ).join("");

    // 聚合厂商下拉
    this._updateAggregator(enabled);

    // 各厂商配置卡片（用 provider 前缀的唯一 ID）
    const pc = document.getElementById("llm-providers");
    pc.innerHTML = all.map(id => {
      const p = providers[id] || {};
      const style = enabled.includes(id) ? "" : "display:none;";
      let fields = this._pfield(id, "api_key", "API Key", p.api_key);
      if (id !== "gemini" && id !== "claude") {
        fields += this._pfield(id, "base_url", "Base URL", p.base_url || "");
      }
      fields += this._pfield(id, "model", "模型", p.model || "");
      return '<div class="provider-card" id="provider-' + id + '" style="' + style + '"><h4>' + labels[id] + "</h4>" + fields + "</div>";
    }).join("");
  },

  _pfield(provider, key, label, val) {
    return '<div class="form-row"><label>' + label + '</label><input type="text" id="llm-' + provider + "-" + key + '" value="' + (val || "") + '"></div>';
  },

  onLlmToggle() {
    const checked = Array.from(document.querySelectorAll("#llm-enabled input:checked")).map(c => c.value);
    const all = ["deepseek", "openai", "claude", "gemini", "qwen"];
    all.forEach(id => {
      const el = document.getElementById("provider-" + id);
      if (el) el.style.display = checked.includes(id) ? "" : "none";
    });
    this._updateAggregator(checked);
  },

  _updateAggregator(enabled) {
    const row = document.getElementById("aggregator-row");
    const sel = document.getElementById("cfg-aggregator");
    const labels = { deepseek: "DeepSeek", openai: "OpenAI", claude: "Claude", gemini: "Gemini", qwen: "通义千问" };
    if (enabled.length > 1) {
      row.style.display = "";
      const cur = this.config.llm?.aggregator || "";
      sel.innerHTML = '<option value="">不聚合（取首个成功结果）</option>' +
        enabled.map(id => '<option value="' + id + '" ' + (cur === id ? "selected" : "") + ">" + labels[id] + "</option>").join("");
    } else {
      row.style.display = "none";
    }
  },

  // ─── 保存设置 ───
  async saveSettings() {
    // 收集表单值
    const w = this.config.wechat || {};
    w.db_storage_path = document.getElementById("cfg-db-path").value.trim();
    w.process_name = document.getElementById("cfg-process-name").value.trim();
    w.all_keys_json_path = document.getElementById("cfg-keys-json").value.trim();
    w.raw_key = document.getElementById("cfg-raw-key").value.trim();
    this.config.wechat = w;

    // ASR
    const asrEngine = document.getElementById("cfg-asr-engine").value;
    const asr = this.config.asr || {};
    asr.engine = asrEngine;
    asr[asrEngine] = asr[asrEngine] || {};
    if (asrEngine === "volcengine") {
      asr[asrEngine].app_id = this._val("asr-app_id");
      asr[asrEngine].access_token = this._val("asr-access_token");
    } else if (asrEngine === "mlx_whisper") {
      asr[asrEngine].model = this._val("asr-model");
      asr[asrEngine].language = this._val("asr-language");
      asr[asrEngine].initial_prompt = this._val("asr-initial_prompt");
    } else if (asrEngine === "openai") {
      asr[asrEngine].api_key = this._val("asr-api_key");
      asr[asrEngine].model = this._val("asr-model");
    }
    this.config.asr = asr;

    // LLM
    const enabled = Array.from(document.querySelectorAll("#llm-enabled input:checked")).map(c => c.value);
    const aggregator = document.getElementById("cfg-aggregator").value;
    const providers = {};
    const all = ["deepseek", "openai", "claude", "gemini", "qwen"];
    all.forEach(id => {
      const p = {};
      p.api_key = this._val("llm-" + id + "-api_key");
      if (id !== "gemini" && id !== "claude") {
        p.base_url = this._val("llm-" + id + "-base_url");
      }
      p.model = this._val("llm-" + id + "-model");
      providers[id] = p;
    });
    this.config.llm = { enabled, aggregator, providers };

    try {
      await this.api("POST", "/api/config", this.config);
      this.toast("设置已保存");
      this.closeSettings();
      await this.refreshStatus();
    } catch (e) {
      this.toast("保存失败: " + e.message);
    }
  },

  _val(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : "";
  },

  // ─── 待办 ───
  async openTodos() {
    document.getElementById("todos-modal").style.display = "flex";
    const body = document.getElementById("todos-body");
    body.innerHTML = '<div class="loading"><span class="spinner"></span> 加载中...</div>';
    try {
      const data = await this.api("GET", "/api/todos");
      const todos = data.todos || [];
      if (todos.length === 0) {
        body.innerHTML = '<div class="empty-hint">暂无待办</div>';
        return;
      }
      body.innerHTML = todos.map(t =>
        '<div class="todo-item" style="cursor:pointer" onclick="App.doneTodo(\'' + (t.id || t.talker || "") + '\')">' +
        (t.content || t.description || JSON.stringify(t)) + "</div>"
      ).join("");
    } catch (e) {
      body.innerHTML = '<div class="empty-hint">加载失败: ' + e.message + "</div>";
    }
  },

  closeTodos() { document.getElementById("todos-modal").style.display = "none"; },

  async doneTodo(id) {
    try {
      await this.api("POST", "/api/todos/" + encodeURIComponent(id) + "/done");
      this.toast("已标记完成");
      this.openTodos();
    } catch (e) { this.toast("失败: " + e.message); }
  },

  // ─── MCP 信息 ───
  openMcpInfo() {
    document.getElementById("mcp-modal").style.display = "flex";
    document.getElementById("mcp-info").textContent =
      "启动方式: 外贸助手.app/Contents/MacOS/TradeTools --mcp\n" +
      "协议: JSON-RPC 2.0 over stdio\n" +
      "工具: search_chats / list_contacts / get_chat_history\n" +
      "      transcribe_voice / analyze_customer / natural_language_search";
  },
  closeMcpInfo() { document.getElementById("mcp-modal").style.display = "none"; },

  async quitApp() {
    try { await this.api("POST", "/api/shutdown", {}); } catch (e) { /* 忽略 */ }
    document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#6b7280;font-family:sans-serif;">已退出，可关闭此页面</div>';
  },
};

// 启动
App.init();
