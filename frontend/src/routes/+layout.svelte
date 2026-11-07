<script lang="ts">
  import '../app.css';
  import { ModeWatcher } from 'mode-watcher';
  let { children } = $props();
</script>

<ModeWatcher />
<!-- 液态玻璃折射镜头（全站共享，仅 Chromium 生效） -->
<svg class="hidden" aria-hidden="true">
  <filter
    id="liquid-glass-lens"
    x="-20%"
    y="-20%"
    width="140%"
    height="140%"
    color-interpolation-filters="sRGB"
  >
    <feTurbulence type="fractalNoise" baseFrequency="0.006 0.009" numOctaves="2" seed="7" result="noise" />
    <feGaussianBlur in="noise" stdDeviation="1.6" result="lens-map" />
    <feDisplacementMap in="SourceGraphic" in2="lens-map" scale="8" xChannelSelector="R" yChannelSelector="G" />
  </filter>
</svg>
<div class="app-root min-h-screen flex flex-col">
  {@render children()}
</div>
