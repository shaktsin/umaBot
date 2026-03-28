<script lang="ts">
  let {
    data = [] as number[],
    width = 100,
    height = 28,
    color = '#8b5cf6',
  }: { data?: number[]; width?: number; height?: number; color?: string } = $props();

  const points = $derived(
    data.length < 2
      ? ''
      : (() => {
          const max = Math.max(...data, 1);
          const min = Math.min(...data, 0);
          const range = max - min || 1;
          const pad = 2;
          return data
            .map((v, i) => {
              const x = (i / (data.length - 1)) * width;
              const y = height - pad - ((v - min) / range) * (height - pad * 2);
              return `${x.toFixed(1)},${y.toFixed(1)}`;
            })
            .join(' ');
        })()
  );
</script>

<svg {width} {height} viewBox="0 0 {width} {height}" class="overflow-visible">
  {#if points}
    <polyline
      points={points}
      fill="none"
      stroke={color}
      stroke-width="1.5"
      stroke-linecap="round"
      stroke-linejoin="round"
      opacity="0.9"
    />
  {/if}
</svg>
