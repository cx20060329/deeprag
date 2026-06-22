/** BCM-RAG Interactive Test Frontend — with LLM Answer Generation */

import {
  checkHealth, search, searchStream, configureLLM,
  type SearchResponse,
} from "./api";

// --- DOM ---
const statusEl = document.getElementById("status")!;
const statsEl = document.getElementById("stats")!;
const queryInput = document.getElementById("queryInput") as HTMLTextAreaElement;
const searchBtn = document.getElementById("searchBtn")!;
const modeSelect = document.getElementById("modeSelect") as HTMLSelectElement;
const agenticToggle = document.getElementById("agenticToggle") as HTMLInputElement;
const llmToggle = document.getElementById("llmToggle") as HTMLInputElement;
const resultsArea = document.getElementById("results")!;
const evidenceChain = document.getElementById("evidenceChain")!;
const toolCalls = document.getElementById("toolCalls")!;
const reflections = document.getElementById("reflections")!;
const confidenceBar = document.getElementById("confidenceBar")!;
const confidenceFill = confidenceBar.querySelector(".fill") as HTMLElement;
const confidenceLabel = confidenceBar.querySelector(".label")!;
const llmConfigBtn = document.getElementById("llmConfigBtn")!;
const llmConfigPanel = document.getElementById("llmConfigPanel")!;
const llmProvider = document.getElementById("llmProvider") as HTMLSelectElement;
const llmApiKey = document.getElementById("llmApiKey") as HTMLInputElement;
const llmModel = document.getElementById("llmModel") as HTMLInputElement;
const llmSave = document.getElementById("llmSave")!;
const llmStatus = document.getElementById("llmStatus")!;
const answerArea = document.getElementById("answerArea")!;

let connected = false;
let llmConfigured = false;
let currentController: AbortController | null = null;

// --- Init ---
async function init() {
  await ping();
  bindEvents();
  loadLLMConfig();
}

async function ping() {
  try {
    const h = await checkHealth();
    connected = h.loaded;
    statusEl.textContent = "● Connected";
    statusEl.className = "status online";
    const s = h.stats || {};
    const llmOk = s.llm_configured ? "LLM" : "No LLM";
    statsEl.textContent = `${s.graph_nodes || "?"} nodes | ${s.chunks || "?"} chunks | ${llmOk}`;
  } catch {
    connected = false;
    statusEl.textContent = "● Disconnected (start: python -m api.main)";
    statusEl.className = "status offline";
  }
}

function loadLLMConfig() {
  const saved = localStorage.getItem("bcm-llm-config");
  if (saved) {
    try {
      const cfg = JSON.parse(saved);
      if (cfg.provider) llmProvider.value = cfg.provider;
      if (cfg.api_key) llmApiKey.value = cfg.api_key;
      if (cfg.model) llmModel.value = cfg.model;
    } catch { /* ignore */ }
  }
  updateModelPlaceholder();
}

function updateModelPlaceholder() {
  const defaults: Record<string, string> = {
    zhipu: "glm-4-flash",
    ark: "ep-20250616115653-bxlm6",
    deepseek: "deepseek-v4-flash",
  };
  const p = llmProvider.value;
  llmModel.placeholder = defaults[p] || "model-name";
  if (!llmModel.value) llmModel.value = defaults[p] || "";
}

llmProvider.addEventListener("change", updateModelPlaceholder);

function bindEvents() {
  searchBtn.addEventListener("click", doSearch);
  queryInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSearch();
    }
  });

  document.querySelectorAll(".btn[data-query]").forEach((btn) => {
    btn.addEventListener("click", () => {
      queryInput.value = (btn as HTMLElement).dataset.query || "";
      doSearch();
    });
  });

  llmConfigBtn.addEventListener("click", () => {
    llmConfigPanel.classList.toggle("hidden");
  });

  llmSave.addEventListener("click", async () => {
    await saveLLMConfig();
  });
}

