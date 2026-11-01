"""Local OBS WebRTC & Browser Source Bridge Server.

Serves the OBS Browser Source endpoints for realtime avatar rendering and dynamic 9:16 overlays.
Runs locally on http://127.0.0.1:8088.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from aiohttp import web
from loguru import logger


AVATAR_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <title>LiveFlow Realtime AI Presenter Engine</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: transparent;
            overflow: hidden;
            width: 1080px;
            height: 1920px;
            display: flex;
            align-items: flex-end;
            justify-content: center;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        }
        #stage-container {
            position: relative;
            width: 1080px;
            height: 1920px;
            display: flex;
            justify-content: center;
            align-items: flex-end;
            pointer-events: none;
            overflow: hidden;
        }
        #webgl-canvas {
            position: absolute;
            bottom: 0;
            left: 0;
            width: 1080px;
            height: 1920px;
            pointer-events: none;
        }
        #status-pill {
            position: absolute;
            top: 40px;
            right: 40px;
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(12px);
            color: #00FF88;
            font-size: 20px;
            font-weight: 700;
            padding: 10px 22px;
            border-radius: 30px;
            border: 1px solid rgba(0, 255, 136, 0.4);
            display: flex;
            align-items: center;
            gap: 12px;
            box-shadow: 0 8px 25px rgba(0,0,0,0.4);
            letter-spacing: 0.5px;
            transition: all 0.3s ease;
        }
        .dot {
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: #00FF88;
            box-shadow: 0 0 12px #00FF88;
            animation: pulse-live 1.8s infinite;
        }
        @keyframes pulse-live {
            0% { transform: scale(0.9); opacity: 0.75; }
            50% { transform: scale(1.25); opacity: 1; }
            100% { transform: scale(0.9); opacity: 0.75; }
        }
        /* Bottom Subtitle Bar - Positioned safely away from avatar body and hands */
        #subtitle-box {
            position: absolute;
            bottom: 125px;
            left: 50%;
            transform: translateX(-50%) translateY(20px);
            width: 880px;
            max-width: 90%;
            background: linear-gradient(135deg, rgba(15, 23, 42, 0.94), rgba(30, 41, 59, 0.94));
            backdrop-filter: blur(16px);
            border: 1px solid rgba(0, 255, 136, 0.35);
            border-radius: 22px;
            padding: 16px 28px;
            color: #ffffff;
            font-size: 26px;
            font-weight: 600;
            line-height: 1.45;
            text-align: center;
            box-shadow: 0 12px 35px rgba(0, 0, 0, 0.6);
            opacity: 0;
            transition: opacity 0.35s cubic-bezier(0.16, 1, 0.3, 1), transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
            pointer-events: none;
            z-index: 100;
        }
        #subtitle-box.active {
            opacity: 1;
            transform: translateX(-50%) translateY(0);
        }
    </style>
</head>
<body>
    <div id="stage-container">
        <!-- Dual Hidden Video Buffers for Smooth Zero-Jerk Crossfading -->
        <video id="video-a" loop playsinline muted preload="auto" crossorigin="anonymous" style="display:none"></video>
        <video id="video-b" loop playsinline muted preload="auto" crossorigin="anonymous" style="display:none"></video>
        
        <!-- Hardware-Accelerated WebGL Chroma Key Canvas -->
        <canvas id="webgl-canvas" width="1080" height="1920"></canvas>

        <div id="status-pill">
            <span class="dot"></span>
            <span id="status-text">AI PRESENTER ONLINE</span>
        </div>
        
        <div id="subtitle-box">...</div>
    </div>

    <!-- Realtime Audio Player -->
    <audio id="tts-audio" style="display:none"></audio>

    <script>
        const statusText = document.getElementById('status-text');
        const subtitleBox = document.getElementById('subtitle-box');
        const videoA = document.getElementById('video-a');
        const videoB = document.getElementById('video-b');
        const canvas = document.getElementById('webgl-canvas');
        const audioElement = document.getElementById('tts-audio');

        // Dynamic clip mapping
        let avatarBase = "mai_phuong";
        let CLIPS = {
            idle: `/static/avatars/${avatarBase}_idle.mp4`,
            talk: `/static/avatars/${avatarBase}_talk.mp4`,
            point_product: `/static/avatars/${avatarBase}_point.mp4`,
            point_cart: `/static/avatars/${avatarBase}_cta.mp4`,
            cta: `/static/avatars/${avatarBase}_cta.mp4`
        };

        function updateClipPaths(base) {
            avatarBase = base;
            CLIPS = {
                idle: `/static/avatars/${base}_idle.mp4`,
                talk: `/static/avatars/${base}_talk.mp4`,
                point_product: `/static/avatars/${base}_point.mp4`,
                point_cart: `/static/avatars/${base}_cta.mp4`,
                cta: `/static/avatars/${base}_cta.mp4`
            };
        }

        // WebGL Setup for Ultra-Fast Zero-CPU Chroma-Keying & Spill Suppression
        const gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false });
        let glProgram = null;
        let texA = null, texB = null;
        let uCrossfadeLoc = null;

        if (gl) {
            const vsSource = `
                attribute vec2 a_position;
                attribute vec2 a_texCoord;
                varying vec2 v_texCoord;
                void main() {
                    gl_Position = vec4(a_position, 0.0, 1.0);
                    v_texCoord = a_texCoord;
                }
            `;
            const fsSource = `
                precision mediump float;
                uniform sampler2D u_tex_a;
                uniform sampler2D u_tex_b;
                uniform float u_crossfade;
                varying vec2 v_texCoord;

                vec4 keyColor(vec4 c) {
                    float max_rb = max(c.r, c.b);
                    float diff = c.g - max_rb;
                    if (diff > 0.12) {
                        float alpha = 1.0 - smoothstep(0.12, 0.32, diff);
                        // Clean green spill suppression
                        c.g = min(c.g, max_rb * 1.06);
                        return vec4(c.rgb, c.a * alpha);
                    } else {
                        if (diff > 0.0) {
                            c.g = max_rb;
                        }
                        return c;
                    }
                }

                void main() {
                    vec4 ca = texture2D(u_tex_a, v_texCoord);
                    vec4 cb = texture2D(u_tex_b, v_texCoord);
                    vec4 ka = keyColor(ca);
                    vec4 kb = keyColor(cb);
                    gl_FragColor = mix(ka, kb, u_crossfade);
                }
            `;

            function createShader(gl, type, source) {
                const s = gl.createShader(type);
                gl.shaderSource(s, source);
                gl.compileShader(s);
                return s;
            }
            const vs = createShader(gl, gl.VERTEX_SHADER, vsSource);
            const fs = createShader(gl, gl.FRAGMENT_SHADER, fsSource);
            glProgram = gl.createProgram();
            gl.attachShader(glProgram, vs);
            gl.attachShader(glProgram, fs);
            gl.linkProgram(glProgram);
            gl.useProgram(glProgram);

            // Buffers
            const posBuf = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
                -1, -1,  1, -1, -1,  1,
                -1,  1,  1, -1,  1,  1
            ]), gl.STATIC_DRAW);
            const aPos = gl.getAttribLocation(glProgram, 'a_position');
            gl.enableVertexAttribArray(aPos);
            gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

            const texBuf = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, texBuf);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
                0, 1,  1, 1,  0, 0,
                0, 0,  1, 1,  1, 0
            ]), gl.STATIC_DRAW);
            const aTex = gl.getAttribLocation(glProgram, 'a_texCoord');
            gl.enableVertexAttribArray(aTex);
            gl.vertexAttribPointer(aTex, 2, gl.FLOAT, false, 0, 0);

            // Textures
            function createTexture() {
                const t = gl.createTexture();
                gl.bindTexture(gl.TEXTURE_2D, t);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
                return t;
            }
            texA = createTexture();
            texB = createTexture();

            gl.uniform1i(gl.getUniformLocation(glProgram, 'u_tex_a'), 0);
            gl.uniform1i(gl.getUniformLocation(glProgram, 'u_tex_b'), 1);
            uCrossfadeLoc = gl.getUniformLocation(glProgram, 'u_crossfade');
        }

        // State Machine Controller
        let activeSlot = 'A'; // 'A' or 'B'
        let currentClipKey = 'idle';
        let crossfadeValue = 0.0;
        let isTransitioning = false;
        let transitionStart = 0;
        const TRANSITION_DURATION = 220; // 220ms smooth crossfade

        // Initialize videos
        videoA.src = CLIPS.idle;
        videoB.src = CLIPS.idle;
        videoA.play().catch(e => console.log('Auto-play A prevented:', e));

        function transitionTo(clipKey) {
            if (clipKey === currentClipKey && !isTransitioning) return;
            const targetUrl = CLIPS[clipKey] || CLIPS.idle;

            if (activeSlot === 'A') {
                videoB.src = targetUrl;
                videoB.currentTime = 0;
                videoB.play().then(() => {
                    currentClipKey = clipKey;
                    isTransitioning = true;
                    transitionStart = performance.now();
                }).catch(e => console.log('Video B switch error:', e));
            } else {
                videoA.src = targetUrl;
                videoA.currentTime = 0;
                videoA.play().then(() => {
                    currentClipKey = clipKey;
                    isTransitioning = true;
                    transitionStart = performance.now();
                }).catch(e => console.log('Video A switch error:', e));
            }
        }

        // Web Audio Setup for Syllable-Level Lip-Sync & Energy Analysis
        let audioCtx = null;
        let analyser = null;
        let freqData = new Uint8Array(128);

        function initAudioContext() {
            if (!audioCtx) {
                try {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    const source = audioCtx.createMediaElementSource(audioElement);
                    analyser = audioCtx.createAnalyser();
                    analyser.fftSize = 256;
                    source.connect(analyser);
                    analyser.connect(audioCtx.destination);
                } catch (e) {
                    console.log('Web Audio init:', e);
                }
            }
        }

        // Animation Loop
        function renderLoop(now) {
            requestAnimationFrame(renderLoop);

            // Handle smooth crossfade
            if (isTransitioning) {
                const elapsed = now - transitionStart;
                const progress = Math.min(1.0, elapsed / TRANSITION_DURATION);
                
                // Ease-in-out cosine curve
                const smoothT = 0.5 - 0.5 * Math.cos(progress * Math.PI);

                if (activeSlot === 'A') {
                    crossfadeValue = smoothT;
                } else {
                    crossfadeValue = 1.0 - smoothT;
                }

                if (progress >= 1.0) {
                    isTransitioning = false;
                    activeSlot = (activeSlot === 'A') ? 'B' : 'A';
                    crossfadeValue = (activeSlot === 'A') ? 0.0 : 1.0;
                }
            }

            // Audio speech energy tracking
            if (analyser && !audioElement.paused) {
                analyser.getByteFrequencyData(freqData);
                let sum = 0;
                for (let i = 0; i < 32; i++) sum += freqData[i];
                const speechEnergy = sum / 32;
                // Active speech modulation
                if (speechEnergy > 18 && currentClipKey === 'idle') {
                    transitionTo('talk');
                }
            }

            // WebGL draw
            if (gl && glProgram) {
                gl.useProgram(glProgram);
                gl.uniform1f(uCrossfadeLoc, crossfadeValue);

                if (videoA.readyState >= 2) {
                    gl.activeTexture(gl.TEXTURE0);
                    gl.bindTexture(gl.TEXTURE_2D, texA);
                    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, videoA);
                }
                if (videoB.readyState >= 2) {
                    gl.activeTexture(gl.TEXTURE1);
                    gl.bindTexture(gl.TEXTURE_2D, texB);
                    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, videoB);
                }

                gl.viewport(0, 0, canvas.width, canvas.height);
                gl.clearColor(0, 0, 0, 0);
                gl.clear(gl.COLOR_BUFFER_BIT);
                gl.drawArrays(gl.TRIANGLES, 0, 6);
            }
        }
        requestAnimationFrame(renderLoop);

        // WebSocket Connection
        let ws;
        function connectWS() {
            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${proto}//${window.location.host}/ws/avatar`);

            ws.onopen = () => {
                statusText.innerText = "DIGITAL HUMAN READY";
                document.querySelector('.dot').style.background = "#00FF88";
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'speak') {
                    handleSpeak(data);
                } else if (data.type === 'stop' || data.type === 'idle') {
                    handleStop();
                } else if (data.type === 'set_avatar') {
                    if (data.avatar_id) {
                        updateClipPaths(data.avatar_id);
                        transitionTo('idle');
                    }
                } else if (data.type === 'set_gesture') {
                    if (data.gesture) {
                        transitionTo(data.gesture);
                    }
                }
            };

            ws.onclose = () => {
                statusText.innerText = "RECONNECTING...";
                document.querySelector('.dot').style.background = "#FFB800";
                setTimeout(connectWS, 2000);
            };
        }
        connectWS();

        function handleSpeak(data) {
            initAudioContext();
            if (audioCtx && audioCtx.state === 'suspended') {
                audioCtx.resume();
            }

            // Display Subtitle in designated bottom safe zone
            subtitleBox.innerText = data.text;
            subtitleBox.classList.add('active');

            // Dispatch targeted gesture
            const gesture = data.gesture || 'talk';
            if (gesture === 'point_product') {
                transitionTo('point_product');
            } else if (gesture === 'point_cart' || gesture === 'cta') {
                transitionTo('point_cart');
            } else {
                transitionTo('talk');
            }

            if (data.audio_url) {
                audioElement.src = data.audio_url;
                audioElement.play().catch(e => console.log('Audio autoplay prevented:', e));
                audioElement.onended = () => {
                    handleStop();
                };
            } else {
                const dur = Math.max(2500, (data.text.length / 13) * 1000);
                setTimeout(handleStop, dur);
            }
        }

        function handleStop() {
            subtitleBox.classList.remove('active');
            audioElement.pause();
            // Smoothly return to natural breathing & blinking idle motion
            setTimeout(() => {
                transitionTo('idle');
            }, 300);
        }

        // Window interaction unlock for audio
        window.addEventListener('click', () => {
            initAudioContext();
            if (audioCtx) audioCtx.resume();
            videoA.play();
        });
    </script>
</body>
</html>
"""


