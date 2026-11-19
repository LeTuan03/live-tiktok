# SoShop Flow Bridge

A small Chrome extension that lets the local SoShop server generate product videos with Google Flow in your own browser, on your own Flow account and credits.

## Install

1. Open `chrome://extensions` in the Chrome profile where you are signed in to Flow.
2. Turn on **Developer mode**.
3. Click **Load unpacked** and pick this folder (`server/flow_extension`).
4. Keep the SoShop API running (`http://127.0.0.1:8000`). The extension icon shows **ON** once it is connected. The popup lets you change the server address.

After pulling a new version of this folder, click the reload icon on the extension card.

## What it can do

- Dial out to the SoShop server over a WebSocket and run the commands it sends.
- Open, navigate and close tabs on `flow.google.com`, and nothing else.
- Run Chrome DevTools Protocol commands on those tabs through `chrome.debugger`. Chrome shows a "SoShop Flow Bridge started debugging this browser" bar while a job runs. Clicking Cancel on that bar stops the job.

It never reads other tabs, and every command is checked against the tab's URL before it runs.
