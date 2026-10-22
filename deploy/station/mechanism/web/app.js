"use strict";

const byId = (id) => document.getElementById(id);
let token = "";
let current = null;
let sending = false;
let lastLabel = "";
let polling = false;

function showError(message) {
  byId("error").textContent = message || "";
  byId("error").hidden = !message;
}

function record(label) {
  if (label === lastLabel) return;
  lastLabel = label;
  const list = byId("activity");
  if (!list.querySelector("time")) list.replaceChildren();
  const item = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString();
  item.append(time, document.createTextNode(label));
  list.prepend(item);
  while (list.children.length > 30) list.lastElementChild.remove();
}

function render(status) {
  current = status;
  if (status.csrf_token) token = status.csrf_token;
  const ready = status.connected && status.state === "idle";
  const simulation = status.mode === "simulation";
  const mode = byId("mode");
  mode.textContent = simulation ? "SIMULATION · no movement" : status.connected ? "HARDWARE CONNECTED" : "HARDWARE DISCONNECTED";
  mode.className = `badge ${simulation ? "simulation" : status.connected ? "connected" : ""}`;
  byId("connect").textContent = status.connected ? "Disconnect" : simulation ? "Connect Simulation" : "Connect Hardware";
  byId("connect").disabled = sending;
  byId("status").textContent = status.label;
  byId("detail").textContent = status.state === "cycle"
    ? `Step ${status.step} of ${status.total_steps} · ${status.remaining.toFixed(1)} seconds remaining`
    : status.state === "continuous" ? "Motor running backward. Click again or press Stop All."
    : status.connected ? "Ready for a command. Watch the mechanism while running." : "Connect first. No movement starts automatically.";
  byId("progress").value = status.total_steps ? Math.round(100 * (status.state === "idle" ? status.total_steps : status.step - 1) / status.total_steps) : 0;
  byId("cycle").disabled = !ready || sending;
  byId("motor").disabled = !status.connected || !["idle", "continuous"].includes(status.state) || sending;
  byId("motor").textContent = status.state === "continuous" ? "Stop Continuous Motor" : "Continuous Motor";
  byId("stop").disabled = !status.connected;
  byId("settings-fields").disabled = !ready || sending;
  for (const id of ["test-motor", "test-servo2"]) byId(id).disabled = !ready || sending;
  byId("backend").textContent = status.backend || "GPIO is not connected";
  if (status.error) showError(status.error);
  record(status.label);
}

async function post(name, data = {}) {
  const response = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Mechanism-Token": token },
    body: JSON.stringify(data),
    signal: AbortSignal.timeout(8000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}

async function command(name, data = {}) {
  if (sending && name !== "stop") return;
  sending = true;
  showError("");
  if (current) render(current);
  try {
    render(await post(name, data));
  } catch (error) {
    showError(error.message);
  } finally {
    sending = false;
    if (current) render(current);
  }
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    const response = await fetch("/api/status", { cache: "no-store", signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error("Cannot read mechanism status.");
    render(await response.json());
  } catch (error) {
    showError(`Connection lost: ${error.message} The controller stops movement if the browser connection is lost.`);
    byId("cycle").disabled = byId("motor").disabled = true;
    byId("settings-fields").disabled = true;
    byId("test-motor").disabled = byId("test-servo2").disabled = true;
  } finally {
    polling = false;
  }
}

function settings() {
  if (!byId("settings").reportValidity()) return null;
  return {
    up_angle: 180,
    down_angle: 90,
    down_seconds: Number(byId("down-seconds").value),
    after_feed: byId("after-feed").value,
  };
}

byId("settings").addEventListener("submit", (event) => event.preventDefault());
byId("connect").addEventListener("click", () => command(current?.connected ? "disconnect" : "connect"));
byId("cycle").addEventListener("click", () => { const data = settings(); if (data) command("cycle", data); });
for (const name of ["motor", "stop", "test-motor", "test-servo2"]) {
  byId(name).addEventListener("click", () => command(name));
}
for (const name of ["up", "down"]) {
  byId(name).addEventListener("click", () => { const data = settings(); if (data) command(name, { angle: data[`${name}_angle`] }); });
}
byId("after-feed").addEventListener("change", () => {
  byId("sequence-placement").textContent = byId("after-feed").value === "motor"
    ? "After the DC motor: UP → DOWN → UP" : "After the second servo: UP → DOWN → UP";
});
window.addEventListener("pagehide", () => {
  if (token && current?.connected) {
    navigator.sendBeacon("/api/stop", new Blob([JSON.stringify({ csrf_token: token })], { type: "application/json" }));
  }
});
setInterval(() => {
  if (current?.connected && token) post("heartbeat").catch(() => {});
}, 1000);
setInterval(poll, 400);
poll();