OVERLAY_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <title>LiveFlow 9:16 Overlay</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: transparent;
            overflow: hidden;
            width: 1080px;
            height: 1920px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #ffffff;
        }
        #overlay-canvas {
            position: relative;
            width: 1080px;
            height: 1920px;
            pointer-events: none;
        }

        /* Top Brand Bar */
        .top-bar {
            position: absolute;
            top: 40px;
            left: 40px;
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .live-tag {
            background: linear-gradient(135deg, #FF0055, #FF3366);
            color: #fff;
            font-size: 20px;
            font-weight: 800;
            padding: 8px 18px;
            border-radius: 12px;
            letter-spacing: 1px;
            box-shadow: 0 4px 15px rgba(255, 0, 85, 0.4);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .verified-badge {
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(0, 255, 136, 0.4);
            color: #00FF88;
            font-size: 18px;
            font-weight: 600;
            padding: 8px 16px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* Product Showcase Card */
        .product-card {
            position: absolute;
            bottom: 220px;
            left: 40px;
            width: 480px;
            background: rgba(15, 23, 42, 0.88);
            backdrop-filter: blur(16px);
            border: 2px solid rgba(255, 255, 255, 0.15);
            border-radius: 24px;
            padding: 20px;
            box-shadow: 0 15px 40px rgba(0, 0, 0, 0.5);
            display: flex;
            flex-direction: column;
            gap: 14px;
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            transform: translateY(0);
        }
        .product-card.hidden {
            transform: translateY(120%);
            opacity: 0;
        }
        .product-header {
            display: flex;
            gap: 16px;
            align-items: center;
        }
        .product-img {
            width: 100px;
            height: 100px;
            border-radius: 16px;
            object-fit: cover;
            border: 1px solid rgba(255, 255, 255, 0.2);
            background: #1E293B;
        }
        .product-info {
            flex: 1;
        }
        .product-sku {
            font-size: 14px;
            color: #94A3B8;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .product-title {
            font-size: 22px;
            font-weight: 700;
            color: #ffffff;
            line-height: 1.3;
            margin-top: 4px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .price-row {
            display: flex;
            align-items: baseline;
            gap: 12px;
            margin-top: 8px;
        }
        .effective-price {
            font-size: 32px;
            font-weight: 800;
            color: #FFD700;
            text-shadow: 0 2px 10px rgba(255, 215, 0, 0.3);
        }
        .original-price {
            font-size: 18px;
            color: #94A3B8;
            text-decoration: line-through;
        }
        .discount-tag {
            background: #FF0055;
            color: #ffffff;
            font-size: 15px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 8px;
        }

        /* Comment Notification Toast */
        .comment-toast {
            position: absolute;
            top: 140px;
            left: 40px;
            width: 520px;
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.95), rgba(15, 23, 42, 0.95));
            backdrop-filter: blur(14px);
            border-left: 5px solid #00D2FF;
            border-radius: 16px;
            padding: 16px 20px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
            opacity: 0;
            transform: translateY(-20px);
            transition: all 0.4s ease;
        }
        .comment-toast.show {
            opacity: 1;
            transform: translateY(0);
        }
        .comment-user {
            font-size: 16px;
            font-weight: 700;
            color: #00D2FF;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .comment-text {
            font-size: 18px;
            color: #E2E8F0;
            margin-top: 6px;
            line-height: 1.35;
        }
        .reply-box {
            margin-top: 10px;
            background: rgba(0, 210, 255, 0.1);
            border-radius: 10px;
            padding: 10px 14px;
            font-size: 16px;
            color: #A5F3FC;
            border-left: 3px solid #00D2FF;
        }

        /* Bottom Call To Action */
        .bottom-cta {
            position: absolute;
            bottom: 40px;
            left: 40px;
            right: 40px;
            background: linear-gradient(90deg, #FF0055, #FF5500);
            padding: 18px 24px;
            border-radius: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 8px 25px rgba(255, 0, 85, 0.4);
            animation: pulse-cta 2s infinite;
        }
        @keyframes pulse-cta {
            0% { transform: scale(1); }
            50% { transform: scale(1.015); }
            100% { transform: scale(1); }
        }
        .cta-text {
            font-size: 24px;
            font-weight: 800;
            letter-spacing: 0.5px;
        }
        .cta-btn {
            background: #ffffff;
            color: #FF0055;
            font-size: 20px;
            font-weight: 800;
            padding: 10px 24px;
            border-radius: 14px;
        }
    </style>
</head>
<body>
    <div id="overlay-canvas">
        <div class="top-bar">
            <div class="live-tag">● TIKTOK LIVE</div>
            <div class="verified-badge">✓ CHÍNH HÃNG 100%</div>
        </div>

        <div id="comment-toast" class="comment-toast">
            <div class="comment-user">
                <span>💬 @<span id="toast-username">khach_hang</span></span>
            </div>
            <div id="toast-text" class="comment-text">Sản phẩm này dùng cho da nhạy cảm được không shop?</div>
            <div id="toast-reply" class="reply-box">
                <strong>MC Trả lời:</strong> <span id="toast-reply-content">Dạ sản phẩm 100% lành tính, kiểm định an toàn cho mọi loại da ạ!</span>
            </div>
        </div>

        <div id="product-card" class="product-card">
            <div class="product-header">
                <img id="product-img" class="product-img" src="" alt="Product">
                <div class="product-info">
                    <div id="product-sku" class="product-sku">SKU: PROD-01</div>
                    <div id="product-title" class="product-title">Tên sản phẩm mẫu</div>
                </div>
            </div>
            <div class="price-row">
                <span id="effective-price" class="effective-price">299.000đ</span>
                <span id="original-price" class="original-price">450.000đ</span>
                <span id="discount-tag" class="discount-tag">-33%</span>
            </div>
        </div>

        <div class="bottom-cta">
            <span class="cta-text">🔥 ƯU ĐÃI ĐỘC QUYỀN TRÊN PHIÊN LIVE HÔM NAY!</span>
            <span class="cta-btn">MUA NGAY 👉</span>
        </div>
    </div>

    <script>
        let ws;
        function connectOverlayWS() {
            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${proto}//${window.location.host}/ws/overlay`);

            ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);
                if (msg.type === 'update_product') {
                    renderProduct(msg.data);
                } else if (msg.type === 'show_comment') {
                    renderComment(msg.data);
                }
            };

            ws.onclose = () => {
                setTimeout(connectOverlayWS, 2000);
            };
        }
        connectOverlayWS();

        function renderProduct(p) {
            const card = document.getElementById('product-card');
            if (!p) {
                card.classList.add('hidden');
                return;
            }
            card.classList.remove('hidden');
            document.getElementById('product-sku').innerText = 'SKU: ' + (p.sku || 'LIVE-SKU');
            document.getElementById('product-title').innerText = p.name || 'Sản phẩm đang giới thiệu';
            document.getElementById('effective-price').innerText = (p.effective_price || 0).toLocaleString('vi-VN') + 'đ';

            if (p.price && p.price > p.effective_price) {
                document.getElementById('original-price').innerText = p.price.toLocaleString('vi-VN') + 'đ';
                document.getElementById('original-price').style.display = 'inline';
                document.getElementById('discount-tag').innerText = '-' + (p.discount_percent || 0) + '%';
                document.getElementById('discount-tag').style.display = 'inline';
            } else {
                document.getElementById('original-price').style.display = 'none';
                document.getElementById('discount-tag').style.display = 'none';
            }

            if (p.image_url) {
                document.getElementById('product-img').src = p.image_url;
            }
        }

        let toastTimer = null;
        function renderComment(c) {
            const toast = document.getElementById('comment-toast');
            document.getElementById('toast-username').innerText = c.username || 'nguoi_xem';
            document.getElementById('toast-text').innerText = c.text || '';
            document.getElementById('toast-reply-content').innerText = c.reply || '';

            toast.classList.add('show');
            if (toastTimer) clearTimeout(toastTimer);
            toastTimer = setTimeout(() => {
                toast.classList.remove('show');
            }, 8000);
        }
    </script>
</body>
</html>
"""


class RealtimeBridgeServer:
    """Embedded aiohttp server for OBS Browser Source streaming and overlays."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8088) -> None:
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.avatar_sockets: Set[web.WebSocketResponse] = set()
        self.overlay_sockets: Set[web.WebSocketResponse] = set()
        self.is_running = False
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/avatar.html", self._handle_avatar_page)
        self.app.router.add_get("/overlay.html", self._handle_overlay_page)
        self.app.router.add_get("/ws/avatar", self._handle_avatar_ws)
        self.app.router.add_get("/ws/overlay", self._handle_overlay_ws)
        self.app.router.add_get("/api/health", self._handle_health)
        self.app.router.add_post("/api/speak", self._handle_api_speak)
        self.app.router.add_post("/api/product", self._handle_api_product)
        self.app.router.add_post("/api/comment", self._handle_api_comment)

        # Serve avatar static assets from storage/avatars
        from app.config.settings import get_project_root
        static_dir = get_project_root() / "storage" / "avatars"
        if static_dir.exists():
            self.app.router.add_static("/static/avatars/", path=str(static_dir), name="avatars")

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text="LiveFlow Realtime Avatar & OBS Bridge Server is running.")

    async def _handle_avatar_page(self, request: web.Request) -> web.Response:
        return web.Response(text=AVATAR_HTML, content_type="text/html")

    async def _handle_overlay_page(self, request: web.Request) -> web.Response:
        return web.Response(text=OVERLAY_HTML, content_type="text/html")

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "healthy",
            "avatar_clients": len(self.avatar_sockets),
            "overlay_clients": len(self.overlay_sockets),
        })

    async def _handle_avatar_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.avatar_sockets.add(ws)
        logger.info("OBS Avatar Browser Source connected (total: {})", len(self.avatar_sockets))
        try:
            async for msg in ws:
                pass
        finally:
            self.avatar_sockets.discard(ws)
            logger.info("OBS Avatar Browser Source disconnected")
        return ws

    async def _handle_overlay_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.overlay_sockets.add(ws)
        logger.info("OBS Overlay Browser Source connected (total: {})", len(self.overlay_sockets))
        try:
            async for msg in ws:
                pass
        finally:
            self.overlay_sockets.discard(ws)
            logger.info("OBS Overlay Browser Source disconnected")
        return ws

    async def _handle_api_speak(self, request: web.Request) -> web.Response:
        data = await request.json()
        await self.broadcast_avatar_speech(
            text=data.get("text", ""),
            audio_url=data.get("audio_url", ""),
            emotion=data.get("emotion", "friendly"),
        )
        return web.json_response({"ok": True})

    async def _handle_api_product(self, request: web.Request) -> web.Response:
        data = await request.json()
        await self.broadcast_overlay_product(data)
        return web.json_response({"ok": True})

    async def _handle_api_comment(self, request: web.Request) -> web.Response:
        data = await request.json()
        await self.broadcast_overlay_comment(data)
        return web.json_response({"ok": True})

    async def broadcast_avatar_speech(
        self,
        text: str,
        audio_url: str = "",
        emotion: str = "friendly",
        gesture: str = "talk",
    ) -> None:
        """Broadcast speech cue to all connected OBS avatar browser sources."""
        msg = json.dumps({
            "type": "speak",
            "text": text,
            "audio_url": audio_url,
            "emotion": emotion,
            "gesture": gesture,
        })
        for ws in list(self.avatar_sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                self.avatar_sockets.discard(ws)

    async def broadcast_avatar_stop(self) -> None:
        msg = json.dumps({"type": "stop"})
        for ws in list(self.avatar_sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                self.avatar_sockets.discard(ws)

    async def broadcast_avatar_idle(self) -> None:
        """Broadcast idle event to return avatar smoothly to breathing and blinking pose."""
        msg = json.dumps({"type": "idle"})
        for ws in list(self.avatar_sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                self.avatar_sockets.discard(ws)

    async def broadcast_avatar_gesture(self, gesture: str) -> None:
        """Broadcast targeted gesture switch (e.g. 'point_product', 'point_cart', 'talk', 'idle')."""
        msg = json.dumps({"type": "set_gesture", "gesture": gesture})
        for ws in list(self.avatar_sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                self.avatar_sockets.discard(ws)

    async def broadcast_overlay_product(self, product_dict: Optional[Dict[str, Any]]) -> None:
        """Update active product card on OBS overlay."""
        msg = json.dumps({"type": "update_product", "data": product_dict})
        for ws in list(self.overlay_sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                self.overlay_sockets.discard(ws)

    async def broadcast_overlay_comment(self, comment_dict: Dict[str, Any]) -> None:
        """Display incoming comment and verified answer on OBS overlay."""
        msg = json.dumps({"type": "show_comment", "data": comment_dict})
        for ws in list(self.overlay_sockets):
            try:
                await ws.send_str(msg)
            except Exception:
                self.overlay_sockets.discard(ws)

    async def start(self) -> None:
        """Start the HTTP & WebSocket server asynchronously."""
        if self.is_running:
            return
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        self.is_running = True
        logger.info("RealtimeBridgeServer running at http://{}:{}", self.host, self.port)

    async def stop(self) -> None:
        """Stop the bridge server."""
        if not self.is_running:
            return
        for ws in list(self.avatar_sockets):
            await ws.close()
        for ws in list(self.overlay_sockets):
            await ws.close()
        if self.runner:
            await self.runner.cleanup()
        self.is_running = False
        logger.info("RealtimeBridgeServer stopped")
