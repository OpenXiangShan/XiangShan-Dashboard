export const MAX_METRICS = 512;

// Group membership comes from the server, independently of the search/page.
export function toggleMetricGroup(
  metrics: string[],
  groups: string[],
  name: string,
  members: Record<string, string[]>,
): { metrics: string[]; groups: string[] } {
  if (!groups.includes(name)) {
    return {
      groups: [...groups, name],
      metrics: [...new Set([...metrics, ...(members[name] || [])])],
    };
  }
  const remaining = groups.filter((group) => group !== name);
  const retained = new Set(remaining.flatMap((group) => members[group] || []));
  const removed = new Set(members[name] || []);
  return {
    groups: remaining,
    metrics: metrics.filter(
      (metric) => !removed.has(metric) || retained.has(metric),
    ),
  };
}
