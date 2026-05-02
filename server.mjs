import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { execSync, exec, execFile } from "child_process";
import express from "express";
import cors from "cors";
import * as fflate from "fflate";
import { path7za } from "7zip-bin";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = process.env.PORT || 17580;
const INDEX_PATH = path.join(__dirname, "data", "code-index.json");
const EMBEDDING_PATH = path.join(__dirname, "data", "embeddings.npy");
const EMBEDDING_META_PATH = path.join(__dirname, "data", "embeddings-meta.json");
const INFERNO_ROOT = process.env.INFERNO_ROOT || "/mnt/d/Projects/infernoStart01";
const LLM_API_BASE = process.env.LLM_API_BASE || "https://api.avision-gb10.org/v1";
const LLM_API_KEY = process.env.LLM_API_KEY || "";
const LLM_MODEL = process.env.LLM_MODEL || "qwen3.6:35b-a3b-200k";

// --- Runtime LLM config (overridable via API) ---
let llmConfig = {
  baseUrl: LLM_API_BASE,
  apiKey: LLM_API_KEY,
  model: LLM_MODEL,
};
const LLM_CONFIG_PATH = path.join(__dirname, "data", "llm-config.json");
try { llmConfig = { ...llmConfig, ...JSON.parse(fs.readFileSync(LLM_CONFIG_PATH, "utf-8")) }; } catch {}

const app = express();
app.use(cors({
  origin: ["http://localhost:17580", "http://127.0.0.1:17580", "https://bug.avision-gb10.org"],
  credentials: true,
}));
app.use(express.json({ limit: "50mb" }));

// --- Index loading ---
let indexCache = null;
function getIndex() {
  if (indexCache) return indexCache;
  indexCache = JSON.parse(fs.readFileSync(INDEX_PATH, "utf-8"));
  return indexCache;
}

// --- Tokenize & Search ---
function tokenize(text) {
  return new Set(
    text.toLowerCase().replace(/[^a-z0-9_]+/g, " ").split(/\s+/).filter(Boolean)
  );
}

function scoreFile(file, queryTokens) {
  let score = 0;
  const pathTokens = tokenize(file.path);
  for (const qt of queryTokens) {
    if (pathTokens.has(qt)) score += 10;
  }
  // NEW: file-level symbols are now aggregated from chunks
  const symbols = file.chunkSymbols || file.symbols || [];
  for (const sym of symbols) {
    const symLower = sym.toLowerCase();
    for (const qt of queryTokens) {
      if (symLower === qt) score += 20;
      else if (symLower.includes(qt)) score += 8;
    }
  }
  // NEW: score against chunk names (function names, struct names, etc.)
  const chunkNames = (file.chunkNames || []).map(n => n.toLowerCase());
  for (const qt of queryTokens) {
    for (const cn of chunkNames) {
      if (cn === qt) score += 15;
      else if (cn.includes(qt)) score += 6;
    }
  }
  return score;
}

// --- API: Upload & extract log (text, zip, or 7z) ---
const TMP_DIR = path.join(__dirname, "tmp");

function isTextFile(name) {
  const textExts = new Set([".log", ".txt", ".csv", ".json", ".xml", ".out", ".err", ".sys", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".toml", ".md"]);
  return textExts.has(path.extname(name).toLowerCase()) || !path.extname(name);
}

function readTextFilesFromDir(dir) {
  let combined = [];
  let extractedCount = 0;
  try {
    const entries = fs.readdirSync(dir, { recursive: true, withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      try {
        const data = fs.readFileSync(path.join(entry.parentPath || dir, entry.name));
        if (data.length === 0) continue;
        if (!isTextFile(entry.name)) {
          // Skip likely binary
          const head = data.slice(0, 512).toString("utf-8");
          if (/[\x00-\x08\x0E-\x1F]/.test(head)) continue;
        }
        const text = new TextDecoder("utf-8", { fatal: false }).decode(data);
        combined.push(`=== ${entry.name} ===\n${text}`);
        extractedCount++;
      } catch {}
    }
  } catch {}
  return { combined: combined.join("\n\n"), extractedCount };
}

app.post("/api/upload-log", express.raw({ type: ["application/zip", "application/x-zip-compressed", "application/x-7z-compressed", "application/octet-stream"], limit: "100mb" }), (req, res) => {
  try {
    const buf = req.body;
    const rawName = req.headers["x-filename"] || "upload";
    const fname = decodeURIComponent(rawName);
    const ext = path.extname(fname).toLowerCase();

    if (ext === ".zip") {
      const files = fflate.unzipSync(new Uint8Array(buf));
      let combined = [], extractedCount = 0;
      for (const [name, data] of Object.entries(files)) {
        const basename = name.split("/").pop();
        if (!basename || data.length === 0) continue;
        try {
          if (!isTextFile(basename)) {
            const head = data.slice(0, 512).toString("utf-8");
            if (/[\x00-\x08\x0E-\x1F]/.test(head)) continue;
          }
          const text = new TextDecoder("utf-8", { fatal: false }).decode(data);
          combined.push(`=== ${basename} ===\n${text}`);
          extractedCount++;
        } catch {}
      }
      res.json({ content: combined.join("\n\n"), filesExtracted: extractedCount });

    } else if (ext === ".7z") {
      fs.mkdirSync(TMP_DIR, { recursive: true });
      const extractDir = fs.mkdtempSync(path.join(TMP_DIR, "7z-"));
      const archivePath = path.join(extractDir, "archive.7z");
      fs.writeFileSync(archivePath, buf);
      try {
        execSync(`"${path7za}" x "${archivePath}" -o"${extractDir}/out" -y`, { stdio: "pipe" });
        const result = readTextFilesFromDir(path.join(extractDir, "out"));
        res.json({ content: result.combined, filesExtracted: result.extractedCount });
      } finally {
        fs.rmSync(extractDir, { recursive: true, force: true });
      }

    } else {
      // Plain text
      const text = new TextDecoder("utf-8").decode(buf);
      res.json({ content: text, filesExtracted: 1 });
    }
  } catch (err) {
    res.status(500).json({ error: `Upload failed: ${err.message}` });
  }
});

// --- API: Search code ---
app.post("/api/search", (req, res) => {
  try {
    const { query, maxResults = 20 } = req.body;
    if (!query) return res.status(400).json({ error: "query is required" });

    const index = getIndex();
    const queryTokens = tokenize(query);

    const scored = index.files
      .map((file) => ({ ...file, score: scoreFile(file, queryTokens) }))
      .filter((file) => file.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, maxResults);

    const results = scored.map((file) => {
      const fullPath = path.join(index.sourceRoot, file.path);
      let fullContent = file.snippet;
      try { fullContent = fs.readFileSync(fullPath, "utf-8"); } catch {}
      return {
        path: file.path,
        score: file.score,
        lines: file.lines,
        symbols: file.symbols,
        content: fullContent,
      };
    });

    res.json({ query, results, totalScanned: index.totalFiles });
  } catch (err) {
    console.error("Search error:", err);
    res.status(500).json({ error: "Search failed" });
  }
});


// --- API: Semantic Search (RAG) ---
function hasEmbeddingIndex() {
  return fs.existsSync(EMBEDDING_PATH) && fs.existsSync(EMBEDDING_META_PATH);
}

function embedSearch(query, topK = 10) {
  return new Promise((resolve, reject) => {
    const script = path.join(__dirname, "scripts", "embed-search.py");
    const env = { ...process.env };
    if (process.env.VLLM_EMBED_URL) env.VLLM_EMBED_URL = process.env.VLLM_EMBED_URL;
    const python = process.env.EMBED_PYTHON || "python3";
    execFile(python, [script, query, "--top", String(topK)], { timeout: 30_000, maxBuffer: 50 * 1024 * 1024, env }, (err, stdout, stderr) => {
      if (err) {
        console.error("Embed search error:", stderr?.slice(0, 500));
        return reject(new Error(stderr?.slice(0, 200) || err.message));
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error("Failed to parse embed-search output"));
      }
    });
  });
}

