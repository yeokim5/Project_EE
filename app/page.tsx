"use client";

import {
  AlertTriangle,
  ArrowLeft,
  Download,
  FileText,
  Info,
  Loader2,
  Upload,
  X,
} from "lucide-react";
import {
  ChangeEvent,
  DragEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

const PDFJS_VERSION = "3.11.174";
const MAX_HOSTED_UPLOAD_BYTES = 4 * 1024 * 1024;

// Same-origin in production (Vercel serves api/*.py). For local dev, set
// NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000 in .env and run the Python server
// (`npm run dev:api`) so the browser calls it directly, sidestepping next dev's
// flaky external rewrites. The server already sends permissive CORS headers.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

type EvidenceRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

type Metric = {
  metric_index: number;
  metric_name: string;
  value: string | number | null;
  unit: string | null;
  scale: string | null;
  source_page: number;
  source_quote: string;
  confidence: number;
  needs_review: boolean;
  review_reason: string | null;
  evidence: { matched: boolean; rects: EvidenceRect[] };
};

type ExtractResponse = {
  ok: true;
  document_name: string;
  run_id: string;
  // The full DraftRun JSON is opaque to the client; we round-trip it untouched
  // (apart from edited metric values) back to /api/export.
  draft: Record<string, unknown> & {
    metrics: Array<{ value: string | number | null; unit: string | null }>;
  };
  metrics: Metric[];
};

type DocSource =
  | { kind: "demo"; id: "tesla" | "citi" }
  | { kind: "file"; file: File };

type DocState = {
  documentName: string;
  draft: ExtractResponse["draft"];
  metrics: Metric[];
  source: DocSource;
};

type ApiError = { ok: false; error?: unknown; detail?: unknown };

const sampleDocs: Array<{ id: "tesla" | "citi"; label: string }> = [
  { id: "tesla", label: "Tesla Q2 2025" },
  { id: "citi", label: "Citi Q1 2025" },
];

export default function Home() {
  const [mode, setMode] = useState<"live" | "sample">("sample");
  const [files, setFiles] = useState<File[]>([]);
  const [step, setStep] = useState<"upload" | "review">("upload");
  const [docs, setDocs] = useState<DocState[]>([]);
  const [activeDoc, setActiveDoc] = useState(0);
  const [activeMetric, setActiveMetric] = useState(0);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const pdfReady = usePdfJs();

  function reset() {
    setDocs([]);
    setFiles([]);
    setStep("upload");
    setActiveDoc(0);
    setActiveMetric(0);
    setError(null);
  }

  // Append, never replace. Dedupe by name+size so the same file isn't added
  // twice across multiple "choose files" clicks.
  function addFiles(incoming: File[]) {
    const pdfs = incoming.filter((file) =>
      file.name.toLowerCase().endsWith(".pdf"),
    );
    if (!pdfs.length) return;
    setMode("live");
    setError(null);
    setFiles((current) => {
      const seen = new Set(current.map((f) => `${f.name}:${f.size}`));
      const merged = [...current];
      for (const file of pdfs) {
        const key = `${file.name}:${file.size}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(file);
        }
      }
      return merged;
    });
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    addFiles(Array.from(event.target.files ?? []));
    // Reset so picking the same file again still fires onChange.
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    addFiles(Array.from(event.dataTransfer.files ?? []));
  }

  function removeFile(index: number) {
    setFiles((current) => current.filter((_, i) => i !== index));
  }

  async function extractAll(sources: DocSource[]) {
    setError(null);
    const collected: DocState[] = [];
    try {
      for (let index = 0; index < sources.length; index += 1) {
        setProgress(`Extracting ${index + 1} of ${sources.length}…`);
        const source = sources[index];
        const payload =
          source.kind === "demo"
            ? { mode: "recorded", demoDocument: source.id }
            : {
                mode: "live",
                filename: source.file.name,
                fileBase64: await fileToBase64(source.file),
              };
        const response = await fetch(`${API_BASE}/api/extract`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const body = (await readApiJson(response)) as ExtractResponse | ApiError;
        if (!response.ok || !body.ok) {
          throw new Error(apiErrorMessage(body, response.status));
        }
        collected.push({
          documentName: body.document_name,
          draft: body.draft,
          metrics: body.metrics,
          source,
        });
      }
      setDocs(collected);
      setActiveDoc(0);
      setActiveMetric(0);
      setStep("review");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Extraction failed.");
    } finally {
      setProgress(null);
    }
  }

  function runSample() {
    void extractAll(sampleDocs.map((doc) => ({ kind: "demo", id: doc.id })));
  }

  function runLive() {
    if (!files.length) {
      setError("Add at least one PDF.");
      return;
    }
    const oversized = files.filter(
      (file) => !sampleIdForFile(file) && file.size > MAX_HOSTED_UPLOAD_BYTES,
    );
    if (oversized.length) {
      setError(
        `${oversized[0].name} is ${formatFileSize(
          oversized[0].size,
        )}. The hosted upload path is capped at ${formatFileSize(
          MAX_HOSTED_UPLOAD_BYTES,
        )}; use Samples here, or run the local CLI/API path for larger PDFs.`,
      );
      return;
    }
    void extractAll(
      files.map((file) => {
        const sampleId = sampleIdForFile(file);
        return sampleId ? { kind: "demo", id: sampleId } : { kind: "file", file };
      }),
    );
  }

  function updateDoc(index: number, next: Partial<DocState>) {
    setDocs((current) =>
      current.map((doc, i) => (i === index ? { ...doc, ...next } : doc)),
    );
  }

  function editValue(metricIndex: number, raw: string) {
    const doc = docs[activeDoc];
    if (!doc) return;
    const coerced = coerceValue(raw);
    const metrics = doc.metrics.map((metric) =>
      metric.metric_index === metricIndex
        ? { ...metric, value: coerced }
        : metric,
    );
    // Mirror the edit into the opaque draft so /api/export reads the new value.
    const draftMetrics = [
      ...(doc.draft.metrics as ExtractResponse["draft"]["metrics"]),
    ];
    draftMetrics[metricIndex] = {
      ...draftMetrics[metricIndex],
      value: coerced,
    };
    updateDoc(activeDoc, {
      metrics,
      draft: { ...doc.draft, metrics: draftMetrics },
    });
  }

  async function exportWorkbook() {
    setIsExporting(true);
    setError(null);
    try {
      // Decisions are derived from the (possibly edited) values: a present
      // value is approved, a blank one is not-applicable. No notes required —
      // the server fills audit notes for any flagged rows automatically.
      const payload = {
        reviewer: "Web reviewer",
        documents: docs.map((doc) => ({
          draft: doc.draft,
          decisions: doc.metrics.map((metric) => ({
            metric_index: metric.metric_index,
            review_status:
              metric.value === null || metric.value === ""
                ? "not_applicable"
                : "approved",
            note: null,
          })),
        })),
      };
      const response = await fetch(`${API_BASE}/api/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = (await readApiJson(response)) as
        | {
            ok: true;
            workbook: { filename: string; content_type: string; base64: string };
          }
        | ApiError;
      if (!response.ok || !body.ok) {
        throw new Error(apiErrorMessage(body, response.status, "Export failed."));
      }
      downloadBase64(
        body.workbook.base64,
        body.workbook.filename,
        body.workbook.content_type,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Export failed.");
    } finally {
      setIsExporting(false);
    }
  }

  // ----- Upload screen -----------------------------------------------------
  if (step === "upload") {
    return (
      <main className="page upload-page">
        <div className="upload-card panel">
          <div className="brand">
            <span className="brand-mark">EE</span>
            <span className="brand-name">
              Project EE <span>· Earnings Extractor</span>
            </span>
          </div>
          <p className="eyebrow">Review-first extraction</p>
          <h1>Earnings Extractor</h1>
          <p className="copy" style={{ marginTop: 10 }}>
            Turn earnings PDFs into one clean Excel workbook
          </p>

          <ol className="steps">
            <li>
              <span className="step-num">1</span>
              <span>
                <strong>Add your PDFs.</strong> Drop in one or more earnings
                reports
              </span>
            </li>
            <li>
              <span className="step-num">2</span>
              <span>
                <strong>Let it extract.</strong> Each document is read
                automatically and the key metrics are pulled out.
              </span>
            </li>
            <li>
              <span className="step-num">3</span>
              <span>
                <strong>Check the values.</strong> Every number is shown next to
                its highlighted source so you can verify it at a glance.
              </span>
            </li>
            <li>
              <span className="step-num">4</span>
              <span>
                <strong>Fix &amp; export.</strong> Correct anything that looks
                off, then download a single Excel workbook.
              </span>
            </li>
          </ol>

          <div className="mode-grid" style={{ marginTop: 24, maxWidth: 320 }}>
            <button
              className={`mode-button ${mode === "sample" ? "active" : ""}`}
              onClick={() => setMode("sample")}
              type="button"
            >
              <FileText size={18} /> Samples
            </button>
            <button
              className={`mode-button ${mode === "live" ? "active" : ""}`}
              onClick={() => setMode("live")}
              type="button"
            >
              <Upload size={18} /> Upload
            </button>
          </div>

          {mode === "sample" ? (
            <section style={{ marginTop: 20 }}>
              <button
                className="primary-button"
                disabled={progress !== null}
                onClick={runSample}
                type="button"
              >
                {progress ? <Loader2 size={18} className="spin" /> : null}
                {progress ?? "Run samples (Tesla + Citi)"}
              </button>
            </section>
          ) : (
            <section style={{ marginTop: 20 }}>
              <label
                className="dropzone"
                onDragOver={(event) => event.preventDefault()}
                onDrop={onDrop}
              >
                <input
                  accept="application/pdf,.pdf"
                  multiple
                  onChange={onFileChange}
                  type="file"
                />
                <span>
                  <Upload size={26} />
                  <span style={{ display: "block", fontWeight: 800 }}>
                    {files.length ? "Add more PDFs" : "Drop PDFs or choose files"}
                  </span>
                  <span className="copy">You can add several, one at a time.</span>
                </span>
              </label>

              {files.length ? (
                <ul className="file-list">
                  {files.map((file, index) => (
                    <li className="file-row" key={`${file.name}:${file.size}`}>
                      <FileText size={16} />
                      <span className="file-row-name">{file.name}</span>
                      <button
                        aria-label={`Remove ${file.name}`}
                        className="file-remove"
                        onClick={() => removeFile(index)}
                        type="button"
                      >
                        <X size={15} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}

              <button
                className="primary-button"
                disabled={progress !== null || !files.length}
                onClick={runLive}
                style={{ marginTop: 14 }}
                type="button"
              >
                {progress ? <Loader2 size={18} className="spin" /> : null}
                {progress ??
                  `Extract ${files.length || ""} document${
                    files.length === 1 ? "" : "s"
                  }`.trim()}
              </button>
            </section>
          )}

          {error ? (
            <div className="status-box error" style={{ marginTop: 18 }}>
              <strong>Could not extract</strong>
              <p>{error}</p>
            </div>
          ) : null}
        </div>
      </main>
    );
  }

  // ----- Review screen -----------------------------------------------------
  const doc = docs[activeDoc];
  const metric = doc?.metrics[activeMetric] ?? null;

  return (
    <main className="page">
      <div className="review-shell">
        <aside className="panel review-list">
          <div className="list-header">
            {docs.length > 1 ? (
              <div className="doc-tabs">
                {docs.map((entry, index) => (
                  <button
                    className={`doc-tab ${index === activeDoc ? "active" : ""}`}
                    key={entry.documentName + index}
                    onClick={() => {
                      setActiveDoc(index);
                      setActiveMetric(0);
                    }}
                    type="button"
                  >
                    {entry.documentName}
                  </button>
                ))}
              </div>
            ) : (
              <h2>{doc?.documentName}</h2>
            )}
            <button className="link-button" onClick={reset} type="button">
              <ArrowLeft size={14} /> New batch
            </button>
          </div>

          <div className="metric-rows">
            {doc?.metrics.map((row, index) => {
              const rowBlank = row.value === null || row.value === "";
              return (
              <div
                className={`metric-row ${index === activeMetric ? "active" : ""}`}
                key={row.metric_index}
                onClick={() => setActiveMetric(index)}
              >
                <div className="metric-row-top">
                  <span className="metric-row-name">{row.metric_name}</span>
                  {row.needs_review ? (
                    rowBlank ? (
                      <span className="hint info" title="Not reported in this document">
                        <Info size={13} /> optional
                      </span>
                    ) : (
                      <span className="hint" title={row.review_reason ?? "Worth a quick check"}>
                        <AlertTriangle size={13} /> check
                      </span>
                    )
                  ) : null}
                </div>
                <div className="metric-edit" onClick={(e) => e.stopPropagation()}>
                  <input
                    onChange={(event) =>
                      editValue(row.metric_index, event.target.value)
                    }
                    onFocus={() => setActiveMetric(index)}
                    placeholder="Blank"
                    value={row.value === null ? "" : String(row.value)}
                  />
                  {row.unit || row.scale ? (
                    <span className="unit-label">
                      {[row.unit, row.scale].filter(Boolean).join(" · ")}
                    </span>
                  ) : null}
                </div>
                {row.needs_review ? (
                  <p className={`row-reason${rowBlank ? " info" : ""}`}>
                    {friendlyReason(row.review_reason, rowBlank)}
                  </p>
                ) : null}
              </div>
              );
            })}
          </div>

          <div className="list-footer">
            <button
              className="primary-button"
              disabled={isExporting}
              onClick={exportWorkbook}
              type="button"
            >
              {isExporting ? (
                <Loader2 size={18} className="spin" />
              ) : (
                <Download size={18} />
              )}
              {isExporting ? "Building workbook…" : "Export Excel"}
            </button>
            <p className="copy" style={{ fontSize: 12, margin: 0 }}>
              Exports every metric as shown above. Edit any value first if it
              needs a fix.
            </p>
            {error ? (
              <div className="status-box error">
                <strong>Export problem</strong>
                <p>{error}</p>
              </div>
            ) : null}
          </div>
        </aside>

        <section className="panel review-main">
          <div className="source-head">
            <div>
              <p className="eyebrow">Source</p>
              <h2>{metric ? metric.metric_name : "Select a metric"}</h2>
            </div>
            {metric ? (
              metric.value === null || metric.value === "" ? (
                <span className="confidence info">Not reported</span>
              ) : (
                <span className="confidence">
                  {Math.round(metric.confidence * 100)}% confidence
                </span>
              )
            ) : null}
          </div>

          {metric ? (
            <blockquote className="quote">
              Page {metric.source_page}: {metric.source_quote}
            </blockquote>
          ) : null}

          {metric?.needs_review ? (
            metric.value === null || metric.value === "" ? (
              <div className="why-check info">
                <Info size={16} />
                <div>
                  <strong>Not reported in this document</strong>
                  <p>{friendlyReason(metric.review_reason, true)}</p>
                </div>
              </div>
            ) : (
              <div className="why-check">
                <AlertTriangle size={16} />
                <div>
                  <strong>Why this is flagged for a check</strong>
                  <p>{friendlyReason(metric.review_reason, false)}</p>
                </div>
              </div>
            )
          ) : null}

          {doc ? (
            <PdfViewer metric={metric} ready={pdfReady} source={doc.source} />
          ) : null}
        </section>
      </div>
    </main>
  );
}

function PdfViewer({
  source,
  metric,
  ready,
}: {
  source: DocSource;
  metric: Metric | null;
  ready: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const docRef = useRef<unknown>(null);
  const tokenRef = useRef(0);
  const [rects, setRects] = useState<EvidenceRect[]>([]);
  const [pixelSize, setPixelSize] = useState({ width: 0, height: 0 });
  const [status, setStatus] = useState<string>("");

  const sourceKey =
    source.kind === "demo" ? `demo:${source.id}` : `file:${source.file.name}`;

  // Load the PDF document whenever the source changes.
  useEffect(() => {
    docRef.current = null;
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const lib = (window as unknown as { pdfjsLib: PdfjsLib }).pdfjsLib;
        const params =
          source.kind === "demo"
            ? { url: `/demo/${source.id}.pdf` }
            : { data: new Uint8Array(await source.file.arrayBuffer()) };
        const loaded = await lib.getDocument(params).promise;
        if (!cancelled) docRef.current = loaded;
      } catch {
        if (!cancelled) setStatus("Could not load PDF.");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, ready]);

  const renderPage = useCallback(async () => {
    if (!ready || !metric) return;
    const token = (tokenRef.current += 1);
    // Wait for the document to be loaded.
    for (let attempt = 0; attempt < 60 && !docRef.current; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (token !== tokenRef.current) return;
    }
    const pdf = docRef.current as PdfDocument | null;
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!pdf || !canvas || !wrap) return;
    setStatus("");
    try {
      const page = await pdf.getPage(metric.source_page);
      if (token !== tokenRef.current) return;
      const containerWidth = wrap.clientWidth || 600;
      const base = page.getViewport({ scale: 1 });
      const scale = Math.min(2, containerWidth / base.width);
      const viewport = page.getViewport({ scale });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      setPixelSize({ width: viewport.width, height: viewport.height });
      const context = canvas.getContext("2d");
      if (!context) return;
      await page.render({ canvasContext: context, viewport }).promise;
      if (token !== tokenRef.current) return;
      setRects(metric.evidence.matched ? metric.evidence.rects : []);
      if (!metric.evidence.matched)
        setStatus("No on-page match for this quote.");
    } catch {
      setStatus("Could not render this page.");
    }
  }, [metric, ready]);

  useEffect(() => {
    void renderPage();
  }, [renderPage, sourceKey]);

  return (
    <div className="pdf-pane">
      <div className="pdf-pane-head">
        <span className="copy" style={{ fontSize: 12 }}>
          {metric ? `Source page ${metric.source_page}` : "Source"}
        </span>
        {status ? <span className="pdf-status">{status}</span> : null}
      </div>
      <div className="pdf-scroll">
        <div
          className="page-wrap"
          ref={wrapRef}
          style={{ width: pixelSize.width || "100%" }}
        >
          <canvas ref={canvasRef} />
          {rects.map((rect, index) => (
            <div
              className="hl"
              key={index}
              style={{
                left: rect.left * pixelSize.width,
                top: rect.top * pixelSize.height,
                width: rect.width * pixelSize.width,
                height: rect.height * pixelSize.height,
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// --- pdf.js typing + loader ------------------------------------------------

type PdfViewport = { width: number; height: number };
type PdfPage = {
  getViewport: (opts: { scale: number }) => PdfViewport;
  render: (opts: {
    canvasContext: CanvasRenderingContext2D;
    viewport: PdfViewport;
  }) => { promise: Promise<void> };
};
type PdfDocument = { getPage: (n: number) => Promise<PdfPage> };
type PdfjsLib = {
  GlobalWorkerOptions: { workerSrc: string };
  getDocument: (params: { url: string } | { data: Uint8Array }) => {
    promise: Promise<PdfDocument>;
  };
};

function usePdfJs() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if ((window as unknown as { pdfjsLib?: PdfjsLib }).pdfjsLib) {
      setReady(true);
      return;
    }
    const script = document.createElement("script");
    script.src = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VERSION}/pdf.min.js`;
    script.onload = () => {
      const lib = (window as unknown as { pdfjsLib: PdfjsLib }).pdfjsLib;
      lib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VERSION}/pdf.worker.min.js`;
      setReady(true);
    };
    document.body.appendChild(script);
  }, []);
  return ready;
}

// --- helpers ---------------------------------------------------------------

// Turn the backend's terse review reasons into a plain-language explanation of
// why a metric was flagged for a check. Multiple reasons arrive joined by "; ".
function friendlyReason(reason: string | null, isBlank = false): string {
  // A required field with no extracted value isn't an error to fix — the metric
  // simply isn't reported in this document (common when a template built for one
  // company type is applied to another, e.g. gross margin on a bank). Present it
  // as optional rather than surfacing the underlying low-confidence / blank-field
  // flags, which read like something went wrong.
  if (isBlank) {
    return "Not reported in this document — leave it blank if the metric doesn't apply, or type a value above if you have one.";
  }
  if (!reason || !reason.trim()) {
    return "Worth a quick look — confirm this value against the highlighted source.";
  }
  const parts = reason
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const lower = part.toLowerCase();
      if (lower.startsWith("confidence below")) {
        return "The model wasn't confident about this one, so double-check it against the source.";
      }
      if (lower.startsWith("missing source evidence")) {
        return "No supporting text was found in the document — confirm the value or leave it blank.";
      }
      if (lower.startsWith("source quote not found on cited page")) {
        return "The quoted text wasn't found on the page it cites — verify the page and the value.";
      }
      if (lower.startsWith("reported value not found")) {
        return "This number doesn't appear in the quoted text — make sure it matches the source.";
      }
      if (lower.startsWith("template field is blank")) {
        return "Nothing was extracted for this required field — add the value, or leave it blank if it isn't reported.";
      }
      if (lower.endsWith("was not reported in the selected source pages.")) {
        return "This metric wasn't reported on the pages we scanned — add it manually if it appears elsewhere.";
      }
      return part;
    });
  return Array.from(new Set(parts)).join(" ");
}

function coerceValue(raw: string): string | number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const numeric = Number(trimmed.replace(/,/g, ""));
  if (trimmed !== "" && Number.isFinite(numeric) && /^[-\d.,]+$/.test(trimmed)) {
    return numeric;
  }
  return trimmed;
}

function sampleIdForFile(file: File): "tesla" | "citi" | null {
  const normalized = file.name.toLowerCase();
  if (normalized.includes("tsla") || normalized.includes("tesla")) {
    return "tesla";
  }
  if (normalized.includes("citi")) {
    return "citi";
  }
  return null;
}

function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

async function readApiJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) {
    return { ok: false, error: `Server returned ${response.status}.` };
  }
  try {
    return JSON.parse(text);
  } catch {
    return { ok: false, error: text.slice(0, 240) };
  }
}

function apiErrorMessage(
  body: { ok: true } | ApiError,
  status: number,
  fallback = "Extraction failed.",
): string {
  if (body.ok) return fallback;
  const raw = body.error ?? body.detail;
  if (typeof raw === "string" && raw.trim()) return raw;
  if (raw && typeof raw === "object") {
    const message = "message" in raw ? raw.message : null;
    if (typeof message === "string" && message.trim()) return message;
    try {
      return JSON.stringify(raw);
    } catch {
      return fallback;
    }
  }
  if (status >= 500) {
    return "Live upload failed on the hosted API. Use Samples for Tesla/Citi, or run locally with OPENAI_API_KEY for arbitrary PDFs.";
  }
  return fallback;
}

function fileToBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("Could not read file."));
        return;
      }
      resolve(result.split(",", 2)[1] ?? "");
    };
    reader.onerror = () => reject(new Error("Could not read file."));
    reader.readAsDataURL(file);
  });
}

function downloadBase64(base64: string, filename: string, type: string) {
  const binary = window.atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const blob = new Blob([bytes], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
