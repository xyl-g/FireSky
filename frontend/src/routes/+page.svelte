<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';

  // SSR 关闭时 load() 的 redirect 不生效（纯客户端渲染），这里兜底跳转。
  onMount(() => {
    void goto('/forecast', { replaceState: true });
  });
</script>

<div class="flex h-screen items-center justify-center text-sm text-muted-foreground">
  正在进入预测工作台…
</div>
