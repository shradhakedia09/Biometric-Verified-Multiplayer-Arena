const sessionRoomId = sessionStorage.getItem("roomId");
const sessionUid = sessionStorage.getItem("uid");
const sessionName = sessionStorage.getItem("name");
const sessionElo = sessionStorage.getItem("elo");
const hasValidRoomSession = Boolean(
  sessionRoomId && sessionRoomId !== "ROOM-000" && sessionUid && sessionUid !== "--"
);

const defaultRoomState = {
  roomId: sessionRoomId || "ROOM-000",
  turn: "X",
  winner: null,
  p1: null,
  p2: null,
  p1Name: null,
  p2Name: null,
  p1Elo: null,
  p2Elo: null,
  xPlayer: null,
  self: {
    name: sessionName || "Awaiting Player",
    uid: sessionUid || "--",
    elo: sessionElo || "1200",
  },
  opponent: {
    name: "Waiting...",
    uid: "--",
    elo: "--",
  },
  board: [null, null, null, null, null, null, null, null, null],
};

const WS_PROTOCOL = window.location.protocol === "https:" ? "wss" : "ws";
const WS_HOST = window.location.host || "localhost:8000";
const WS_URL = hasValidRoomSession
  ? `${WS_PROTOCOL}://${WS_HOST}/ws/${encodeURIComponent(sessionRoomId)}/${encodeURIComponent(sessionUid)}`
  : null;
let ws;
const roomState = {
  ...defaultRoomState,
  self: { ...defaultRoomState.self },
  opponent: { ...defaultRoomState.opponent },
  board: [...defaultRoomState.board],
};
let boardState = normalizeBoardState(roomState.board);
let selectedCellIndex = null;
let rematchRequested = false;
window.pendingRoomMove = null;

const boardEl = document.getElementById("board");
const selfNameEl = document.getElementById("self-name");
const selfUidEl = document.getElementById("self-uid");
const selfEloEl = document.getElementById("self-elo");
const opponentNameEl = document.getElementById("opponent-name");
const opponentUidEl = document.getElementById("opponent-uid");
const opponentEloEl = document.getElementById("opponent-elo");
const roomIdEl = document.getElementById("room-id");
const turnStateEl = document.getElementById("turn-state");
const currentTurnChipEl = document.getElementById("current-turn-chip");
const resultPanelEl = document.getElementById("result-panel");
const resultTitleEl = document.getElementById("result-title");
const resultSubtitleEl = document.getElementById("result-subtitle");
const rematchBtnEl = document.getElementById("rematch-btn");
const lobbyBtnEl = document.getElementById("lobby-btn");
const toastStackEl = document.getElementById("toast-stack");
const logoutBtnEl = document.getElementById("logout-btn");
const PROFILE_SYNC_KEY = "arena-profile-sync";

let announcedWinner = null;
let connectionToastCooldownUntil = 0;

function showStatus(message) {
  turnStateEl.textContent = message;
  currentTurnChipEl.textContent = message;
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

function showConnectionToast(message) {
  const now = Date.now();
  if (now < connectionToastCooldownUntil) {
    return;
  }

  connectionToastCooldownUntil = now + 4000;
  pushToast(message, "error");
}

function logout() {
  fetch("/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  }).finally(() => {
    window.location.href = "/";
  });
}

function normalizeBoardState(nextState) {
  const source = Array.isArray(nextState) ? nextState : [];
  return Array.from({ length: 9 }, (_, index) => {
    const value = source[index];
    if (value === undefined || value === null || value === "" || value === 0 || value === "0") {
      return null;
    }
    if (value === 1 || value === "1") {
      return "1";
    }
    if (value === -1 || value === "-1") {
      return "-1";
    }
    return String(value);
  });
}

function toDisplayElo(value, fallback = "--") {
  if (value === undefined || value === null || value === "") {
    return String(fallback);
  }
  return String(value);
}