app.post("/api/semantic-search", async (req, res) => {
  try {
    const { query, topK = 10 } = req.body;
    if (!query) return res.status(400).json({ error: "query is required" });
    if (!hasEmbeddingIndex()) return res.status(503).json({ error: "Embedding index not built yet" });

    const result = await embedSearch(query, topK);
    res.json(result);
  } catch (err) {
    console.error("Semantic search error:", err);
    res.status(500).json({ error: `Semantic search failed: ${err.message}` });
  }
});

// --- RAG: Hybrid Analyze with LLM ---
// Combines keyword search + semantic search for better code retrieval

function loadFileContent(fileEntry) {
  const fullPath = path.join(getIndex().sourceRoot, fileEntry.path);
  let content = "";
  try { content = fs.readFileSync(fullPath, "utf-8"); } catch {}
  return content;
}

/** Enrich file entries with aggregated chunk info for keyword scoring.
 *  Called once after index load to avoid repeated computation. */
function enrichFileEntries(files, chunks) {
  const chunksByFile = new Map();
  for (const c of chunks) {
    if (!chunksByFile.has(c.path)) chunksByFile.set(c.path, []);
    chunksByFile.get(c.path).push(c);
  }
  for (const f of files) {
    const fileChunks = chunksByFile.get(f.path) || [];
    // Aggregate all symbols from chunks
    const allSyms = new Set();
    const chunkNames = [];
    for (const c of fileChunks) {
      for (const s of c.symbols || []) allSyms.add(s);
      if (c.name && !c.name.match(/^(function|struct|enum|typedef|macro_group|prototype|block)_L/)) {
        chunkNames.push(c.name);
      }
    }
    f.chunkSymbols = [...allSyms];
    f.chunkNames = chunkNames;
  }
}

// Cache enriched flag
let _enriched = false;

async function hybridSearch(query, topK = 15) {
  const index = getIndex();

  // Enrich file entries with chunk data on first call
  if (!_enriched && index.chunks) {
    enrichFileEntries(index.files, index.chunks);
    _enriched = true;
  }

  const allResults = new Map(); // path -> result

  // 1. Keyword search (fast) — now scores against chunk names + aggregated symbols
  const queryTokens = tokenize(query);
  const keywordResults = index.files
    .map((file) => ({ ...file, score: scoreFile(file, queryTokens) }))
    .filter((file) => file.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);

  for (const r of keywordResults) {
    allResults.set(r.path, {
      path: r.path,
      score: r.score,
      source: "keyword",
      lines: r.lines,
      symbols: r.chunkSymbols || r.symbols || [],
      content: loadFileContent(r),
    });
  }

  // 2. Semantic search — now returns chunk-level results grouped by file
  if (hasEmbeddingIndex()) {
    try {
      const semResult = await embedSearch(query, topK);
      for (const r of semResult.results || []) {
        const existing = allResults.get(r.path);
        // Merge chunk info into existing or new result
        const chunkInfo = (r.chunks || []).map(c => ({
          name: c.name,
          type: c.type,
          startLine: c.startLine,
          endLine: c.endLine,
          content: c.content,
          score: c.score,
        }));
        if (existing) {
          existing.score = Math.max(existing.score, r.score * 100) + (existing.source === "keyword" ? 50 : 0);
          existing.source = "hybrid";
          // Append semantic chunks (don't overwrite keyword content)
          if (chunkInfo.length && !existing.semanticChunks) {
            existing.semanticChunks = chunkInfo;
          } else if (chunkInfo.length) {
            existing.semanticChunks.push(...chunkInfo);
          }
        } else {
          allResults.set(r.path, {
            path: r.path,
            score: r.score * 100,
            source: "semantic",
            lines: r.lines,
            symbols: r.symbols || [],
            content: loadFileContent(r),
            semanticChunks: chunkInfo,
          });
        }
      }
    } catch (err) {
      console.warn("Semantic search failed, using keyword only:", err.message);
    }
  }

  // Sort by combined score, return top results
  return [...allResults.values()]
    .sort((a, b) => b.score - a.score)
    .slice(0, topK);
}

// --- Phase 1: Rule-based log/steps analysis to extract search clues ---
// No LLM dependency — pure regex + domain knowledge

