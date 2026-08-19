/**
 * Acdyon Job Ingestion Dashboard API Client
 */

const API_BASE_URL = window.PUBLIC_API_BASE_URL || "";
let currentPage = 1;
const pageSize = 25;
let currentTotalJobs = 0;
let cachedJobs = [];

document.addEventListener("DOMContentLoaded", () => {
  initDashboard();
});

async function initDashboard() {
  await checkApiHealth();
  await loadSourceHealth();
  await loadLatestRun();
  await loadJobs(currentPage);
}

async function checkApiHealth() {
  const dot = document.getElementById("api-status-dot");
  const label = document.getElementById("api-status-text");
  try {
    const res = await fetch(API_BASE_URL + "/health");
    if (res.ok) {
      dot.className = "status-indicator online";
      label.textContent = "API: Online";
    } else {
      dot.className = "status-indicator offline";
      label.textContent = "API: Degrading";
    }
  } catch (err) {
    dot.className = "status-indicator offline";
    label.textContent = "API: Offline";
  }
}

async function loadSourceHealth() {
  try {
    const res = await fetch(API_BASE_URL + "/api/v1/health/weworkremotely");
    if (!res.ok) {
      if (res.status === 404) {
        document.getElementById("health-status-value").textContent = "UNINITIALIZED";
        document.getElementById("health-status-value").className = "text-muted";
        document.getElementById("health-last-success").textContent = "Never";
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    const statusEl = document.getElementById("health-status-value");
    const healthStr = (data.health_status || "UNKNOWN").toUpperCase();
    statusEl.textContent = healthStr;

    if (healthStr === "HEALTHY") {
      statusEl.className = "status-healthy";
    } else if (healthStr === "DEGRADED") {
      statusEl.className = "status-degraded";
    } else {
      statusEl.className = "status-unreachable";
    }

    if (data.last_success_at) {
      const dt = new Date(data.last_success_at);
      document.getElementById("health-last-success").textContent = dt.toLocaleString();
    } else {
      document.getElementById("health-last-success").textContent = "Never";
    }
  } catch (err) {
    console.warn("Failed to load source health:", err);
  }
}

async function loadLatestRun() {
  try {
    const res = await fetch(API_BASE_URL + "/api/v1/runs/latest/weworkremotely");
    if (!res.ok) return;
    const data = await res.json();
    
    document.getElementById("telemetry-run-id").textContent = data.run_id || "run_—";
    document.getElementById("telemetry-status").textContent = (data.status || "UNKNOWN").toUpperCase();
    document.getElementById("telemetry-duration").textContent = `${data.duration_ms || 0} ms`;
    document.getElementById("telemetry-received").textContent = data.records_received || 0;
    document.getElementById("telemetry-accepted").textContent = data.records_accepted || 0;
    document.getElementById("telemetry-rejected").textContent = data.records_rejected || 0;
    document.getElementById("telemetry-duplicates").textContent = data.duplicates_detected || 0;
  } catch (err) {
    console.warn("Failed to load latest run:", err);
  }
}

async function loadJobs(page = 1) {
  const tbody = document.getElementById("jobs-tbody");
  const offset = (page - 1) * pageSize;

  try {
    const res = await fetch(API_BASE_URL + `/api/v1/jobs?source_name=weworkremotely&limit=${pageSize}&offset=${offset}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const jobs = await res.json();
    cachedJobs = jobs;

    if (jobs.length === 0) {
      tbody.innerHTML = `
        <tr class="empty-row">
          <td colspan="6" class="text-muted text-center" style="padding: 24px;">
            No jobs ingested yet. Click <strong>'Ingest latest jobs'</strong> above to fetch canonical postings.
          </td>
        </tr>
      `;
      document.getElementById("jobs-count-tag").textContent = "0 jobs";
      document.getElementById("health-total-jobs").textContent = "0";
      updatePagination(0, page);
      return;
    }

    // Estimate count or update tag
    document.getElementById("jobs-count-tag").textContent = `Showing ${jobs.length} jobs (Page ${page})`;
    
    let html = "";
    jobs.forEach(job => {
      const pubDate = job.published_at ? new Date(job.published_at).toLocaleDateString() : "Unknown";
      const sourceUrl = job.source_url || "#";

      html += `
        <tr>
          <td class="job-title-cell">${escapeHtml(job.title || "Untitled")}</td>
          <td>${escapeHtml(job.company || "Unknown")}</td>
          <td>${escapeHtml(job.location || "Remote")}</td>
          <td class="mono-cell">${pubDate}</td>
          <td class="mono-cell">We Work Remotely</td>
          <td class="text-right">
            <button class="btn-link" onclick="openJobDetail('${job.canonical_id}')">Details</button>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
    updatePagination(jobs.length, page);

    // Update total count indicator from latest fetch
    if (page === 1) {
      document.getElementById("health-total-jobs").textContent = jobs.length >= pageSize ? `${jobs.length}+` : jobs.length;
    }
  } catch (err) {
    console.error("Failed to load jobs:", err);
    tbody.innerHTML = `
      <tr class="empty-row">
        <td colspan="6" class="text-muted text-center text-unreachable" style="padding: 24px;">
          Failed to load jobs: ${escapeHtml(err.message)}
        </td>
      </tr>
    `;
  }
}

function updatePagination(fetchedCount, page) {
  currentPage = page;
  const prevBtn = document.getElementById("btn-prev");
  const nextBtn = document.getElementById("btn-next");
  const info = document.getElementById("pagination-info");

  prevBtn.disabled = page <= 1;
  nextBtn.disabled = fetchedCount < pageSize;
  info.textContent = `Page ${page}`;
}

function prevPage() {
  if (currentPage > 1) {
    loadJobs(currentPage - 1);
  }
}

function nextPage() {
  loadJobs(currentPage + 1);
}

async function triggerIngestion() {
  const btn = document.getElementById("btn-ingest");
  const errorBanner = document.getElementById("error-banner");
  const errorMessage = document.getElementById("error-message");
  
  errorBanner.classList.add("hidden");
  btn.disabled = true;
  btn.textContent = "Ingesting...";

  try {
    const res = await fetch(API_BASE_URL + "/api/v1/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_name: "weworkremotely" })
    });

    const data = await res.json();

    if (!res.ok && res.status !== 502) {
      throw new Error(data.detail || `Ingestion request failed with HTTP ${res.status}`);
    }

    if (data.status === "failed") {
      errorBanner.classList.remove("hidden");
      const errText = (data.errors && data.errors.length > 0) ? data.errors[0].message : "Ingestion run failed.";
      errorMessage.textContent = `Ingestion run status: FAILED. Reason: ${errText}`;
    }

    // Refresh dashboard state after request completes
    await loadSourceHealth();
    await loadLatestRun();
    await loadJobs(1);

  } catch (err) {
    console.error("Ingestion failed:", err);
    errorBanner.classList.remove("hidden");
    errorMessage.textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Ingest latest jobs";
  }
}

function openJobDetail(canonicalId) {
  const job = cachedJobs.find(j => j.canonical_id === canonicalId);
  if (!job) return;

  document.getElementById("drawer-job-title").textContent = job.title || "Job Details";
  
  const body = document.getElementById("drawer-body");
  const pubDate = job.published_at ? new Date(job.published_at).toLocaleString() : "N/A";
  
  body.innerHTML = `
    <div class="drawer-field">
      <div class="drawer-field-label">Canonical ID</div>
      <div class="drawer-field-value mono-cell">${escapeHtml(job.canonical_id)}</div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">Company</div>
      <div class="drawer-field-value">${escapeHtml(job.company || "N/A")}</div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">Location</div>
      <div class="drawer-field-value">${escapeHtml(job.location || "N/A")}</div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">Employment Type</div>
      <div class="drawer-field-value">${escapeHtml(job.employment_type || "N/A")}</div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">Published At</div>
      <div class="drawer-field-value mono-cell">${pubDate}</div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">Source URL</div>
      <div class="drawer-field-value">
        <a href="${escapeHtml(job.source_url)}" target="_blank" rel="noopener noreferrer" class="feed-link">
          ${escapeHtml(job.source_url)} ↗
        </a>
      </div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">Raw Description</div>
      <div class="drawer-field-value" style="max-height: 200px; overflow-y: auto; white-space: pre-wrap; font-size: 12px; background: var(--bg-subtle); padding: 10px; border-radius: 4px; border: 1px solid var(--border-color);">
        ${escapeHtml(job.description || "No description")}
      </div>
    </div>
    <div class="drawer-field">
      <div class="drawer-field-label">Metadata & Raw Tags</div>
      <div class="drawer-json-box">${escapeHtml(JSON.stringify(job.metadata || {}, null, 2))}</div>
    </div>
  `;

  document.getElementById("drawer-backdrop").classList.remove("hidden");
}

function closeDrawer() {
  document.getElementById("drawer-backdrop").classList.add("hidden");
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