function syncProfileState() {
  if (!roomState.self || !roomState.self.uid || roomState.self.uid === "--") {
    return;
  }

  const nextName = roomState.self.name || sessionName || roomState.self.uid;
  const nextElo = roomState.self.elo !== undefined && roomState.self.elo !== null
    ? String(roomState.self.elo)
    : sessionElo || "1200";

  try {
    sessionStorage.setItem("name", nextName);
    sessionStorage.setItem("elo", nextElo);
    localStorage.setItem(
      PROFILE_SYNC_KEY,
      JSON.stringify({
        uid: roomState.self.uid,
        name: nextName,
        elo: nextElo,
        updatedAt: Date.now(),
      }),
    );
  } catch {}
}

function applyPlayerCards() {
  const p1 = roomState.p1;
  const p2 = roomState.p2;
  const currentUid = sessionUid || roomState.self.uid;

  const p1Data = {
    uid: p1 || currentUid || "--",
    name: roomState.p1Name || sessionName || p1 || "Awaiting Player",
    elo: toDisplayElo(roomState.p1Elo, sessionElo || "1200"),
  };

  const p2Data = p2
    ? {
      uid: p2,
      name: roomState.p2Name || p2,
      elo: toDisplayElo(roomState.p2Elo, "--"),
    }
    : null;

  if (currentUid && p2Data && currentUid === p2Data.uid) {
    roomState.self = { ...p2Data };
    roomState.opponent = { ...p1Data };
    syncProfileState();
    return;
  }

  if (currentUid && currentUid === p1Data.uid) {
    roomState.self = { ...p1Data };
    roomState.opponent = p2Data
      ? { ...p2Data }
      : { name: "Waiting...", uid: "--", elo: "--" };
    syncProfileState();
    return;
  }

  roomState.self = { ...p1Data };
  roomState.opponent = p2Data
    ? { ...p2Data }
    : { name: "Waiting...", uid: "--", elo: "--" };
  syncProfileState();
}

function getSelfSymbol() {
  if (!roomState.p1 || !roomState.p2 || !roomState.xPlayer) {
    return null;
  }
  if (roomState.self.uid !== roomState.p1 && roomState.self.uid !== roomState.p2) {
    return null;
  }
  return roomState.self.uid === roomState.xPlayer ? "X" : "O";
}

function renderResultPanel() {
  if (!resultPanelEl || !resultTitleEl || !resultSubtitleEl || !rematchBtnEl) {
    return;
  }

  if (!roomState.winner) {
    resultPanelEl.hidden = true;
    resultPanelEl.style.display = "none";
    rematchBtnEl.disabled = false;
    rematchBtnEl.textContent = "Rematch";
    return;
  }

  let title = "Round Complete";
  let subtitle = "Choose what to do next.";
  if (roomState.winner === "D") {
    title = "Draw";
    subtitle = "No winner this round.";
  } else {
    const selfSymbol = getSelfSymbol();
    if (selfSymbol && selfSymbol === roomState.winner) {
      title = "You Won";
      subtitle = "Great game. Want another round?";
    } else if (selfSymbol) {
      title = "You Lost";
      subtitle = "Try a rematch to bounce back.";
    } else {
      title = `${roomState.winner} Won`;
      subtitle = "Match finished.";
    }
  }

  const canRequestRematch = Boolean(
    roomState.p1 && roomState.p2 && ws && ws.readyState === WebSocket.OPEN
  );
  if (rematchRequested) {
    subtitle = "Rematch requested. Waiting for opponent.";
  }

  resultTitleEl.textContent = title;
  resultSubtitleEl.textContent = subtitle;
  rematchBtnEl.textContent = rematchRequested ? "Rematch Requested" : "Rematch";
  rematchBtnEl.disabled = !canRequestRematch || rematchRequested;
  resultPanelEl.hidden = false;
  resultPanelEl.style.display = "";
}