// Common English stop words to filter out from log identifiers
const STOP_WORDS = new Set([
  "the", "this", "that", "when", "then", "them", "they", "from", "with", "after",
  "before", "during", "about", "would", "could", "should", "which", "where", "there",
  "their", "being", "other", "under", "still", "every", "first", "second", "third",
  "also", "into", "over", "only", "just", "than", "been", "will", "back", "open",
  "close", "start", "stop", "while", "down", "next", "last", "long", "time",
]);

// Detect if input is a system log (has structured log patterns) or operation steps
function detectInputType(logContent) {
  if (!logContent) return "steps";
  const text = logContent.slice(0, 3000);
  const logSignals = [
    /\[\w+\]/,           // [MCU], [PCU], [ERR], [INFO]
    /\d{4}[-/]\d{2}[-/]\d{2}/,  // timestamps
    /\d{2}:\d{2}:\d{2}/,         // time HH:MM:SS
    /0x[0-9a-fA-F]{2,}/,        // hex addresses
    /ERR[_A-Z]+/,                // error codes like ERR_PAPER_JAM
    /#\d+\s/,                    // line numbers
    /at\s+0x/,                   // stack trace addresses
    /assert|fault|segfault|panic|abort/i,
    /error|warning|debug|info/i,
  ];
  let signalCount = 0;
  for (const pat of logSignals) {
    if (pat.test(text)) signalCount++;
  }
  return signalCount >= 2 ? "log" : "steps";
}