// --- LLM Config ---
async function saveLLMConfig() {
  const config = {
    provider: llmProvider.value,
    api_key: llmApiKey.value || undefined,
    model: llmModel.value || undefined,
  };

  localStorage.setItem("bcm-llm-config", JSON.stringify(config));
  llmStatus.textContent = "Configuring...";
  llmStatus.className = "llm-status";

  try {
    // Only send non-empty values
    const body: Record<string, string> = { provider: config.provider };
    if (config.api_key) body.api_key = config.api_key;
    if (config.model) body.model = config.model;

    const result = await configureLLM(body as any);
    llmConfigured = true;
    llmStatus.textContent = `✓ ${result.model}`;
    llmStatus.className = "llm-status ok";
    llmToggle.checked = true;
    await ping();
  } catch (e: any) {
    llmConfigured = false;
    llmStatus.textContent = `✗ ${e.message}`;
    llmStatus.className = "llm-status err";
    llmToggle.checked = false;
  }
}

// --- Search ---
async function doSearch() {
  const query = queryInput.value.trim();
  if (!query) return;

  // Cancel previous stream
  if (currentController) {
    currentController.abort();
    currentController = null;
  }

  const useLlm = llmToggle.checked && llmConfigured;
  resultsArea.innerHTML = '<div class="loading">Searching...</div>';
  answerArea.innerHTML = useLlm
    ? '<div class="loading">Generating answer...</div>'
    : '';
  clearAnalysis();

  if (useLlm) {
    await doStreamSearch(query);
  } else {
    await doNormalSearch(query);
  }

  ping();
}

async function doNormalSearch(query: string) {
  try {
    const resp = await search({ query, top_k: 10, enable_llm: false });
    renderResults(resp, false);
    answerArea.innerHTML = `
      <div class="llm-off">
        <p>LLM summary disabled. Configure LLM (top-right) or enable toggle.</p>
      </div>`;
  } catch (e) {
    resultsArea.innerHTML = `<div class="error">Error: ${e}</div>`;
  }
}

async function doStreamSearch(query: string) {
  // First do retrieval
  let retrievalResult: SearchResponse;
  try {
    retrievalResult = await search({ query, top_k: 10, enable_llm: false });
  } catch (e) {
    resultsArea.innerHTML = `<div class="error">Retrieval error: ${e}</div>`;
    return;
  }
  renderResults(retrievalResult, true);

  // Then stream LLM answer
  answerArea.innerHTML = '<div class="llm-answer streaming"><div class="llm-header">AI Answer (streaming...)</div><div class="llm-content" id="llmContent"></div></div>';
  const llmContent = document.getElementById("llmContent")!;
  let fullAnswer = "";

  currentController = new AbortController();
  const signal = currentController.signal;

  await searchStream(
    { query, top_k: 10, enable_llm: true },
    (token) => {
      if (signal.aborted) return;
      fullAnswer += token;
      llmContent.textContent = fullAnswer;
      llmContent.scrollTop = llmContent.scrollHeight;
    },
    () => {
      currentController = null;
      const header = document.querySelector(".llm-header");
      if (header) {
        header.textContent = `AI Answer (${retrievalResult.model || "LLM"})`;
        header.classList.remove("streaming");
      }
    },
    (err) => {
      currentController = null;
      llmContent.textContent = `Error: ${err}`;
    }
  );
}

// --- Render ---
function renderResults(resp: SearchResponse, agentic: boolean) {
  const merged = resp.merged || [];
  const evidence = resp.evidence || "";

  let html = "";

  if (merged.length > 0) {
    const top = merged[0];
    html += `<div class="result-header">`;
    html += `<span class="module-tag">${top.module}</span>`;
    html += `<span class="section-tag">§${top.section_path}</span>`;
    html += `<span class="score-tag">score: ${top.score.toFixed(4)}</span>`;
    html += `<span class="time-tag">${resp.retrieval_time_ms.toFixed(0)}ms</span>`;
    html += `</div>`;
  }

  html += `<div class="evidence-block"><h4>Evidence</h4>`;
  html += `<pre class="evidence-text">${escapeHtml(evidence.slice(0, 2000))}</pre>`;
  html += `</div>`;

  html += `<div class="chunks-block"><h4>Top Chunks</h4>`;
  merged.slice(0, 5).forEach((m, i) => {
    html += `<div class="chunk-item">`;
    html += `<span class="chunk-rank">#${i + 1}</span>`;
    html += `<span class="chunk-module">${m.module}</span>`;
    html += `<span class="chunk-section">§${m.section_path} ${m.section_title}</span>`;
    html += `<span class="chunk-score">${m.score.toFixed(4)}</span>`;
    html += `<div class="chunk-text">${escapeHtml(m.text_preview || "").slice(0, 200)}</div>`;
    html += `</div>`;
  });
  html += `</div>`;

  resultsArea.innerHTML = html;
  updateAnalysis(resp, agentic);
}

