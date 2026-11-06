// 管理端设计令牌 —— 使用应用语义色，玻璃卡片 + 柔和阴影

export const inputCls =
  'glass-input w-full rounded-lg border px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground outline-none transition disabled:opacity-50';

export const labelCls = 'block text-xs font-medium text-muted-foreground mb-1';

export const primaryBtn =
  'inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition hover:bg-indigo-500 active:bg-indigo-700 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50';

export const ghostBtn =
  'glass-control-well inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground transition hover:text-foreground cursor-pointer';

export const dangerBtn =
  'inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition hover:bg-rose-500 cursor-pointer disabled:opacity-50';

export const cardCls =
  'glass-panel rounded-2xl border';

export const iconBtn =
  'rounded-lg p-1.5 text-muted-foreground transition hover:bg-accent hover:text-foreground cursor-pointer';

// 卡片内小节标题
export const cardTitle = 'text-[13px] font-semibold text-foreground';

// ── 轻量 Toast（替代 alert）──

export type ToastType = 'success' | 'error' | 'info';
export const TOAST_EVENT = 'admin-toast';

export const notify = (message: string, type: ToastType = 'success') => {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(TOAST_EVENT, { detail: { message, type } }));
  }
};
