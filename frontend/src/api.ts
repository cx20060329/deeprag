/** BCM-RAG API Client */

const BASE = "http://localhost:8000";

export interface SearchRequest {
  query: string;
  top_k?: number;
  enable_llm?: boolean;
  quality?: "fast" | "accurate";
}

export interface ChunkResult {
  chunk_id: string;
  chunk_type: string;
  module: string;
  section_path: string;
  section_title: string;
  text_preview: string;
  score: number;
  sources: string[];
  has_table: boolean;
  has_image: boolean;
}

export interface SearchResponse {
  query: string;
  intent: Record<string, unknown>;
  merged: ChunkResult[];
  evidence: string;
  answer: string | null;
  usage: Record<string, number> | null;
  model: string;
  retrieval_time_ms: number;
}

export interface HealthResponse {
  status: string;
  loaded: boolean;
  stats: Record<string, unknown>;
}

export interface LLMConfig {
  provider: string;
  api_key?: string;
  model?: string;
}

export async function checkHealth(): Promise<HealthResponse> {
  const r = await fetch(`${BASE}/health`);
  return r.json();
}

export async function search(req: SearchRequest): Promise<SearchResponse> {
  const r = await fetch(`${BASE}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: req.query,
      top_k: req.top_k ?? 10,
      enable_llm: req.enable_llm ?? false,
    }),
  });
  return r.json();
}

export async function configureLLM(config: LLMConfig): Promise<{ status: string; model: string }> {
  const r = await fetch(`${BASE}/llm/configure`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!r.ok) {
    const err = await r.json();
    throw new Error(err.detail || "LLM config failed");
  }
  return r.json();
}

export async function searchStream(
  req: SearchRequest,
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (err: string) => void
): Promise<void> {
  try {
    const r = await fetch(`${BASE}/search/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: req.query,
        top_k: req.top_k ?? 10,
        enable_llm: true,
      }),
    });

    if (!r.ok) {
      const err = await r.json();
      onError(err.detail || "Stream failed");
      return;
    }

    const reader = r.body?.getReader();
    if (!reader) { onError("No response body"); return; }

    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value, { stream: true });
      const lines = text.split("\n");
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const token = line.slice(6);
          if (token === "[DONE]") { onDone(); return; }
          if (token.startsWith("[ERROR]")) { onError(token.slice(8)); return; }
          onToken(token);
        }
      }
    }
    onDone();
  } catch (e) {
    onError(String(e));
  }
}
