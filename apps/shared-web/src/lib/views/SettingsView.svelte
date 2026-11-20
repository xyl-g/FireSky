<script lang="ts">
  import { onDestroy, untrack } from "svelte";
  import { KeyRound, Map, Check, Palette, Database, RefreshCw, CircleCheck, Cpu, HardDrive, Save } from "@lucide/svelte";
  import type { BaseId, SourceId } from "../map-grid";
  import { rpcRequest } from "../ipc";
  import { camsConfiguration, camsDescription, elevationSourceDescription, type CamsConfiguration, type ElevationSource } from "../settings-state";

  let {
    provider = $bindable("tianditu"), tdtKey = $bindable(""), base = $bindable("imagery"),
    appearance = $bindable("system"), ecSource = $bindable("ecmwf"), elevationSource = $bindable("auto"),
    online = false, engineWorkspace = "", engineBinary = "", onReconnect = () => {},
    reconnecting = false, generation = 0,
  }: {
    provider?: SourceId; tdtKey?: string; base?: BaseId;
    appearance?: "system" | "light" | "dark"; ecSource?: string; elevationSource?: ElevationSource;
    online?: boolean; engineWorkspace?: string; engineBinary?: string;
    onReconnect?: () => void; reconnecting?: boolean; generation?: number;
  } = $props();
  let tdtDraft = $state("");
  let message = $state("");
  let failed = $state(false);
  let cams = $state<CamsConfiguration | null>(null);
  let camsLoading = $state(false);
  let camsMessage = $state("");
  let camsFailed = $state(false);
  type RetentionValue = number | null;
  type RetentionSettings = {
    gfs_cycles: RetentionValue;
    ecmwf_cycles: RetentionValue;
    cams_cycles: RetentionValue;
    satellite_days: RetentionValue;
    report_days: RetentionValue;
    map_days: RetentionValue;
  };
  const emptyRetention = (): RetentionSettings => ({
    gfs_cycles: null, ecmwf_cycles: null, cams_cycles: null,
    satellite_days: null, report_days: null, map_days: null,
  });
  let retention = $state<RetentionSettings>(emptyRetention());
  let retentionLoading = $state(false);
  let retentionSaving = $state(false);
  let retentionMessage = $state("");
  let retentionFailed = $state(false);
  let revision = 0;
  let alive = true;
  const canSaveRetention = $derived(online && !retentionLoading && !retentionSaving);

  function applyKeys() {
    if (/-----BEGIN|PRIVATE KEY/i.test(tdtDraft) || tdtDraft.length > 512) {
      failed = true;
      message = "请输入天地图浏览器 Key，不要输入私钥。";
      return;
    }
    if (tdtDraft.trim()) tdtKey = tdtDraft.trim();
    tdtDraft = "";
    failed = false;
    message = "地图凭据已应用，仅本次运行有效。";
  }

  async function refreshCams() {
    if (!online) return;
    const current = ++revision;
    camsLoading = true;
    camsMessage = "";
    camsFailed = false;
    try {
      const result = await rpcRequest("cams_credentials");
      if (alive && current === revision) cams = camsConfiguration(result);
    } catch {
      if (alive && current === revision) {
        cams = null;
        camsMessage = "无法读取本地 CAMS 配置，请重新检查。";
        camsFailed = true;
      }
    } finally {
      if (alive && current === revision) camsLoading = false;
    }
  }

  async function refreshRetention() {
    if (!online || retentionSaving) return;
    const current = ++revision;
    retentionLoading = true;
    retentionMessage = "";
    retentionFailed = false;
    try {
      const result = await rpcRequest<{ settings?: Partial<RetentionSettings> }>("retention_settings");
      const settings = result.settings ?? {};
      if (alive && current === revision) {
        retention = {
          gfs_cycles: typeof settings.gfs_cycles === "number" ? settings.gfs_cycles : null,
          ecmwf_cycles: typeof settings.ecmwf_cycles === "number" ? settings.ecmwf_cycles : null,
          cams_cycles: typeof settings.cams_cycles === "number" ? settings.cams_cycles : null,
          satellite_days: typeof settings.satellite_days === "number" ? settings.satellite_days : null,
          report_days: typeof settings.report_days === "number" ? settings.report_days : null,
          map_days: typeof settings.map_days === "number" ? settings.map_days : null,
        };
      }
    } catch {
      if (alive && current === revision) {
        retentionMessage = "无法读取保留策略，请检查服务工作区的写入权限。";
        retentionFailed = true;
      }
    } finally {
      if (alive && current === revision) retentionLoading = false;
    }
  }

  async function saveRetention() {
    if (!canSaveRetention) return;
    const current = ++revision;
    retentionSaving = true;
    retentionMessage = "";
    retentionFailed = false;
    try {
      await rpcRequest("save_retention_settings", { settings: retention });
      if (alive && current === revision) retentionMessage = "保留策略已保存；下一次成功准备数据后自动清理。";
    } catch {
      if (alive && current === revision) {
        retentionMessage = "保留策略保存失败，请检查数值范围和服务工作区权限。";
        retentionFailed = true;
      }
    } finally {
      if (alive && current === revision) retentionSaving = false;
    }
  }

  $effect(() => {
    const connected = online;
    const connection = generation;
    void connection;
    ++revision;
    cams = null;
    camsMessage = "";
    camsLoading = false;
    retention = emptyRetention();
    retentionMessage = "";
    retentionFailed = false;
    if (connected) untrack(() => { void refreshCams(); });
    if (connected) untrack(() => { void refreshRetention(); });
  });
  onDestroy(() => { alive = false; ++revision; });
