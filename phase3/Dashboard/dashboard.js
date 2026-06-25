const uid = sessionStorage.getItem("uid");
if (!uid || uid === "--") {
  window.location.href = "/";
}
const WS_PROTOCOL = window.location.protocol === "https:" ? "wss" : "ws";
const WS_URL = `${WS_PROTOCOL}://${window.location.host}/ws/${encodeURIComponent(uid || "")}`;

const grid = document.getElementById("user-grid");
const countEl = document.getElementById("user-count");
const statusText = document.getElementById("status-text");
const dotEl = document.getElementById("status-dot");
const emptyEl = document.getElementById("empty-state");
const heroOnlineCountEl = document.getElementById("hero-online-count");
const profileNameEl = document.getElementById("profile-name");
const profileUidEl = document.getElementById("profile-uid");
const profileEloEl = document.getElementById("profile-elo");
const logoutBtnEl = document.getElementById("logout-btn");
const toastStackEl = document.getElementById("toast-stack");
const incomingModalEl = document.getElementById("incoming-modal");
const incomingTitleEl = document.getElementById("incoming-title");
const incomingCopyEl = document.getElementById("incoming-copy");
const incomingAcceptEl = document.getElementById("incoming-accept");
const incomingDeclineEl = document.getElementById("incoming-decline");
const incomingCloseTargets = document.querySelectorAll("[data-modal-close]");
const PROFILE_SYNC_KEY = "arena-profile-sync";

let ws; // <-- global websocket
let activeChallengeTarget = null;
let connectionToastCooldownUntil = 0;

let currentName = sessionStorage.getItem("name") || "Awaiting Player";
let currentElo = sessionStorage.getItem("elo") || "1200";

const COLORS = [
  { bg: "#EEEDFE", text: "#3C3489" },
  { bg: "#E1F5EE", text: "#085041" },
  { bg: "#FAECE7", text: "#712B13" },
  { bg: "#E6F1FB", text: "#0C447C" },
  { bg: "#FBEAF0", text: "#72243E" },
  { bg: "#FAEEDA", text: "#633806" },
];

const colorMap = {};

function getColor(uid) {
  if (!colorMap[uid]) {
    colorMap[uid] = COLORS[Object.keys(colorMap).length % COLORS.length];
  }
  return colorMap[uid];
}

function initials(name) {
  return name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
}

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

function renderProfile() {
  if (profileNameEl) {
    profileNameEl.textContent = currentName;
  }
  if (profileUidEl) {
    profileUidEl.textContent = `UID: ${uid}`;
  }
  if (profileEloEl) {
    profileEloEl.textContent = `ELO: ${currentElo}`;
  }
}

function applyProfileSync(payload) {
  if (!payload || payload.uid !== uid) {
    return;
  }

  if (payload.name) {
    currentName = payload.name;
    sessionStorage.setItem("name", currentName);
  }

  if (payload.elo !== undefined && payload.elo !== null && payload.elo !== "") {
    currentElo = String(payload.elo);
    sessionStorage.setItem("elo", currentElo);
  }

  renderProfile();
}

async function syncProfileFromBackend() {
  try {
    const response = await fetch("/auth/session", { credentials: "same-origin" });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    if (!payload || !payload.success) {
      return;
    }

    applyProfileSync({
      uid: payload.uid,
      name: payload.name,
      elo: payload.elo_rating,
    });
  } catch {
    renderProfile();
  }
}

function openIncomingChallenge(from) {
  activeChallengeTarget = from;
  if (incomingTitleEl) {
    incomingTitleEl.textContent = `Challenge from ${from}`;
  }
  if (incomingCopyEl) {
    incomingCopyEl.textContent = "Accept to open the match room or decline to stay in the lobby.";
  }
  if (incomingModalEl) {
    incomingModalEl.classList.remove("hidden");
    incomingModalEl.setAttribute("aria-hidden", "false");
  }
}

function closeIncomingChallenge() {
  activeChallengeTarget = null;
  if (incomingModalEl) {
    incomingModalEl.classList.add("hidden");
    incomingModalEl.setAttribute("aria-hidden", "true");
  }
}

function logout() {
  fetch("/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  }).finally(() => {
    window.location.href = "/";
  });
}

function openRoom(roomid) {
  sessionStorage.setItem("roomId", roomid);
  const roomUrl = `/room/${encodeURIComponent(roomid)}`;
  const opened = window.open(roomUrl, "_blank");
  if (!opened) {
    window.location.href = roomUrl;
  }
}

