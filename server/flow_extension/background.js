// SoShop Flow Bridge: relays commands from the local SoShop server to Google
// Flow tabs in this browser.
//
// The server (FastAPI, `/promo/flow/bridge`) cannot drive this browser itself:
// Chrome refuses remote debugging on the profile you browse with, and Flow's
// requests need this profile's sign-in and reCAPTCHA. So this worker dials
// out to the server over a WebSocket and executes what it asks, scoped to
// flow.google.com tabs only:
//
//   server -> {id, method, params}       extension -> {id, result} | {id, error}
//   extension -> {event: "hello" | "detached" | "cdp", ...}
//
// `cdp` runs a Chrome DevTools Protocol command on a Flow tab through
// chrome.debugger, which yields trusted clicks and key presses, file inputs
// set from local paths, and screenshots. Chrome shows its "is debugging this
// browser" bar while attached; the server detaches when a job finishes.

const DEFAULT_SERVER = "ws://127.0.0.1:8000/promo/flow/bridge";
const FLOW_ORIGIN = "https://flow.google.com";
const VERSION = chrome.runtime.getManifest().version;

let socket = null;
let connecting = false;
let lastError = "";
const attached = new Set();
let forwardedEvents = new Set();

// ------------------------------------------------------------------ helpers

async function serverUrl() {
  const { server } = await chrome.storage.local.get("server");
  return server || DEFAULT_SERVER;
}

function isFlowUrl(url) {
  return typeof url === "string" && url.startsWith(`${FLOW_ORIGIN}/`);
}

async function flowTab(tabId) {
  const tab = await chrome.tabs.get(tabId);
  if (!isFlowUrl(tab.url)) throw new Error(`tab ${tabId} is not a Google Flow tab`);
  return tab;
}

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function setBadge() {
  const ok = socket && socket.readyState === WebSocket.OPEN;
  chrome.action.setBadgeText({ text: ok ? "ON" : "" });
  chrome.action.setBadgeBackgroundColor({ color: ok ? "#16a34a" : "#6b7280" });
}

// ----------------------------------------------------------------- commands

async function attach(tabId) {
  await flowTab(tabId);
  if (attached.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (err) {
    // Attached by an earlier worker instance: reuse it.
    if (!String(err.message || err).includes("Another debugger")) throw err;
  }
  attached.add(tabId);
}

async function detach(tabId) {
  attached.delete(tabId);
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    // Already detached.
  }
}

const COMMANDS = {
  ping: async () => "pong",

  "tabs.list": async () => {
    const tabs = await chrome.tabs.query({ url: `${FLOW_ORIGIN}/*` });
    return tabs.map((t) => ({
      id: t.id,
      url: t.url,
      title: t.title,
      active: t.active,
      windowId: t.windowId,
      status: t.status,
    }));
  },

  "tabs.open": async ({ url, active = false }) => {
    if (!isFlowUrl(url)) throw new Error("only Google Flow URLs can be opened");
    const tab = await chrome.tabs.create({ url, active });
    return { id: tab.id };
  },

  "tabs.navigate": async ({ tabId, url }) => {
    await flowTab(tabId);
    if (!isFlowUrl(url)) throw new Error("only Google Flow URLs can be opened");
    await chrome.tabs.update(tabId, { url });
    return { id: tabId };
  },

  // A separate, unfocused window for SoShop jobs, so the user's own tabs are
  // never touched.
  "windows.open": async ({ url, width = 1280, height = 900 }) => {
    if (!isFlowUrl(url)) throw new Error("only Google Flow URLs can be opened");
    const win = await chrome.windows.create({
      url,
      focused: false,
      type: "normal",
      state: "normal",
      width,
      height,
    });
    return { tabId: win.tabs[0].id, windowId: win.id };
  },

  // Flow only accepts a real click on its send button, and Chrome only routes
  // one to a tab it is painting. Bring the job window to the front for that
  // moment; the caller hands focus back with windows.restore.
  "windows.focus": async ({ tabId }) => {
    const tab = await flowTab(tabId);
    const previous = await chrome.windows.getLastFocused();
    await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true, drawAttention: false });
    return { previousWindowId: previous && previous.id !== tab.windowId ? previous.id : null };
  },

  "windows.restore": async ({ windowId }) => {
    if (typeof windowId !== "number") return { restored: false };
    try {
      await chrome.windows.update(windowId, { focused: true });
      return { restored: true };
    } catch {
      return { restored: false };
    }
  },

  "tabs.close": async ({ tabId }) => {
    await flowTab(tabId);
    attached.delete(tabId);
    await chrome.tabs.remove(tabId);
    return { id: tabId };
  },

  "tabs.activate": async ({ tabId }) => {
    const tab = await flowTab(tabId);
    await chrome.tabs.update(tabId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
    return { id: tabId };
  },

  "debugger.attach": async ({ tabId }) => {
    await attach(tabId);
    return { id: tabId };
  },

  "debugger.detach": async ({ tabId }) => {
    await detach(tabId);
    return { id: tabId };
  },

  "events.forward": async ({ methods = [] }) => {
    forwardedEvents = new Set(methods);
    return { methods: [...forwardedEvents] };
  },

  cdp: async ({ tabId, method, params = {} }) => {
    // Checked on every command: a tab that navigated away from Flow is off limits.
    await attach(tabId);
    return await chrome.debugger.sendCommand({ tabId }, method, params);
  },
};

async function handle(message) {
  const { id, method, params } = message;
  const command = COMMANDS[method];
  if (!command) {
    send({ id, error: `unknown method ${method}` });
    return;
  }
  try {
    const result = await command(params || {});
    send({ id, result: result === undefined ? null : result });
  } catch (err) {
    send({ id, error: String((err && err.message) || err) });
  }
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (source.tabId !== undefined && forwardedEvents.has(method)) {
    send({ event: "cdp", tabId: source.tabId, method, params });
  }
});

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId === undefined) return;
  attached.delete(source.tabId);
  send({ event: "detached", tabId: source.tabId, reason });
});

// --------------------------------------------------------------- connection

async function connect() {
  if (connecting || (socket && socket.readyState <= WebSocket.OPEN)) return;
  connecting = true;
  const url = await serverUrl();
  try {
    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => {
      lastError = "";
      setBadge();
      send({ event: "hello", version: VERSION, userAgent: navigator.userAgent });
    };
    ws.onmessage = (ev) => {
      let message;
      try {
        message = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (message && message.method) handle(message);
    };
    ws.onerror = () => {
      lastError = `Không kết nối được ${url}. Server SoShop đã chạy chưa?`;
    };
    ws.onclose = () => {
      if (socket === ws) socket = null;
      setBadge();
    };
  } catch (err) {
    lastError = String((err && err.message) || err);
  } finally {
    connecting = false;
  }
}

// MV3 workers sleep when idle. The server pings over the socket every 20 s,
// which keeps an open connection alive; the alarm brings the worker back to
// reconnect after the server restarts.
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "reconnect") connect();
});
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message && message.type === "status") {
    serverUrl().then((url) =>
      reply({
        connected: !!socket && socket.readyState === WebSocket.OPEN,
        server: url,
        error: lastError,
        version: VERSION,
      }),
    );
    return true;
  }
  if (message && message.type === "reconnect") {
    if (socket) socket.close();
    socket = null;
    connect().then(() => reply({ ok: true }));
    return true;
  }
  return false;
});

connect();