function setRoomMeta(nextMeta = {}) {
  const winnerBefore = roomState.winner;

  if (nextMeta.roomId) {
    roomState.roomId = nextMeta.roomId;
  }

  if (nextMeta.turn) {
    roomState.turn = nextMeta.turn;
  }
  if (Object.prototype.hasOwnProperty.call(nextMeta, "winner")) {
    roomState.winner = nextMeta.winner;
  }

  if (Object.prototype.hasOwnProperty.call(nextMeta, "p1") && nextMeta.p1) {
    roomState.p1 = nextMeta.p1;
  }
  if (Object.prototype.hasOwnProperty.call(nextMeta, "p2")) {
    roomState.p2 = nextMeta.p2 || null;
  }
  if (Object.prototype.hasOwnProperty.call(nextMeta, "p1Name") && nextMeta.p1Name) {
    roomState.p1Name = nextMeta.p1Name;
  }
  if (Object.prototype.hasOwnProperty.call(nextMeta, "p2Name")) {
    roomState.p2Name = nextMeta.p2Name || null;
  }
  if (Object.prototype.hasOwnProperty.call(nextMeta, "p1Elo")) {
    roomState.p1Elo = nextMeta.p1Elo;
  }
  if (Object.prototype.hasOwnProperty.call(nextMeta, "p2Elo")) {
    roomState.p2Elo = nextMeta.p2Elo;
  }
  if (Object.prototype.hasOwnProperty.call(nextMeta, "xPlayer") && nextMeta.xPlayer) {
    roomState.xPlayer = nextMeta.xPlayer;
  }
  
  if (nextMeta.opponent) {
    roomState.opponent = { ...roomState.opponent, ...nextMeta.opponent };
  }

  if (Array.isArray(nextMeta.board) || Array.isArray(nextMeta.state)) {
    boardState = normalizeBoardState(nextMeta.board || nextMeta.state);
    roomState.board = [...boardState];
  }

  if (winnerBefore && roomState.winner === null) {
    rematchRequested = false;
    announcedWinner = null;
  }

  applyPlayerCards();

  if (roomState.winner && roomState.winner !== announcedWinner) {
    announcedWinner = roomState.winner;
    if (roomState.winner === "D") {
      pushToast("The match ended in a draw.", "info");
    } else {
      const selfSymbol = getSelfSymbol();
      pushToast(selfSymbol === roomState.winner ? "You won the match." : "You lost the match.", "success");
    }
  }

  renderRoom();
}

function setBoardState(nextBoard) {
  boardState = normalizeBoardState(nextBoard);
  roomState.board = [...boardState];
  selectedCellIndex = null;
  renderBoard();
}

function renderHeader() {
  selfNameEl.textContent = roomState.self.name;
  selfUidEl.textContent = `UID: ${roomState.self.uid}`;
  selfEloEl.textContent = `ELO: ${roomState.self.elo}`;

  opponentNameEl.textContent = roomState.opponent.name;
  opponentUidEl.textContent = `UID: ${roomState.opponent.uid}`;
  opponentEloEl.textContent = `ELO: ${roomState.opponent.elo}`;

  roomIdEl.textContent = roomState.roomId;
  if (roomState.winner) {
    const winnerText = roomState.winner === "D" ? "Draw game" : `${roomState.winner} wins`;
    turnStateEl.textContent = winnerText;
    currentTurnChipEl.textContent = winnerText;
  } else if (!roomState.p2) {
    turnStateEl.textContent = "Waiting for opponent";
    currentTurnChipEl.textContent = "Waiting";
  } else {
    turnStateEl.textContent = roomState.turn === "X"
      ? "X to play"
      : roomState.turn === "O"
        ? "O to play"
        : `Turn: ${roomState.turn}`;
    currentTurnChipEl.textContent = `Turn: ${roomState.turn}`;
  }
}

