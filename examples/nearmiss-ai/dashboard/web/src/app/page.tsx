"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, BookOpen, ChevronDown, FileText, List, Menu, RefreshCw, Search } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

type CaseSummary = { acn: string; report_month: string; synopsis: string | null; anomaly: string | null; primary_problem: string | null; communication_breakdown: string | null };
type Candidate = { case_id: string; narrative: string; retrieval_score: number };
type CaseContext = { contract_version: string; case_id: string; narrative: string; metadata: Record<string, string | null>; candidate_incidents: Candidate[]; retrieval_method: string; data_source: { name: string; url: string; sample: string } };
type EvidenceRef = { document_id: string; title: string; source: string; page_or_region: string | null; passage_text: string; status: string; relevant_claim: string };
type PatternClaim = { pattern_family: string; supporting_incident_ids: string[] };
type AgentMessage = { agent: string; round_number: number; input_summary: string; decision_question: string; allowed_options: string[]; selected_option: string; probabilities: Record<string, number>; supporting_incident_ids: string[]; evidence_refs: EvidenceRef[]; uncertainty_status: string; duration_seconds: number; action_summary: string };
type InvestigationResult = { contract_version: string; case_id: string; run_id: string; status: "complete" | "incomplete"; agent_trace: AgentMessage[]; initial_claim: PatternClaim | null; revised_claim: PatternClaim | null; final_recommended_next_action: string | null; review_priority: string | null; references: EvidenceRef[]; total_duration_seconds: number; provider_model_details: Record<string, unknown>; errors: { code: string; message: string; agent: string | null }[]; policy_notes?: string[] };
type Stats = { records: number; first_month: string | null; last_month: string | null; sample: string; source_url: string; primary_problems: { name: string; count: number }[] };

async function readJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? `Request failed (${response.status})`);
  return body as T;
}

const savedDemos: Record<string, string> = { "2134024": "Supported live run", "2132897": "Evidence review and second round" };
const agentSteps = [
  { name: "Pattern scan", detail: "Compare the report with historical cases." },
  { name: "Evidence review", detail: "Check located guidance passages." },
  { name: "Investigation planning", detail: "Choose a human review step." },
  { name: "Coordinator synthesis", detail: "Resolve findings and uncertainty." },
];
const agentNames: Record<string, string> = { pattern: "Pattern analyst", evidence: "Evidence reviewer", investigation: "Investigation planner", coordinator: "Coordinator" };
const label = (value: string | null | undefined) => value ? value.replaceAll("_", " ") : "Not reported";
const PAGE_SIZE = 1000;
const monthLabel = (month: string) => { const [year, number] = month.split("-"); return new Date(Number(year), Number(number) - 1, 1).toLocaleString("en-US", { month: "short", year: "numeric" }); };
const periodLabel = (stats: Stats | null) => stats?.first_month && stats.last_month ? (stats.first_month === stats.last_month ? monthLabel(stats.first_month) : `${monthLabel(stats.first_month)} – ${monthLabel(stats.last_month)}`) : "";
const lastEvidence = (result: InvestigationResult | null) => [...(result?.agent_trace ?? [])].reverse().find((turn) => turn.agent === "evidence");

