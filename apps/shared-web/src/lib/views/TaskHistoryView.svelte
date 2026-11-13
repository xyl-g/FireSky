<script lang="ts">
  import { onMount } from "svelte";
  import { History, RefreshCw, RotateCcw, Trash2, List, X } from "@lucide/svelte";
  import { rpcRequest } from "../ipc";
  import { TaskHistoryController, taskMethod, taskStatus, type HistoryState, type HistoryTask, type RetryRequest } from "../task-history";

  let { online, generation, visible, retryDisabled = false, onRestore }: {
    online: boolean;
    generation: number;
    visible: boolean;
    retryDisabled?: boolean;
    onRestore: (request: RetryRequest) => Promise<boolean>;
  } = $props();
  const controller = new TaskHistoryController({ request: rpcRequest }, (next) => { historyState = next; });
  let historyState = $state<HistoryState>(controller.state);
  let alive = $state(false);
  let previousGeneration = -1;
  let previouslyOnline = false;
  let forgetTarget = $state<HistoryTask | null>(null);
  let restoring = $state<string | null>(null);
  let restoreError = $state("");
  let confirmDialog: HTMLDialogElement;
  const selected = $derived(historyState.tasks.find((item) => item.task_id === historyState.selectedId));
  const localTime = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
  const kindTitle = (item: HistoryTask) => item.retry_request?.kind === "regional" ? "区域"
    : item.retry_request?.kind === "nowcast" ? "实况" : item.retry_request?.kind === "point" ? "单点" : "";
  const context = (item: HistoryTask) => {
    const payload = item.retry_request?.payload ?? {};
    const name = typeof payload.name === "string" ? payload.name : Array.isArray(payload.regions) ? payload.regions.join("、") : "";
    const date = typeof payload.target_date === "string" ? payload.target_date : "";
    const event = payload.phenomenon === "dawn" ? "朝霞" : payload.phenomenon === "dusk" ? "晚霞" : "";
    return [name, date, event].filter(Boolean).join(" · ");
  };
  const eventTitle = (event: string) =>
    ({ running: "开始", progress: "进度", cancel_requested: "请求取消", done: "完成", failed: "失败", cancelled: "已取消", interrupted: "已中断" } as Record<string, string>)[event] ?? "状态更新";
  const eventDetail = (data: Record<string, unknown>) => {
    const parts = ["stage", "source"].map((key) => typeof data[key] === "string" ? String(data[key]).slice(0, 128) : "").filter(Boolean);
    if (typeof data.percent === "number" && Number.isFinite(data.percent)) parts.push(`${data.percent}%`);
    return parts.join(" · ");
  };
  export function refresh() { void controller.refresh(); }

  async function restore(item: HistoryTask) {
    if (!online || retryDisabled || restoring) return;
    const retry = controller.retry(item.task_id);
    if (!retry) return;
    restoring = item.task_id;
    restoreError = "";
    try {
      if (!await onRestore(retry) && alive) restoreError = "无法恢复这份参数，请等待当前任务结束，或重新选择地点与日期。";
    } catch (error) {
      if (alive) restoreError = error instanceof Error ? error.message : String(error);
    } finally {
      if (alive) restoring = null;
    }
  }

  async function forget() {
    if (!forgetTarget || !online) return;
    const target = forgetTarget;
    const removed = await controller.forget(target.task_id);
    if (alive && removed) forgetTarget = null;
  }

  $effect(() => {
    if (generation === previousGeneration && online === previouslyOnline) return;
    previousGeneration = generation;
    previouslyOnline = online;
    controller.reset();
    forgetTarget = null;
    restoreError = "";
  });
  $effect(() => {
    if (!alive || !online || !visible) return;
    void controller.refresh();
    const timer = setInterval(() => { void controller.refresh(); }, 5000);
    return () => clearInterval(timer);
  });
  $effect(() => {
    if (forgetTarget && visible) confirmDialog?.showModal();
    else confirmDialog?.close();
  });
  onMount(() => {
    alive = true;
    return () => { alive = false; controller.dispose(); };
  });
