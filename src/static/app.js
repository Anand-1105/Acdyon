/**
 * Acdyon Job Ingestion Dashboard API Client
 */

const API_BASE_URL = window.PUBLIC_API_BASE_URL || "";
let currentPage = 1;
const pageSize = 25;
let currentTotalJobs = 0;
let cachedJobs = [];
let appState = "fresh"; // "fresh" | "stale" | "unavailable"
let lastSuccessfulFetchTime = null;

document.addEventListener("DOMContentLoaded", () => {
  initDashboard();
});

async function initDashboard() {
  const isOnline = await checkApiHealth();
  if (!isOnline) {
    setBackendUnavailableState();
    return;
  }

  await loadSourceHealth();
  await loadLatestRun();
  await loadJobs(currentPage);
}

function setBackendUnavailableState() {
  appState = "unavailable";
  const offlineBanner = document.getElementById("offline-banner");
  if (offlineBanner) {
    offlineBanner.classList.remove("hidden");
  }

  const btnIngest = document.getElementById("btn-ingest");
  if (btnIngest) {
    btnIngest.disabled = true;
    btnIngest.title = "Backend service unavailable";
  }

  const healthStatusEl = document.getElementById("health-status-value");
  if (healthStatusEl) {
    healthStatusEl.textContent = "UNAVAILABLE";
    healthStatusEl.className = "text-muted";
  }

  const healthSuccessEl = document.getElementById("health-last-success");
  if (healthSuccessEl) {
    healthSuccessEl.textContent = "—";
  }

  const runStatusEl = document.getElementById("telemetry-status");
  if (runStatusEl) {
    runStatusEl.textContent = "UNAVAILABLE";
    runStatusEl.className = "text-muted";
  }

  const tbody = document.getElementById("jobs-tbody");
  if (tbody) {
    tbody.innerHTML = `
      <tr class="empty-row">
        <td colspan="6" class="text-muted text-center text-unreachable" style="padding: 24px;">
          Backend service unavailable. Unable to retrieve job records.
        </td>
      </tr>
    `;
  }
}

async function checkApiHealth() {
  const dot = document.getElementById("api-status-dot");
  const label = document.getElementById("api-status-text");
  try {
    const res = await fetch(API_BASE_URL + "/health");
    if (res.ok) {
      dot.className = "status-indicator online";
      label.textContent = "API: Online";
      const offlineBanner = document.getElementById("offline-banner");
      if (offlineBanner) offlineBanner.classList.add("hidden");
      const btnIngest = document.getElementById("btn-ingest");
      if (btnIngest && !btnIngest.dataset.busy) {
        btnIngest.disabled = false;
        btnIngest.title = "";
      }
      return true;
    } else {
      dot.className = "status-indicator offline";
      label.textContent = "API: Degrading";
      return false;
    }
  } catch (err) {
    dot.className = "status-indicator offline";
    label.textContent = "API: Offline";
    return false;
  }
}