</script>

<div class="settings-view">
  <section aria-labelledby="appearance-settings">
    <h2 id="appearance-settings"><Palette size={17} />外观</h2>
    <div class="setting-row">
      <label for="appearance">主题</label>
      <select id="appearance" bind:value={appearance}>
        <option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option>
      </select>
    </div>
  </section>
  <section aria-labelledby="source-settings">
    <h2 id="source-settings"><Database size={17} />数据源</h2>
    <div class="setting-row">
      <label for="ec-source">EC 下载源</label>
      <select id="ec-source" bind:value={ecSource}>
        <option value="ecmwf">ECMWF 官方</option><option value="aws">AWS 欧洲镜像</option><option value="google">Google Cloud 镜像</option>
      </select>
    </div>
    <div class="setting-row">
      <label for="elevation-source">海拔来源</label>
      <select id="elevation-source" bind:value={elevationSource}>
        <option value="auto">自动（在线优先）</option><option value="http">HTTP 在线查询</option><option value="srtm">SRTM 本地地形</option>
      </select>
    </div>
    <p class="muted">{elevationSourceDescription(elevationSource)}</p>
    <p class="muted">海拔来源作用于「获取海拔与时区」和地名搜索；获取成功后观测点信息会显示实际来源。</p>
    <p class="muted">GFS · NOAA NOMADS；CAMS · Copernicus ADS</p>
  </section>
  <section aria-labelledby="retention-settings">
    <h2 id="retention-settings"><HardDrive size={17} />缓存与历史保留</h2>
    <p class="muted">留空表示不自动清理该类数据。周期数按最新周期计，卫星按扫描时间滚动计算。</p>
    <div class="retention-grid">
      <label for="gfs-retention">GFS 周期数<input id="gfs-retention" type="number" min="1" max="10000" step="1" placeholder="不设置" bind:value={retention.gfs_cycles} disabled={!online || retentionLoading} /></label>
      <label for="ec-retention">EC 周期数<input id="ec-retention" type="number" min="1" max="10000" step="1" placeholder="不设置" bind:value={retention.ecmwf_cycles} disabled={!online || retentionLoading} /></label>
      <label for="cams-retention">CAMS 周期数<input id="cams-retention" type="number" min="1" max="10000" step="1" placeholder="不设置" bind:value={retention.cams_cycles} disabled={!online || retentionLoading} /></label>
      <label for="satellite-retention">卫星保留天数<input id="satellite-retention" type="number" min="0" max="3650" step="0.5" placeholder="不设置" bind:value={retention.satellite_days} disabled={!online || retentionLoading} /></label>
      <label for="report-retention">报告保留天数<input id="report-retention" type="number" min="0" max="3650" step="0.5" placeholder="不设置" bind:value={retention.report_days} disabled={!online || retentionLoading} /></label>
      <label for="map-retention">地图保留天数<input id="map-retention" type="number" min="0" max="3650" step="0.5" placeholder="不设置" bind:value={retention.map_days} disabled={!online || retentionLoading} /></label>
    </div>
    <div class="actions">
      <span class="muted">{retentionLoading ? "正在读取保留策略…" : retentionSaving ? "正在保存…" : "准备成功后执行自动清理"}</span>
      <button type="button" class="primary" disabled={!canSaveRetention} onclick={() => { void saveRetention(); }}><Save size={15} />保存保留策略</button>
    </div>
    {#if retentionMessage}<p class:error={retentionFailed} role="status">{retentionMessage}</p>{/if}
  </section>
  <section aria-labelledby="map-settings">
    <h2 id="map-settings"><Map size={17} />地图</h2>
    <div class="setting-row">
      <label for="map-provider">地图服务</label>
      <select id="map-provider" bind:value={provider}>
        <option value="tianditu">天地图</option><option value="openfreemap">OpenFreeMap</option>
      </select>
    </div>
    {#if provider === "tianditu"}
      <div class="setting-row"><label for="base-type">底图类型</label><select id="base-type" bind:value={base}>
        <option value="imagery">影像</option><option value="vector">街道</option><option value="terrain">地形</option>
      </select></div>
    {/if}
    {#if provider === "openfreemap"}<p class="notice">OpenFreeMap 提供全球矢量底图，不需要 Key；天地图用于国内影像、街道和地形。</p>{/if}
  </section>
  <section aria-labelledby="map-credentials">
    <h2 id="map-credentials"><KeyRound size={17} />地图凭据</h2>
    <form onsubmit={(event) => { event.preventDefault(); applyKeys(); }}>
      <div class="credential-row">
        <label for="tdt-key">天地图浏览器 Key<span>{tdtKey ? "已配置" : "未配置"}</span></label>
        <input id="tdt-key" type="password" bind:value={tdtDraft} placeholder={tdtKey ? "输入以替换" : "浏览器端 Key"} autocomplete="off" spellcheck={false} maxlength="512" />
      </div>
      <p class="muted">地图凭据仅保存在本次运行内存中，不写入磁盘。</p>
      <div class="actions">
        <button type="button" onclick={() => { tdtKey = ""; tdtDraft = ""; message = "地图凭据已清除。"; failed = false; }}>清除地图凭据</button>
        <button type="submit" class="primary" disabled={!tdtDraft.trim()}><Check size={16} />应用地图凭据</button>
      </div>
      {#if message}<p class:error={failed} role="status">{message}</p>{/if}
    </form>
  </section>
  <section aria-labelledby="cams-settings">
    <h2 id="cams-settings"><KeyRound size={17} />CAMS · Copernicus ADS</h2>
    <div class="configuration-status">
      {#if cams?.configured}<CircleCheck size={17} class="success" />{:else}<KeyRound size={17} />{/if}
      <p role="status">{camsLoading ? "正在检查本地 CAMS 配置…" : camsDescription(cams)}</p>
      <button class="icon-button" type="button" title="重新检查 CAMS 配置" aria-label="重新检查 CAMS 配置" disabled={!online || camsLoading} onclick={refreshCams}><RefreshCw size={16} class={camsLoading ? "spinning" : ""} /></button>
    </div>
    <p class="notice">CAMS 凭据由服务端配置，浏览器不提供凭据写入。修改服务端配置后需重新启动 Rust 服务。</p>
    {#if camsMessage}<p class:error={camsFailed} role="status">{camsMessage}</p>{/if}
    <details class="tutorial">
      <summary>配置教程</summary>
      <ol>
        <li><strong>注册并登录 ADS</strong><span>使用 ECMWF 账号登录 Atmosphere Data Store。</span><a href="https://ads.atmosphere.copernicus.eu/" target="_blank" rel="noreferrer">打开 ADS</a></li>
        <li><strong>复制 Personal Access Token</strong><span>在官方 API 配置页复制 Token，不要粘贴 url 或整段配置。</span><a href="https://ads.atmosphere.copernicus.eu/how-to-api" target="_blank" rel="noreferrer">获取 Token</a></li>
        <li><strong>接受 CAMS 数据集条款</strong><span>在数据集 Download 页接受 Terms of Use，完成网页端授权。</span><a href="https://ads.atmosphere.copernicus.eu/datasets/cams-global-atmospheric-composition-forecasts?tab=download" target="_blank" rel="noreferrer">打开数据集</a></li>
        <li><strong>配置 Rust 服务的 CAMS 凭据</strong><span>配置检查不代表 Token 已通过实际下载验证。</span></li>
      </ol>
    </details>
  </section>
  <section aria-labelledby="engine-settings">
    <h2 id="engine-settings"><Cpu size={17} />Rust 服务</h2>
    <div class="engine-row"><span>服务工作区</span><code>{engineWorkspace || "未连接"}</code></div>
    <div class="actions engine-actions"><span class="engine-state"><span class:connected={online} class="status-dot"></span>{online ? "引擎已连接" : "引擎未连接"}</span><button type="button" onclick={onReconnect} disabled={reconnecting}><RefreshCw size={15} />{reconnecting ? "重连中…" : "重新连接"}</button></div>
  </section>
</div>

<style>
  .settings-view { max-width: 920px; margin: 0 auto; padding: 2px 30px 30px; }
  section { padding: 20px 0; border-bottom: 1px solid var(--fs-border); }
  section:last-child { border-bottom: 0; }
  h2 { display: flex; align-items: center; gap: 9px; margin: 0 0 12px; font-size: 13px; font-weight: 600; }
  h2 :global(svg) { color: var(--fs-ink2); flex-shrink: 0; }
  .setting-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; min-height: 42px; }
  label { font-size: 12px; }
  select, input { height: 34px; min-width: 0; padding: 6px 10px; color: var(--fs-ink); background: var(--fs-surface-strong); border: 1px solid var(--fs-border); border-radius: 6px; font: inherit; }
  select { width: 240px; max-width: 60%; }
  input:disabled { opacity: .6; }
  .credential-row { display: flex; flex-direction: column; gap: 8px; margin: 14px 0; }
  .credential-row label { display: flex; justify-content: space-between; gap: 12px; }
  .credential-row span { color: var(--fs-ink2); font-size: 11px; }
  .actions { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; margin-top: 16px; }
  button { display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 7px 12px; color: var(--fs-ink); background: var(--fs-surface-strong); border: 1px solid var(--fs-border); border-radius: 6px; font: inherit; cursor: pointer; }
  button :global(svg) { flex-shrink: 0; }
  .primary { background: var(--fs-accent); color: var(--fs-on-accent); }
  button:disabled { opacity: .5; cursor: default; }
  p { margin: 9px 0; line-height: 1.7; font-size: 11px; overflow-wrap: anywhere; }
  .muted { color: var(--fs-ink2); }
  .notice { color: var(--fs-ink2); }
  .error { color: #c43c41; }
  .configuration-status { display: flex; align-items: flex-start; gap: 10px; }
  .configuration-status > :global(svg) { margin-top: 9px; flex-shrink: 0; color: var(--fs-ink2); }
  .configuration-status > :global(svg.success) { color: var(--fs-teal); }
  .configuration-status p { flex: 1; min-width: 0; }
  .icon-button { padding: 8px; flex: 0 0 34px; justify-content: center; }
  .retention-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
  .retention-grid label { display: flex; flex-direction: column; gap: 6px; color: var(--fs-ink2); font-size: 11px; }
  .retention-grid input { width: 100%; }
  .tutorial { margin-top: 14px; font-size: 11px; }
  .tutorial summary { cursor: pointer; }
  ol { margin: 14px 0 0; padding-left: 22px; }
  li { padding: 0 0 14px 5px; line-height: 1.7; }
  li strong, li span { display: block; }
  li strong { font-size: 12px; font-weight: 500; }
  li span { color: var(--fs-ink2); }
  a { color: var(--fs-accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .engine-row { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 16px; align-items: baseline; padding: 9px 0; font-size: 12px; }
  .engine-row code { min-width: 0; overflow-wrap: anywhere; font-size: 11px; color: var(--fs-ink2); }
  .engine-state { display: inline-flex; align-items: center; gap: 7px; color: var(--fs-ink2); font-size: 11px; }
  .status-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--fs-ink2); }
  .status-dot.connected { background: var(--fs-teal); }
  .settings-view :global(.spinning) { animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .settings-view :global(.spinning) { animation: none; } }
  @media (max-width: 600px) {
    .settings-view { padding: 2px 14px 24px; }
    select { max-width: 65%; }
    .setting-row { gap: 10px; }
    .engine-row { grid-template-columns: 64px minmax(0, 1fr); gap: 10px; }
    button, select, input { min-height: 38px; }
    .retention-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 420px) { .retention-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
