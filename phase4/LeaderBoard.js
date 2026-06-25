const bodyEl = document.getElementById("leaderboard-body");
const tableEl = document.getElementById("leaderboard-table");
const stateMessageEl = document.getElementById("state-message");
const totalPlayersEl = document.getElementById("total-players");
const highestEloEl = document.getElementById("highest-elo");
const onlineCountEl = document.getElementById("online-count");
const lastUpdatedEl = document.getElementById("last-updated");
const refreshBtnEl = document.getElementById("refresh-btn");
const toastStackEl = document.getElementById("toast-stack");
const logoutBtnEl = document.getElementById("logout-btn");

const selfUid = sessionStorage.getItem("uid");

function pushToast(message, type = "info") {
  if (!toastStackEl) {
    return;
  }

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-dot"></span>
    <div class="toast-copy">
      <strong>${type === "error" ? "Error" : type === "success" ? "Success" : "Notice"}</strong>
      <span>${message}</span>
    </div>
  `;

  toastStackEl.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("show"));

  window.setTimeout(() => {
    toast.classList.remove("show");
    window.setTimeout(() => toast.remove(), 260);
  }, 3400);
}

function logout() {
  fetch("/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  }).finally(() => {
    window.location.href = "/";
  });
}

function formatNow() {
  return new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function setState(message, showTable = false) {
  stateMessageEl.textContent = message;
  stateMessageEl.hidden = showTable;
  tableEl.hidden = !showTable;
}

function rankClass(rank) {
  if (rank === 1) {
    return "top-1";
  }
  if (rank === 2) {
    return "top-2";
  }
  if (rank === 3) {
    return "top-3";
  }
  return "";
}

function statusChip(isOnline) {
  if (isOnline) {
    return '<span class="status-chip online">Online</span>';
  }
  return '<span class="status-chip">Offline</span>';
}

function renderRows(players) {
  bodyEl.innerHTML = "";
  const sorted = [...players].sort((a, b) => {
    const eloA = Number(a.elo_rating || 0);
    const eloB = Number(b.elo_rating || 0);
    if (eloB !== eloA) {
      return eloB - eloA;
    }
    return String(a.uid || "").localeCompare(String(b.uid || ""));
  });

  sorted.forEach((player, index) => {
    const rank = index + 1;
    const row = document.createElement("tr");

    const cls = rankClass(rank);
    if (cls) row.classList.add(cls);

    if (selfUid && player.uid === selfUid) {
      row.classList.add("self-row");
    }

    row.innerHTML = `
      <td><span class="rank-pill">#${rank}</span></td>
      <td>${player.name || "Unknown"}</td>
      <td>${player.uid || "--"}</td>
      <td>${Number(player.elo_rating || 0)}</td>
      <td>${statusChip(Boolean(player.is_online))}</td>
    `;

    bodyEl.appendChild(row);
  });

  totalPlayersEl.textContent = String(sorted.length);
  highestEloEl.textContent = sorted.length ? String(Number(sorted[0].elo_rating || 0)) : "-";
  onlineCountEl.textContent = String(sorted.filter((p) => Boolean(p.is_online)).length);
  lastUpdatedEl.textContent = `Last updated: ${formatNow()}`;
}

async function loadLeaderboard() {
  setState("Loading leaderboard...");

  try {
    const response = await fetch("/leaderboard-data", { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`Leaderboard request failed (${response.status})`);
    }

    const payload = await response.json();
    if (!Array.isArray(payload)) {
      throw new Error("Invalid leaderboard response format.");
    }

    if (payload.length === 0) {
      setState("No players found yet.");
      totalPlayersEl.textContent = "0";
      highestEloEl.textContent = "-";
      onlineCountEl.textContent = "0";
      lastUpdatedEl.textContent = `Last updated: ${formatNow()}`;
      return;
    }

    renderRows(payload);
    setState("Loaded", true);
  } catch (error) {
    console.error(error);
    setState("Could not load leaderboard. Try refresh.");
    pushToast("Could not load leaderboard. Try again.", "error");
  }
}

if (refreshBtnEl) {
  refreshBtnEl.addEventListener("click", loadLeaderboard);
}

if (logoutBtnEl) {
  logoutBtnEl.addEventListener("click", logout);
}

loadLeaderboard();