// Parse system log and extract structured clues
function parseSystemLog(logContent) {
  const clues = { queries: [], exactFiles: [], errorCodes: [], functionNames: [], modules: [] };
  const text = logContent || "";
  const lines = text.split("\n");

  for (const line of lines) {
    // 1. File:line references — "O_PrintFlow_av.c:1234" or "at file.c:line"
    const fileRefs = line.match(/([\w/.-]+\.(c|cpp|h|hpp)):(\d+)/g) || [];
    for (const ref of fileRefs) {
      const filePath = ref.replace(/:\d+$/, "");
      if (!filePath.includes("openssl") && !filePath.includes("ssl")) {
        clues.exactFiles.push(filePath);
      }
    }

    // 2. Bare file paths without line numbers
    const barePaths = line.match(/([\w/.-]+\.(c|cpp|h|hpp))\b/g) || [];
    for (const p of barePaths) {
      if (!p.includes("openssl") && !p.includes("ssl") && !p.includes("tls")) {
        clues.exactFiles.push(p);
      }
    }

    // 3. Error codes — ERR_PAPER_JAM_07, ERR_CODE, FAX_BOARD_ERROR
    const errCodes = line.match(/\b(ERR_[A-Z_0-9]+|PRN_[A-Z_0-9]+|NTF_[A-Z_0-9]+|FUNC_[A-Z_0-9]+|API_[A-Z_0-9]+)\b/g) || [];
    clues.errorCodes.push(...errCodes);

    // 4. Hex error/status codes — 0x80530001
    const hexCodes = line.match(/\b0x[0-9a-fA-F]{4,8}\b/g) || [];
    clues.errorCodes.push(...hexCodes);

    // 5. Function calls — CamelCase_Prefix or camelCase with parens
    const funcCalls = line.match(/\b([A-Z][a-zA-Z0-9_]{3,})\s*\(/g) || [];
    for (const fc of funcCalls) {
      const name = fc.replace(/\s*\($/, "");
      if (!STOP_WORDS.has(name.toLowerCase()) && name.length > 4) {
        clues.functionNames.push(name);
      }
    }

    // 6. Module tags in brackets — [MCU], [PCU], [RIP], [SCAN]
    const moduleTags = line.match(/\[(\w+)\]/g) || [];
    for (const tag of moduleTags) {
      const mod = tag.replace(/[\[\]]/g, "").toUpperCase();
      if (mod.length >= 2 && mod.length <= 20) {
        clues.modules.push(mod);
      }
    }

    // 7. State machine patterns — "state X", "STATE_", "at state"
    const stateNames = line.match(/\b(state[_ ]?\w+|STATE_\w+)\b/gi) || [];
    for (const s of stateNames) {
      if (s.length > 5) clues.functionNames.push(s);
    }
  }

  // Deduplicate
  clues.exactFiles = [...new Set(clues.exactFiles)];
  clues.errorCodes = [...new Set(clues.errorCodes)];
  clues.functionNames = [...new Set(clues.functionNames)];
  clues.modules = [...new Set(clues.modules)];

  // Build search queries from extracted clues
  if (clues.errorCodes.length) {
    clues.queries.push(clues.errorCodes.join(" "));
  }
  if (clues.functionNames.length) {
    clues.queries.push(clues.functionNames.slice(0, 15).join(" "));
  }
  if (clues.exactFiles.length) {
    // Search for file base names to also find related headers/sources
    const baseNames = clues.exactFiles.map(p => {
      const base = p.split("/").pop().replace(/\.(c|cpp|h|hpp)$/i, "");
      return base;
    });
    clues.queries.push(baseNames.join(" "));
  }

  return clues;
}

// Parse operation steps / bug description for domain-mapped keywords
function parseStepsForClues(bugDesc, logContent) {
  const text = (bugDesc + "\n" + (logContent || "")).toLowerCase();
  const clues = { queries: [], exactFiles: [], errorCodes: [], functionNames: [], modules: [] };

  // Extract any file paths even from steps
  const filePaths = (text.match(/[\w/.-]+\.(c|cpp|h|hpp)/g) || [])
    .map(p => p.replace(/:\d+$/, ""))
    .filter(p => !p.includes("openssl") && !p.includes("ssl") && !p.includes("tls"));
  if (filePaths.length) {
    clues.exactFiles.push(...[...new Set(filePaths)]);
  }

  // MFP domain keyword expansion
  const domainMap = {
    // Print flow
    "duplex": ["DuplexPath", "PrintFlow", "DuplexUnit", "TwoSide", "DUPLEX"],
    "雙面": ["DuplexPath", "PrintFlow", "DuplexUnit", "TwoSide", "DUPLEX"],
    "雙面列印": ["DuplexPath", "PrintFlow", "DuplexUnit"],
    "打印": ["PrintEngine", "PrintFlow", "PrnJobMgr", "PrintState", "PrintTask"],
    "列印": ["PrintEngine", "PrintFlow", "PrnJobMgr", "PrintState", "PrintTask"],
    "gdi": ["GDI", "PrintFlow", "VP", "VEngine", "PrintData"],
    "usb": ["USB", "UsbHost", "USBPrint", "UsbPrint"],
    // Paper handling
    "paper": ["PaperFeed", "PaperPath", "PaperPick", "PaperJam", "PaperSize", "PaperTransport"],
    "紙": ["PaperFeed", "PaperPath", "PaperPick", "PaperJam", "PaperTransport"],
    "進紙": ["PaperFeed", "PaperPick", "PaperPath", "PickupRoller"],
    "paper feed": ["PaperFeed", "PaperPick", "PickupRoller", "FeedMotor"],
    "paper jam": ["PaperJam", "JamDetect", "JamClear", "ErrorMgr"],
    "卡紙": ["PaperJam", "JamDetect", "JamClear", "ErrorMgr"],
    "tray": ["TraySelect", "InputTray", "PaperSize", "MediaDetect", "TrayStatus"],
    "紙匣": ["TraySelect", "InputTray", "PaperSize", "MediaDetect"],
    // Engine components
    "drum": ["DrumUnit", "DrumRotate", "DrumMotor", "EP", "EngineParam"],
    "fuser": ["Fuser", "FuserTemp", "HeaterControl", "HeatRoller"],
    "定影": ["Fuser", "FuserTemp", "HeaterControl"],
    "motor": ["MotorControl", "MotorDriver", "StepperMotor", "DCMotor"],
    "sensor": ["SensorDetect", "SensorCheck", "PaperSensor", "HomeSensor"],
    // States/errors
    "打印中": ["PrintState", "PrintEngine", "JobState", "JobMgr"],
    "stuck": ["PaperJam", "MotorControl", "PaperFeed", "EngineState"],
    "停住": ["PaperJam", "MotorControl", "PaperFeed", "EngineState", "EngineStuck"],
    "side door": ["SideDoor", "DoorSensor", "CoverOpen", "ErrorMgr"],
    // RIP/protocol
    "pcl": ["PCL", "RIP", "PrintParser", "PCLInterpreter"],
    "postscript": ["PSInterpreter", "RIP", "PrintParser"],
    "scan": ["ScanEngine", "ScanParser", "ADF", "Flatbed"],
    "掃描": ["ScanEngine", "ScanParser", "ADF", "Flatbed"],
    // Error handling
    "error": ["ErrorDef", "ErrorMgr", "ErrorHandler", "ErrorCode"],
    "錯誤": ["ErrorDef", "ErrorMgr", "ErrorHandler"],
  };

  const matchedDomains = [];
  for (const [keyword, expansions] of Object.entries(domainMap)) {
    if (text.includes(keyword.toLowerCase())) {
      matchedDomains.push(...expansions);
    }
  }
  if (matchedDomains.length > 0) {
    clues.queries.push([...new Set(matchedDomains)].join(" "));
  }

  // Extract CamelCase identifiers from text
  const codeIds = [...new Set((text.match(/\b[a-z][a-zA-Z0-9_]{5,}[a-z]\b/g) || [])
    .filter(id => !STOP_WORDS.has(id.toLowerCase())))];
  if (codeIds.length) clues.queries.push(codeIds.slice(0, 10).join(" "));

  return clues;
}

// Unified analysis: detect type → parse accordingly → build queries
function analyzeInputForClues(bugDesc, logContent) {
  const inputType = detectInputType(logContent);
  console.log(`[log-parser] Input type: ${inputType}`);

  let clues;
  if (inputType === "log") {
    clues = parseSystemLog(logContent);
    console.log(`[log-parser] Extracted: ${clues.errorCodes.length} error codes, ${clues.functionNames.length} functions, ${clues.exactFiles.length} files, ${clues.modules.length} modules`);
  } else {
    clues = parseStepsForClues(bugDesc, logContent);
  }

  // If exact files found, search for them specifically
  if (clues.exactFiles.length) {
    // Query for exact file names (high priority)
    const baseNames = clues.exactFiles.map(p => p.split("/").pop().replace(/\.(c|cpp|h|hpp)$/i, ""));
    clues.queries.unshift(baseNames.join(" "));
  }

  // Add original bug description as fallback
  clues.queries.push(bugDesc);

  // Deduplicate queries
  clues.queries = [...new Set(clues.queries)];

  // Exclude patterns — files we know are never useful for bug analysis
  clues.excludePatterns = [
    /openssl/i, /ssl\.h$/i, /tls\.h$/i, /obj_mac/i, /safestack/i,
    /MainMenuStrTable/i, /ReportStringTable/i, /ReportVariables/i,
  ];

  console.log(`[log-parser] Generated ${clues.queries.length} queries`);
  clues.queries.forEach((q, i) => console.log(`  Q${i + 1}: "${q.slice(0, 100)}"`));

  return clues;
}

// --- Shared LLM call (non-streaming, returns full response) ---
async function llmCall(systemPrompt, userMessage, { maxTokens = 4096, temperature = 0.3, timeout = 120_000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const res = await fetch(`${llmConfig.baseUrl}/chat/completions`, {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(llmConfig.apiKey ? { Authorization: `Bearer ${llmConfig.apiKey}` } : {}),
      },
      body: JSON.stringify({
        model: llmConfig.model,
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userMessage },
        ],
        max_tokens: maxTokens,
        temperature,
      }),
    });

    if (!res.ok) {
      let errText = "";
      try { errText = await res.text(); } catch {}
      throw new Error(`LLM API ${res.status}: ${errText.slice(0, 300)}`);
    }

    const data = await res.json();
    const choice = data.choices?.[0];
    // Extract both reasoning and content
    const reasoning = choice?.message?.reasoning_content || choice?.message?.reasoning || "";
    const content = choice?.message?.content || "";
    return { reasoning, content };
  } finally {
    clearTimeout(timer);
  }
}

