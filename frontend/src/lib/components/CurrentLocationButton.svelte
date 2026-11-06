<script lang="ts">
  import LoaderCircle from '@lucide/svelte/icons/loader-circle';
  import LocateFixed from '@lucide/svelte/icons/locate-fixed';
  import { Button } from '$lib/components/ui/button';
  import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
  } from '$lib/components/ui/tooltip';
  import {
    geolocationErrorStatus,
    geolocationMessage,
    geolocationState,
    requestCurrentLocation,
    type GeolocationState,
    type GeolocationStatus,
    type LocalCoordinates,
  } from '$lib/utils/geolocation';

  interface Props {
    onlocate: (coordinates: LocalCoordinates) => void | Promise<void>;
    onstatechange?: (state: GeolocationState) => void;
    class?: string;
    disabled?: boolean;
  }

  let {
    onlocate,
    onstatechange,
    class: className = '',
    disabled = false,
  }: Props = $props();

  let status = $state<GeolocationStatus>('idle');
  let message = $state('');

  function publish(nextStatus: GeolocationStatus, nextMessage?: string) {
    status = nextStatus;
    message = nextMessage ?? geolocationMessage(nextStatus);
    onstatechange?.(geolocationState(nextStatus, message));
  }

  async function locate() {
    if (disabled || status === 'loading') return;
    publish('loading');
    try {
      const coordinates = await requestCurrentLocation();
      await onlocate(coordinates);
      publish('success');
    } catch (error: unknown) {
      const requestError = geolocationErrorStatus(error);
      publish(requestError.status, requestError.message);
    }
  }

  function label(): string {
    if (status === 'loading') return '正在获取当前位置';
    if (status === 'success') return '已使用当前位置，再次点击可刷新';
    return '使用当前位置';
  }
</script>

<TooltipProvider>
  <Tooltip>
    <TooltipTrigger>
      {#snippet child({ props })}
        <Button
          {...props}
          type="button"
          variant="outline"
          size="icon-sm"
          class={className}
          disabled={disabled || status === 'loading'}
          aria-label={label()}
          aria-busy={status === 'loading'}
          title={message || label()}
          onclick={locate}
        >
          {#if status === 'loading'}
            <LoaderCircle class="animate-spin" aria-hidden="true" />
          {:else}
            <LocateFixed aria-hidden="true" />
          {/if}
        </Button>
      {/snippet}
    </TooltipTrigger>
    <TooltipContent>使用当前位置</TooltipContent>
  </Tooltip>
</TooltipProvider>