function CaseList({ cases, selected, query, category, loading, loadingMore, total, period, onQuery, onCategory, onSelect, onLoadMore }: {
  cases: CaseSummary[]; selected: string; query: string; category: string; loading: boolean; loadingMore: boolean; total: number | undefined; period: string;
  onQuery: (value: string) => void; onCategory: (value: string) => void; onSelect: (acn: string) => void; onLoadMore: () => void;
}) {
  return <div className="case-list">
    <div className="case-controls">
      <div className="search-field"><Search className="size-4 text-muted-foreground" aria-hidden="true" /><Input aria-label="Search reports or ACN" placeholder="Search reports or ACN" value={query} onChange={(event) => onQuery(event.target.value)} className="h-9 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0" /></div>
      <label className="sr-only" htmlFor="case-filter">Filter reports</label>
      <div className="relative"><select id="case-filter" value={category} onChange={(event) => onCategory(event.target.value)} className="filter-select"><option value="">All reports</option><option value="Communication">Communication</option></select><ChevronDown className="pointer-events-none absolute right-3 top-2.5 size-4 text-muted-foreground" aria-hidden="true" /></div>
      <div className="flex items-center justify-between text-xs text-muted-foreground"><span>{loading ? "Loading reports…" : `${cases.length} reports${total !== undefined && cases.length !== total ? ` of ${total}` : ""}`}</span><span>{period}</span></div>
    </div>
    <Separator />
    <ScrollArea className="case-list-scroll">
      <div className="case-items">
        {loading && [1, 2, 3, 4].map((item) => <Skeleton key={item} className="h-19 rounded-md" />)}
        {!loading && cases.length === 0 && <p className="p-4 text-sm text-muted-foreground">No reports match the filters.</p>}
        {cases.map((item) => <button type="button" key={item.acn} onClick={() => onSelect(item.acn)} aria-current={item.acn === selected ? "true" : undefined} className={`case-item ${item.acn === selected ? "is-selected" : ""}`}>
          <span className="font-mono text-xs font-semibold tabular-nums text-foreground">ACN {item.acn}</span>
          <span className="line-clamp-2 text-left text-sm leading-5 text-foreground/85">{item.synopsis || "No synopsis available"}</span>
          <span className="w-fit rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{item.primary_problem || "Uncoded"}</span>
        </button>)}
        {!loading && total !== undefined && cases.length < total && <Button type="button" variant="outline" size="sm" onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? "Loading…" : `Load more (${total - cases.length} remaining)`}</Button>}
      </div>
    </ScrollArea>
  </div>;
}

function CaseSummaryBlock({ context, result }: { context: CaseContext; result: InvestigationResult | null }) {
  const evidence = lastEvidence(result);
  return <section className="case-summary" aria-label="Case summary">
    <div className="summary-facts">
      <div><span className="field-label">Primary problem</span><strong>{label(context.metadata.primary_problem)}</strong></div>
      <div><span className="field-label">Flight phase</span><strong>{label(context.metadata.flight_phase)}</strong></div>
      <div><span className="field-label">Location</span><strong>{label(context.metadata.locale)}</strong></div>
      {result?.review_priority && <div><span className="field-label">Review priority</span><strong>{label(result.review_priority)}</strong></div>}
      {evidence && <div><span className="field-label">Evidence status</span><strong className={evidence.uncertainty_status === "supported" ? "text-success" : "text-warning"}>{label(evidence.uncertainty_status)}</strong></div>}
    </div>
    <Separator />
    <div className="summary-action"><span className="field-label">Recommended next analyst action</span><p className={result?.status === "complete" ? "font-semibold text-foreground" : "text-muted-foreground"}>{result?.status === "complete" ? label(result.final_recommended_next_action) : "Run analysis to get a recommendation"}</p></div>
  </section>;
}

function EvidenceSheet({ reference }: { reference: EvidenceRef }) {
  return <Sheet>
    <SheetTrigger render={<Button variant="outline" size="xs" className="h-auto max-w-full justify-start whitespace-normal text-left" />}><BookOpen className="size-3.5 shrink-0" />{reference.title} · {reference.page_or_region || "Location unavailable"}</SheetTrigger>
    <SheetContent side="right" className="w-full max-w-md gap-0 border-border shadow-none duration-150">
      <SheetHeader className="border-b border-border p-6 pr-12"><SheetTitle>Source passage</SheetTitle><SheetDescription>Evidence returned by the analysis service.</SheetDescription></SheetHeader>
      <div className="space-y-5 overflow-y-auto p-6 text-sm">
        <div><span className="field-label">Document</span><p className="mt-1 font-medium">{reference.title}</p></div>
        <div><span className="field-label">Source</span><a href={reference.source} target="_blank" rel="noreferrer" className="mt-1 block break-all text-primary underline underline-offset-4">{reference.source}</a></div>
        <div><span className="field-label">Location</span><p className="mt-1">{reference.page_or_region || "Location unavailable"}</p></div>
        <div><span className="field-label">Grounding status</span><p className={`mt-1 font-medium ${reference.status === "grounded" ? "text-success" : "text-warning"}`}>{label(reference.status)}</p></div>
        <Separator />
        <blockquote className="text-[15px] leading-7 text-foreground">{reference.passage_text}</blockquote>
        <div><span className="field-label">Relevant claim</span><p className="mt-1">{reference.relevant_claim}</p></div>
      </div>
    </SheetContent>
  </Sheet>;
}