// --- Phase 1.5: LLM-based bug-log correlation analysis ---
async function correlateBugAndLog(bugDesc, logContent) {
  if (!logContent || !logContent.trim()) {
    console.log("[correlate] No log content, skipping correlation analysis");
    return null;
  }

  console.log("[correlate] Starting bug-log correlation analysis...");
  console.log(`[correlate] bugDesc=${bugDesc.length} chars, logContent=${logContent.length} chars`);

  const systemPrompt = `你是一位資深的嵌入式系統韌體 Debug 專家，專精於 Multi-Function Printer (MFP)。

你的任務是分析使用者提供的「Bug 描述」和「Debug Log」之間的**關聯性**，找出 Log 中與 Bug 直接相關的關鍵證據。

請嚴格以 JSON 格式回覆（不要加 markdown code block），格式如下：
{
  "relevance": "high" | "medium" | "low",
  "summary": "簡短摘要這份 log 與 bug 描述的關聯性（2-3 句話）",
  "keyFindings": [
    {
      "type": "error_code" | "function" | "state_change" | "contradiction" | "timing" | "sequence",
      "description": "描述這個發現",
      "evidence": "log 中的原文（關鍵行）",
      "keywords": ["從這個發現提取的搜尋關鍵字（函式名、錯誤碼、模組名等）"]
    }
  ],
  "searchTerms": ["建議用來搜尋相關原始碼的關鍵字（優先函式名和模組名）"],
  "rootCauseHypothesis": "根據 log 分析，對 bug 根本原因的假設"
}

分析重點：
1. 找出 log 中直接觸發或導致 bug 的函式呼叫和錯誤碼
2. 找出「預期行為」與「實際 log 行為」的矛盾（contradiction）
3. 追蹤狀態機轉換異常（state machine 的異常轉換）
4. 找出時序問題（timing issue）— 例如 timeout、race condition
5. 如果 log 中有成功案例和失敗案例的對比，提取差異點`;

  // Truncate log to avoid exceeding token limits
  const logSnippet = logContent.length > 15000 ? logContent.slice(0, 15000) + "\n// ... (log truncated)" : logContent;

  const userMessage = `## Bug 描述
${bugDesc}

## Debug Log
\`\`\`
${logSnippet}
\`\`\`

請分析這份 Debug Log 與上述 Bug 描述的關聯性，找出關鍵證據。`;

  try {
    const result = await llmCall(systemPrompt, userMessage, { maxTokens: 4096, timeout: 120_000 });

    // Try to parse JSON from the content (some models wrap it in markdown code block)
    let parsed;
    const jsonStr = result.content.replace(/^```(?:json)?\s*/m, "").replace(/\s*```$/m, "").trim();
    try {
      parsed = JSON.parse(jsonStr);
    } catch {
      // If JSON parse fails, use the raw text as search clues
      console.warn("[correlate] JSON parse failed, extracting keywords from raw text");
      const keywords = result.content.match(/[\w_]{3,}/g)?.filter(w => /[A-Z]/.test(w) && w.length > 4).slice(0, 15) || [];
      parsed = {
        relevance: "medium",
        summary: result.content.slice(0, 300),
        keyFindings: [],
        searchTerms: keywords,
        rootCauseHypothesis: result.content.slice(0, 500),
      };
    }

    console.log(`[correlate] Relevance: ${parsed.relevance}`);
    console.log(`[correlate] Summary: ${parsed.summary?.slice(0, 100)}`);
    console.log(`[correlate] Key findings: ${parsed.keyFindings?.length || 0}`);
    console.log(`[correlate] Search terms: ${parsed.searchTerms?.join(", ")}`);
    console.log(`[correlate] Root cause hypothesis: ${parsed.rootCauseHypothesis?.slice(0, 100)}`);

    return parsed;
  } catch (err) {
    console.warn("[correlate] Correlation analysis failed:", err.message);
    return null;
  }
}

// --- Phase 2: Multi-strategy search with domain-aware filtering ---
async function iterativeSearch(bugDesc, logContent, maxRounds = 5, existingCorrelation = null) {
  const allResults = new Map();
  const seenPaths = new Set();
  const clues = analyzeInputForClues(bugDesc, logContent);

  // Phase 1.5: LLM correlation analysis (if log content provided and not already done)
  const correlation = existingCorrelation || await correlateBugAndLog(bugDesc, logContent);
  if (correlation) {
    // Merge LLM-suggested search terms into queries (highest priority)
    if (correlation.searchTerms?.length) {
      const llmQuery = correlation.searchTerms.join(" ");
      clues.queries.unshift(llmQuery); // Add as first query (highest priority)
    }
    // Extract keywords from key findings
    if (correlation.keyFindings?.length) {
      for (const finding of correlation.keyFindings) {
        if (finding.keywords?.length) {
          clues.queries.push(finding.keywords.join(" "));
        }
        // Also add individual keyword terms
        if (finding.keywords) {
          for (const kw of finding.keywords) {
            if (kw.length > 3 && /[A-Z_]/.test(kw)) {
              clues.queries.push(kw);
            }
          }
        }
      }
    }
    // Add root cause hypothesis as a search query
    if (correlation.rootCauseHypothesis) {
      clues.queries.push(correlation.rootCauseHypothesis);
    }
  }

  console.log(`[iterative-search] Generated ${clues.queries.length} queries`);
  clues.queries.forEach((q, i) => console.log(`  Q${i + 1}: "${q.slice(0, 80)}"`));

  const addResults = (results) => {
    for (const r of results) {
      if (seenPaths.has(r.path)) continue;
      // Filter out irrelevant files
      if (clues.excludePatterns.some(p => p.test(r.path))) {
        console.log(`  [skip] ${r.path} (excluded)`);
        continue;
      }
      seenPaths.add(r.path);
      allResults.set(r.path, r);
    }
  };

  // Execute up to maxRounds queries
  const queries = clues.queries.slice(0, maxRounds);
  for (let i = 0; i < queries.length; i++) {
    console.log(`[iterative-search] Round ${i + 1}/${queries.length}: "${queries[i].slice(0, 80)}"`);
    try {
      addResults(await hybridSearch(queries[i], 15));
    } catch (err) {
      console.warn(`[iterative-search] Round ${i + 1} failed:`, err.message);
    }
    console.log(`[iterative-search] Round ${i + 1}: ${seenPaths.size} unique total`);
  }

  // Sort: strongly prefer .c/.cpp implementation files, then by score
  const sorted = [...allResults.values()].sort((a, b) => {
    const aIsImpl = /\.(c|cpp|cc|cxx)$/i.test(a.path);
    const bIsImpl = /\.(c|cpp|cc|cxx)$/i.test(b.path);
    // Implementation files always beat headers
    if (aIsImpl && !bIsImpl) return -1;
    if (!aIsImpl && bIsImpl) return 1;
    // Within same type, prefer files in pcu/ (print control unit) directory
    const aIsPcu = a.path.includes("pcu/");
    const bIsPcu = b.path.includes("pcu/");
    if (aIsPcu && !bIsPcu) return -1;
    if (!aIsPcu && bIsPcu) return 1;
    // Within same type, prefer files in ErrorMgr/
    const aIsErr = a.path.includes("ErrorMgr/");
    const bIsErr = b.path.includes("ErrorMgr/");
    if (aIsErr && !bIsErr) return -1;
    if (!aIsErr && bIsErr) return 1;
    // Then by score
    return b.score - a.score;
  });

  console.log(`[iterative-search] Final: ${sorted.length} files, top 5:`);
  sorted.slice(0, 5).forEach(f => console.log(`  ${f.path} (score=${f.score})`));
  return sorted;
}