function updateAnalysis(resp: SearchResponse, agentic: boolean) {
  const merged = resp.merged || [];

  evidenceChain.innerHTML = merged.slice(0, 8).map((m, i) => `
    <div class="evidence-item">
      <span class="ev-rank">${i + 1}</span>
      <div>
        <strong>${m.module}</strong> §${m.section_path}
        <div class="ev-sources">${(m.sources || []).join(", ")}</div>
      </div>
      <span class="ev-score">${m.score.toFixed(3)}</span>
    </div>
  `).join("");

  const sources = [...new Set(merged.flatMap((m) => m.sources || []))];
  toolCalls.innerHTML = `
    <div class="tool-item"><span class="tool-icon">🔍</span><div><strong>Hybrid Search</strong><div>Dense(BGE) + BM25 RRF</div></div></div>
    <div class="tool-item"><span class="tool-icon">🕸</span><div><strong>Graph</strong><div>${sources.includes("graph") ? "active" : "inactive"}</div></div></div>
    <div class="tool-item"><span class="tool-icon">📋</span><div><strong>Rules</strong><div>122 rules</div></div></div>
    <div class="tool-item"><span class="tool-icon">🔀</span><div><strong>State Machine</strong><div>4 states, 7 transitions</div></div></div>
    ${agentic ? `<div class="tool-item"><span class="tool-icon">🧠</span><div><strong>Agentic</strong><div>Decompose→Verify</div></div></div>` : ""}
  `;

  const intent = resp.intent || {};
  const qtype = (intent as any).question_type || "factual";
  reflections.innerHTML = `
    <div class="reflection-item"><span>${qtype === "reasoning" || qtype === "diagnostic" ? "✅" : "ℹ"}</span>Type: <strong>${qtype}</strong></div>
    <div class="reflection-item"><span>${merged.length >= 3 ? "✅" : "⚠"}</span>Chunks: <strong>${merged.length}</strong></div>
    <div class="reflection-item"><span>${resp.retrieval_time_ms < 500 ? "✅" : "⚠"}</span>Latency: <strong>${resp.retrieval_time_ms.toFixed(0)}ms</strong></div>
  `;

  const conf = computeConfidence(resp, agentic);
  confidenceFill.style.width = `${conf * 100}%`;
  confidenceLabel.textContent = `${Math.round(conf * 100)}%`;
  confidenceFill.style.background = conf > 0.7 ? "#22c55e" : conf > 0.4 ? "#f59e0b" : "#ef4444";
}

function computeConfidence(resp: SearchResponse, agentic: boolean): number {
  const merged = resp.merged || [];
  if (merged.length === 0) return 0;
  let conf = 0.5;
  const allSources = new Set(merged.flatMap((m) => m.sources || []));
  conf += allSources.size * 0.1;
  if (agentic) conf += 0.1;
  if (merged.length > 0 && merged[0].score > 0.02) conf += 0.1;
  const topMod = merged[0]?.module;
  if (merged.filter((m) => m.module === topMod).length >= 3) conf += 0.1;
  return Math.min(conf, 0.95);
}

function clearAnalysis() {
  evidenceChain.innerHTML = "";
  toolCalls.innerHTML = "";
  reflections.innerHTML = "";
  confidenceFill.style.width = "0%";
  confidenceLabel.textContent = "0%";
}

function escapeHtml(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

init();
