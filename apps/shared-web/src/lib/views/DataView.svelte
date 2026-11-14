<script lang="ts">
  import { onMount } from "svelte";
  import { Database, Download, HardDrive, RefreshCw, Network, Square, CircleCheck, CircleAlert, SearchCheck } from "@lucide/svelte";
  import { onEngineProgress, rpcRequest, rpcStart, RpcError, type ProgressEvent, type RpcRequestHandle } from "../ipc";
  import { DataSessionController } from "../data-session";
  import TaskHistoryView from "./TaskHistoryView.svelte";
  import type { RetryRequest } from "../task-history";
  import {
    cycleCoverage, formatMB, initialProbes, localDate, probeLabel, sourceTitle,
    type InventoryDataset, type ProbeRow,
  } from "../data-display";

  let { online, generation, regions = [], visible = true, ecSource = $bindable("ecmwf"), onBusy = () => {}, retryDisabled = false, onRestore = async () => false }: {
    online: boolean;
    generation: number;
    regions?: Array<Record<string, unknown>>;
    visible?: boolean;
    ecSource?: string;
    onBusy?: (busy: boolean) => void;
    retryDisabled?: boolean;
    onRestore?: (request: RetryRequest) => Promise<boolean>;
  } = $props();
  const controller = new DataSessionController({ start: rpcStart, request: rpcRequest }, (next) => { session = next; });
  let session = $state(controller.state);
  let inventory = $state<InventoryDataset[]>([]);
  let inventoryLoading = $state(false);
  let inventoryLoaded = $state(false);
  let inventoryError = $state("");
  let inventoryTime = $state("");
  let region = $state("east-asia");
  let targetDate = $state(localDate());
  let phenomenon = $state("dusk");
  let confirmOpen = $state(false);
  let confirmationDialog: HTMLDialogElement;
  let probeRows = $state(initialProbes());
  let probeBusy = $state(false);
  let probeCancelling = $state(false);
  let probeError = $state("");
  let probeTime = $state("");
  let probeECSource = $state("ecmwf");
  let eventError = $state("");
  let probeHandle: RpcRequestHandle<{ datasets: ProbeRow[]; checked_at: string }> | null = null;
  let inventoryRevision = 0;
  let connectionRevision = 0;
  let previousGeneration = -1;
  let previouslyOnline = false;
  let previousECSource: string | undefined;
  let alive = false;
  let historyView = $state<TaskHistoryView>();

  const busy = $derived(["checking", "preparing", "cancelling"].includes(session.phase));
  const percent = $derived(Math.min(100, Math.max(0, session.progress?.percent ?? 0)));
  const totalFiles = $derived(inventory.reduce((sum, item) => sum + item.total_file_count, 0));
  const totalMB = $derived(inventory.reduce((sum, item) => sum + item.total_storage_mb, 0));
  const regionOptions = $derived(regions.filter((item) => typeof item.code === "string" && typeof item.label === "string"));
  const regionTitle = $derived(String(regionOptions.find((item) => item.code === region)?.label ?? region));
  const ecSourceLabel = $derived(ecSource === "aws" ? "AWS 欧洲镜像" : ecSource === "google" ? "Google Cloud 镜像" : "ECMWF 官方");
  const phaseTitle = $derived(
    session.phase === "checking" ? "正在检查本地数据"
    : session.phase === "preparing" ? "正在准备数据"
    : session.phase === "cancelling" ? "正在取消，等待当前阶段结束"
    : session.phase === "error" ? "准备未完成"
    : session.report?.ready ? "本次数据已齐"
    : session.report ? "本次数据有缺失" : "尚未检查",
  );

  function errorText(error: unknown) {
    return error instanceof Error ? error.message : String(error);
  }

  async function refreshInventory() {
    if (!online || inventoryLoading) return;
    const revision = ++inventoryRevision;
    inventoryLoading = true;
    inventoryError = "";
    try {
      const result = await rpcRequest<{ datasets: InventoryDataset[] }>("inventory");
      if (!alive || revision !== inventoryRevision) return;
      inventory = result.datasets;
      inventoryLoaded = true;
      inventoryTime = new Date().toLocaleTimeString();
    } catch (error) {
      if (alive && revision === inventoryRevision) inventoryError = errorText(error);
    } finally {
      if (alive && revision === inventoryRevision) inventoryLoading = false;
    }
  }

  function parametersChanged() {
    confirmOpen = false;
    controller.invalidate();
  }

  function check() {
    if (!online || busy || !targetDate || !region) return;
    void controller.check({
      regions: [region], target_date: targetDate, phenomenon,
      ecmwf_source: ecSource, spacing_deg: 0.25, workers: 1,
      model_source: "both", forecast_mode: "model", satellite_source: "off",
    });
  }

  async function prepare() {
    confirmOpen = false;
    if (!online || busy || !session.session) return;
    await controller.prepare();
    if (alive && online) void refreshInventory();
  }

  function handleProgress(id: string, event: ProgressEvent) {
    controller.progress(id, event);
    if (id !== probeHandle?.requestId || probeCancelling || probeECSource !== ecSource || !event.probe) return;
    const row = event.probe as unknown as ProbeRow;
    if (probeRows.some((item) => item.source === row.source)) {
      probeRows = probeRows.map((item) => item.source === row.source ? row : item);
    }
  }

  async function probe() {
    if (!online || probeBusy) return;
    const revision = connectionRevision;
    const source = ecSource;
    probeECSource = source;
    probeBusy = true;
    probeCancelling = false;
    probeError = "";
    probeTime = "";
    probeRows = initialProbes().map((row) => ({ ...row, state: "testing", detail: "正在连接数据源" }));
    const handle = rpcStart<{ datasets: ProbeRow[]; checked_at: string }>("network_probe", { ecmwf_source: source });
    probeHandle = handle;
    try {
      const result = await handle.result;
      if (!alive || revision !== connectionRevision || probeCancelling) return;
      if (source !== ecSource) return;
      probeRows = result.datasets;
      probeTime = new Date(result.checked_at).toLocaleString();
    } catch (error) {
      if (!alive || revision !== connectionRevision) return;
      if (source !== ecSource) return;
      const cancelled = probeCancelling || (error instanceof RpcError && error.code === "cancelled");
      probeError = cancelled ? "探测已取消；未完成的数据源尚未证明可下载。" : errorText(error);
    } finally {
      if (alive && revision === connectionRevision) {
        if (source !== ecSource) {
          probeRows = initialProbes();
          probeTime = "";
          probeError = "EC 下载源已变更，请重新探测当前来源。";
        } else {
          probeRows = probeRows.map((row) => row.state === "testing"
            ? { ...row, state: probeCancelling ? "cancelled" : "error", detail: "未完成探测" } : row);
        }
        probeBusy = false;
        probeCancelling = false;
        probeHandle = null;
      }
    }
  }

  async function cancelProbe() {
    if (!probeHandle || probeCancelling) return;
    const handle = probeHandle;
    const revision = connectionRevision;
    probeCancelling = true;
    try { await handle.cancel(); }
    catch (error) {
      if (alive && revision === connectionRevision && probeHandle === handle) probeError = errorText(error);
    }
  }
  export async function cancelTask() {
    const probeResult = probeHandle?.result;
    await Promise.all([controller.cancel(), cancelProbe()]);
    if (probeResult) await probeResult.catch(() => undefined);
  }
  export function refresh() { void refreshInventory(); historyView?.refresh(); }
  $effect(() => { onBusy(busy || probeBusy); });

  $effect(() => {
    if (confirmOpen && visible) confirmationDialog?.showModal();
    else confirmationDialog?.close();
  });

  $effect(() => {
    const source = ecSource;
    if (source === previousECSource) return;
    const changed = previousECSource !== undefined;
    previousECSource = source;
    if (!changed) return;
    parametersChanged();
    probeRows = initialProbes();
    probeTime = "";
    probeError = probeBusy ? "EC 下载源已变更，等待原探测结束后可重新探测。" : "";
  });

  function keydown(event: KeyboardEvent) {
    if (!visible) return;
    // F5 刷新由 App 层统一处理（desktopAction → refresh），这里只负责 Escape 取消。
    if (event.key === "Escape") {
      if (confirmOpen) confirmOpen = false;
      else if (busy) void controller.cancel();
      else if (probeBusy) void cancelProbe();
    }
  }

  $effect(() => {
    const connected = online;
    const currentGeneration = generation;
    if (currentGeneration !== previousGeneration || connected !== previouslyOnline) {
      previousGeneration = currentGeneration;
      previouslyOnline = connected;
      ++connectionRevision;
      ++inventoryRevision;
      inventoryLoading = false;
      inventoryLoaded = false;
      inventory = [];
      inventoryTime = "";
      inventoryError = "";
      probeHandle = null;
      probeBusy = false;
      probeCancelling = false;
      probeRows = initialProbes();
      probeTime = "";
      probeError = "";
      parametersChanged();
      if (connected && alive) void refreshInventory();
    }
  });

  onMount(() => {
    alive = true;
    let unlisten: (() => void) | undefined;
    void onEngineProgress(handleProgress).then((stop) => {
      if (alive) unlisten = stop;
      else stop();
    }).catch((error) => { if (alive) eventError = errorText(error); });
    if (online) void refreshInventory();
    return () => {
      alive = false;
      ++connectionRevision;
      ++inventoryRevision;
      unlisten?.();
      controller.dispose();
      void probeHandle?.cancel().catch(() => undefined);
      onBusy(false);
    };
  });
