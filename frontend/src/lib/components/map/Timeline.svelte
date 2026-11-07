<script lang="ts">
  import type { ForecastPayload, MapFramePayload } from '@/api/types';
  import { getMapFrame } from '@/api/map-frame';
  import { CHINA_TIME_ZONE, formatTime } from '@/utils/format';
  import { onDestroy } from 'svelte';
  import Play from '@lucide/svelte/icons/play';
  import Pause from '@lucide/svelte/icons/pause';

  export let forecast: ForecastPayload | null = null;
  export let timeZone: string = CHINA_TIME_ZONE;
  export let selectedTime: string | null = null;
  export let onFrameLoaded: (frame: MapFramePayload, index: number, mapToken: string) => void = () => {};
  export let onTimeSelected: (step: ForecastPayload['steps'][number]) => void = () => {};

  let sliderMax = 0;
  let sliderValue = 0;
  let timeReadout = '';
  let playing = false;
  let playTimer: ReturnType<typeof setInterval> | null = null;
  let timelineGeneration = 0;
  let frameRequestSequence = 0;

  const frameCache = new Map<string, MapFramePayload>();

  $: hasMapFrames = Boolean(forecast?.map_token && forecast.map_times.length > 0);
  $: timelineSteps = forecast?.steps?.filter((step) => step.valid) ?? [];
  $: timelineTimes = hasMapFrames
    ? (forecast?.map_times ?? [])
    : timelineSteps.map((step) => step.valid_time_local);

  $: if (forecast) {
    setupTimeline(forecast);
  }

  $: if (!forecast) {
    timelineGeneration += 1;
    frameRequestSequence += 1;
    frameCache.clear();
  }

  $: if (forecast && selectedTime) {
    const selectedIndex = timelineTimes.indexOf(selectedTime);
    if (selectedIndex >= 0 && selectedIndex !== sliderValue) {
      sliderValue = selectedIndex;
      if (hasMapFrames) loadMapFrame(selectedIndex);
      else notifyStep(selectedIndex);
    }
  }

  function setupTimeline(data: ForecastPayload) {
    timelineGeneration += 1;
    frameRequestSequence += 1;
    frameCache.clear();
    sliderMax = Math.max(0, timelineTimes.length - 1);
    const bestIndex = Math.max(
      0,
      timelineTimes.findIndex((t) => t === data.best_time_local),
    );
    sliderValue = bestIndex;
    if (hasMapFrames) {
      loadMapFrame(bestIndex, true, timelineGeneration);
    } else {
      notifyStep(bestIndex);
    }
  }

  async function loadMapFrame(index: number, fit: boolean = false, generation = timelineGeneration) {
    if (!forecast || !hasMapFrames) return;
    const activeForecast = forecast;
    const mapToken = forecast.map_token;
    const time = timelineTimes[index];
    if (!mapToken || !time) return;
    const requestSequence = ++frameRequestSequence;
    const isCurrentRequest = () => (
      requestSequence === frameRequestSequence
      && generation === timelineGeneration
      && forecast === activeForecast
      && forecast.map_token === mapToken
      && timelineTimes[index] === time
    );
    if (!isCurrentRequest()) return;

    timeReadout = `${formatTime(time, timeZone)} · 云场加载中`;

    try {
      let frame = frameCache.get(time);
      if (!frame) {
        frame = await getMapFrame(mapToken, time);
        if (!isCurrentRequest()) return;
        frameCache.set(time, frame);
      }

      if (!isCurrentRequest()) return;
      timeReadout = frame.valid
        ? `${formatTime(time, timeZone)} · 太阳高度 ${Number(frame.solar_elevation_deg).toFixed(1)}° · 质量 ${Number(frame.instant_quality_index).toFixed(3)} / 2.5`
        : `${formatTime(time, timeZone)} · 超出有效太阳窗口`;
      if (!isCurrentRequest()) return;
      onFrameLoaded(frame, index, mapToken);
    } catch (error) {
      if (!isCurrentRequest()) return;
      timeReadout = `${formatTime(time, timeZone)} · 加载失败`;
    }
  }

  function onSliderInput(event: Event) {
    const target = event.target as HTMLInputElement;
    sliderValue = Number(target.value);
    if (hasMapFrames) loadMapFrame(sliderValue);
    else notifyStep(sliderValue);
  }

  function notifyStep(index: number) {
    const step = timelineSteps[index];
    if (!step) return;
    timeReadout = `${formatTime(step.valid_time_local, timeZone)} · 质量 ${Number(step.instant_quality_index).toFixed(3)} / 2.5`;
    onTimeSelected(step);
  }

  function togglePlay() {
    if (playTimer) {
      clearInterval(playTimer);
      playTimer = null;
      playing = false;
      return;
    }
    playing = true;
    playTimer = setInterval(() => {
      sliderValue = (sliderValue + 1) % (sliderMax + 1);
      if (hasMapFrames) loadMapFrame(sliderValue);
      else notifyStep(sliderValue);
    }, 900);
  }

  onDestroy(() => {
    if (playTimer) clearInterval(playTimer);
  });
</script>

{#if forecast && timelineTimes.length > 0}
  <div class="flex items-center gap-3 px-4 py-2.5 border-b border-border bg-card/80 backdrop-blur-sm">
    <!-- Play/Pause button -->
    <button
      type="button"
      onclick={togglePlay}
      class="glass-control-well flex items-center justify-center w-8 h-8 rounded-lg border transition-all duration-200 shrink-0"
    >
      {#if playing}
        <Pause class="w-3.5 h-3.5 text-card-foreground" />
      {:else}
        <Play class="w-3.5 h-3.5 text-card-foreground ml-0.5" />
      {/if}
    </button>

    <!-- Slider -->
    <input
      type="range"
      min="0"
      max={sliderMax}
      value={sliderValue}
      step="1"
      aria-label="预报时刻"
      oninput={onSliderInput}
      class="flex-1 min-h-[28px] accent-[var(--firesky-2)]"
    />

    <!-- Time readout -->
    <div class="min-w-[200px] text-right text-xs text-muted-foreground font-mono-data shrink-0 hidden sm:block">
      {timeReadout || (hasMapFrames ? '云场加载中' : '逐时质量')}
    </div>
    <div class="min-w-[100px] text-right text-xs text-muted-foreground font-mono-data shrink-0 sm:hidden">
      {timeReadout.split(' · ')[0]}
    </div>
  </div>
{/if}