// --- API: Analyze with LLM (background job + polling) ---
const analyzeJobs = new Map();

// --- Phase orchestration: correlation → search → analysis ---
async function runCorrelationAndAnalysis(jobId) {
  const job = analyzeJobs.get(jobId);
  try {
    // Phase 1: LLM correlation analysis (if log provided)
    job.status = "correlating";
    job.message = "🧠 正在分析 Bug 描述與 Log 的關聯性...";
    job.correlation = null;

    if (job.logContent && job.logContent.trim()) {
      job.correlation = await correlateBugAndLog(job.bugDescription, job.logContent);
      if (job.correlation) {
        // Re-run iterative search with correlation-enhanced queries
        job.status = "searching";
        const relevanceText = job.correlation.relevance === 'high' ? '高' : job.correlation.relevance === 'medium' ? '中' : '低';
        job.message = `🔍 關聯分析完成（${relevanceText}度相關），重新搜尋程式碼...`;

        try {
          const newResults = await iterativeSearch(job.bugDescription, job.logContent, 5, job.correlation);
          // Replace code results with correlation-enhanced results
          const seenPaths = new Set();
          const mergedResults = [];
          // New results first (correlation-enhanced, higher priority)
          for (const r of newResults) {
            if (!seenPaths.has(r.path)) {
              mergedResults.push(r);
              seenPaths.add(r.path);
            }
          }
          // Then add original results not already included
          for (const r of job.codeResults) {
            if (!seenPaths.has(r.path)) {
              mergedResults.push(r);
              seenPaths.add(r.path);
            }
          }
          job.codeResults = mergedResults;
          job.message = `🔍 搜尋完成，找到 ${job.codeResults.length} 個相關檔案，開始 AI 分析...`;
        } catch (err) {
          console.warn("[correlate] Re-search failed:", err.message);
          // Keep original results
        }
      }
    }

    // Phase 2: Run the actual analysis
    await runAnalysis(jobId);
  } catch (err) {
    console.error("Correlation analysis error:", err);
    // Fall through to normal analysis
    job.correlation = null;
    await runAnalysis(jobId);
  }
}

