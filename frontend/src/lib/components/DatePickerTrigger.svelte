<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import CalendarDays from '@lucide/svelte/icons/calendar-days';
  import ChevronLeft from '@lucide/svelte/icons/chevron-left';
  import ChevronRight from '@lucide/svelte/icons/chevron-right';
  import { chinaDateValue } from '$lib/utils/format';
  import { cn } from '$lib/utils/cn';

  interface Props {
    id: string;
    value: string;
    min?: string;
    max?: string;
    label?: string;
    active?: boolean;
    disabled?: boolean;
    class?: string;
    onchange?: (value: string) => void;
  }

  type CalendarDate = { year: number; month: number; day: number };

  let {
    id,
    value,
    min,
    max,
    label = '更多日期',
    active = false,
    disabled = false,
    class: className = '',
    onchange,
  }: Props = $props();

  let triggerRef: HTMLButtonElement | null = null;
  let calendarRef: HTMLDivElement | null = null;
  let open = $state(false);
  let viewYear = $state(0);
  let viewMonth = $state(0);

  const weekdays = ['一', '二', '三', '四', '五', '六', '日'];

  function parseDate(value: string): CalendarDate | null {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return null;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const parsed = new Date(Date.UTC(year, month - 1, day));
    if (
      parsed.getUTCFullYear() !== year
      || parsed.getUTCMonth() !== month - 1
      || parsed.getUTCDate() !== day
    ) return null;
    return { year, month, day };
  }

  function todayParts(): CalendarDate {
    return parseDate(chinaDateValue()) ?? { year: 1970, month: 1, day: 1 };
  }

  function dateValue(year: number, month: number, day: number): string {
    return new Date(Date.UTC(year, month - 1, day)).toISOString().slice(0, 10);
  }

  function formatDate(value: string): string {
    const date = parseDate(value);
    return date ? `${date.year}年${String(date.month).padStart(2, '0')}月${String(date.day).padStart(2, '0')}日` : '选择日期';
  }

  function daysInMonth(year: number, month: number): number {
    return new Date(Date.UTC(year, month, 0)).getUTCDate();
  }

  function firstWeekday(year: number, month: number): number {
    const sundayFirst = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
    return (sundayFirst + 6) % 7;
  }

  function syncViewToValue() {
    const selected = parseDate(value) ?? todayParts();
    viewYear = selected.year;
    viewMonth = selected.month;
  }

  function toggleCalendar() {
    if (disabled) return;
    if (!open) syncViewToValue();
    open = !open;
  }

  function closeCalendar(restoreFocus = true) {
    if (!open) return;
    open = false;
    if (restoreFocus) triggerRef?.focus();
  }

  function shiftMonth(offset: number) {
    const next = new Date(Date.UTC(viewYear, viewMonth - 1 + offset, 1));
    viewYear = next.getUTCFullYear();
    viewMonth = next.getUTCMonth() + 1;
  }

  function isDisabled(date: string): boolean {
    return Boolean((min && date < min) || (max && date > max));
  }

  function isToday(date: string): boolean {
    return date === chinaDateValue();
  }

  function selectDate(date: string) {
    if (isDisabled(date)) return;
    onchange?.(date);
    closeCalendar();
  }

  function handleDocumentPointerDown(event: PointerEvent) {
    if (open && calendarRef && !calendarRef.contains(event.target as Node)) closeCalendar(false);
  }

  function handleDocumentFocusIn(event: FocusEvent) {
    if (open && calendarRef && !calendarRef.contains(event.target as Node)) closeCalendar(false);
  }

  function handleDocumentKeydown(event: KeyboardEvent) {
    if (open && event.key === 'Escape') {
      event.preventDefault();
      closeCalendar();
    }
  }

  onMount(() => {
    document.addEventListener('pointerdown', handleDocumentPointerDown);
    document.addEventListener('focusin', handleDocumentFocusIn);
    document.addEventListener('keydown', handleDocumentKeydown);
  });

  onDestroy(() => {
    document.removeEventListener('pointerdown', handleDocumentPointerDown);
    document.removeEventListener('focusin', handleDocumentFocusIn);
    document.removeEventListener('keydown', handleDocumentKeydown);
  });
</script>

<div class="relative min-w-0 {className}" bind:this={calendarRef}>
  <button
    bind:this={triggerRef}
    id={id}
    type="button"
    class={cn(
      'flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded-lg border px-3 text-sm outline-none transition-colors focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20',
      active
        ? 'border-primary bg-primary/10 text-primary'
        : 'glass-control-well text-foreground',
      disabled && 'cursor-not-allowed opacity-50',
    )}
    aria-label={label}
    aria-haspopup="dialog"
    aria-expanded={open}
    aria-controls={`${id}-calendar`}
    title={label}
    {disabled}
    onclick={toggleCalendar}
  >
    <span class="min-w-0 truncate">{formatDate(value)}</span>
    <CalendarDays class="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
  </button>

  {#if open}
    <div
      id={`${id}-calendar`}
      class="glass-panel-strong absolute right-0 z-[1100] mt-2 w-[min(18rem,calc(100vw-2rem))] max-w-[calc(100vw-2rem)] rounded-lg border p-3 text-popover-foreground outline-none"
      role="dialog"
      aria-label={`${label}日历`}
    >
      <div class="mb-3 flex items-center justify-between gap-2">
        <button
          type="button"
          class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
          aria-label="上一个月"
          onclick={() => shiftMonth(-1)}
        >
          <ChevronLeft class="h-4 w-4" aria-hidden="true" />
        </button>
        <strong class="text-sm font-semibold">{viewYear}年{viewMonth}月</strong>
        <button
          type="button"
          class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
          aria-label="下一个月"
          onclick={() => shiftMonth(1)}
        >
          <ChevronRight class="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      <div class="grid grid-cols-7 gap-1 text-center text-[11px] text-muted-foreground" role="row">
        {#each weekdays as weekday}
          <span role="columnheader" aria-label={`星期${weekday}`}>{weekday}</span>
        {/each}
      </div>

      <div class="mt-1 grid grid-cols-7 gap-1" role="grid" aria-label={`${viewYear}年${viewMonth}月日期`}>
        {#each Array(firstWeekday(viewYear, viewMonth)) as _}
          <span aria-hidden="true"></span>
        {/each}
        {#each Array(daysInMonth(viewYear, viewMonth)) as _, index}
          {@const day = index + 1}
          {@const currentDate = dateValue(viewYear, viewMonth, day)}
          {@const selected = currentDate === value}
          <button
            type="button"
            class={cn(
              'relative flex h-8 min-w-0 items-center justify-center rounded-md text-xs tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30',
              selected
                ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                : 'text-foreground hover:bg-muted',
              isToday(currentDate) && !selected && 'font-semibold text-primary ring-1 ring-primary/40',
              isDisabled(currentDate) && 'cursor-not-allowed opacity-35 hover:bg-transparent',
            )}
            aria-label={formatDate(currentDate)}
            aria-current={isToday(currentDate) ? 'date' : undefined}
            aria-pressed={selected}
            disabled={isDisabled(currentDate)}
            onclick={() => selectDate(currentDate)}
          >
            {day}
            {#if isToday(currentDate) && !selected}
              <span class="absolute bottom-0.5 h-0.5 w-0.5 rounded-full bg-primary" aria-hidden="true"></span>
            {/if}
          </button>
        {/each}
      </div>
    </div>
  {/if}
</div>