</script>

<svelte:window onkeydown={keydown} />

<div class="data-view">
  <div class="data-header">
    <h2>本地数据</h2>
    <button class="icon-button" title="刷新本地清单" aria-label="刷新本地清单" disabled={!online || inventoryLoading} onclick={refreshInventory}><RefreshCw size={16} class={inventoryLoading ? "spinning" : ""} /></button>
  </div>
  <p class="muted summary" aria-live="polite">
    {inventoryLoading ? "正在扫描缓存周期与时效…" : inventoryLoaded ? `${totalFiles} 个文件 · ${formatMB(totalMB)} · ${inventoryTime}` : "本地清单尚未读取"}
  </p>
  {#if !online}<p class="notice" role="status">引擎未连接，暂时无法读取或准备数据。</p>{/if}
  {#if eventError}<p class="error" role="alert">进度订阅失败：{eventError}</p>{/if}
  <section class="download-probe" aria-labelledby="probe-title">
    <div class="section-head"><h2 id="probe-title"><Network size={17} />下载连通性</h2>
      {#if probeBusy}<button disabled={probeCancelling} onclick={cancelProbe}><Square size={14} />{probeCancelling ? "取消中…" : "取消探测"}</button>
      {:else}<button disabled={!online || !!eventError} onclick={probe}><Network size={16} />探测下载</button>{/if}
    </div>
    <p class="muted probe-source">EC · {ecSourceLabel}</p>
    <div class="probe-list">
      {#each probeRows as row (row.source)}
        <div class="probe-row">
          <div class="probe-icon" class:ready={row.state === "passed"}>
            {#if row.state === "testing"}<RefreshCw size={18} class="spinning" />
            {:else if row.state === "passed"}<CircleCheck size={18} />
            {:else if !["idle", "testing"].includes(row.state)}<CircleAlert size={18} />
            {:else}<Network size={18} />{/if}
          </div>
          <div class="probe-content">
            <div class="probe-name"><strong>{sourceTitle(row.source)}</strong><span class="probe-state" class:ready={row.state === "passed"} class:error={!["idle", "testing", "passed"].includes(row.state)}>{probeLabel(row.state)}</span></div>
            <div class="probe-detail">{#if row.host}<small>{row.host}</small>{/if}<span>{row.detail}</span></div>
            {#if row.bytes_received != null || row.elapsed_ms != null}<p class="number muted measurement">{row.bytes_received == null ? "" : formatMB(row.bytes_received / 1024 / 1024)}{row.elapsed_ms == null ? "" : ` · ${(row.elapsed_ms / 1000).toFixed(1)} s`}</p>{/if}
          </div>
        </div>
      {/each}
    </div>
    <p class="muted probe-footer">{probeBusy ? probeCancelling ? "正在停止探测" : "正在验证真实数据下载" : probeTime ? `核验时间：${probeTime} · ${probeRows.filter((row) => row.state === "passed").length}/${probeRows.length} 项下载验证通过` : "尚无本次网络的下载验证结果"}</p>
    {#if probeError}<p class="error" role="alert">{probeError}</p>{/if}
  </section>

  <section class="inventory-section" aria-labelledby="inventory-title">
    <div class="section-head"><h2 id="inventory-title"><Database size={17} />本地资料库</h2></div>
    {#if inventoryError}<p class="error" role="alert">清单读取失败：{inventoryError}</p>{/if}
    <div class="inventory-list">
      {#each inventory as dataset (dataset.source)}
        <div class="dataset">
          <div class="dataset-header">
            <HardDrive size={22} class="drive-icon" />
            <h3>{sourceTitle(dataset.source)}</h3>
            <span>{dataset.total_file_count} 个文件</span>
            <span class="number">{formatMB(dataset.total_storage_mb)}</span>
          </div>
          <details>
            <summary>{Object.keys(dataset.cycles).length} 个周期<span class="muted latest-cycle">{dataset.latest_cycle ?? "暂无周期"}</span></summary>
            <div class="cache-path" title={dataset.path}>{dataset.path}</div>
            <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to horizontally scrollable data) -->
            <div class="table-scroll" tabindex="0" role="region" aria-label={`${sourceTitle(dataset.source)} 缓存周期`}>
              <table>
                <thead><tr><th>周期 UTC</th><th>文件</th><th>大小</th><th>预报时效 (h)</th><th>覆盖范围</th></tr></thead>
                <tbody>
                  {#each Object.entries(dataset.cycles).sort(([a], [b]) => b.localeCompare(a)) as [key, cycle] (key)}
                    <tr><td class="number">{cycle.label ?? key}</td><td>{cycle.files}</td><td class="number">{formatMB(cycle.mb)}</td><td class="hours">{cycle.hours.join(", ") || "未知"}</td><td>{cycleCoverage(dataset, cycle)}</td></tr>
                  {:else}
                    <tr><td colspan="5" class="muted">暂无已索引周期</td></tr>
                  {/each}
                </tbody>
              </table>
            </div>
            {#if dataset.unindexed_file_count > 0}<p class="muted">{dataset.unindexed_file_count} 个旧格式、无索引或未完成文件，不计入数据就绪。</p>{/if}
          </details>
        </div>
      {:else}
        <p class="empty">{inventoryLoading ? "正在读取本地资料库" : inventoryLoaded ? "暂无数据源记录" : "等待本地引擎"}</p>
      {/each}
    </div>
  </section>

  <section aria-labelledby="prepare-title">
    <div class="section-head"><h2 id="prepare-title"><Download size={17} />区域数据准备</h2><span class="muted">GFS + EC + CAMS</span></div>
    <div class="form-row">
      <label>区域<select bind:value={region} onchange={parametersChanged} disabled={busy || !online || !regionOptions.length}>
        {#each regionOptions as item}<option value={String(item.code)}>{String(item.label)}</option>{/each}
      </select></label>
      <label>日期<input type="date" bind:value={targetDate} onchange={parametersChanged} disabled={busy} required /></label>
      <label>事件<select bind:value={phenomenon} onchange={parametersChanged} disabled={busy}><option value="dawn">朝霞</option><option value="dusk">晚霞</option></select></label>
      <button disabled={!online || busy || !targetDate || !regionOptions.length} onclick={check}><SearchCheck size={16} />检查数据</button>
    </div>
    <div class="preparation-status" aria-live="polite">
      <strong>{phaseTitle}</strong>
      {#if session.report}
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard access to horizontally scrollable data) -->
        <div class="table-scroll" tabindex="0" role="region" aria-label="本次数据检查">
          <table>
            <thead><tr><th>数据源</th><th>本次所需周期 UTC</th><th>本地状态</th><th>最新发布 UTC</th></tr></thead>
            <tbody>{#each session.report.datasets as row, index (`${row.source}-${index}`)}
              <tr><td><strong>{sourceTitle(row.source)}</strong></td><td>{row.request_cycle ?? "未知"}</td>
                <td class:ready={row.ready}>{row.ready ? "本地已齐" : `缺少 ${row.missing_count ?? "未知"} 项`}</td>
                <td>{row.verified ? `${row.latest_available_cycle ?? "未知"}${row.is_latest ? " · 已是最新" : ""}` : "未核验"}</td></tr>
            {/each}</tbody>
          </table>
        </div>
        {#each session.report.notes ?? [] as note}<p class="muted">{note}</p>{/each}
      {/if}
      {#if busy}
        <progress max="100" value={session.progress ? percent : undefined} aria-label="数据准备进度"></progress>
        <p class="muted">{session.progress?.message || session.progress?.stage || phaseTitle}{session.progress ? ` · ${percent}%` : ""}</p>
      {/if}
      {#if session.error}<p class="error" role="alert">{session.error}</p>{/if}
      {#if session.publicationError}<p class="error" role="alert">发布核验未完成：{session.publicationError}</p>{/if}
      <div class="action-row">
        <button disabled={!online || busy || !session.session || session.publicationPending} onclick={() => controller.publications()}><RefreshCw size={15} />{session.publicationPending ? "核验发布中…" : "核验最新发布"}</button>
        <span class="action-spacer"></span>
        {#if busy}<button disabled={session.phase === "cancelling"} onclick={() => controller.cancel()}><Square size={14} />{session.phase === "cancelling" ? "取消中…" : "取消"}</button>
        {:else}<button class="primary" disabled={!online || !session.session || !session.report || !!eventError} onclick={() => { confirmOpen = true; }}><Download size={16} />准备 / 更新数据</button>{/if}
      </div>
    </div>
  </section>
  <TaskHistoryView bind:this={historyView} {online} {generation} {visible} retryDisabled={retryDisabled || busy || probeBusy} {onRestore} />
</div>

<dialog bind:this={confirmationDialog} class="confirm-dialog" aria-labelledby="download-confirm" oncancel={(event) => { event.preventDefault(); confirmOpen = false; }} onclose={() => { confirmOpen = false; }}>
      <h2 id="download-confirm">准备区域数据？</h2>
      <p>{regionTitle} · {targetDate} · {phenomenon === "dawn" ? "朝霞" : "晚霞"}</p>
      <p>将下载或更新 GFS、EC 与 CAMS 所需原始数据。区域文件可能较大，CAMS 需要已配置的 ADS 凭据。取消需等待当前下载阶段结束。</p>
      <div class="action-row"><button onclick={() => { confirmOpen = false; }}>返回</button><span class="action-spacer"></span><button class="primary" onclick={prepare}><Download size={16} />确认准备</button></div>
</dialog>

<style>
  .data-view { padding: 20px; max-width: 1440px; margin: 0 auto; }
  .data-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 10px; }
  .data-header h2 { font-size: 20px; }
  section { padding: 20px 0; border-bottom: 1px solid var(--fs-border); }
  section:last-child { border: 0; }
  .section-head, h2, .action-row, button, .probe-state { display: flex; align-items: center; gap: 8px; }
  .section-head { justify-content: space-between; min-height: 34px; margin-bottom: 10px; }
  h2 { margin: 0; font-size: 15px; font-weight: 600; }
  p { line-height: 1.7; overflow-wrap: anywhere; }
  .muted { color: var(--fs-ink2); }
  .summary { margin: 0 0 12px; font-size: 11px; }
  button { justify-content: center; min-height: 32px; padding: 6px 12px; border: 1px solid var(--fs-control-border); border-radius: 6px; color: var(--fs-ink); background: var(--fs-control); box-shadow: var(--fs-control-shadow); font: inherit; cursor: pointer; white-space: normal; }
  button:hover:enabled { background: var(--fs-hover); }
  button:disabled { opacity: .5; cursor: default; }
  button.primary { background: var(--fs-accent); color: var(--fs-on-accent); border-color: #00000008; box-shadow: inset 0 1px 0 #ffffff24, 0 1px 2px #00000012; }
  button.primary:hover:enabled { filter: brightness(1.1); background: var(--fs-accent); }
  .icon-button { width: 34px; padding: 0; flex: 0 0 34px; }
  .dataset { padding: 18px 0; border-bottom: 1px solid var(--fs-border); }
  .dataset:last-child { border-bottom: 0; padding-bottom: 0; }
  .dataset-header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
  .dataset-header h3 { font-size: 14px; font-weight: 600; margin: 0 auto 0 0; }
  .dataset-header > span { font-size: 12px; }
  .dataset-header > .number { color: var(--fs-ink2); }
  .dataset-header :global(.drive-icon) { color: var(--fs-ink2); flex-shrink: 0; }
  details { padding-top: 12px; }
  summary { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; cursor: pointer; min-height: 24px; }
  summary::before { content: ""; width: 6px; height: 6px; border-right: 1px solid currentColor; border-bottom: 1px solid currentColor; transform: rotate(-45deg); margin: 0 2px; }
  details[open] summary::before { transform: rotate(45deg); }
  summary .latest-cycle { margin-left: auto; font-size: 10px; }
  .cache-path { color: var(--fs-ink2); margin: 8px 0; font-size: 10px; overflow-wrap: anywhere; }
  .table-scroll { max-width: 100%; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; text-align: left; font-size: 11px; table-layout: auto; }
  th { color: var(--fs-ink2); font-size: 11px; font-weight: 500; background: var(--fs-hover); }
  td, th { padding: 9px 10px; border-bottom: 1px solid var(--fs-border); vertical-align: top; overflow-wrap: anywhere; }
  td:first-child, th:first-child { padding-left: 8px; }
  .hours { max-width: 260px; min-width: 110px; }
  .number { font-variant-numeric: tabular-nums; }
  .empty { padding: 20px 0; color: var(--fs-ink2); }
  .form-row { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin: 12px 0 18px; }
  label { display: flex; flex-direction: column; gap: 7px; color: var(--fs-ink2); font-size: 11px; flex: 1 1 130px; min-width: 0; }
  input, select { width: 100%; min-width: 0; height: 32px; padding: 6px 9px; color: var(--fs-ink); background: var(--fs-control); border: 1px solid var(--fs-control-border); border-radius: 5px; box-shadow: inset 0 1px 2px #00000004; font: inherit; font-size: 12px; }
  input:focus-visible, select:focus-visible { outline: 2px solid var(--fs-accent); outline-offset: 2px; }
  .preparation-status > strong { display: block; margin-bottom: 12px; font-size: 12px; }
  .action-row { flex-wrap: wrap; margin-top: 14px; }
  .action-spacer { flex: 1; }
  progress { width: 100%; height: 6px; margin-top: 16px; accent-color: var(--fs-teal); }
  .ready { color: var(--fs-teal); }
  .error { color: var(--fs-error); }
  .notice { padding: 10px 0; color: var(--fs-accent); }
  .download-probe { padding: 18px 0 20px; margin-top: 6px; border-bottom: 1px solid var(--fs-border); }
  .probe-source { margin: 0 0 14px; font-size: 11px; }
  .probe-row { display: flex; align-items: start; gap: 12px; padding: 14px 0; border-bottom: 1px solid var(--fs-border); }
  .probe-row:last-child { border-bottom: 0; }
  .probe-icon { flex: 0 0 20px; color: var(--fs-ink2); }
  .probe-content { flex: 1; min-width: 0; }
  .probe-name { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
  .probe-state { font-size: 10px; }
  .probe-detail { min-width: 0; overflow-wrap: anywhere; line-height: 1.6; font-size: 11px; color: var(--fs-ink2); }
  .probe-detail small { display: block; color: var(--fs-ink2); font-size: 10px; }
  .measurement { font-size: 10px; margin: 3px 0 0; }
  .probe-footer { font-size: 10px; margin: 10px 0 0; }
  .confirm-dialog { width: min(480px, calc(100% - 40px)); padding: 24px; color: var(--fs-ink); background: var(--fs-bg-strong); border: 1px solid var(--fs-control-border); border-radius: 8px; box-shadow: inset 0 1px 0 var(--fs-edge), 0 16px 50px #0003; }
  .confirm-dialog::backdrop { background: #0006; }
  .confirm-dialog h2 { margin-bottom: 12px; font-size: 17px; }
  .confirm-dialog p { font-size: 12px; }
  @media (max-width: 700px) { .data-view { padding: 20px 14px 24px; } .data-header h2 { font-size: 20px; } .download-probe { padding: 14px 0; } table { min-width: 540px; } }
  :global(.spinning) { animation: spin 1.5s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { :global(.spinning) { animation: none; } }
</style>