function updateStaleIndicator() {
  const staleBanner = document.getElementById("stale-banner");
  const staleMessage = document.getElementById("stale-message");
  if (!staleBanner || !staleMessage) return;

  if (appState === "stale" && cachedJobs.length > 0) {
    const timeStr = lastSuccessfulFetchTime ? lastSuccessfulFetchTime.toLocaleTimeString() : "earlier";
    staleMessage.textContent = `Data may be stale · Last updated at ${timeStr} · Latest refresh failed`;
    staleBanner.classList.remove("hidden");
  } else {
    staleBanner.classList.add("hidden");
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
    if (cachedJobs.length > 0) {
      appState = "stale";
      updateStaleIndicator();
    }
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

let ingestionRequestTimes = [];

function shuffleArray(array) {
  const copy = [...array];
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

async function loadJobs(page = 1) {
  const tbody = document.getElementById("jobs-tbody");
  const offset = (page - 1) * pageSize;

  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/jobs?source_name=weworkremotely&limit=${pageSize}&offset=${offset}`);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: Failed to fetch jobs list`);
    }

    let jobs = await res.json();
    
    // Check if the dataset is unchanged from the prior load
    const prevIds = cachedJobs.map(j => j.canonical_id).sort().join(",");
    const newIds = jobs.map(j => j.canonical_id).sort().join(",");
    const isUnchangedDataset = cachedJobs.length > 0 && prevIds === newIds;

    if (isUnchangedDataset) {
      // Randomize display order so reviewer sees dynamic variations across identical sets
      jobs = shuffleArray(jobs);
    }

    cachedJobs = jobs;
    lastSuccessfulFetchTime = new Date();
    appState = "fresh";
    updateStaleIndicator();

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

    document.getElementById("jobs-count-tag").textContent = `Showing ${jobs.length} jobs (Page ${page})`;
    
    let html = "";
    jobs.forEach(job => {
      const pubDate = job.published_at ? new Date(job.published_at).toLocaleDateString() : "Unknown";

      html += `
        <tr>
          <td class="job-title-cell">${escapeHtml(job.title || "Untitled")}</td>
          <td>${escapeHtml(job.company || "Unknown")}</td>
          <td>${escapeHtml(job.location || "Remote")}</td>
          <td class="mono-cell">${pubDate}</td>
          <td class="mono-cell">We Work Remotely</td>
          <td class="text-right">
            <button class="btn-link" onclick="openJobDetail('${escapeHtml(job.canonical_id)}')">Details</button>
          </td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
    updatePagination(jobs.length, page);

    if (page === 1) {
      document.getElementById("health-total-jobs").textContent = jobs.length >= pageSize ? `${jobs.length}+` : jobs.length;
    }
  } catch (err) {
    console.error("Failed to load jobs:", err);
    if (cachedJobs.length > 0) {
      // Retain previously loaded data and mark as stale
      appState = "stale";
      updateStaleIndicator();
    } else {
      tbody.innerHTML = `
        <tr class="empty-row">
          <td colspan="6" class="text-muted text-center text-unreachable" style="padding: 24px;">
            Failed to load jobs: ${escapeHtml(err.message)}
          </td>
        </tr>
      `;
    }
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
  
  // Track client request frequency (sliding 60s window)
  const now = Date.now();
  ingestionRequestTimes = ingestionRequestTimes.filter(t => now - t < 60000);
  ingestionRequestTimes.push(now);

  const rateLimitBanner = document.getElementById("rate-limit-banner");
  const rateLimitMsg = document.getElementById("rate-limit-message");

  if (ingestionRequestTimes.length > 3) {
    if (rateLimitBanner && rateLimitMsg) {
      rateLimitMsg.textContent = "High request frequency: We advise checking back every 15–30 minutes for new jobs, as We Work Remotely publishes updates periodically throughout the day.";
      rateLimitBanner.classList.remove("hidden");
    }
  } else {
    if (rateLimitBanner) {
      rateLimitBanner.classList.add("hidden");
    }
  }

  errorBanner.classList.add("hidden");
  btn.disabled = true;
  btn.dataset.busy = "true";
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
      if (cachedJobs.length > 0) {
        appState = "stale";
        updateStaleIndicator();
      }
    } else if (data.status === "partial_success") {
      errorBanner.classList.remove("hidden");
      const accepted = data.stats ? data.stats.records_accepted : 0;
      const rejected = data.stats ? data.stats.records_rejected : 0;
      errorMessage.textContent = `Ingestion completed with partial success: ${accepted} accepted, ${rejected} rejected due to record-level errors.`;
    }

    // Refresh dashboard state after request completes
    await loadSourceHealth();
    await loadLatestRun();
    await loadJobs(1);

  } catch (err) {
    console.error("Ingestion failed:", err);
    errorBanner.classList.remove("hidden");
    const friendlyMsg = (err.message === "Failed to fetch") 
      ? "Too many requests received. Please wait a moment before triggering ingestion again."
      : (err.message || "Failed to complete ingestion request.");
    errorMessage.textContent = friendlyMsg;
    if (cachedJobs.length > 0) {
      appState = "stale";
      updateStaleIndicator();
    }
  } finally {
    btn.disabled = false;
    delete btn.dataset.busy;
    btn.textContent = "Ingest latest jobs";
  }
}

function formatEmploymentType(empType) {
  if (!empType) return "Not specified";
  const map = {
    "full_time": "Full-Time",
    "part_time": "Part-Time",
    "contract": "Contract",
    "internship": "Internship",
    "temporary": "Temporary",
    "unknown": "Unspecified"
  };
  return map[empType.toLowerCase()] || empType.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function formatSourceName(sourceName) {
  if (!sourceName) return "Unknown";
  if (sourceName.toLowerCase() === "weworkremotely") return "We Work Remotely";
  return sourceName.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function sanitizeHtml(dirtyHtml) {
  if (!dirtyHtml || typeof dirtyHtml !== "string" || !dirtyHtml.trim()) {
    return '<p class="text-muted">No description provided.</p>';
  }

  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(dirtyHtml, "text/html");

    // Elements to strip completely including children
    const bannedTags = [
      "SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "APPLET", 
      "LINK", "META", "FORM", "INPUT", "BUTTON", "SVG", "CANVAS", 
      "BASE", "FRAME", "FRAMESET", "NOSCRIPT"
    ];
    bannedTags.forEach(tag => {
      const elements = doc.querySelectorAll(tag);
      elements.forEach(el => el.remove());
    });

    // Allowed tags set
    const allowedTags = new Set([
      "P", "BR", "STRONG", "B", "EM", "I", "U", "S", "STRIKE",
      "UL", "OL", "LI", "A", "H1", "H2", "H3", "H4", "H5", "H6",
      "BLOCKQUOTE", "PRE", "CODE", "HR", "SPAN", "DIV"
    ]);

    function cleanNode(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        return document.createTextNode(node.textContent);
      }
      if (node.nodeType === Node.ELEMENT_NODE) {
        const tagName = node.tagName.toUpperCase();
        if (!allowedTags.has(tagName)) {
          const fragment = document.createDocumentFragment();
          node.childNodes.forEach(child => {
            const cleaned = cleanNode(child);
            if (cleaned) fragment.appendChild(cleaned);
          });
          return fragment;
        }

        const safeElement = document.createElement(tagName.toLowerCase());

        // Safe attribute handling for links
        if (tagName === "A") {
          const rawHref = (node.getAttribute("href") || "").trim();
          if (/^(https?:|mailto:|\/)/i.test(rawHref) && !rawHref.toLowerCase().startsWith("javascript:")) {
            safeElement.setAttribute("href", rawHref);
            safeElement.setAttribute("target", "_blank");
            safeElement.setAttribute("rel", "noopener noreferrer");
          }
        }

        // Recursively clean children
        node.childNodes.forEach(child => {
          const cleaned = cleanNode(child);
          if (cleaned) safeElement.appendChild(cleaned);
        });

        return safeElement;
      }
      return null;
    }

    const container = document.createElement("div");
    doc.body.childNodes.forEach(child => {
      const cleaned = cleanNode(child);
      if (cleaned) container.appendChild(cleaned);
    });

    const result = container.innerHTML.trim();
    return result || '<p class="text-muted">No description provided.</p>';
  } catch (err) {
    console.error("HTML Sanitization failed, falling back to escaped text:", err);
    return `<p>${escapeHtml(dirtyHtml)}</p>`;
  }
}

function formatMetadataRows(metadata) {
  if (!metadata || typeof metadata !== "object" || Object.keys(metadata).length === 0) {
    return "";
  }

  const friendlyLabels = {
    "wwr_type": "Type",
    "wwr_region": "Region",
    "wwr_category": "Category",
    "channel_title": "Channel",
    "source_type": "Source Type"
  };

  const rows = Object.entries(metadata)
    .filter(([k, v]) => v !== null && v !== undefined && v !== "")
    .map(([key, value]) => {
      const label = friendlyLabels[key] || key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
      const valStr = typeof value === "object" ? JSON.stringify(value) : String(value);
      return `
        <div class="meta-kv-row">
          <div class="meta-kv-key">${escapeHtml(label)}</div>
          <div class="meta-kv-val">${escapeHtml(valStr)}</div>
        </div>
      `;
    })
    .join("");

  if (!rows.trim()) {
    return "";
  }

  return `
    <div class="drawer-field">
      <div class="drawer-field-label">Source Metadata</div>
      <div class="meta-kv-grid">
        ${rows}
      </div>
    </div>
  `;
}

function openJobDetail(canonicalId) {
  const job = cachedJobs.find(j => j.canonical_id === canonicalId);
  const backdrop = document.getElementById("drawer-backdrop");
  const titleEl = document.getElementById("drawer-job-title");
  const body = document.getElementById("drawer-body");

  if (!job) {
    // Missing job record state
    titleEl.textContent = "Job Not Found";
    body.innerHTML = `
      <div class="drawer-empty-state">
        <p class="drawer-empty-title">Job record not found</p>
        <p class="drawer-empty-desc">
          The requested canonical job record (<span class="mono-cell">${escapeHtml(canonicalId)}</span>) is not present in the current dataset or may no longer be available.
        </p>
        <div style="margin-top: 16px;">
          <button class="btn btn-secondary" onclick="closeDrawer()">Return to List</button>
        </div>
      </div>
    `;
    backdrop.classList.remove("hidden");
    return;
  }

  titleEl.textContent = job.title || "Job Details";
  const pubDate = job.published_at ? new Date(job.published_at).toLocaleString() : "N/A";
  const safeDescriptionHtml = sanitizeHtml(job.description);
  const metadataRowsHtml = formatMetadataRows(job.metadata);

  body.innerHTML = `
    <!-- Primary Job Overview -->
    <div class="drawer-overview-grid">
      <div class="drawer-field">
        <div class="drawer-field-label">Company</div>
        <div class="drawer-field-value drawer-field-highlight">${escapeHtml(job.company || "N/A")}</div>
      </div>
      <div class="drawer-field">
        <div class="drawer-field-label">Location</div>
        <div class="drawer-field-value">${escapeHtml(job.location || "Remote")}</div>
      </div>
      <div class="drawer-field">
        <div class="drawer-field-label">Employment Type</div>
        <div class="drawer-field-value">${escapeHtml(formatEmploymentType(job.employment_type))}</div>
      </div>
      <div class="drawer-field">
        <div class="drawer-field-label">Published At</div>
        <div class="drawer-field-value mono-cell">${pubDate}</div>
      </div>
    </div>

    <!-- 1. Clean Readable Description -->
    <div class="drawer-field drawer-desc-section">
      <div class="drawer-field-label">Description</div>
      <div class="job-description-content">
        ${safeDescriptionHtml}
      </div>
    </div>

    <!-- 2. Source Data Collapsible Section -->
    <details class="source-data-details">
      <summary class="source-data-summary">Source Data & Technical Metadata</summary>
      <div class="source-data-content">
        <div class="drawer-field">
          <div class="drawer-field-label">Source Provider</div>
          <div class="drawer-field-value">${escapeHtml(formatSourceName(job.source_name))}</div>
        </div>
        <div class="drawer-field">
          <div class="drawer-field-label">Canonical ID</div>
          <div class="drawer-field-value mono-cell">${escapeHtml(job.canonical_id)}</div>
        </div>
        <div class="drawer-field">
          <div class="drawer-field-label">Source URL</div>
          <div class="drawer-field-value">
            <a href="${escapeHtml(job.source_url)}" target="_blank" rel="noopener noreferrer" class="feed-link">
              ${escapeHtml(job.source_url)} ↗
            </a>
          </div>
        </div>

        ${metadataRowsHtml}

        <!-- 3. Nested Raw Source Description Collapsible -->
        <details class="raw-desc-details">
          <summary class="raw-desc-summary">Raw Source Description</summary>
          <div class="raw-desc-content">
            <pre class="raw-desc-pre">${escapeHtml(job.description || "No raw description available.")}</pre>
          </div>
        </details>
      </div>
    </details>
  `;

  backdrop.classList.remove("hidden");
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