function AgentTurn({ message, roundTwoLabel }: { message: AgentMessage; roundTwoLabel: string | null }) {
  const challenged = message.agent === "evidence" && message.selected_option === "conflicts";
  return <div className={`agent-turn ${challenged || roundTwoLabel ? "has-warning" : ""}`}>
    <span className="turn-node" aria-hidden="true" />
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{agentNames[message.agent] ?? label(message.agent)}</h3><span className="text-xs text-muted-foreground">Round {message.round_number}</span>{roundTwoLabel && <Badge variant="outline" className="border-warning/30 bg-warning/10 text-warning">{roundTwoLabel}</Badge>}{challenged && <Badge variant="outline" className="border-warning/30 bg-warning/10 text-warning">Challenged by Evidence</Badge>}</div>
      <p className="text-xs leading-5 text-muted-foreground">{message.decision_question}</p>
      <p className="text-sm font-semibold text-foreground">{label(message.selected_option)}</p>
      <div className="space-y-1.5" aria-label="Drex decision scores">{Object.entries(message.probabilities ?? {}).sort((a, b) => b[1] - a[1]).map(([choice, score]) => <div key={choice} className="score-row"><span className="truncate text-xs text-muted-foreground" title={label(choice)}>{label(choice)}</span><div className="h-1 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${Math.max(0, Math.min(score, 1)) * 100}%` }} /></div><span className="whitespace-nowrap text-right text-[11px] tabular-nums text-muted-foreground">{(score * 100).toFixed(0)}% decision score</span></div>)}</div>
      <p className="truncate text-xs text-muted-foreground" title={message.action_summary}>Shared state: {message.action_summary}</p>
      {!!message.evidence_refs?.length && <div className="flex flex-wrap gap-1.5">{message.evidence_refs.map((reference, index) => <EvidenceSheet key={`${reference.document_id}-${reference.page_or_region}-${index}`} reference={reference} />)}</div>}
    </div>
  </div>;
}

function InvestigationPanel({ context, online, evidenceReady, analyzing, result, cached, error, onRun, onCached }: {
  context: CaseContext | null; online: boolean; evidenceReady: boolean; analyzing: boolean; result: InvestigationResult | null; cached: boolean; error: string | null; onRun: () => void; onCached: () => void;
}) {
  const challengedByEvidence = result?.agent_trace.some((turn) => turn.agent === "evidence" && turn.round_number === 1 && turn.selected_option === "conflicts") ?? false;
  return <aside className="investigation-panel" aria-label="Investigation">
    <div className="investigation-top"><div><h2 className="text-lg font-semibold">Investigation</h2><p className="mt-1 text-xs text-muted-foreground">Four agents review the selected report.</p></div><Badge variant="outline" className="text-muted-foreground">Drex + NDI</Badge></div>
    <div className="investigation-content">
      <TooltipProvider><Tooltip><TooltipTrigger render={<span className="block w-full" />}><Button onClick={onRun} disabled={!online || !context || analyzing} variant={online ? "default" : "secondary"} className="w-full justify-center rounded-md" size="lg">{analyzing ? "Analysis in progress" : "Run collaborative analysis"}</Button></TooltipTrigger>{!online && <TooltipContent>Analysis service is offline. Refresh when the service starts.</TooltipContent>}</Tooltip></TooltipProvider>
      {online && <p className="text-xs text-muted-foreground">Evidence service: {evidenceReady ? "ready" : "not ready"}</p>}
      <Button variant="link" className="h-auto justify-start px-0 text-xs" onClick={onCached}>{context?.case_id && savedDemos[context.case_id] ? `Open saved run · ${savedDemos[context.case_id]}` : "Open a prior live run"}</Button>
      {error && <Alert variant="destructive"><AlertCircle className="size-4" /><AlertTitle>Analysis unavailable</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      {cached && result && <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">Cached run</div>}
      {result?.status === "complete" && <div className="final-action"><span className="field-label">Final recommendation</span><p className="mt-1 font-semibold">{label(result.final_recommended_next_action)}</p><Badge variant="outline" className="mt-2">{label(result.review_priority)} priority</Badge></div>}
      {result?.status === "incomplete" && <Alert className="border-warning/40 bg-warning/10 text-foreground"><AlertCircle className="size-4 text-warning" /><AlertTitle>Analysis incomplete</AlertTitle><AlertDescription>No final recommendation is available.</AlertDescription></Alert>}
      {!!result?.errors?.length && result.errors.map((item, index) => <Alert key={`${item.code}-${index}`} variant="destructive"><AlertCircle className="size-4" /><AlertTitle>{item.agent ? `${label(item.agent)} error` : "Service error"}</AlertTitle><AlertDescription>{item.message}</AlertDescription></Alert>)}
      {result?.revised_claim && <div className="border-l-2 border-warning pl-3 text-xs leading-5"><strong className="text-warning">Second round</strong><p className="mt-1 text-muted-foreground">Pattern claim changed from {label(result.initial_claim?.pattern_family)} to {label(result.revised_claim.pattern_family)}.</p></div>}
      {!!result?.policy_notes?.length && <div className="text-xs leading-5 text-muted-foreground">{result.policy_notes.join(" ")}</div>}
      <Separator />
      {analyzing ? <div className="space-y-4" aria-live="polite"><p className="text-sm font-medium">Review in progress</p><p className="text-xs text-muted-foreground">The service returns the complete trace when the run finishes.</p><AgentStepper active /></div> : result ? <div className="space-y-4"><div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">Agent trace</h3><span className="text-xs tabular-nums text-muted-foreground">{result.agent_trace.length} turns · {result.total_duration_seconds.toFixed(1)}s</span></div><div className="timeline">{result.agent_trace.map((turn, index) => <AgentTurn key={`${turn.agent}-${turn.round_number}-${index}`} message={turn} roundTwoLabel={turn.round_number === 2 && turn.agent === "pattern" ? challengedByEvidence ? "Challenged by Evidence" : "Re-evaluated after uncertainty" : null} />)}</div><p className="break-all text-[11px] text-muted-foreground">Run {result.run_id}</p></div> : <AgentStepper active={false} />}
      <p className="text-xs leading-5 text-muted-foreground">Decision scores show model preference, not accident probability. Source passages appear only when returned by the analysis service.</p>
    </div>
  </aside>;
}

function AgentStepper({ active }: { active: boolean }) {
  return <ol className="space-y-4" aria-label="Investigation stages">{agentSteps.map((step, index) => <li key={step.name} className="flex gap-3"><span className={`step-dot ${active && index === 0 ? "is-active" : ""}`}>{active && index === 0 ? <span className="size-1.5 rounded-full bg-primary" /> : index + 1}</span><div><p className={`text-sm font-medium ${active && index === 0 ? "text-foreground" : "text-muted-foreground"}`}>{step.name}</p><p className="mt-0.5 text-xs text-muted-foreground">{active && index === 0 ? "Processing; later stages are pending." : step.detail}</p></div></li>)}</ol>;
}

function RelatedReports({ context }: { context: CaseContext }) {
  const [open, setOpen] = useState<string | null>(null);
  return <section className="space-y-4"><div><h3 className="text-base font-semibold">Related reports</h3><p className="mt-1 text-sm text-muted-foreground">{context.candidate_incidents.length} text-matched historical reports</p></div>
    {context.candidate_incidents.length ? <Table><TableHeader><TableRow><TableHead>ACN</TableHead><TableHead className="w-24">Match</TableHead><TableHead className="w-32">Score</TableHead></TableRow></TableHeader><TableBody>{context.candidate_incidents.map((candidate) => <FragmentCandidate key={candidate.case_id} candidate={candidate} open={open === candidate.case_id} onToggle={() => setOpen(open === candidate.case_id ? null : candidate.case_id)} />)}</TableBody></Table> : <p className="text-sm text-muted-foreground">No historical neighbors found.</p>}
    <p className="text-xs leading-5 text-muted-foreground">{context.retrieval_method}</p>
  </section>;
}

function FragmentCandidate({ candidate, open, onToggle }: { candidate: Candidate; open: boolean; onToggle: () => void }) {
  return <><TableRow><TableCell><button type="button" className="font-mono text-xs font-medium text-primary underline-offset-2 hover:underline focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring" onClick={onToggle} aria-expanded={open}>ACN {candidate.case_id}</button></TableCell><TableCell className="text-xs tabular-nums">{Math.round(candidate.retrieval_score * 100)}% match</TableCell><TableCell><div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(0, Math.min(candidate.retrieval_score, 1)) * 100}%` }} /></div></TableCell></TableRow>{open && <TableRow><TableCell colSpan={3} className="whitespace-normal pb-4 text-sm leading-6 text-muted-foreground">{candidate.narrative}</TableCell></TableRow>}</>;
}

function ReportDetails({ context, stats }: { context: CaseContext; stats: Stats | null }) {
  const rows = [["Primary problem", context.metadata.primary_problem], ["Anomaly", context.metadata.anomaly], ["Flight phase", context.metadata.flight_phase], ["Location", context.metadata.locale], ["Communication", context.metadata.communication_breakdown], ["Human factors", context.metadata.human_factors], ["Local time", context.metadata.local_time]];
  return <section className="space-y-7"><div><h3 className="mb-3 text-base font-semibold">Report details</h3><dl>{rows.map(([name, value]) => <div className="detail-row" key={name}><dt>{name}</dt><dd>{value || "Not reported"}</dd></div>)}</dl></div><div><h3 className="mb-1 text-base font-semibold">Archive context</h3><p className="mb-4 text-xs text-muted-foreground">Source-coded counts in this export only; they do not estimate incident prevalence.</p><div className="space-y-2">{stats?.primary_problems.slice(0, 8).map((item) => <div key={item.name} className="grid grid-cols-[minmax(105px,1fr)_minmax(50px,2fr)_30px] items-center gap-3 text-xs"><span className="truncate">{item.name}</span><div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${(item.count / (stats.primary_problems[0]?.count || 1)) * 100}%` }} /></div><span className="text-right tabular-nums text-muted-foreground">{item.count}</span></div>)}</div>{stats && <p className="mt-5 text-xs text-muted-foreground">Source: <a href={stats.source_url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-4">NASA ASRS Database Online</a>. {stats.sample}</p>}</div></section>;
}

export default function Home() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selected, setSelected] = useState("2134024");
  const [context, setContext] = useState<CaseContext | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [analysisOnline, setAnalysisOnline] = useState(false);
  const [evidenceReady, setEvidenceReady] = useState(false);
  const [loadingCases, setLoadingCases] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [caseTotal, setCaseTotal] = useState<number | undefined>(undefined);
  const listKey = useRef("");
  const [loadingContext, setLoadingContext] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [cached, setCached] = useState(false);
  const [activeTab, setActiveTab] = useState("brief");
  const [mobileCasesOpen, setMobileCasesOpen] = useState(false);

  const refreshHealth = useCallback(async () => {
    try { const value = await readJSON<{status: string}>("/api/analysis-health"); setAnalysisOnline(value.status === "ready"); } catch { setAnalysisOnline(false); }
    try { const value = await readJSON<{status: string}>("/api/evidence-status"); setEvidenceReady(value.status === "ready"); } catch { setEvidenceReady(false); }
  }, []);

  useEffect(() => { readJSON<Stats>("/api/data/stats").then(setStats).catch((e) => setError(e.message)); refreshHealth(); }, [refreshHealth]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoadingCases(true);
      listKey.current = `${query}|${category}`;
      const params = new URLSearchParams({ phrase: query, category, limit: String(PAGE_SIZE) });
      readJSON<{cases: CaseSummary[]; total: number}>(`/api/data/cases?${params}`, { signal: controller.signal })
        .then((value) => { setCases(value.cases); setCaseTotal(value.total); setError(null); if (value.cases.length) setSelected((current) => value.cases.some((item) => item.acn === current) ? current : value.cases[0].acn); })
        .catch((e) => { if (e.name !== "AbortError") setError(e.message); })
        .finally(() => setLoadingCases(false));
    }, 220);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, category]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setLoadingContext(true); setContext(null); setResult(null); setCached(false); setAnalysisError(null);
    readJSON<CaseContext>(`/api/data/cases/${selected}/context`, { signal: controller.signal })
      .then((value) => { setContext(value); setError(null); })
      .catch((e) => { if (e.name !== "AbortError") setError(e.message); })
      .finally(() => setLoadingContext(false));
    return () => controller.abort();
  }, [selected]);

  const selectedSummary = useMemo(() => cases.find((item) => item.acn === selected), [cases, selected]);
  const displayedCases = useMemo(() => { const featured = cases.find((item) => item.acn === "2134024"); return featured ? [featured, ...cases.filter((item) => item.acn !== featured.acn)] : cases; }, [cases]);
  async function loadMoreCases() {
    const key = `${query}|${category}`;
    setLoadingMore(true);
    try {
      const params = new URLSearchParams({ phrase: query, category, limit: String(PAGE_SIZE), offset: String(cases.length) });
      const value = await readJSON<{cases: CaseSummary[]; total: number}>(`/api/data/cases?${params}`);
      if (listKey.current !== key) return; // filters changed while loading; discard the stale page
      setCases((current) => { const seen = new Set(current.map((item) => item.acn)); return [...current, ...value.cases.filter((item) => !seen.has(item.acn))]; });
      setCaseTotal(value.total);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load more reports"); }
    finally { setLoadingMore(false); }
  }
  const selectCase = (acn: string) => { setSelected(acn); setActiveTab("brief"); setMobileCasesOpen(false); };

  async function runAnalysis() {
    if (!context) return;
    setAnalyzing(true); setAnalysisError(null); setResult(null); setCached(false);
    try {
      const value = await readJSON<InvestigationResult>("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(context) });
      if (value.contract_version !== "1.0" || value.case_id !== context.case_id || !Array.isArray(value.agent_trace)) throw new Error("Analysis service returned a mismatched result");
      setResult(value);
      if (value.status === "complete" && value.agent_trace.length > 0) localStorage.setItem(`nearmiss:run:${context.case_id}`, JSON.stringify(value));
    } catch (e) { setAnalysisError(e instanceof Error ? e.message : "Analysis failed"); }
    finally { setAnalyzing(false); refreshHealth(); }
  }
  async function loadCached() {
    try {
      let value = JSON.parse(localStorage.getItem(`nearmiss:run:${selected}`) || "null") as InvestigationResult | null;
      if (!value && savedDemos[selected]) value = await readJSON<InvestigationResult>(`/demos/${selected}.json`);
      if (!value || value.case_id !== selected || value.contract_version !== "1.0" || !Array.isArray(value.agent_trace)) throw new Error("No valid prior live run found for this case");
      setResult(value); setCached(true); setAnalysisError(null);
    } catch (e) { setAnalysisError(e instanceof Error ? e.message : "Could not load saved run"); }
  }

  const caseList = <CaseList cases={displayedCases} selected={selected} query={query} category={category} loading={loadingCases} loadingMore={loadingMore} total={caseTotal} period={periodLabel(stats)} onQuery={setQuery} onCategory={setCategory} onSelect={selectCase} onLoadMore={loadMoreCases} />;
  return <div className="app-shell">
    <header className="topbar"><div className="wordmark">NearMiss <span>AI</span></div><div className="flex items-center gap-2"><Badge variant="outline" className={`status-pill ${analysisOnline ? "is-online" : "is-offline"}`}><span className="status-dot" />Analysis {analysisOnline ? "online" : "offline"}</Badge><Button variant="ghost" size="icon-sm" onClick={refreshHealth} aria-label="Refresh analysis status" title="Refresh analysis status"><RefreshCw className="size-4" /></Button></div></header>
    <div className="workspace-grid">
      <aside className="case-column" aria-label="Case directory">{caseList}</aside>
      <main className="case-main">
        <div className="case-main-inner">
          <div className="mobile-case-trigger"><Sheet open={mobileCasesOpen} onOpenChange={setMobileCasesOpen}><SheetTrigger render={<Button variant="outline" size="sm" />}><Menu className="size-4" />Browse cases</SheetTrigger><SheetContent side="left" className="w-[min(100vw,340px)] max-w-none gap-0 border-border bg-background p-0 shadow-none duration-150"><SheetHeader className="border-b border-border p-4"><SheetTitle>Case directory</SheetTitle><SheetDescription>Search the ASRS export.</SheetDescription></SheetHeader>{caseList}</SheetContent></Sheet></div>
          {error && <Alert variant="destructive" className="mb-5"><AlertCircle className="size-4" /><AlertTitle>Data service issue</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
          <div className="case-heading"><div><p className="field-label">Selected report</p><h1>Incident <span className="font-mono tabular-nums">{selected}</span></h1><p className="mt-1 text-xs text-muted-foreground">NASA ASRS · {selectedSummary?.report_month || context?.metadata.report_month || ""}</p></div><Badge variant="outline">ASRS source</Badge></div>
          {loadingContext ? <div className="space-y-4"><Skeleton className="h-35 rounded-lg" /><Skeleton className="h-8 w-48" /><Skeleton className="h-64 rounded-lg" /></div> : context ? <><CaseSummaryBlock context={context} result={result} /><Tabs value={activeTab} onValueChange={setActiveTab} className="case-tabs"><TabsList variant="line" className="w-full justify-start gap-5 border-b border-border"><TabsTrigger value="brief" className="case-tab"><FileText className="size-4" />Brief</TabsTrigger><TabsTrigger value="related" className="case-tab"><List className="size-4" />Related</TabsTrigger><TabsTrigger value="details" className="case-tab">Details</TabsTrigger></TabsList><TabsContent value="brief" className="case-tab-content"><p className="mb-3 text-sm font-medium">{context.metadata.synopsis || selectedSummary?.synopsis || "Original ASRS report"}</p><p className="narrative-text whitespace-pre-wrap">{context.narrative}</p>{context.metadata.supplemental_narrative && <details className="mt-6 border-t border-border pt-4"><summary className="cursor-pointer text-sm font-medium text-primary focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-ring">Supplemental reporter narrative</summary><p className="narrative-text mt-4 whitespace-pre-wrap">{context.metadata.supplemental_narrative}</p></details>}</TabsContent><TabsContent value="related" className="case-tab-content"><RelatedReports context={context} /></TabsContent><TabsContent value="details" className="case-tab-content"><ReportDetails context={context} stats={stats} /></TabsContent></Tabs></> : <p className="text-sm text-muted-foreground">Select a report to load its original narrative.</p>}
        </div>
      </main>
      <InvestigationPanel context={context} online={analysisOnline} evidenceReady={evidenceReady} analyzing={analyzing} result={result} cached={cached} error={analysisError} onRun={runAnalysis} onCached={loadCached} />
    </div>
    <footer className="app-footer">De-identified ASRS reports, unverified by NASA. Text similarity is not a risk score. Human review required.</footer>
  </div>;
}
