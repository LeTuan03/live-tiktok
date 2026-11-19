const $ = (id) => document.getElementById(id);

function refresh() {
  chrome.runtime.sendMessage({ type: "status" }, (status) => {
    if (!status) return;
    $("dot").className = status.connected ? "dot on" : "dot";
    $("state").textContent = status.connected
      ? "Đã kết nối server SoShop"
      : "Chưa kết nối server SoShop";
    $("error").textContent = status.connected ? "" : status.error || "";
    if (document.activeElement !== $("server")) $("server").value = status.server;
  });
}

$("save").addEventListener("click", async () => {
  const value = $("server").value.trim();
  await chrome.storage.local.set({ server: value });
  chrome.runtime.sendMessage({ type: "reconnect" }, () => setTimeout(refresh, 800));
});

$("flow").addEventListener("click", () => {
  chrome.tabs.create({ url: "https://flow.google.com/" });
});

refresh();
setInterval(refresh, 1500);
