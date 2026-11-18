import InvestmentResearch from './InvestmentResearch';
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronRight,
  Download,
  FileText,
  Filter,
  LayoutDashboard,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  X,
  ExternalLink,
  Clock,
  AlertTriangle,
  Database,
  BarChart3,
} from "lucide-react";
import {
  api,
  post,
  labels,
  format,
  money,
  localTime,
  navigate,
  useParams,
  AnyRecord,
} from "./api";
import "./styles.css";
import Conversation, { ChatLink, AgentPlan, ResearchGaps } from "./Conversation";

function Badge({
  status,
  children,
}: {
  status?: string;
  children?: React.ReactNode;
}) {
  return (
    <span className={"badge " + (status || "")}>
      {children || labels[status || ""] || status}
    </span>
  );
}
function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="notice danger" role="alert">
      <AlertTriangle size={17} />
      {error}
    </div>
  ) : null;
}
function Empty({
  title = "暂无内容",
  text = "调整筛选条件后重试。",
  children,
}: {
  title?: string;
  text?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <FileText size={32} />
      <h3>{title}</h3>
      <p>{text}</p>
      {children}
    </div>
  );
}
function Loading() {
  return (
    <div className="empty">
      <LoaderCircle className="spin" />
      <p>正在加载…</p>
    </div>
  );
}
function MultiSelect({
  label,
  values,
  options,
  onChange,
}: {
  label: string;
  values: string[];
  options: { id: string; name: string }[];
  onChange: (v: string[]) => void;
}) {
  return (
    <label className="multi-label">
      {label}
      <select
        multiple
        aria-label={label}
        value={values}
        onChange={(e) =>
          onChange(Array.from(e.target.selectedOptions, (o) => o.value))
        }
      >
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </label>
  );
}
function Dialog({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: React.ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      className={wide ? "drawer" : "modal"}
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === ref.current) close();
      }}
    >
      <header>
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭" onClick={close}>
          <X />
        </button>
      </header>
      {children}
    </dialog>
  );
}

