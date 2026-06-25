const uid = sessionStorage.getItem("uid");
const WS_URL = `ws://localhost:8000/ws/${uid}`;

const grid = document.getElementById("user-grid");
const countEl = document.getElementById("user-count");
const statusText = document.getElementById("status-text");
const dotEl = document.getElementById("status-dot");
const emptyEl = document.getElementById("empty-state");

let ws; // <-- global websocket

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

function renderUsers(users) {
  grid.innerHTML = "";
  countEl.textContent = users.length;
  emptyEl.style.display = users.length === 0 ? "block" : "none";

  users.forEach(user => {
    const { bg, text } = getColor(user.uid);
    const card = document.createElement("div");
    card.className = "user-card";

    // CLICK HANDLER (challenge)
    card.onclick = () => {
      ws.send(JSON.stringify({
        type: "challenge",
        to: user.uid
      }));
    };

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
  const accept = confirm(`${from} challenged you. Accept?`);

  ws.send(JSON.stringify({
    type: accept ? "challenge_accept" : "challenge_decline",
    to: from
  }));
}

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => setStatus(true);

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);

      if (msg.type === "users") {
        renderUsers(msg.data);
      }

      // RECEIVE CHALLENGE
      if (msg.type === "challenge") {
        showChallengePopup(msg.from);
      }

      if (msg.type === "challenge_accepted") {
        alert(`Match starting with ${msg.by}`);
      }

      if (msg.type === "challenge_declined") {
        alert(`${msg.by} declined your challenge`);
      }

    } catch {}
  };

  ws.onclose = () => {
    setStatus(false);
    setTimeout(connect, 3000);
  };

  ws.onerror = () => setStatus(false);
}

connect();