</script>

<section class="history-section" aria-labelledby="history-title">
  <header>
    <h2 id="history-title"><History size={17} />任务历史</h2>
    <button class="icon-button" aria-label="刷新任务历史" title="刷新任务历史" disabled={!online || historyState.loading || !!historyState.forgetting} onclick={() => controller.refresh()}><RefreshCw size={16} class={historyState.loading ? "spinning" : ""} /></button>
  </header>
  <p class="summary muted" aria-live="polite">{!online ? "引擎未连接，暂时无法读取历史。" : historyState.loaded ? `显示最近 ${historyState.tasks.length} 项，共 ${historyState.total} 项` : historyState.loading ? "正在读取任务记录…" : "尚未读取任务记录"}</p>
  {#if historyState.error}<p class="error" role="alert">{historyState.error}</p>{/if}
  {#if restoreError}<p class="error" role="alert">{restoreError}</p>{/if}
  {#if historyState.issues}<p class="error" role="status">{historyState.issues} 项记录无法核验，未将未知状态标为中断。</p>{/if}
  <div class="task-list">
    {#each historyState.tasks as item (item.task_id)}
      <article class="task-row" aria-label={`${taskMethod(item.method)} ${taskStatus(item.status, item.cancel_requested)}`}>
        <div class="task-main">
          <div class="task-heading"><strong>{kindTitle(item)}{taskMethod(item.method)}</strong><span class="status" class:failed={item.status === "failed"} class:attention={item.status === "interrupted"}>{taskStatus(item.status, item.cancel_requested)}</span></div>
          {#if context(item)}<p class="context">{context(item)}</p>{/if}
          <p class="muted task-meta"><time datetime={item.created_at_utc}>{localTime(item.created_at_utc)}</time>
            {#if item.status === "running" && item.progress.stage}<span>{item.progress.stage}</span>{/if}
            {#if item.status === "running" && item.progress.percent != null}<span>{item.progress.percent}%</span>{/if}
            {#if item.status === "interrupted"}<span>未自动重跑</span>{/if}
          </p>
        </div>
        <div class="actions">
          <button class="icon-button" title="查看任务事件" aria-label={`查看${taskMethod(item.method)}事件`} disabled={!online || !!historyState.forgetting} onclick={() => historyState.selectedId === item.task_id ? controller.closeEvents() : controller.select(item.task_id)} aria-expanded={historyState.selectedId === item.task_id}><List size={16} /></button>
          {#if item.retry_request}<button title="恢复参数后需重新检查数据" disabled={!online || retryDisabled || item.status === "running" || !!restoring || !!historyState.forgetting} onclick={() => restore(item)}><RotateCcw size={15} />{restoring === item.task_id ? "恢复中…" : "恢复参数"}</button>{/if}
          <button class="icon-button" title="删除任务记录" aria-label={`删除${taskMethod(item.method)}记录`} disabled={!online || item.status === "running" || !!historyState.forgetting} onclick={() => { forgetTarget = item; }}><Trash2 size={15} /></button>
        </div>
      </article>
    {:else}
      {#if online && historyState.loaded}<p class="empty muted">暂无任务记录</p>{/if}
    {/each}
  </div>
  {#if historyState.selectedId}
    <div class="events">
      <header><h3>{selected ? taskMethod(selected.method) : "任务"}事件</h3><button class="icon-button" title="关闭任务事件" aria-label="关闭任务事件" onclick={() => controller.closeEvents()}><X size={15} /></button></header>
      {#if historyState.eventsLoading}<p role="status" class="muted">正在读取事件…</p>{/if}
      {#if historyState.eventsError}<p class="error" role="alert">{historyState.eventsError}</p>{/if}
      {#if historyState.truncated}<p class="muted">较早的事件已超出保留上限。</p>{/if}
      <ol>
        {#each historyState.events as event (event.seq)}
          <li><time datetime={event.at_utc}>{localTime(event.at_utc)}</time><strong>{eventTitle(event.event)}</strong><span class="muted">{eventDetail(event.data)}</span></li>
        {/each}
      </ol>
    </div>
  {/if}
</section>

<dialog bind:this={confirmDialog} class="confirm-dialog" aria-labelledby="forget-title" oncancel={(event) => { event.preventDefault(); if (!historyState.forgetting) forgetTarget = null; }}>
  <h2 id="forget-title">删除任务记录？</h2>
  <p>仅删除这份任务的状态与事件，不删除已发布结果、报告或天气数据。</p>
  <footer><button disabled={!!historyState.forgetting} onclick={() => { forgetTarget = null; }}>保留记录</button><button class="destructive" disabled={!online || !!historyState.forgetting} onclick={forget}><Trash2 size={15} />{historyState.forgetting ? "删除中…" : "删除记录"}</button></footer>
</dialog>

<style>
  .history-section { padding: 20px 0; border-top: 1px solid var(--fs-border); }
  header, h2, .task-heading, .task-meta, .actions, button, footer { display: flex; align-items: center; gap: 8px; }
  header { justify-content: space-between; gap: 16px; }
  h2 { font-size: 15px; margin: 0; font-weight: 600; }
  h3 { margin: 0; font-size: 13px; }
  p { line-height: 1.7; overflow-wrap: anywhere; margin: 4px 0; }
  .muted { color: var(--fs-ink2); }
  .summary, .task-meta { font-size: 11px; }
  .summary { margin: 8px 0; }
  .task-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 20px; padding: 14px 0; border-bottom: 1px solid var(--fs-border); }
  .task-main { flex: 1 1 240px; min-width: 0; }
  .task-heading, .task-meta { flex-wrap: wrap; }
  .task-heading strong { font-size: 12px; }
  .context { font-size: 12px; }
  .task-meta { gap: 6px 12px; }
  .status { color: var(--fs-ink2); font-size: 11px; }
  .failed, .error, .destructive { color: var(--fs-error); }
  .attention { color: var(--fs-accent); }
  .actions { flex-shrink: 0; flex-wrap: wrap; }
  button { justify-content: center; min-height: 32px; padding: 6px 10px; color: var(--fs-ink); background: var(--fs-control); border: 1px solid var(--fs-control-border); border-radius: 6px; font: inherit; cursor: pointer; }
  button:hover:enabled { background: var(--fs-hover); }
  button:disabled { opacity: .5; cursor: default; }
  .icon-button { width: 34px; flex: 0 0 34px; padding: 0; }
  .empty { padding: 18px 0; }
  .events { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--fs-border); }
  ol { margin: 8px 0 0; padding: 0; list-style: none; max-height: 320px; overflow-y: auto; }
  li { display: flex; flex-wrap: wrap; gap: 6px 14px; padding: 9px 0; line-height: 1.6; border-bottom: 1px solid var(--fs-border); font-size: 11px; overflow-wrap: anywhere; }
  time { font-variant-numeric: tabular-nums; }
  .confirm-dialog { width: min(440px, calc(100% - 40px)); padding: 24px; color: var(--fs-ink); background: var(--fs-bg-strong); border: 1px solid var(--fs-control-border); border-radius: 8px; box-shadow: 0 16px 50px #0003; }
  .confirm-dialog::backdrop { background: #0006; }
  .confirm-dialog h2 { margin-bottom: 12px; font-size: 17px; }
  footer { justify-content: end; flex-wrap: wrap; margin-top: 18px; }
  .destructive { color: var(--fs-error); }
  @media (pointer: coarse) { button { min-height: 44px; } .icon-button { width: 44px; flex-basis: 44px; } }
</style>
