<script lang="ts">
  // firesky runs without user accounts, so the cloud "saved cities" feature is
  // intentionally absent. The component keeps the same props contract so the
  // forecast/nowcast pages do not need to branch on its availability.
  import { Badge } from '$lib/components/ui/badge';
  import MapPin from '@lucide/svelte/icons/map-pin';
  import type { UserCity } from '$lib/api/types';

  interface Props {
    lat: number;
    lon: number;
    name: string;
    timezoneName: string;
    elevationM: number;
    onselect: (city: UserCity) => void;
  }

  let { lat, lon, name, timezoneName, elevationM }: Props = $props();
</script>

<div class="flex items-center gap-1.5 text-xs text-[var(--muted)]">
  <Badge variant="secondary" class="gap-1">
    <MapPin class="h-3 w-3" />
    {name || `${lat.toFixed(3)}, ${lon.toFixed(3)}`}
  </Badge>
  <span>{timezoneName}</span>
  {#if elevationM}
    <span>· 海拔 {Math.round(elevationM)} m</span>
  {/if}
</div>
