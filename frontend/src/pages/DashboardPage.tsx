import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listRepositories, listScans } from "../api";
import type { Repository, ScanSummary } from "../api";
import { Scan, GitBranch, Plus } from "lucide-react";

interface RecentScan extends ScanSummary {
  repoName: string;
  repoBranch: string;
}

export function DashboardPage() {
  const navigate = useNavigate();
  const [repos, setRepos] = useState<Repository[]>([]);
  const [recentScans, setRecentScans] = useState<RecentScan[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const repositories = await listRepositories();
        setRepos(repositories);

        // Fetch scans for all repos
        const scanPromises = repositories.map(async (repo) => {
          const scans = await listScans(repo.id);
          return scans.map((s) => ({
            ...s,
            repoName: `${repo.name}`,
            repoBranch: repo.default_branch ?? "main",
          }));
        });

        const allScans = (await Promise.all(scanPromises)).flat();
        allScans.sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );
        setRecentScans(allScans.slice(0, 10));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const totalScans = recentScans.length;

  if (loading) {
    return <div className="loading">Loading...</div>;
  }

  return (
    <>
      <div className="content-header">
        <h1 className="content-header-title">Dashboard</h1>
        <div className="content-header-actions">
          <button
            className="btn btn-primary"
            onClick={() => navigate("/repositories")}
          >
            <Plus size={16} />
            New Scan
          </button>
        </div>
      </div>

      <div className="content-body">
        <div className="dashboard-body">
          {/* Stats Row */}
          <div className="stats-row">
            <div className="stat-card">
              <div className="stat-card-top">
                <span className="stat-card-label">Total Scans</span>
                <Scan size={18} className="stat-card-icon" />
              </div>
              <span className="stat-card-value">{totalScans}</span>
            </div>
            <div className="stat-card">
              <div className="stat-card-top">
                <span className="stat-card-label">Repos Connected</span>
                <GitBranch size={18} className="stat-card-icon" />
              </div>
              <span className="stat-card-value">{repos.length}</span>
            </div>
          </div>

          {/* Recent Scans */}
          <div>
            <div className="section-header">
              <h2 className="section-title">Recent Scans</h2>
              <a href="/repositories" className="section-link">
                View All
              </a>
            </div>

            <div className="table-card">
              <div className="table-header-row">
                <span className="table-header-cell col-repo">Repository</span>
                <span className="table-header-cell col-branch">Branch</span>
                <span className="table-header-cell col-status">Status</span>
                <span className="table-header-cell col-vuln">
                  Vulnerabilities
                </span>
                <span className="table-header-cell col-date">Date</span>
              </div>
              {recentScans.length === 0 ? (
                <div className="empty-state">
                  <Scan size={32} />
                  <p>No scans yet. Connect a repository to get started.</p>
                </div>
              ) : (
                recentScans.map((scan) => (
                  <div
                    key={scan.id}
                    className="table-row"
                    onClick={() => navigate(`/scans/${scan.id}`)}
                  >
                    <span className="table-cell repo col-repo">
                      {scan.repoName}
                    </span>
                    <span className="table-cell branch col-branch">
                      {scan.repoBranch}
                    </span>
                    <span className="table-cell col-status">
                      <span className={`status-badge ${scan.status}`}>
                        <span className="status-dot" />
                        {scan.status === "complete" ? "Completed" : scan.status.charAt(0).toUpperCase() + scan.status.slice(1)}
                      </span>
                    </span>
                    <span className="table-cell vuln col-vuln">—</span>
                    <span className="table-cell date col-date">
                      {new Date(scan.created_at).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
