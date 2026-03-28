<script lang="ts">
  let {
    variant = 'default',
    children,
  }: { variant?: 'default' | 'success' | 'warning' | 'error' | 'accent'; children?: import('svelte').Snippet } = $props();

  const styles: Record<string, string> = {
    default: 'bg-zinc-800 text-zinc-400',
    success: 'bg-emerald-500/15 text-emerald-400',
    warning: 'bg-amber-500/15 text-amber-400',
    error: 'bg-red-500/15 text-red-400',
    accent: 'bg-violet-500/15 text-violet-400',
  };
</script>

<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium {styles[variant]}">
  {@render children?.()}
</span>