function showConnectionToast(message) {
  const now = Date.now();
  if (now < connectionToastCooldownUntil) {
    return;
  }
  connectionToastCooldownUntil = now + 4000;
  pushToast(message, "error");
}

function handleIncomingChallengeResponse(action) {
  if (!ws || ws.readyState !== WebSocket.OPEN || !activeChallengeTarget) {
    closeIncomingChallenge();
    return;
  }

  ws.send(JSON.stringify({
    type: action,
    to: activeChallengeTarget,
    from: uid,
  }));

  if (action === "challenge_accept") {
    pushToast(`Accepted challenge from ${activeChallengeTarget}`, "success");
  } else {
    pushToast(`Declined challenge from ${activeChallengeTarget}`, "info");
  }

  closeIncomingChallenge();
}

if (incomingAcceptEl) {
  incomingAcceptEl.addEventListener("click", () => handleIncomingChallengeResponse("challenge_accept"));
}

if (incomingDeclineEl) {
  incomingDeclineEl.addEventListener("click", () => handleIncomingChallengeResponse("challenge_decline"));
}

incomingCloseTargets.forEach((target) => {
  target.addEventListener("click", closeIncomingChallenge);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeIncomingChallenge();
  }
});

if (logoutBtnEl) {
  logoutBtnEl.addEventListener("click", logout);
}


function renderUsers(users) {
  const visibleUsers = users.filter((user) => user.uid !== uid);
  grid.innerHTML = "";
  countEl.textContent = visibleUsers.length;
  if (heroOnlineCountEl) {
    heroOnlineCountEl.textContent = String(visibleUsers.length);
  }
  emptyEl.style.display = visibleUsers.length === 0 ? "block" : "none";

  visibleUsers.forEach(user => {
    const { bg, text } = getColor(user.uid);
    const card = document.createElement("div");
    card.className = "user-card";

    card.addEventListener("click", (e) => {
      const menu = document.getElementById("menu");
      const list = document.getElementById("menu-list");
      menu.style.top = `${e.clientY - 8}px`;
      menu.style.left = `${e.clientX - 8}px`;
      list.classList.remove("hidden");
      document.getElementById("challenge-btn").onclick = () => {
          ws.send(JSON.stringify({
            type: "challenge",
            to: user.uid,
            from: uid
          }));
          pushToast(`Challenge sent to ${user.name}`, "success");
          list.classList.add("hidden");

      };
      document.getElementById("cancel").onclick = () => {
          list.classList.add("hidden");
      };
    });

    card.innerHTML = `
      <div class="card-top">
        <div class="avatar" style="background: ${bg}; color: ${text};">${initials(user.name)}</div>
        <div class="user-info">
          <div class="user-name">${user.name}</div>
          <div class="user-uid">uid: ${user.uid}</div>
        </div>
      </div>
      <div class="card-bottom">
        <span class="elo-label">ELO</span>
        <span class="elo-value">${user.elo_rating}</span>
      </div>
    `;

    grid.appendChild(card);
  });
}

function setStatus(connected) {
  dotEl.className = connected ? "dot-online" : "dot-offline";
  statusText.textContent = connected ? "Live" : "Disconnected";
}

function showChallengePopup(from) {
  openIncomingChallenge(from);
}

renderUsers([]);
renderProfile();
syncProfileFromBackend();

window.addEventListener("storage", (event) => {
  if (event.key !== PROFILE_SYNC_KEY || !event.newValue) {
    return;
  }

  try {
    applyProfileSync(JSON.parse(event.newValue));
  } catch {}
});

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    setStatus(true);
    renderProfile();
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);

      if (msg.type === "users") {
        renderUsers(msg.data);
      }

      // RECEIVE CHALLENGE
      if (msg.type === "challenge") {
        showChallengePopup(msg.from);
        pushToast(`Challenge received from ${msg.from}`, "info");
      }

      if (msg.type === "challenge_accepted") {
        pushToast(`Match starting with ${msg.by}`, "success");
        openRoom(msg.roomid);
      }

      if (msg.type === "challenge_declined") {
        pushToast(`${msg.by} declined your challenge`, "info");
      }

    } catch {}
  };

  ws.onclose = () => {
    setStatus(false);
    showConnectionToast("Lobby connection closed. Reconnecting...");
    setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    setStatus(false);
    showConnectionToast("Lobby connection error.");
  };
}

connect();