async function runAnalysis(jobId) {
  const job = analyzeJobs.get(jobId);
  try {
    // Prefer .c/.cpp implementation files over .h headers
    const sorted = [...job.codeResults].sort((a, b) => {
      const aIsC = /\.(c|cpp|cc|cxx)$/i.test(a.path);
      const bIsC = /\.(c|cpp|cc|cxx)$/i.test(b.path);
      if (aIsC && !bIsC) return -1;
      if (!aIsC && bIsC) return 1;
      return b.score - a.score;
    });
    const topFiles = sorted.slice(0, 8);  // More files now — we send chunks not full files
    job.status = "calling";
    job.message = "正在呼叫 AI 分析（這可能需要 30-120 秒）...";

    // NEW: Build codeContext from chunk-level content when available
    // If semanticChunks exist, use those precise chunks instead of full file truncation
    const MAX_TOTAL_CHARS = 60000;  // Total budget for all code context
    const MAX_PER_FILE = 12000;    // Per-file budget
    const codeParts = [];
    let totalChars = 0;

    for (let i = 0; i < topFiles.length && totalChars < MAX_TOTAL_CHARS; i++) {
      const file = topFiles[i];
      let fileContent = "";

      if (file.semanticChunks && file.semanticChunks.length > 0) {
        // Use the specific matching chunks — this is the precision win
        const chunkTexts = file.semanticChunks
          .sort((a, b) => (b.score || 0) - (a.score || 0))
          .slice(0, 10)  // Max 10 chunks per file
          .map(c => {
            let text = c.content || "";
            // Strip BOM + comments
            if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
            text = text.replace(/\/\*[\s\S]*?\*\//g, '');
            text = text.replace(/\/\/[^\n]*/g, '');
            text = text.trim();
            const label = c.type === "function" ? `function ${c.name}` : `${c.type} ${c.name}`;
            return `// --- ${label} (L${c.startLine}-${c.endLine}) ---\n${text}`;
          });
        fileContent = chunkTexts.join("\n\n");
      } else {
        // Fallback: load full file and truncate (backward compatible)
        let content = file.content || loadFileContent(file);
        if (content.charCodeAt(0) === 0xFEFF) content = content.slice(1);
        content = content.replace(/\/\*[\s\S]*?\*\//g, '');
        content = content.replace(/\/\/[^\n]*/g, '');
        content = content.trim();
        if (content.length > MAX_PER_FILE) {
          content = content.slice(0, MAX_PER_FILE) + `\n// ... (truncated, ${file.lines} total lines)`;
        }
        fileContent = content;
      }

      if (fileContent.length > MAX_PER_FILE) {
        fileContent = fileContent.slice(0, MAX_PER_FILE) + `\n// ... (truncated)`;
      }

      const syms = (file.symbols || []).slice(0, 30).join(", ");
      const header = `=== File ${i + 1}: ${file.path} (${file.lines} lines) ===\nKey symbols: ${syms}`;
      const part = `${header}\n\n${fileContent}`;

      if (totalChars + part.length > MAX_TOTAL_CHARS) {
        // Partially add this file
        const remaining = MAX_TOTAL_CHARS - totalChars;
        if (remaining > 500) {
          codeParts.push(part.slice(0, remaining) + "\n// ... (context limit)");
          totalChars = MAX_TOTAL_CHARS;
        }
        break;
      }
      codeParts.push(part);
      totalChars += part.length;
    }

    const codeContext = codeParts.join("\n\n");

    console.log(`[analyze] codeResults=${job.codeResults.length}, selected=${topFiles.length} files, codeContext=${codeContext.length} chars`);
    for (const f of topFiles) {
      const chunks = f.semanticChunks?.length || 0;
      console.log(`  ${f.path}: chunks=${chunks}, lines=${f.lines}`);
    }

    const systemPrompt = `你是一位資深的嵌入式系統 C/C++ 韌體工程師，專精於 Multi-Function Printer (MFP) 架構、SoC 整合和即時作業系統開發。

你的任務是根據使用者提供的 bug 描述和 log，分析以下原始碼，找出 bug 最可能出現的位置和原因。

分析時請考慮：
1. 記憶體管理問題（buffer overflow, null pointer, use-after-free）
2. 執行緒安全（race condition, deadlock, missing mutex）
3. 硬體暫存器操作問題（volatile 遺漏, magic number, timing）
4. 即時性問題（ISR 中做太多事, blocking call）
5. 錯誤處理遺漏

以下是專案的相關原始碼：

${codeContext}

重要：如果上面的原始碼與這個 bug 完全不相關（例如都是 header 定義檔、NVRAM 參數檔，沒有任何 paper feed / print flow / duplex / engine control 的實作邏輯），請在回答的開頭用這個格式：
CODE_NOT_RELEVANT: 你認為真正需要的程式碼應該包含哪些關鍵字或模組（例如：PaperFeed, PrintEngine, DuplexPath, MotorControl 等）

如果原始碼與 bug 相關，請正常分析，格式如下：
1. **可能的 Bug 位置** — 列出最可疑的檔案和行號範圍
2. **原因分析** — 解釋為什麼這裡可能是問題根源
3. **Log 關聯** — 說明 log 訊息和程式碼的關聯
4. **建議修正** — 提供具體的修正建議和程式碼片段${job.correlation ? `

---
## Bug-Log 關聯性分析結果（供參考）
- 關聯度：${job.correlation.relevance}
- 摘要：${job.correlation.summary}
- 根本原因假設：${job.correlation.rootCauseHypothesis || '無'}
${job.correlation.keyFindings?.length ? '- 關鍵發現：\n' + job.correlation.keyFindings.map(f => `  * [${f.type}] ${f.description}（證據：${f.evidence?.slice(0, 80)}）`).join('\n') : ''}
---` : ''}`;

    const userMessage = `## Bug 描述\n${job.bugDescription}\n\n## Log 內容\n\`\`\`\n${job.logContent.slice(0, 8000)}\n\`\`\`\n\n請根據以上資訊和提供的原始碼，分析 bug 可能出現的位置和原因。`;

    job.status = "analyzing";
    job.message = "AI 正在分析中...";

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 180_000);

    let llmRes;
    try {
      llmRes = await fetch(`${llmConfig.baseUrl}/chat/completions`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...(llmConfig.apiKey ? { Authorization: `Bearer ${llmConfig.apiKey}` } : {}),
        },
        body: JSON.stringify({
          model: llmConfig.model,
          messages: [
            { role: "system", content: systemPrompt },
            { role: "user", content: userMessage },
          ],
          max_tokens: 4096,
          temperature: 0.3,
          stream: true,
        }),
      });
    } catch (err) {
      clearTimeout(timeout);
      job.status = "error";
      job.message = err.name === "AbortError" ? "LLM API 回應超時（超過 3 分鐘）" : `LLM API 連線失敗: ${err.message}`;
      return;
    }
    clearTimeout(timeout);

    if (!llmRes.ok) {
      let errText = "";
      try { errText = await llmRes.text(); } catch {}
      job.status = "error";
      job.message = `LLM API 回應 ${llmRes.status}: ${errText.slice(0, 300)}`;
      return;
    }

    // Stream from LLM
    const reader = llmRes.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6).trim();
        if (jsonStr === "[DONE]") break;
        try {
          const chunk = JSON.parse(jsonStr);
          const d = chunk.choices?.[0]?.delta;
          // Separate reasoning (thinking) from content (final answer)
          const reasoning = d?.reasoning_content || d?.reasoning || "";
          const content = d?.content || "";
          if (reasoning) {
            job.reasoning += reasoning;
          }
          if (content) {
            job.analysis += content;
          }
        } catch {}
      }
    }

    // Check if LLM says code is not relevant → trigger iterative re-search
    const notRelevantMatch = job.analysis.match(/^CODE_NOT_RELEVANT:\s*(.+)/ms);
    if (notRelevantMatch && job.searchRound < 3) {
      const suggestedTerms = notRelevantMatch[1].trim();
      console.log(`[analyze] LLM says code not relevant. Suggested terms: "${suggestedTerms.slice(0, 100)}"`);
      console.log(`[analyze] Search round ${job.searchRound} → ${job.searchRound + 1}`);

      job.searchRound = (job.searchRound || 1) + 1;
      job.status = "searching";
      job.message = `第 ${job.searchRound} 輪搜尋中（AI 建議搜尋：${suggestedTerms.slice(0, 50)}...）`;

      // Search with LLM's suggested terms
      const newResults = await hybridSearch(suggestedTerms, 15);

      // Merge with existing results, excluding files already seen
      const seenPaths = new Set(job.codeResults.map(r => r.path));
      const freshResults = newResults.filter(r => !seenPaths.has(r.path));

      if (freshResults.length === 0) {
        // No new files found, try extracting terms from suggestion
        const altTerms = suggestedTerms.match(/[\w_]+/g)?.slice(0, 5) || [];
        for (const term of altTerms) {
          const altResults = await hybridSearch(term, 10);
          for (const r of altResults) {
            if (!seenPaths.has(r.path)) freshResults.push(r);
          }
        }
      }

      if (freshResults.length > 0) {
        // Merge and retry
        job.codeResults = [...job.codeResults, ...freshResults];
        job.analysis = "";
        job.reasoning = "";
        job.message = `找到 ${freshResults.length} 個新檔案，重新分析中...`;
        return runAnalysis(jobId); // recursive retry
      } else {
        job.analysis = `⚠️ 經過 ${job.searchRound} 輪搜尋仍找不到高度相關的原始碼。\n\nAI 建議需要以下類型的程式碼：\n${suggestedTerms}\n\n可能的改善方向：\n- 搜尋引擎索引可能未涵蓋相關模組\n- 嘗試在 bug 描述中加入更具體的函式名稱或錯誤代碼`;
        job.reasoning = `Iterative search round ${job.searchRound}: no new relevant files found. LLM suggested: ${suggestedTerms}`;
        job.status = "done";
        job.filesAnalyzed = job.codeResults.map((f) => f.path);
      }
      return;
    }

    if (job.analysis) {
      job.status = "done";
      job.filesAnalyzed = topFiles.map((f) => f.path);
      console.log(`[analyze] reasoning=${job.reasoning.length} chars, analysis=${job.analysis.length} chars (round ${job.searchRound || 1})`);
    } else if (job.reasoning) {
      // Model only returned reasoning, no content — treat reasoning as analysis
      job.analysis = job.reasoning;
      job.reasoning = "";
      job.status = "done";
      job.filesAnalyzed = topFiles.map((f) => f.path);
      console.log(`[analyze] no content, promoted reasoning (${job.analysis.length} chars) to analysis`);
    } else {
      job.status = "error";
      job.message = "AI 未返回有效回覆";
    }
  } catch (err) {
    console.error("Analysis error:", err);
    job.status = "error";
    job.message = err.message;
  }
}