function App() {
  const [route, setRoute] = useState(window.location.hash || "#/new");
  useEffect(() => {
    const change = () => setRoute(window.location.hash || "#/new");
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  const path = route.split("?")[0].slice(1),
    id = path.split("/")[2];
  return (
    <div className="shell">
      <aside className="sidebar">
        <a href="#/new" className="brand">
          <span className="brand-mark">
            <Activity size={26} />
          </span>
          <span>
            TradingAgents<small>A 股研究工作台</small>
          </span>
        </a>
        <div className="nav-group">
          <a className={path.startsWith("/chat") ? "active" : ""} href="#/chat"><Activity size={19}/>对话分析</a>
          <a className={path === "/new" ? "active" : ""} href="#/new">
            <Plus size={19} />
            新建复盘
          </a>
          <a
            className={
              path.startsWith("/history") ||
              path.startsWith("/report") ||
              path.startsWith("/run")
                ? "active"
                : ""
            }
            href="#/history"
          >
            <BookOpen size={19} />
            历史复盘
          </a>
          <a className={path === "/settings" ? "active" : ""} href="#/settings">
            <Settings2 size={19} />
            数据与模型
          </a>
        </div>
        <div className="sidebar-foot">
          <span className="dot" /> 本地工作区
          <small>数据与报告保存在此设备</small>
          <span className="version">V4 · 开发预览</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>
            研究 /{" "}
            {path.startsWith("/chat") ? "对话分析" : path === "/settings"
              ? "数据与模型"
              : path === "/new"
                ? "新建复盘"
                : "盘后复盘"}
          </span>
          <span className="timezone">
            <Clock size={14} />
            北京时间 UTC+8
          </span>
        </header>
        <main>
          {path.startsWith("/chat") ? (
            <Conversation key={id || "new-chat"} id={id} />
          ) : path === "/settings" ? (
            <Settings />
          ) : path === "/history" ? (
            <History />
          ) : path.startsWith("/run/") ? (
            <Run key={id} id={id} />
          ) : path.startsWith("/report/") ? (
            <Report key={id} id={id} />
          ) : (
            <NewReview key={route.split("?")[0]} />
          )}
        </main>
      </div>
    </div>
  );
}

function Picker({
  kind,
  mode,
  selected,
  setSelected,
  limit,
}: {
  kind: string;
  mode: string;
  selected: AnyRecord[];
  setSelected: (v: AnyRecord[]) => void;
  limit: number;
}) {
  const [q, setQ] = useState(""),
    [options, setOptions] = useState<AnyRecord[]>([]),
    [open, setOpen] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [hasCatalog, setHasCatalog] = useState(false),
    [refresh, setRefresh] = useState(0),
    [syncing, setSyncing] = useState(false),
    [syncNote, setSyncNote] = useState("");
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    const base = `/instruments?mode=${mode}&kind=${kind}`;
    Promise.all([
      api(base + `&q=${encodeURIComponent(q)}`),
      q ? api(base) : Promise.resolve(null),
    ])
      .then(([r, all]) => {
        if (live) {
          setOptions(r);
          setHasCatalog((all || r).length > 0);
        }
      })
      .catch((e) => {
        if (live) {
          setOptions([]);
          setError(e.message);
        }
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [mode, q, kind, refresh]);
  const available = options.filter((o) => !selected.some((s) => s.id === o.id));
  return (
    <div className="picker">
      <div className="input-icon">
        <Search size={17} />
        <input
          aria-label={kind === "stock" ? "搜索股票" : "搜索行业"}
          placeholder={
            kind === "stock" ? "输入股票名称或代码，如 600519" : "搜索行业名称"
          }
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
          }}
        />
        <span className="count">
          {selected.length}/{limit}
        </span>
      </div>
      {open && (
        <div className="suggestions">
          <div className="suggestion-top">
            <span>{kind === "stock" ? "匹配证券" : "行业分类"}</span>
            <button
              aria-label="关闭搜索建议"
              className="icon-button"
              onClick={() => setOpen(false)}
            >
              <X size={14} />
            </button>
          </div>
          {loading ? (
            <p>正在加载目录…</p>
          ) : error ? (
            <>
              <p role="alert">目录加载失败：{error}</p>
              <button type="button" onClick={() => setRefresh(refresh + 1)}>
                重试加载
              </button>
            </>
          ) : available.length ? (
            available.slice(0, 10).map((o) => (
              <button
                type="button"
                key={o.id}
                disabled={selected.length >= limit}
                onClick={() => {
                  setSelected([...selected, o]);
                  setQ("");
                  setOpen(false);
                }}
              >
                <span>
                  {o.name}
                  <small>{o.id}</small>
                </span>
                <span className="muted">{o.source}</span>
                <Plus size={15} />
              </button>
            ))
          ) : (
            <div>
              <p>
                {hasCatalog
                  ? options.length
                    ? "匹配项已全部选择。"
                    : "没有匹配结果，请尝试股票代码或其他名称。"
                  : mode === "live"
                    ? `正式${kind === "stock" ? "股票" : "行业"}目录尚未同步成功。股票与行业权限独立，行业不可用时仍可按股票复盘。`
                    : "样例目录没有可选项。"}
              </p>
              {!hasCatalog && mode === "live" && (
                <>
                  <button
                    type="button"
                    disabled={syncing}
                    onClick={async () => {
                      setSyncing(true);
                      setSyncNote("");
                      try {
                        const result = await post("/catalog/sync");
                        setSyncNote(
                          `已同步 ${result.count} 条记录。${(result.warnings || []).join("；")}`,
                        );
                        setRefresh((v) => v + 1);
                      } catch (e: any) {
                        setSyncNote(e.message);
                      } finally {
                        setSyncing(false);
                      }
                    }}
                  >
                    {syncing ? "正在同步…" : "同步正式目录"}
                  </button>
                  <a href="#/settings">配置行情凭证与查看权限</a>
                </>
              )}
            </div>
          )}
          {syncNote && <p role="status">{syncNote}</p>}
        </div>
      )}
      <div className="chips">
        {selected.map((s) => (
          <span className="chip" key={s.id}>
            {s.name}
            <small>{s.id}</small>
            <button
              aria-label={"移除" + s.name}
              onClick={() => setSelected(selected.filter((x) => x.id !== s.id))}
            >
              <X size={13} />
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}

function NewReview() {
  const [mode, setMode] = useState("live"),
    [day, setDay] = useState(""),
    [time, setTime] = useState("18:00"),
    [lookback, setLookback] = useState(7);
  const [stocks, setStocks] = useState<AnyRecord[]>([]),
    [sectors, setSectors] = useState<AnyRecord[]>([]),
    [settings, setSettings] = useState<AnyRecord | null>(null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [preflight, setPreflight] = useState<AnyRecord | null>(null),
    [advanced, setAdvanced] = useState(false);
  const [executionMode, setExecutionMode] = useState("workflow");
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => {
    api("/settings")
      .then(setSettings)
      .catch((e) => setError(e.message));
    api("/defaults").then((d) => {
      setDay(d.trade_date);
      setTime(d.time);
    });
    const copy = useParams().get("copy");
    if (copy)
      api("/reviews/" + copy)
        .then(async (r) => {
          setMode(r.request.mode);
          setDay(r.request.trade_date);
          setTime(r.request.as_of.slice(11, 16));
          setLookback(r.request.lookback);
          const all = await api("/instruments?mode=" + r.request.mode);
          setStocks(
            all.filter((x: AnyRecord) => r.request.stocks.includes(x.id)),
          );
          setSectors(
            all.filter((x: AnyRecord) => r.request.sectors.includes(x.id)),
          );
        })
        .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    setPreflight(null);
    requestId.current = crypto.randomUUID();
  }, [day, time, mode, stocks, sectors, lookback, executionMode]);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      const request = {
        trade_date: day,
        as_of: `${day}T${time}:00+08:00`,
        stocks: stocks.map((s) => s.id),
        sectors: sectors.map((s) => s.id),
        mode,
        lookback,
        execution_mode: executionMode,
      };
      const check = await post("/reviews/preflight", request);
      setPreflight(check);
      if (!check.ok) throw new Error(check.errors.join("；"));
      const r = await api("/reviews", {
        method: "POST",
        headers: { "Idempotency-Key": requestId.current },
        body: JSON.stringify(request),
      });
      navigate("/run/" + r.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function switchMode(value: string) {
    setMode(value);
    setStocks([]);
    setSectors([]);
    setError("");
    api("/defaults?mode=" + value).then((d) => {
      setDay(d.trade_date);
      setTime(d.time);
    });
  }
  return (
    <>
      <div className="page-title">
        <div>
          <h1>把今天看清楚</h1>
          <p>汇集量价、板块与政策证据，形成一份可追溯的盘后复盘。</p>
        </div>
        <span className="heading-symbol">
          <BarChart3 size={32} />
        </span>
      </div>
      <div className="notice"><a href="#/chat">用自然语言描述需求</a><label> 执行方式 <select value={executionMode} onChange={e=>setExecutionMode(e.target.value)}><option value="workflow">标准流程</option><option value="autonomous">自主分析（预览）</option></select></label></div>
      <div className="new-grid">
        <section className="panel form-panel">
          <div className="section-heading">
            <h2>复盘范围</h2>
            <Badge>{mode === "demo" ? "样例数据" : "正式数据"}</Badge>
          </div>
          <div className="mode-switch" role="group" aria-label="数据模式">
            <button
              className={mode === "live" ? "selected" : ""}
              onClick={() => switchMode("live")}
            >
              正式数据
            </button>
            <button
              className={mode === "demo" ? "selected" : ""}
              onClick={() => switchMode("demo")}
            >
              样例演示
            </button>
          </div>
          {mode === "demo" && (
            <div className="notice amber">
              合成行情与虚构事件，仅用于验证流程。分析使用规则模板，未调用模型。
            </div>
          )}
          <div className="form-row">
            <label>
              交易日
              <input
                type="date"
                value={day}
                max={new Date().toLocaleDateString("en-CA", {
                  timeZone: "Asia/Shanghai",
                })}
                onChange={(e) => setDay(e.target.value)}
              />
            </label>
            <label>
              信息截止时间
              <input
                type="time"
                value={time}
                min="15:00"
                onChange={(e) => setTime(e.target.value)}
              />
              <small>北京时间，资料仅纳入该时点之前</small>
            </label>
          </div>
          <div className="field">
            <label>
              关注股票<span className="optional">最多 10 只</span>
            </label>
            <Picker
              kind="stock"
              mode={mode}
              selected={stocks}
              setSelected={setStocks}
              limit={10}
            />
          </div>
          <div className="field">
            <label>
              关注行业<span className="optional">最多 5 个</span>
            </label>
            <Picker
              kind="sector"
              mode={mode}
              selected={sectors}
              setSelected={setSectors}
              limit={5}
            />
          </div>
          <button
            className="text-button"
            onClick={() => setAdvanced(!advanced)}
          >
            <SlidersHorizontal size={16} />
            高级选项
            <ChevronRight className={advanced ? "rotate" : ""} size={14} />
          </button>
          {advanced && (
            <label className="compact-label">
              政策回看天数
              <input
                type="number"
                min="1"
                max="30"
                value={lookback}
                onChange={(e) => setLookback(Number(e.target.value))}
              />
            </label>
          )}
          <ErrorBox error={error} />
          {preflight?.warnings.map((w: string) => (
            <p className="muted" key={w}>
              {w}
            </p>
          ))}
          <div className="form-footer">
            <span>
              {stocks.length} 只股票，{sectors.length} 个行业
            </span>
            <button
              className="primary"
              disabled={busy || (!stocks.length && !sectors.length)}
              onClick={submit}
            >
              {busy ? (
                <LoaderCircle className="spin" size={17} />
              ) : (
                <Activity size={17} />
              )}
              开始复盘
            </button>
          </div>
        </section>
        <div className="aside-column">
          <section className="panel source-panel">
            <h2>数据准备</h2>
            <div className="source-row">
              <Database size={18} />
              <span>行情与行业</span>
              <Badge
                status={
                  mode === "demo" || settings?.data_provider === "akshare"
                    ? "ok"
                    : "limited"
                }
              >
                {mode === "demo"
                  ? "样例可用"
                  : settings?.data_provider === "akshare"
                    ? "AKShare · 无需密钥"
                    : "加载中"}
              </Badge>
            </div>
            <div className="source-row">
              <Activity size={18} />
              <span>分析模型</span>
              <Badge
                status={
                  mode === "demo" || settings?.llm_configured ? "ok" : "limited"
                }
              >
                {mode === "demo"
                  ? "规则演示"
                  : settings?.llm_configured
                    ? "已配置"
                    : "待配置"}
              </Badge>
            </div>
            <div className="source-row">
              <FileText size={18} />
              <span>官方政策</span>
              <Badge status={mode === "demo" ? "ok" : "limited"}>
                {mode === "demo"
                  ? "虚构事件"
                  : settings?.sources?.some((s: AnyRecord) => s.last_success)
                    ? "已采集"
                    : "待更新"}
              </Badge>
            </div>
            <p className="hint">
              AKShare 上游网络及数据覆盖将在取数时校验，模型仍需单独配置密钥。
            </p>
            <a className="inline-link" href="#/settings">
              设置数据来源
              <ArrowUpRight size={15} />
            </a>
          </section>
          <section className="workflow-preview">
            <h2>一份报告，八个分析环节</h2>
            {[
              "数据核验",
              "板块 · 量价 · 政策并行分析",
              "多头证据与空头反证",
              "风险审查",
              "复盘报告与证据附录",
            ].map((s, i) => (
              <div key={s}>
                <span className="flow-point">
                  {i === 4 ? <Check size={12} /> : null}
                </span>
                <p>{s}</p>
              </div>
            ))}
            <p className="hint">关键结论附带证据，点击即可核对来源与时间。</p>
          </section>
        </div>
      </div>
    </>
  );
}

function Settings() {
  const [ifindEnabled, setIfindEnabled] = useState(false),
    [ifindAccess, setIfindAccess] = useState(""),
    [ifindRefresh, setIfindRefresh] = useState("");
  const [data, setData] = useState<AnyRecord | null>(null),
    [key, setKey] = useState(""),
    [url, setUrl] = useState(""),
    [model, setModel] = useState("");
  const [busy, setBusy] = useState(""),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [checks, setChecks] = useState<AnyRecord[]>([]);
  function load() {
    api("/settings")
      .then((d) => {
        setData(d);
        setUrl(d.llm_url);
        setModel(d.llm_model);
        setIfindEnabled(!!d.ifind_enabled);
      })
      .catch((e) => setError(e.message));
  }
  useEffect(load, []);
  async function action(name: string, fn: () => Promise<any>) {
    setBusy(name);
    setError("");
    setMessage("");
    try {
      await fn();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }
  return (
    <>
      <div className="page-title">
        <div>
          <h1>数据与模型</h1>
          <p>连接真实数据，检查可用范围。凭证仅保存在本机服务端。</p>
        </div>
      </div>
      <ErrorBox error={error} />
      {message && (
        <div className="notice success">
          <Check size={17} />
          {message}
        </div>
      )}
      <div className="settings-grid">
        <section className="panel">
          <h2>服务连接</h2>
          <div className="notice success">
            <Database size={18} />
            行情来源：AKShare，无需 Token 或积分。上游数据可用性以实际取数为准。
          </div>
          <label>
            <input type="checkbox" checked={ifindEnabled} onChange={(e) => setIfindEnabled(e.target.checked)} />
            启用同花顺 iFinD 股票日线备用
          </label>
          <p className="hint">AKShare 原有日线来源失败后才调用 iFinD。仅覆盖股票日线，不替代行业、公告和政策接口。</p>
          <label>
            iFinD access_token（{data?.ifind_configured ? "已配置凭据" : "未配置凭据"}）
            <input type="password" autoComplete="new-password" value={ifindAccess} onChange={(e) => setIfindAccess(e.target.value)} placeholder="留空保留已保存的 token" />
          </label>
          <label>
            iFinD refresh_token
            <input type="password" autoComplete="new-password" value={ifindRefresh} onChange={(e) => setIfindRefresh(e.target.value)} placeholder="推荐填写；调用时获取有效 access_token" />
          </label>
          <p className="hint">两种 token 至少填写一种；同时填写时优先使用 refresh_token。凭据仅保存在本机服务端，留空不覆盖。</p>
          <button disabled={!!busy || !data?.ifind_enabled || !data?.ifind_configured} onClick={() => action("ifind", async () => {
            const result = await post("/settings/ifind/test");
            setMessage(result.note);
          })}>{busy === "ifind" ? "正在检测 iFinD…" : "检测已保存的 iFinD 配置"}</button>
          <label>
            模型服务地址
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…/v1"
            />
          </label>
          <label>
            模型名称
            <input value={model} onChange={(e) => setModel(e.target.value)} />
          </label>
          <label>
            模型密钥{" "}
            <Badge status={data?.llm_configured ? "ok" : "limited"}>
              {data?.llm_configured ? "已保存" : "未配置"}
            </Badge>
            <input
              type="password"
              autoComplete="new-password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="兼容 Chat Completions 和工具调用的服务"
            />
          </label>
          <p className="hint">
            密钥不会出现在报告和浏览器响应中。更换模型后，新任务使用新配置。
          </p>
          <div className="actions">
            <button
              className="primary"
              disabled={!!busy}
              onClick={() =>
                action("save", async () => {
                  await api("/settings", {
                    method: "PUT",
                    body: JSON.stringify({
                      llm_key: key || null,
                      llm_url: url,
                      llm_model: model,
                      ifind_enabled: ifindEnabled,
                      ifind_access_token: ifindAccess.trim() || null,
                      ifind_refresh_token: ifindRefresh.trim() || null,
                    }),
                  });
                  setKey("");
                  setIfindAccess("");
                  setIfindRefresh("");
                  load();
                  setMessage("配置已保存");
                })
              }
            >
              {busy === "save" ? "保存中…" : "保存配置"}
            </button>
            <button
              disabled={!!busy}
              onClick={() =>
                action("test", async () => {
                  setChecks(await post("/settings/test"));
                })
              }
            >
              {busy === "test" ? "正在检测…" : "检测连接"}
            </button>
          </div>
        </section>
        <div>
          <section className="panel">
            <h2>证券与行业目录</h2>
            <p className="muted">
              正式模式从 AKShare 同步沪深证券及申万
              二级行业指数。首次使用需要同步目录；历史成分家数不以当前成分回填。
            </p>
            <button
              disabled={!!busy}
              onClick={() =>
                action("catalog", async () => {
                  const r = await post("/catalog/sync");
                  setMessage(
                    `已同步 ${r.count} 条记录（股票 ${r.counts.stock} 条、行业 ${r.counts.sector} 条）。${(r.warnings || []).join("；")}`,
                  );
                })
              }
            >
              <RefreshCw
                size={16}
                className={busy === "catalog" ? "spin" : ""}
              />
              同步证券目录
            </button>
          </section>
          <section className="panel">
            <div className="section-heading">
              <h2>官方政策采集</h2>
              <button
                className="icon-button"
                aria-label="更新政策"
                disabled={!!busy}
                onClick={() =>
                  action("policy", async () => {
                    await post("/policies/sync");
                    load();
                    setMessage("政策来源更新已结束，请查看各来源状态");
                  })
                }
              >
                <RefreshCw
                  className={busy === "policy" ? "spin" : ""}
                  size={18}
                />
              </button>
            </div>
            <p className="hint">
              发改委通知栏目与证监会公告栏目，采集最近文件并保留原文版本。
            </p>
            {data?.sources?.length ? (
              data.sources.map((s: AnyRecord) => (
                <div className="source-detail" key={s.name}>
                  <strong>{s.name}</strong>
                  <Badge status={s.status} />
                  <p>{s.note}</p>
                  <small>
                    最近成功{" "}
                    {s.last_success ? localTime(s.last_success) : "尚未成功"}
                  </small>
                </div>
              ))
            ) : (
              <p className="muted">尚未完成采集，点击更新获取来源状态。</p>
            )}
          </section>
        </div>
      </div>
      {checks.length > 0 && (
        <section className="panel">
          <h2>连接检测结果</h2>
          {checks.map((c) => (
            <div className="connection" key={c.name}>
              <strong>{c.name}</strong>
              <Badge status={c.status} />
              <span>{c.note}</span>
            </div>
          ))}
        </section>
      )}
    </>
  );
}

function Run({ id }: { id: string }) {
  const [data, setData] = useState<AnyRecord | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const r = await api("/reviews/" + id);
        if (!active) return;
        setData(r);
        if (["completed", "partial"].includes(r.status)) {
          navigate("/report/" + id);
          return;
        }
        if (!["failed", "cancelled"].includes(r.status))
          timer = setTimeout(refresh, 1500);
      } catch (e: any) {
        if (active) setError(e.message);
      }
    }
    refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id]);
  if (!data)
    return (
      <>
        <ErrorBox error={error} />
        <Loading />
      </>
    );
  const done = Object.values(data.stages).filter(
    (s: any) => s.status === "completed",
  ).length;
  return (
    <>
      <a href="#/history" className="back">
        <ArrowLeft size={16} />
        历史复盘
      </a>
      <div className="page-title">
        <div>
          <h1>{data.request.trade_date} 盘后复盘</h1>
          <p>
            {data.request.stocks.length} 只股票，{data.request.sectors.length}{" "}
            个行业 · 信息截止 {localTime(data.request.as_of)}
          </p>
        </div>
        <Badge status={data.status} />
      </div>
      {data.request.mode === "demo" && (
        <div className="notice amber">样例数据 · 合成行情与虚构事件</div>
      )}
      <ChatLink rid={id}/>{data.request.execution_mode === "autonomous" && <AgentPlan rid={id}/>}
      <ErrorBox error={error || data.error} />
      <section className="panel progress-panel">
        <div className="section-heading">
          <h2>{data.cancel ? "正在取消…" : labels[data.status]}</h2>
          <span>{done} / {Object.keys(data.stages).length} 个阶段已处理</span>
        </div>
        <div className="progress-track">
          <div style={{ width: `${(done / Math.max(1,Object.keys(data.stages).length)) * 100}%` }} />
        </div>
        {Object.entries(data.stages).map(([name, s]: [string, any]) => (
          <div className={"stage " + s.status} key={name}>
            <span className="stage-icon">
              {s.status === "completed" ? (
                <Check size={16} />
              ) : s.status === "running" ? (
                <LoaderCircle className="spin" size={16} />
              ) : s.status === "failed" ? (
                <X size={16} />
              ) : (
                <span />
              )}
            </span>
            <strong>{name}</strong>
            <span className="stage-note">{s.note}</span>
            <Badge status={s.status} />
          </div>
        ))}
        <div className="actions">
          {!["failed", "cancelled"].includes(data.status) && (
            <button
              disabled={!!data.cancel}
              onClick={() =>
                post("/reviews/" + id + "/cancel")
                  .then(setData)
                  .catch((e) => setError(e.message))
              }
            >
              取消任务
            </button>
          )}
          {data.status === "failed" && (
            <button
              className="primary"
              onClick={() =>
                post("/reviews/" + id + "/resume")
                  .then(() => window.location.reload())
                  .catch((e) => setError(e.message))
              }
            >
              从已完成步骤恢复
            </button>
          )}
          <button onClick={() => navigate("/new?copy=" + id)}>
            复制参数新建
          </button>
        </div>
      </section>
      <details className="panel">
        <summary>运行详情</summary>
        {data.usage && (
          <p className="muted">
            模型调用 {data.usage.model_calls} / 60 次 · 输入{" "}
            {data.usage.input_tokens} / 输出 {data.usage.output_tokens} token ·
            费用：价格未配置
          </p>
        )}
        <div className="log-list">
          {data.events.map((e: AnyRecord) => (
            <div key={e.id}>
              <small>{localTime(e.created)}</small>
              <strong>{e.stage}</strong>
              <span>
                {e.event_type} · {e.status}
              </span>
              <code>{e.detail}</code>
            </div>
          ))}
        </div>
      </details>
    </>
  );
}

function History() {
  const params = useParams(),
    [data, setData] = useState<AnyRecord | null>(null),
    [error, setError] = useState("");
  const [start, setStart] = useState(
    params.get("start") ||
      new Date(Date.now() - 30 * 86400000).toLocaleDateString("en-CA", {
        timeZone: "Asia/Shanghai",
      }),
  );
  const [end, setEnd] = useState(params.get("end") || ""),
    [status, setStatus] = useState(params.get("status") || ""),
    [stocks, setStocks] = useState(params.get("stocks") || ""),
    [sectors, setSectors] = useState(params.get("sectors") || ""),
    [page, setPage] = useState(Number(params.get("page") || 1));
  useEffect(() => {
    const query = new URLSearchParams({
      start,
      end,
      status,
      stocks,
      sectors,
      page: String(page),
    });
    history.replaceState(null, "", "#/history?" + query);
    let active = true;
    api("/reviews?" + query)
      .then((d) => {
        if (active) setData(d);
      })
      .catch((e) => setError(e.message));
    return () => {
      active = false;
    };
  }, [start, end, status, stocks, sectors, page]);
  return (
    <>
      <div className="page-title">
        <div>
          <h1>历史复盘</h1>
          <p>回看每一次判断，追溯当时的信息与证据。</p>
        </div>
        <button className="primary" onClick={() => navigate("/new")}>
          <Plus size={17} />
          新建复盘
        </button>
      </div>
      <section className="panel">
        <div className="history-filters">
          <label>
            开始日期
            <input
              type="date"
              value={start}
              onChange={(e) => {
                setStart(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label>
            结束日期
            <input
              type="date"
              value={end}
              onChange={(e) => {
                setEnd(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label>
            任务状态
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部状态</option>
              {[
                "queued",
                "analyzing",
                "completed",
                "partial",
                "failed",
                "cancelled",
              ].map((s) => (
                <option value={s} key={s}>
                  {labels[s]}
                </option>
              ))}
            </select>
          </label>
          <label>
            股票代码
            <input
              value={stocks}
              placeholder="多个代码用逗号分隔"
              onChange={(e) => {
                setStocks(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label>
            行业代码
            <input
              value={sectors}
              placeholder="多个代码用逗号分隔"
              onChange={(e) => {
                setSectors(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <button
            onClick={() => {
              setStart("");
              setEnd("");
              setStatus("");
              setStocks("");
              setSectors("");
              setPage(1);
            }}
          >
            清空
          </button>
        </div>
        <ErrorBox error={error} />
        {!data ? (
          <Loading />
        ) : (
          <>
            <div className="list-meta">
              符合条件 {data.count} / 总计 {data.total}
            </div>
            {data.items.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>交易日 / 创建时间</th>
                      <th>关注范围</th>
                      <th>版本</th>
                      <th>模式</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((r: AnyRecord) => (
                      <tr key={r.id}>
                        <td>
                          <strong>{r.request.trade_date}</strong>
                          <small>{localTime(r.created)}</small>
                        </td>
                        <td>
                          {r.request.stocks.length} 只股票 /{" "}
                          {r.request.sectors.length} 个行业
                          <small>
                            {r.request.stocks.slice(0, 2).join("，")}
                          </small>
                        </td>
                        <td>v{r.version}</td>
                        <td>
                          <Badge>
                            {r.request.mode === "demo"
                              ? "样例数据"
                              : "正式数据"}
                          </Badge>
                        </td>
                        <td>
                          <Badge status={r.status} />
                        </td>
                        <td>
                          <div className="actions">
                            <button
                              className="text-button"
                              onClick={() =>
                                navigate(
                                  (["completed", "partial"].includes(r.status)
                                    ? "/report/"
                                    : "/run/") + r.id,
                                )
                              }
                            >
                              查看
                            </button>
                            <button
                              className="text-button"
                              onClick={() => navigate("/new?copy=" + r.id)}
                            >
                              复制参数
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty
                title="没有匹配的复盘"
                text="清空筛选，或创建第一份盘后复盘。"
              />
            )}
            <div className="pagination">
              <span>每页 20 条</span>
              <button disabled={page === 1} onClick={() => setPage(page - 1)}>
                上一页
              </button>
              <span>{page}</span>
              <button
                disabled={page * 20 >= data.count}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </button>
            </div>
          </>
        )}
      </section>
    </>
  );
}

function CandleChart({ stock }: { stock: AnyRecord }) {
  const [window, setWindow] = useState(60),
    [hover, setHover] = useState<number | null>(null);
  const bars = stock.bars.slice(-window),
    W = 860,
    H = 300,
    pad = 45;
  if (!bars.length) return <Empty title="没有可用 K 线" text={stock.status} />;
  const low = Math.min(...bars.map((b: AnyRecord) => b.adjusted_low)),
    high = Math.max(...bars.map((b: AnyRecord) => b.adjusted_high)),
    range = high - low || 1;
  const y = (v: number) => 20 + ((high - v) / range) * 190,
    x = (i: number) => pad + (i * (W - pad * 2)) / bars.length;
  const maxVol = Math.max(...bars.map((b: AnyRecord) => b.volume)) || 1,
    chosen = bars[hover ?? bars.length - 1];
  return (
    <div className="chart">
      <div className="section-heading">
        <h3>日 K 与成交量</h3>
        <div className="segmented">
          {[20, 60, 120].map((n) => (
            <button
              key={n}
              className={window === n ? "selected" : ""}
              onClick={() => {
                setWindow(n);
                setHover(null);
              }}
            >
              {n} 日
            </button>
          ))}
        </div>
      </div>
      <div className="chart-values">
        <strong>{chosen.date}</strong>
        <span>开 {format(chosen.open)}</span>
        <span>高 {format(chosen.high)}</span>
        <span>低 {format(chosen.low)}</span>
        <span>收 {format(chosen.close)}</span>
        <span>成交量 {format(chosen.volume, 0)} 股</span>
        <span>成交额 {money(chosen.amount)}元</span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={stock.name + "日K与成交量图"}
        onMouseLeave={() => setHover(null)}
      >
        {[0, 1, 2, 3, 4].map((i) => (
          <g key={i}>
            <line
              x1={pad}
              y1={20 + i * 47.5}
              x2={W - pad}
              y2={20 + i * 47.5}
              stroke="#e8edf1"
            />
            <text
              x={W - pad + 5}
              y={24 + i * 47.5}
              fontSize="10"
              fill="#83919c"
            >
              {format(high - (i * range) / 4)}
            </text>
          </g>
        ))}
        {bars.map((b: AnyRecord, i: number) => {
          const c = b.adjusted_close >= b.adjusted_open ? "#c45650" : "#278675",
            width = Math.max(2, ((W - pad * 2) / bars.length) * 0.65);
          return (
            <g key={b.date} onMouseEnter={() => setHover(i)}>
              <rect
                x={x(i) - 3}
                y={0}
                width={(W - pad * 2) / bars.length + 3}
                height={H}
                fill="transparent"
              />
              <line
                x1={x(i)}
                x2={x(i)}
                y1={y(b.adjusted_high)}
                y2={y(b.adjusted_low)}
                stroke={c}
              />
              <rect
                x={x(i) - width / 2}
                y={Math.min(y(b.adjusted_open), y(b.adjusted_close))}
                width={width}
                height={Math.max(
                  1,
                  Math.abs(y(b.adjusted_open) - y(b.adjusted_close)),
                )}
                fill={c}
              />
              <rect
                x={x(i) - width / 2}
                y={275 - (b.volume / maxVol) * 45}
                width={width}
                height={(b.volume / maxVol) * 45}
                fill={c}
                opacity=".6"
              />
            </g>
          );
        })}
        {[
          [5, "#bd8b35"],
          [10, "#4d99a7"],
          [20, "#5574ad"],
          [60, "#a575a2"],
        ].map(([n, c]) => (
          <polyline
            key={n}
            points={bars
              .map((b: AnyRecord, i: number) =>
                b["ma" + n] != null ? `${x(i)},${y(b["ma" + n])}` : "",
              )
              .filter(Boolean)
              .join(" ")}
            fill="none"
            stroke={String(c)}
            strokeWidth="1.2"
            pointerEvents="none"
          />
        ))}
        {hover !== null && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1="15"
            y2="278"
            stroke="#607688"
            strokeDasharray="3 3"
            pointerEvents="none"
          />
        )}
        {[0, Math.floor(bars.length / 2), bars.length - 1].map((i) => (
          <text
            key={i}
            x={x(i)}
            y={296}
            textAnchor="middle"
            fontSize="10"
            fill="#83919c"
          >
            {bars[i].date}
          </text>
        ))}
      </svg>
      <div className="chart-legend">
        <span>MA5</span>
        <span>MA10</span>
        <span>MA20</span>
        <span>MA60</span>
        <small>{stock.basis} · 原始 OHLC 见悬停数值</small>
      </div>
    </div>
  );
}

function Claims({
  claims,
  onEvidence,
  empty = "本模块暂无可用结论",
}: {
  claims: AnyRecord[];
  onEvidence: (id: string) => void;
  empty?: string;
}) {
  return (
    <>
      {claims.length ? (
        claims.map((c, i) => (
          <article className="claim" key={i}>
            <p>{c.text}</p>
            {c.condition && <small>观察条件 · {c.condition}</small>}
            {c.limitation && <small className="muted">{c.limitation}</small>}
            <div className="references">
              {c.evidence_ids.map((id: string) => (
                <button key={id} onClick={() => onEvidence(id)}>
                  <FileText size={12} />
                  {id.split("-").slice(1).join("-")}
                </button>
              ))}
            </div>
          </article>
        ))
      ) : (
        <p className="muted">{empty}</p>
      )}
    </>
  );
}

function Report({ id }: { id: string }) {
  const initial = useParams();
  const [original, setOriginal] = useState<AnyRecord | null>(null),
    [data, setData] = useState<AnyRecord | null>(null),
    [error, setError] = useState("");
  const [tab, setTab] = useState(initial.get("tab") || "overview"),
    [filters, setFilters] = useState<AnyRecord>(() => {
      try {
        return JSON.parse(initial.get("filters") || "{}");
      } catch {
        return {};
      }
    });
  const [page, setPage] = useState(Number(initial.get("page") || 1)),
    [selected, setSelected] = useState(initial.get("selected") || ""),
    [drawer, setDrawer] = useState<AnyRecord | null>(null),
    [exportOpen, setExportOpen] = useState(false),
    [showFilters, setShowFilters] = useState(false);
  function change(key: string, value: any) {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(1);
  }
  useEffect(() => {
    api("/reports/" + id)
      .then((r) => {
        setOriginal(r);
        if (!selected) setSelected(r.stocks[0]?.id || "");
      })
      .catch((e) => setError(e.message));
  }, [id]);
  useEffect(() => {
    if (!original) return;
    let active = true;
    post("/reports/" + id + "/filter", filters)
      .then((r) => {
        if (active) setData(r);
      })
      .catch((e) => setError(e.message));
    return () => {
      active = false;
    };
  }, [original, filters, id]);
  useEffect(() => {
    const q = new URLSearchParams({
      tab,
      filters: JSON.stringify(filters),
      page: String(page),
      selected,
    });
    history.replaceState(null, "", `#/report/${id}?${q}`);
  }, [tab, filters, page, selected, id]);
  async function evidence(eid: string) {
    try {
      setDrawer(
        await api(`/reports/${id}/evidence/${encodeURIComponent(eid)}`),
      );
    } catch (e: any) {
      setError(e.message);
    }
  }
  if (!original || !data)
    return (
      <>
        <ErrorBox error={error} />
        <Loading />
      </>
    );
  if (original.artifact_type === 'answer') return <>
    <a className="back" href={'#/chat/'+original.conversation_id}>返回研究对话</a>
    <div className="page-title"><div><h1>对话研究成果</h1><p>版本 {original.version} · 截止 {localTime(original.request.as_of)}</p></div>
      <button className="primary" onClick={()=>setExportOpen(true)}>导出本次成果</button></div>
    <ErrorBox error={error}/>
    {original.investment_research && <InvestmentResearch research={original.investment_research} onEvidence={evidence} onFiltersChange={values=>setFilters(f=>({...f,...values}))}/>}
    <section className="panel">{!original.investment_research && <Claims claims={original.summary} onEvidence={evidence}/>}
      {original.result_set && <div className="table-scroll"><table><thead><tr>{Object.entries(original.result_set.columns).map(([key,label])=><th key={key}>{String(label)}</th>)}</tr></thead>
        <tbody>{original.result_set.rows.map((row:AnyRecord)=><tr key={row.id}>{Object.keys(original.result_set.columns).map(key=><td key={key}>{row[key] == null ? '不可用' : String(row[key])}</td>)}</tr>)}</tbody></table>
        {!original.result_set.rows.length && <p>当前条件没有匹配记录，可返回对话恢复原比较表。</p>}</div>}
      {original.limitations?.map((note:string)=><p className="notice" key={note}>{note}</p>)}
      <ResearchGaps gaps={original.gaps}/>
      <p><a href={'#/report/'+original.source_report_id}>查看来源报告</a></p>
      {original.parent_artifact_id && <p><a href={'#/report/'+original.parent_artifact_id}>查看上一版本</a></p>}
    </section>
    {drawer && <Dialog title="引用证据" close={()=>setDrawer(null)} wide><h3>{drawer.title}</h3><p>{drawer.source} · {drawer.event_time}</p><pre>{typeof drawer.value === 'string' ? drawer.value : JSON.stringify(drawer.value,null,2)}</pre></Dialog>}
    {exportOpen && <ExportDialog id={id} report={original} filters={original.investment_research?filters:{}} close={()=>setExportOpen(false)}/>}
  </>;
  const stock =
    data.stocks.find((s: AnyRecord) => s.id === selected) || data.stocks[0];
  const tabs = [
    ["overview", "概览"],
    ["sector", "板块"],
    ["stocks", "个股"],
    ["policy", "政策与外围"],
    ["debate", "多空与风险"],
    ["evidence", "证据"],
  ];
  const used = new Set(
    Object.values(original.analysis).flatMap((a: any) =>
      a.claims.flatMap((c: AnyRecord) => c.evidence_ids),
    ),
  );
  let evidences = (data.visible_evidence || data.evidence).filter(
    (e: AnyRecord) => filters.all_evidence || used.has(e.id),
  );
  const events = [...data.events].sort((a, b) =>
    (b.published_at || b.published_date).localeCompare(
      a.published_at || a.published_date,
    ),
  );
  const total =
    tab === "policy"
      ? events.length
      : tab === "evidence"
        ? evidences.length
        : data.stocks.length;
  return (
    <>
      <a className="back" href="#/history">
        <ArrowLeft size={15} />
        历史复盘
      </a>
      <div className="page-title report-title">
        <div>
          <div className="title-row">
            <h1>{original.request.trade_date} {original.artifact_type==='answer'?'对话研究成果':'盘后复盘'}</h1>
            <Badge status={original.status} />
            {original.request.mode === "demo" && (
              <Badge status="limited">样例数据</Badge>
            )}
          </div>
          <p>
            关注范围复盘 · v{original.version} · 信息截止{" "}
            {localTime(original.request.as_of)}
          </p>
        </div>
        <div className="actions">
          <ChatLink rid={original.source_report_id||id} report/>
          <button onClick={() => navigate("/new?copy=" + (original.source_report_id || id))}>
            <RefreshCw size={16} />
            重新分析
          </button>
          <button className="primary" onClick={() => setExportOpen(true)}>
            <Download size={16} />
            导出报告
          </button>
        </div>
      </div>
      {original.execution_mode === "autonomous" && <AgentPlan rid={id}/>}
      <div className="report-meta">
        <span>
          <ShieldCheck size={16} />
          行情覆盖{" "}
          {original.stocks.filter((s: AnyRecord) => s.available).length}/
          {original.stocks.length}
        </span>
        <span>
          <FileText size={16} />
          {original.evidence.length} 条证据
        </span>
        <span>
          <Clock size={16} />
          生成 {localTime(original.created)}
        </span>
      </div>
      {original.coverage_notes?.length > 0 && (
        <details className="quality-strip">
          <summary>数据范围说明</summary>
          <ul>{original.coverage_notes.map((s: string) => <li key={s}>{s}</li>)}</ul>
        </details>
      )}
      {original.issues.length > 0 && (
        <details className="quality-strip">
          <summary>
            <AlertTriangle size={16} />
            数据范围与缺口（{original.issues.length} 项）
          </summary>
          <ul>
            {original.issues.map((s: string) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </details>
      )}
      {original.usage && (
        <details className="quality-strip">
          <summary>分析用量</summary>
          <p>
            模型调用 {original.usage.model_calls} / 60 次；输入{" "}
            {original.usage.input_tokens}、输出 {original.usage.output_tokens}{" "}
            token；费用：价格未配置。
            {!original.usage.token_usage_reported &&
              "本次没有模型服务返回的 token 用量。"}
          </p>
        </details>
      )}
      <ErrorBox error={error} />
      <div className="report-tabs" role="tablist">
        {tabs.map(([key, label]) => (
          <button
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            key={key}
            onClick={() => {
              setTab(key);
              setPage(1);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="report-toolbar">
        <div className="input-icon search">
          <Search size={16} />
          <input
            aria-label="报告关键词"
            placeholder="搜索名称、标题或摘要"
            value={filters.q || ""}
            onChange={(e) => change("q", e.target.value)}
          />
        </div>
        <button
          className={showFilters ? "selected" : ""}
          onClick={() => setShowFilters(!showFilters)}
        >
          <Filter size={16} />
          筛选
        </button>
        <label className="sort-label">
          排序
          <select
            aria-label="排序"
            value={filters.sort || "code"}
            onChange={(e) => change("sort", e.target.value)}
          >
            <option value="code">代码</option>
            <option value="pct_change">涨跌幅</option>
            <option value="amount">成交额</option>
            <option value="risk">风险等级</option>
          </select>
        </label>
        <button onClick={() => change("descending", !filters.descending)}>
          {filters.descending ? "降序" : "升序"}
        </button>
        <button
          className="text-button"
          onClick={() => {
            setFilters({});
            setPage(1);
          }}
        >
          清空
        </button>
      </div>
      {showFilters && (
        <div className="panel filters-panel">
          <MultiSelect
            label="股票"
            values={filters.stocks || []}
            options={original.stocks}
            onChange={(v) => change("stocks", v)}
          />
          <MultiSelect
            label="行业"
            values={filters.sectors || []}
            options={original.sectors}
            onChange={(v) => change("sectors", v)}
          />
          <MultiSelect
            label="风险"
            values={filters.risks || []}
            options={["需重点关注", "一般关注", "资料不足"].map((s) => ({
              id: s,
              name: s,
            }))}
            onChange={(v) => change("risks", v)}
          />
          {tab === "policy" && (
            <>
              <MultiSelect
                label="发布机构"
                values={filters.issuers || []}
                options={Array.from(
                  new Set(original.events.map((e: AnyRecord) => e.issuer)),
                ).map((s: any) => ({ id: s, name: s }))}
                onChange={(v) => change("issuers", v)}
              />
              <MultiSelect
                label="事件类型"
                values={filters.kinds || []}
                options={[
                  "正式文件",
                  "征求意见稿",
                  "解读",
                  "公司公告",
                  "报道",
                ].map((s) => ({ id: s, name: s }))}
                onChange={(v) => change("kinds", v)}
              />
              <label>
                起始日期
                <input
                  type="date"
                  value={filters.event_start || ""}
                  onChange={(e) => change("event_start", e.target.value)}
                />
              </label>
              <label>
                结束日期
                <input
                  type="date"
                  value={filters.event_end || ""}
                  onChange={(e) => change("event_end", e.target.value)}
                />
              </label>
              <label>
                正文
                <select
                  value={filters.body_available || ""}
                  onChange={(e) => change("body_available", e.target.value)}
                >
                  <option value="">全部</option>
                  <option value="yes">可用</option>
                  <option value="no">不可用</option>
                </select>
              </label>
            </>
          )}
          {tab === "evidence" && (
            <>
              <MultiSelect
                label="证据类型"
                values={filters.kinds || []}
                options={["行情", "板块", "政策", "公告", "外围"].map((s) => ({
                  id: s,
                  name: s,
                }))}
                onChange={(v) => change("kinds", v)}
              />
              <MultiSelect
                label="来源"
                values={filters.sources || []}
                options={Array.from(
                  new Set(original.evidence.map((e: AnyRecord) => e.source)),
                ).map((s: any) => ({ id: s, name: s }))}
                onChange={(v) => change("sources", v)}
              />
              <MultiSelect
                label="质量"
                values={filters.qualities || []}
                options={["usable", "limited", "unverified", "unavailable"].map(
                  (s) => ({ id: s, name: labels[s] }),
                )}
                onChange={(v) => change("qualities", v)}
              />
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={!!filters.all_evidence}
                  onChange={(e) => change("all_evidence", e.target.checked)}
                />
                全部候选证据
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={!!filters.fulltext}
                  onChange={(e) => change("fulltext", e.target.checked)}
                />
                检索正文
              </label>
            </>
          )}
          <small>
            同字段多选为「或」，不同字段为「且」。按 Ctrl / Command 多选。
          </small>
        </div>
      )}
      {tab === "overview" && (
        <>
          <div className="overview-grid">
            <section className="panel conclusions">
              <div className="section-heading">
                <h2>原报告核心结论</h2>
                <span className="muted">点击引用核验</span>
              </div>
              <Claims claims={data.summary} onEvidence={evidence} />
            </section>
            <section className="panel market-brief">
              <h2>外围市场</h2>
              {data.markets.map((m: AnyRecord) => (
                <div className="market-item" key={m.id}>
                  <div>
                    <strong>{m.name}</strong>
                    <small>{m.trade_date} 已收盘</small>
                  </div>
                  <div>
                    <strong>{format(m.close)}</strong>
                    <span
                      className={
                        (m.pct_change || 0) >= 0 ? "positive" : "negative"
                      }
                    >
                      {m.pct_change >= 0 ? "+" : ""}
                      {format(m.pct_change)}%
                    </span>
                  </div>
                </div>
              ))}
              {!data.markets.length && (
                <p className="muted">外围数据不可用，请查看数据缺口。</p>
              )}
              <div className="review-scope">
                <strong>本次关注范围</strong>
                <p>
                  {original.request.stocks.length} 只股票 /{" "}
                  {original.request.sectors.length} 个行业
                </p>
                <small>保留生成时的数据快照与模型配置</small>
              </div>
            </section>
          </div>
          <section className="panel">
            <h2>关注股票</h2>
            <StockTable
              stocks={data.stocks}
              select={(s) => {
                setSelected(s);
                setTab("stocks");
              }}
            />
          </section>
        </>
      )}
      {tab === "sector" && (
        <section className="panel">
          <h2>行业表现</h2>
          {data.sectors.length ? (
            data.sectors.map((s: AnyRecord) => (
              <div className="sector-block" key={s.id}>
                <div className="section-heading">
                  <div>
                    <h3>{s.name}</h3>
                    <small>
                      {s.source} · {s.basis}
                    </small>
                  </div>
                  <strong
                    className={
                      "big-change " +
                      (s.pct_change >= 0 ? "positive" : "negative")
                    }
                  >
                    {s.pct_change >= 0 ? "+" : ""}
                    {format(s.pct_change)}%
                  </strong>
                </div>
                {s.index_research && <div className="notice"><strong>指数层分析 · 基准 {s.index_research.benchmark}</strong><p>20日收益 {format(s.index_research.return_20d)}% · 相对强弱 {format(s.index_research.relative_strength_pp)} 个百分点 · 区间最大回撤 {format(s.index_research.max_drawdown_pct)}%</p><p>MA5 {format(s.index_research.ma5)} · MA20 {format(s.index_research.ma20)}</p>{s.index_research.gaps.map((gap:string)=><p key={gap}>{gap}</p>)}</div>}
                <div className="sector-stats">
                  <div>
                    有效样本
                    <strong>
                      {s.index_only ? "不可用" : `${s.valid}/${s.total}`}
                    </strong>
                  </div>
                  <div>
                    上涨 / 下跌
                    <strong>
                      <span className="positive">{s.up ?? "—"}</span> /{" "}
                      <span className="negative">{s.down ?? "—"}</span>
                    </strong>
                  </div>
                  <div>
                    成交额变化<strong>{format(s.amount_change)}%</strong>
                    <small>
                      {s.index_only
                        ? "行业指数成交额较上一交易日"
                        : `共同样本 ${s.common_count} 家`}
                    </small>
                  </div>
                  <div>
                    涨停 / 未知
                    <strong>
                      {s.limit_up ?? "—"} / {s.limit_unknown ?? "—"}
                    </strong>
                  </div>
                  <div>
                    停牌 / 缺失
                    <strong>
                      {s.index_only
                        ? "不可用"
                        : `${s.suspended} / ${s.missing}`}
                    </strong>
                  </div>
                </div>
                <p className="muted">
                  {s.index_only ? "历史成分及领涨股统计未提供" : "当日领涨股"}{" "}
                  {s.leaders
                    .map((l: AnyRecord) => `${l.code} ${format(l.pct_change)}%`)
                    .join("　")}
                </p>
                <Claims
                  claims={data.analysis.sector.claims.filter(
                    (c: AnyRecord) => c.subject === s.id,
                  )}
                  onEvidence={evidence}
                />
              </div>
            ))
          ) : (
            original.request.sectors.length === 0 ? (
              <Empty title="未选择行业" text="本次仅分析所选股票。如需板块复盘，可保留当前参数，添加行业后新建任务。">
                <button onClick={() => navigate("/new?copy=" + id)}>添加行业并新建复盘</button>
              </Empty>
            ) : original.sectors.length === 0 ? (
              <Empty title="所选行业数据暂不可用" text="请查看报告的数据缺口说明；系统不会用其他日期的数据填充。">
                <button onClick={() => navigate("/new?copy=" + id)}>复制参数重新复盘</button>
              </Empty>
            ) : (
              <Empty title="没有匹配行业" text="当前筛选条件未匹配行业，请调整筛选条件。" />
            )
          )}
        </section>
      )}
      {tab === "stocks" && (
        <>
          <section className="panel">
            <StockTable
              stocks={data.stocks.slice((page - 1) * 20, page * 20)}
              select={setSelected}
            />
          </section>
          {stock && (
            <section className="panel">
              <div className="section-heading">
                <h2>
                  {stock.name} <small>{stock.id}</small>
                </h2>
                <Badge>{stock.risk}</Badge>
              </div>
              <CandleChart stock={stock} />
              <div className="metric-row">
                {[
                  ["MA5", stock.metrics.ma5],
                  ["MA10", stock.metrics.ma10],
                  ["MA20", stock.metrics.ma20],
                  ["MA60", stock.metrics.ma60],
                  ["日成交量 / 前5日均量", stock.metrics.volume_ratio],
                  ["MACD", stock.metrics.macd],
                ].map(([name, v]) => (
                  <div key={name as string}>
                    <small>{name}</small>
                    <strong>{format(v)}</strong>
                    {v == null && <small>样本或口径不足</small>}
                  </div>
                ))}
              </div>
              <Claims
                claims={data.analysis.market.claims.filter(
                  (c: AnyRecord) => c.subject === stock.id,
                )}
                onEvidence={evidence}
              />
            </section>
          )}
        </>
      )}
      {tab === "policy" && (
        <section className="panel">
          <div className="section-heading">
            <h2>政策与公司公告</h2>
            <span className="muted">{events.length} 条候选事件</span>
          </div>
          {events.length ? (
            events.slice((page - 1) * 20, page * 20).map((e: AnyRecord) => (
              <details className="event-card" key={e.id}>
                <summary>
                  <div>
                    <Badge>{e.kind}</Badge> <Badge status={e.quality} />
                    <h3>{e.title}</h3>
                    <small>
                      {e.issuer} ·{" "}
                      {e.published_at
                        ? localTime(e.published_at)
                        : e.published_date + "（仅日期）"}
                    </small>
                  </div>
                  <ChevronRight size={18} />
                </summary>
                <div className="event-body">
                  <p>{e.body_text || "正文不可用，请查看原文。"}</p>
                  <dl>
                    <dt>文号</dt>
                    <dd>{e.document_no || "未提供"}</dd>
                    <dt>首次观察</dt>
                    <dd>{localTime(e.first_seen_at)}</dd>
                    <dt>生效日期</dt>
                    <dd>{e.effective_date || "未提取，需查原文"}</dd>
                    <dt>命中主题</dt>
                    <dd>
                      {e.matched_topics?.join("、") || "资本市场通用主题"}
                    </dd>
                  </dl>
                  {e.source_url && (
                    <a
                      className="inline-link"
                      href={e.source_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      官方原文
                      <ExternalLink size={14} />
                    </a>
                  )}
                </div>
              </details>
            ))
          ) : (
            <Empty
              title="本次范围未检索到相关事件"
              text="请同时查看顶部的来源覆盖与获取状态。"
            />
          )}
          <Claims claims={data.analysis.policy.claims} onEvidence={evidence} />
          <h2>外围快照</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>市场</th>
                  <th>交易日</th>
                  <th>收盘</th>
                  <th>涨跌幅</th>
                  <th>北京时间收盘</th>
                </tr>
              </thead>
              <tbody>
                {data.markets.map((m: AnyRecord) => (
                  <tr key={m.id}>
                    <td>{m.name}</td>
                    <td>{m.trade_date}</td>
                    <td>{format(m.close)}</td>
                    <td>{format(m.pct_change)}%</td>
                    <td>{m.close_at ? localTime(m.close_at) : "样例快照"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {tab === "debate" && (
        <>
          <InvestmentResearch research={original.investment_research} onEvidence={evidence} onFiltersChange={values=>setFilters(f=>({...f,...values}))}/>
          {!original.investment_research && <>
          <div className="debate-grid">
            <section className="panel bull">
              <h2>多头证据</h2>
              <Claims
                claims={data.analysis.bull.claims}
                onEvidence={evidence}
                empty={data.analysis.bull.note}
              />
            </section>
            <section className="panel bear">
              <h2>空头反证</h2>
              <Claims
                claims={data.analysis.bear.claims}
                onEvidence={evidence}
                empty={data.analysis.bear.note}
              />
            </section>
          </div>
          <section className="panel">
            <h2>
              <ShieldCheck size={20} />
              风险与次日观察
            </h2>
            <Claims
              claims={data.analysis.risk.claims}
              onEvidence={evidence}
              empty={data.analysis.risk.note}
            />
          </section>
          </>}
        </>
      )}
      {tab === "evidence" && (
        <section className="panel">
          <div className="section-heading">
            <h2>证据记录</h2>
            <span>
              符合条件 {evidences.length} / 总计 {original.evidence.length}
            </span>
          </div>
          {evidences.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>编号</th>
                    <th>类型 / 标题</th>
                    <th>来源</th>
                    <th>时间</th>
                    <th>质量</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {evidences
                    .slice((page - 1) * 20, page * 20)
                    .map((e: AnyRecord) => (
                      <tr key={e.id}>
                        <td>
                          <code>{e.id.split("-").slice(1).join("-")}</code>
                        </td>
                        <td>
                          {e.kind}
                          <small>{e.title}</small>
                        </td>
                        <td>{e.source}</td>
                        <td>{e.event_time?.slice(0, 16)}</td>
                        <td>
                          <Badge status={e.quality} />
                        </td>
                        <td>
                          <button
                            className="text-button"
                            onClick={() => evidence(e.id)}
                          >
                            查看
                          </button>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="没有匹配证据" />
          )}
        </section>
      )}
      {["policy", "stocks", "evidence"].includes(tab) && (
        <div className="pagination">
          <span>每页 20 条</span>
          <button disabled={page === 1} onClick={() => setPage(page - 1)}>
            上一页
          </button>
          <span>{page}</span>
          <button
            disabled={page * 20 >= total}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </div>
      )}
      {drawer && (
        <Dialog title="证据详情" close={() => setDrawer(null)} wide>
          <div className="drawer-body">
            <Badge status={drawer.quality} />
            <h3>{drawer.title}</h3>
            <code>{drawer.id}</code>
            <dl>
              <dt>来源</dt>
              <dd>{drawer.source}</dd>
              <dt>时间</dt>
              <dd>{drawer.event_time}</dd>
              <dt>单位</dt>
              <dd>{drawer.units || "见原文"}</dd>
              <dt>计算口径</dt>
              <dd>{drawer.basis || "原始文本／数据"}</dd>
              <dt>快照</dt>
              <dd className="break">{drawer.snapshot_id}</dd>
            </dl>
            <h3>原始证据</h3>
            <pre>
              {typeof drawer.value === "string"
                ? drawer.value
                : JSON.stringify(drawer.value, null, 2)}
            </pre>
            {drawer.source_url && (
              <a
                className="inline-link"
                href={drawer.source_url}
                target="_blank"
                rel="noreferrer"
              >
                打开来源
                <ExternalLink size={15} />
              </a>
            )}
            <h3>关联结论</h3>
            <Claims
              claims={Object.values(original.analysis)
                .flatMap((a: any) => a.claims)
                .filter((c: any) => c.evidence_ids.includes(drawer.id))}
              onEvidence={evidence}
            />
          </div>
        </Dialog>
      )}
      {exportOpen && (
        <ExportDialog
          id={id}
          report={original}
          filters={filters}
          close={() => setExportOpen(false)}
        />
      )}
    </>
  );
}

function StockTable({
  stocks,
  select,
}: {
  stocks: AnyRecord[];
  select: (s: string) => void;
}) {
  return stocks.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>股票</th>
            <th>收盘价</th>
            <th>涨跌幅</th>
            <th>成交额</th>
            <th>状态</th>
            <th>关注等级</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {stocks.map((s) => (
            <tr key={s.id}>
              <td>
                <button className="stock-name" onClick={() => select(s.id)}>
                  {s.name}
                </button>
                <small>{s.id}</small>
              </td>
              <td>{format(s.close)}</td>
              <td className={s.pct_change >= 0 ? "positive" : "negative"}>
                {s.pct_change == null
                  ? "—"
                  : `${s.pct_change >= 0 ? "+" : ""}${format(s.pct_change)}%`}
              </td>
              <td>{money(s.amount)}</td>
              <td>{s.status}</td>
              <td>
                <Badge>{s.risk}</Badge>
              </td>
              <td>
                <button
                  className="icon-button"
                  aria-label={"查看" + s.name}
                  onClick={() => select(s.id)}
                >
                  <ChevronRight size={17} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="没有匹配股票" />
  );
}

function ExportDialog({
  id,
  report,
  filters,
  close,
}: {
  id: string;
  report: AnyRecord;
  filters: AnyRecord;
  close: () => void;
}) {
  const [format, setFormat] = useState("docx"),
    [scope, setScope] = useState("full"),
    [charts, setCharts] = useState(report.artifact_type !== 'answer'),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [download, setDownload] = useState("");
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
    },
    [],
  );
  async function start() {
    setBusy(true);
    setError("");
    setDownload("");
    try {
      const result = await post(`/reports/${id}/exports`, {
        format,
        scope,
        charts,
        filters,
      });
      for (let i = 0; i < 120; i++) {
        const job = await api("/exports/" + result.id);
        if (!mounted.current) return;
        if (job.status === "failed") throw new Error(job.error);
        if (job.status === "completed") {
          setDownload("/api/exports/" + result.id + "/download");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      throw new Error("导出仍在处理，可稍后重试查看同一任务");
    } catch (e: any) {
      if (mounted.current) setError(e.message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <Dialog title="导出复盘报告" close={close}>
      <div className="modal-body">
        <p className="muted">
          {report.request.trade_date} · v{report.version} ·{" "}
          {labels[report.status]}
        </p>
        <label>
          文件格式
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            disabled={busy}
          >
            <option value="docx">Word 文档 (.docx)</option>
            <option value="md">Markdown (.md / .zip)</option>
          </select>
        </label>
        <label>
          导出范围
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            disabled={busy}
          >
            <option value="full">完整报告</option>
            {(report.artifact_type !== 'answer'||report.investment_research) && <option value="filtered">当前筛选结果（全部匹配记录）</option>}
          </select>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={charts}
            onChange={(e) => setCharts(e.target.checked)}
            disabled={busy || report.artifact_type === 'answer'}
          />
          包含 K 线图表
        </label>
        <p className="hint">
          引用附录和数据缺口始终保留。含图 Markdown 以 ZIP 打包。
        </p>
        {scope === "filtered" && (
          <p className="hint">筛选摘录保留原报告概览与其证据依赖。</p>
        )}
        <ErrorBox error={error} />
        {download && (
          <a className="download-ready" href={download}>
            <Check size={18} />
            文件已生成，点击下载
            <Download size={18} />
          </a>
        )}
        <div className="actions end">
          <button onClick={close}>关闭</button>
          <button className="primary" disabled={busy} onClick={start}>
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Download size={16} />
            )}
            {busy ? "正在生成…" : error ? "重试导出" : "生成文件"}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
