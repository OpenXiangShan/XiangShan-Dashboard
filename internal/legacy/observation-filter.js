"use strict";

// Keep every recorded measurement; missing metrics do not hide its commit.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ObservationFilter = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  function buildObservations(catalogCommits, runs) {
    const byCommit = new Map();
    for (const run of runs || []) {
      if (!run?.run_id || !run.commit_sha) continue;
      const { results, counters, workloads, scores, ...metadata } = run;
      if (!byCommit.has(run.commit_sha)) byCommit.set(run.commit_sha, []);
      byCommit.get(run.commit_sha).push(metadata);
    }
    return (catalogCommits || []).flatMap(commit =>
      (byCommit.get(commit.commit_sha) || []).map(run =>
        ({ ...commit, ...run, observation_key: run.run_id }))
    );
  }

  function measuredCatalogCommits(catalogCommits, observations) {
    const shas = new Set((observations || []).map(row => row.commit_sha).filter(Boolean));
    return (catalogCommits || []).filter(commit => shas.has(commit.commit_sha));
  }

  return { buildObservations, measuredCatalogCommits };
});