function renderBoard() {
  boardEl.innerHTML = "";

  boardState.forEach((value, index) => {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "cell";
    cell.setAttribute("aria-label", `Cell ${index + 1}`);
    cell.dataset.index = String(index);

    if (selectedCellIndex === index) {
      cell.classList.add("selected");
    }

    if (roomState.winner) {
      cell.disabled = true;
    }

    if (value !== null) {
      cell.classList.add("occupied");
      cell.disabled = true;
      const symbol = value === "1" ? "X" : value === "-1" ? "O" : String(value);
      cell.innerHTML = `<span class="cell-mark ${symbol.toLowerCase()}">${symbol}</span>`;
    } else {
      cell.innerHTML = `<span class="cell-index">${index + 1}</span>`;
    }

    cell.addEventListener("pointerup", () => handleCellTap(index));
    boardEl.appendChild(cell);
  });
}

function renderRoom() {
  renderHeader();
  renderBoard();
  renderResultPanel();
}

function handleCellTap(index) {
  if (!ws || ws.readyState !== WebSocket.OPEN || boardState[index] !== null || roomState.winner) {
    return;
  }
  ws.send(JSON.stringify({
    type: "move",
    index: index,
  }));
}

function requestRematch() {
  if (!roomState.winner || rematchRequested || !ws || ws.readyState !== WebSocket.OPEN) {
    return;
  }
  ws.send(JSON.stringify({ type: "rematch" }));
  rematchRequested = true;
  showStatus("Rematch requested. Waiting for opponent.");
  renderResultPanel();
}

function goBackToLobby() {
  window.location.href = "/dashboard";
}

window.setRoomMeta = setRoomMeta;
window.setBoardState = setBoardState;
window.requestRoomMove = handleCellTap;

if (rematchBtnEl) {
  rematchBtnEl.addEventListener("click", requestRematch);
}

if (lobbyBtnEl) {
  lobbyBtnEl.addEventListener("click", goBackToLobby);
}

if (logoutBtnEl) {
  logoutBtnEl.addEventListener("click", logout);
}



renderRoom();

function connect()
{
  if (!WS_URL) {
    showStatus("Invalid room session. Open room from dashboard.");
    pushToast("Invalid room session. Open a room from the lobby.", "error");
    setTimeout(() => {
      window.location.href = "/dashboard";
    }, 1200);
    return;
  }

  ws = new WebSocket(WS_URL);
  ws.onopen = () => {};
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === "set_data" || msg.type === "update_state" || msg.type === "rematch_started") {
        setRoomMeta(msg);
        if (msg.type === "rematch_started") {
          rematchRequested = false;
          pushToast("Rematch started.", "success");
          showStatus("Rematch started.");
          renderResultPanel();
        }
      }
      if (msg.type === "rematch_waiting") {
        rematchRequested = true;
        pushToast(msg.message || "Rematch requested. Waiting for opponent.", "info");
        showStatus(msg.message || "Rematch requested. Waiting for opponent.");
        renderResultPanel();
      }
      if (msg.type === "rematch_requested") {
        pushToast(msg.message || "Opponent requested a rematch.", "info");
        showStatus(msg.message || "Opponent requested a rematch.");
        renderResultPanel();
      }
      if (msg.type === "error" && msg.message) {
        pushToast(msg.message, "error");
        showStatus(msg.message);
      }
      if (msg.type === "opponent_left") {
        rematchRequested = false;
        const selfSymbol = getSelfSymbol();
        const isWinByForfeit = roomState.winner && selfSymbol === roomState.winner;
        pushToast(
          msg.message || (isWinByForfeit ? "Opponent disconnected. You win by forfeit." : "Opponent disconnected. Waiting for reconnect."),
          isWinByForfeit ? "success" : "error",
        );
        showStatus(msg.message || (isWinByForfeit ? "Opponent disconnected. You win by forfeit." : "Opponent disconnected. Waiting for reconnect."));
        renderResultPanel();
      }
    }
    catch{}
  };

  ws.onclose = (event) => {
    if (event.code === 1008) {
      pushToast("You are not allowed in this room.", "error");
      showStatus("You are not allowed in this room.");
      return;
    }
    showConnectionToast("Room connection closed. Reconnecting...");
    showStatus("Connection closed. Return to lobby if it does not reconnect.");
  };
}

connect();