app.post("/api/analyze", async (req, res) => {
  const { bugDescription, logContent, codeResults } = req.body;
  if (!bugDescription || !logContent) return res.status(400).json({ error: "bugDescription and logContent required" });

  // If no code results provided, do multi-round iterative RAG search
  let resolvedCodeResults = codeResults;
  if (!codeResults?.length) {
    try {
      resolvedCodeResults = await iterativeSearch(bugDescription, logContent, 3);
    } catch (err) {
      console.warn("Iterative search failed:", err.message);
      return res.status(503).json({ error: `自動搜尋失敗: ${err.message}` });
    }
  }

  if (!resolvedCodeResults?.length) return res.status(400).json({ error: "No code results found" });

  const jobId = `job_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  analyzeJobs.set(jobId, {
    status: "queued",
    message: "正在準備分析資料...",
    analysis: "",
    reasoning: "",
    filesAnalyzed: [],
    bugDescription,
    logContent,
    codeResults: resolvedCodeResults,
    searchRound: 1,
    createdAt: Date.now(),
  });

  // Run correlation analysis in background, then run full analysis
  runCorrelationAndAnalysis(jobId);

  // Clean up old jobs after 10 minutes
  setTimeout(() => analyzeJobs.delete(jobId), 600_000);

  res.json({ jobId, filesFound: resolvedCodeResults.map(r => r.path), searchMethod: codeResults?.length ? "manual" : "hybrid" });
});

app.get("/api/analyze/:jobId", (req, res) => {
  const job = analyzeJobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ error: "Job not found" });
  res.json({
    status: job.status,
    message: job.message,
    analysis: job.analysis,
    reasoning: job.reasoning,
    correlation: job.correlation,
    filesAnalyzed: job.filesAnalyzed,
  });
});

// --- API: Index stats ---
app.get("/api/stats", (_req, res) => {
  const index = getIndex();
  res.json({
    totalFiles: index.totalFiles,
    totalLines: index.totalLines,
    sourceRoot: index.sourceRoot,
  });
});

// --- API: LLM Config ---
app.get("/api/llm-config", (_req, res) => {
  res.json({ baseUrl: llmConfig.baseUrl, model: llmConfig.model, hasKey: !!llmConfig.apiKey });
});

app.put("/api/llm-config", (req, res) => {
  const { baseUrl, apiKey, model } = req.body;
  if (baseUrl) llmConfig.baseUrl = baseUrl.replace(/\/+$/, "");
  if (apiKey !== undefined) llmConfig.apiKey = apiKey;
  if (model) llmConfig.model = model;
  // Never persist apiKey to disk — only lives in client browser memory
  const diskConfig = { ...llmConfig };
  delete diskConfig.apiKey;
  fs.writeFileSync(LLM_CONFIG_PATH, JSON.stringify(diskConfig, null, 2));
  console.log("LLM config updated:", { baseUrl: llmConfig.baseUrl, model: llmConfig.model, hasKey: !!llmConfig.apiKey });
  res.json({ ok: true });
});

app.get("/api/llm-models", async (_req, res) => {
  try {
    const headers = { "Content-Type": "application/json" };
    if (llmConfig.apiKey) headers["Authorization"] = `Bearer ${llmConfig.apiKey}`;
    const modelsRes = await fetch(`${llmConfig.baseUrl}/models`, { headers });
    if (!modelsRes.ok) throw new Error(`${modelsRes.status}`);
    const data = await modelsRes.json();
    const models = (data.data || []).map((m) => m.id).sort();
    res.json({ models });
  } catch (err) {
    res.status(502).json({ error: `Failed to fetch models: ${err.message}` });
  }
});

// --- Serve static frontend ---
app.use(express.static(path.join(__dirname, "public")));

app.listen(PORT, "0.0.0.0", () => {
  console.log(`🚀 Bug Detective running at http://localhost:${PORT}`);
});
