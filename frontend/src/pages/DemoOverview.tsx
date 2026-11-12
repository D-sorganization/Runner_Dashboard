const runners = [
  { name: 'linux-builder-01', status: 'Online', detail: 'Idle · linux-x64' },
  { name: 'linux-builder-02', status: 'Busy', detail: 'Build #1842 · 6m' },
  { name: 'windows-test-01', status: 'Offline', detail: 'Windows · last seen 2h ago' },
]

const recentRuns = [
  { repo: 'sample-api', workflow: 'CI', branch: 'main', result: 'Passed', ago: '8 min ago' },
  { repo: 'web-console', workflow: 'Frontend tests', branch: 'feature/search', result: 'Running', ago: '2 min ago' },
  { repo: 'sample-api', workflow: 'Release checks', branch: 'v2.4.0', result: 'Failed', ago: '31 min ago' },
]

const metrics = [
  { label: 'Runners online', value: '2 / 3' },
  { label: 'Jobs in progress', value: '1' },
  { label: 'Repositories', value: '2' },
]

export function DemoOverview() {
  return (
    <main className="demo-overview" aria-labelledby="demo-title">
      <header className="demo-header">
        <div>
          <p className="demo-eyebrow">RUNNER DASHBOARD</p>
          <h1 id="demo-title">Fleet overview</h1>
          <p>Explore a sample runner fleet and recent workflow activity.</p>
        </div>
        <span className="demo-badge" role="status">DEMO DATA · READ ONLY</span>
      </header>

      <section className="demo-metrics" aria-label="Fleet summary">
        {metrics.map((metric) => (
          <article className="demo-card" key={metric.label}>
            <p>{metric.label}</p>
            <strong>{metric.value}</strong>
          </article>
        ))}
      </section>

      <div className="demo-columns">
        <section className="demo-card" aria-labelledby="demo-runners-title">
          <h2 id="demo-runners-title">Runners</h2>
          <ul className="demo-list">
            {runners.map((runner) => (
              <li key={runner.name}>
                <span><strong>{runner.name}</strong><small>{runner.detail}</small></span>
                <span className={`demo-status demo-status-${runner.status.toLowerCase()}`}>{runner.status}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="demo-card" aria-labelledby="demo-runs-title">
          <h2 id="demo-runs-title">Recent workflow runs</h2>
          <ul className="demo-list">
            {recentRuns.map((run) => (
              <li key={`${run.repo}-${run.workflow}`}>
                <span><strong>{run.workflow} · {run.repo}</strong><small>{run.branch} · {run.ago}</small></span>
                <span className={`demo-status demo-status-${run.result.toLowerCase()}`}>{run.result}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <p className="demo-note">These examples are local sample data. The demo does not connect to GitHub or change any external services.</p>
    </main>
  )
}
