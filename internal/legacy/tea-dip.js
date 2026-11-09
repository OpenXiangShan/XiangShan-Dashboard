'use strict';

document.getElementById('sliceTeaDipOpen').addEventListener('click', () => {
  const slice = model.sliceIndex.get(state.level === 'counter' ? state.counterSlice : state.slice);
  const run = model.commits[state.target];
  const query = new URLSearchParams({
    slice:`${slice.workload}:${slice.checkpoint}`,
    checkpoint_root:slice.checkpoint_identity.replace(/\/checkpoint\/?$/, ''),
    source_uri:run.source_uri,
    observation:`${run.short_commit} · 测量 ${run.run_id.slice(0, 12)}`
  });
  window.open(`reports/tea-dip/index.html?${query}`, '_blank', 'noopener');
});
