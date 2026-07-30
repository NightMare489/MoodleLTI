/**
 * AAST CodeJudge - Client-Side Screen Proctoring Engine
 * Persistent across SPA navigation — state stored on window.
 */

(function () {
    'use strict';

    // Guard: if already loaded, don't re-initialize (SPA re-execution protection)
    if (window._proctorEngineLoaded) return;
    window._proctorEngineLoaded = true;

    // State stored on window so it survives SPA script re-execution
    window._proctor = window._proctor || {
        stream: null,
        videoElement: null,
        canvasElement: null,
        canvasCtx: null,
        heartbeatTimer: null,
        isCaptured: false,
        sessionUuid: null,
        peerConnection: null,
        pollIntervalMs: 1000,
        pollTimer: null
    };

    const P = window._proctor;
    let proctorStream = P.stream;
    let videoElement = P.videoElement;
    let canvasElement = P.canvasElement;
    let canvasCtx = P.canvasCtx;
    let heartbeatTimer = P.heartbeatTimer;
    let isCaptured = P.isCaptured;
    let sessionUuid = P.sessionUuid;

    // Retrieve or generate unique session UUID
    function getSessionUuid() {
        if (!sessionUuid) {
            sessionUuid = sessionStorage.getItem('_proctor_uuid');
            if (!sessionUuid) {
                sessionUuid = 'proc-' + Math.random().toString(36).substring(2, 15) + '-' + Date.now();
                sessionStorage.setItem('_proctor_uuid', sessionUuid);
            }
        }
        return sessionUuid;
    }

    // Modal UI for requesting screen capture
    function createProctorModal(onAccept) {
        const existing = document.getElementById('proctor-modal-overlay');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'proctor-modal-overlay';
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
            background: rgba(15, 23, 42, 0.92); backdrop-filter: blur(6px);
            z-index: 999999; display: flex; align-items: center; justify-content: center;
            font-family: system-ui, -apple-system, sans-serif; color: #f8fafc;
        `;

        overlay.innerHTML = `
            <div style="background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 2rem; max-width: 480px; width: 90%; text-align: left; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5);">
                <h2 style="font-size: 1.35rem; font-weight: 700; margin-bottom: 0.5rem; color: #ffffff;">Screen Monitoring Required</h2>
                <p style="color: #94a3b8; font-size: 0.9rem; line-height: 1.5; margin-bottom: 1.25rem;">
                    This exam requires screen proctoring. You must select <strong>"Entire Screen"</strong> (your full display) when prompted by your browser.
                </p>
                <div style="background: rgba(234, 179, 8, 0.08); border: 1px solid rgba(234, 179, 8, 0.2); padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.825rem; color: #fef08a; margin-bottom: 1.5rem;">
                    Select <strong>"Entire Screen"</strong> when prompted. Sharing a single window or tab is not allowed.
                </div>
                <button id="btn-start-proctoring" style="
                    background: #2563eb; color: #ffffff; border: none; border-radius: 8px;
                    padding: 0.75rem 1.5rem; font-size: 0.95rem; font-weight: 600; cursor: pointer;
                    width: 100%; transition: background 0.2s;
                ">
                    Share Screen & Begin Exam
                </button>
                <div id="proctor-error-msg" style="color: #f87171; font-size: 0.85rem; margin-top: 1rem; display: none; text-align: center;"></div>
            </div>
        `;

        document.body.appendChild(overlay);

        document.getElementById('btn-start-proctoring').addEventListener('click', function () {
            onAccept();
        });
    }

    // Overlay displayed when screen share is stopped, unsupported, or session is locked
    function showLockedOverlay(message, allowReload = true) {
        isCaptured = false;
        let overlay = document.getElementById('proctor-locked-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'proctor-locked-overlay';
            overlay.style.cssText = `
                position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
                background: rgba(15, 23, 42, 0.96); backdrop-filter: blur(10px);
                z-index: 9999999; display: flex; align-items: center; justify-content: center;
                font-family: system-ui, -apple-system, sans-serif; color: #ffffff; text-align: center;
            `;
            document.body.appendChild(overlay);
        }

        const btnHtml = allowReload ? `
            <button id="btn-reload-proctor" style="
                background: #2563eb; color: #ffffff; border: none;
                border-radius: 8px; padding: 0.75rem 1.5rem; font-weight: 600; cursor: pointer; width: 100%; margin-top: 1.25rem;
            ">
                Re-share Screen
            </button>
        ` : '';

        overlay.innerHTML = `
            <div style="background: #0f172a; border: 1px solid #334155; border-radius: 12px; padding: 2.5rem; max-width: 460px; width: 90%;">
                <h2 style="font-size: 1.4rem; color: #f87171; font-weight: 700; margin-bottom: 0.75rem;">Access Restricted</h2>
                <p style="color: #cbd5e1; font-size: 0.925rem; line-height: 1.5; margin-bottom: 0;">${message}</p>
                ${btnHtml}
            </div>
        `;

        if (allowReload) {
            const btn = document.getElementById('btn-reload-proctor');
            if (btn) {
                btn.onclick = function () {
                    isCaptured = false;
                    location.reload();
                };
            }
        }
    }

    // Start Screen Capture
    async function startScreenCapture(config) {
        const errorDiv = document.getElementById('proctor-error-msg');
        if (errorDiv) errorDiv.style.display = 'none';

        try {
            proctorStream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    displaySurface: "monitor",
                    width: { ideal: 854, max: 854 },
                    height: { ideal: 480, max: 480 },
                    frameRate: { ideal: 24, max: 24 }
                },
                audio: false
            });

            const videoTrack = proctorStream.getVideoTracks()[0];
            const settings = videoTrack.getSettings();

            // Validate that full screen was shared if reported by browser
            if (settings.displaySurface && settings.displaySurface !== 'monitor') {
                videoTrack.stop();
                if (errorDiv) {
                    errorDiv.innerText = '❌ You shared a single tab or window. Please select "Entire Screen".';
                    errorDiv.style.display = 'block';
                }
                return;
            }

            // Screen share successfully granted!
            const modal = document.getElementById('proctor-modal-overlay');
            if (modal) modal.remove();

            isCaptured = true;
            unlockSubmissionUI();

            // Handle track stop event (user clicks native browser "Stop sharing" bar)
            videoTrack.addEventListener('ended', function () {
                isCaptured = false;
                lockSubmissionUI();
                sendTelemetry('SCREEN_STOPPED', 'Student clicked Stop Sharing on browser bar');
                showLockedOverlay('Screen sharing was stopped. Your exam input has been frozen. Please contact your instructor to resume.');
            });

            // Create offscreen video & canvas elements for 480p WebP sampling
            videoElement = document.createElement('video');
            videoElement.srcObject = proctorStream;
            videoElement.play();

            canvasElement = document.createElement('canvas');
            canvasElement.width = 854;  // 480p width
            canvasElement.height = 480; // 480p height
            canvasCtx = canvasElement.getContext('2d');

            // Add live proctoring active badge to top header
            addProctorBadge();

            // Start sending 480p WebP frame heartbeats for fallbacks & admin grid thumbnails
            startHeartbeatLoop(config);

            // Start listening for admin WebRTC request_offer signals
            pollWebRTCSignals();

        } catch (err) {
            console.error('Proctoring Error:', err);
            if (errorDiv) {
                errorDiv.innerText = '❌ Permission denied or browser unsupported. You must grant screen share to take the exam.';
                errorDiv.style.display = 'block';
            }
        }
    }

    // ── WebRTC Peer-to-Peer Video Broadcaster (60 FPS Ultra-Fast Engine) ───
    let peerConnection = null;
    let rtcConfig = null;

    // Fetch ICE server config (STUN + TURN) from backend
    async function getRtcConfig() {
        if (rtcConfig) return rtcConfig;
        try {
            const resp = await fetch('/proctor/ice-config');
            const data = await resp.json();
            rtcConfig = {
                iceServers: data.iceServers || [],
                ...(data.iceTransportPolicy ? { iceTransportPolicy: data.iceTransportPolicy } : {})
            };
        } catch (e) {
            // Fallback to STUN-only if fetch fails
            rtcConfig = {
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:stun1.l.google.com:19302' }
                ]
            };
        }
        return rtcConfig;
    }

    async function setupWebRTCBroadcaster(stream) {
        try {
            if (peerConnection) {
                try { peerConnection.close(); } catch(e){}
                peerConnection = null;
            }
            pollIntervalMs = 1000; // Reset signal polling interval for fast handshake
            const config = await getRtcConfig();
            peerConnection = new RTCPeerConnection(config);

            stream.getTracks().forEach(track => {
                const sender = peerConnection.addTrack(track, stream);
                if (sender && sender.track && sender.track.kind === 'video') {
                    try {
                        const params = sender.getParameters();
                        if (!params.encodings) params.encodings = [{}];
                        params.encodings[0].maxBitrate = 600000; // 600 Kbps optimized for 480p 24 FPS
                        params.degradationPreference = 'maintain-framerate';
                        sender.setParameters(params).catch(() => {});
                    } catch (e) {}
                }
            });

            peerConnection.onicecandidate = (event) => {
                if (event.candidate) {
                    sendWebRTCSignal('admin', 'candidate', event.candidate);
                }
            };

            peerConnection.onconnectionstatechange = () => {
                if (peerConnection && (peerConnection.connectionState === 'connected' || peerConnection.connectionState === 'completed')) {
                    if (pollTimer) {
                        clearInterval(pollTimer);
                        pollTimer = null;
                    }
                } else if (peerConnection && (peerConnection.connectionState === 'failed' || peerConnection.connectionState === 'disconnected')) {
                    pollWebRTCSignals();
                }
            };

            // Create WebRTC Offer
            const offer = await peerConnection.createOffer({
                offerToReceiveVideo: false,
                offerToReceiveAudio: false
            });
            await peerConnection.setLocalDescription(offer);

            // Send offer to admin viewer
            sendWebRTCSignal('admin', 'offer', offer);
        } catch (err) {
            console.warn('WebRTC Broadcast setup warning:', err);
        }
    }

    async function sendWebRTCSignal(recipient, type, payload) {
        try {
            await fetch('/proctor/webrtc/signal', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_uuid: getSessionUuid(),
                    recipient: recipient,
                    type: type,
                    payload: payload
                })
            });
        } catch (e) { }
    }

    let pollIntervalMs = 1000;
    let pollTimer = null;

    const pendingCandidates = [];

    function pollWebRTCSignals() {
        if (pollTimer) clearInterval(pollTimer);

        pollTimer = setInterval(async function () {
            // If connected, relax polling interval to 3 seconds to save 90% bandwidth
            if (peerConnection && (peerConnection.connectionState === 'connected' || peerConnection.connectionState === 'completed')) {
                if (pollIntervalMs !== 3000) {
                    pollIntervalMs = 3000;
                    pollWebRTCSignals(); // Re-arm with 3s interval
                    return;
                }
            }

            try {
                const resp = await fetch(`/proctor/webrtc/poll?session_uuid=${getSessionUuid()}&recipient=student`);
                const data = await resp.json();

                if (data.signals && data.signals.length > 0) {
                    for (const signal of data.signals) {
                        if (signal.type === 'request_offer' && proctorStream) {
                            setupWebRTCBroadcaster(proctorStream);
                        } else if (signal.type === 'answer' && peerConnection) {
                            await peerConnection.setRemoteDescription(new RTCSessionDescription(signal.payload));
                            while (pendingCandidates.length > 0) {
                                const cand = pendingCandidates.shift();
                                try { await peerConnection.addIceCandidate(cand); } catch(e){}
                            }
                        } else if (signal.type === 'candidate' && signal.payload && peerConnection) {
                            const candidate = new RTCIceCandidate(signal.payload);
                            if (peerConnection.remoteDescription && peerConnection.remoteDescription.type) {
                                try { await peerConnection.addIceCandidate(candidate); } catch(e){}
                            } else {
                                pendingCandidates.push(candidate);
                            }
                        }
                    }
                }
            } catch (e) { }
        }, pollIntervalMs);
    }


    // Accidental Refresh Protection
    window.addEventListener('beforeunload', function (e) {
        if (isCaptured) {
            const msg = 'Warning: Refreshing will interrupt your live exam proctoring stream!';
            e.preventDefault();
            e.returnValue = msg;
            return msg;
        }
    });





    // Inject active proctoring status pill into bottom corner
    function addProctorBadge() {
        if (document.getElementById('proctor-active-badge')) return;
        const badge = document.createElement('div');
        badge.id = 'proctor-active-badge';
        badge.style.cssText = `
            position: fixed; bottom: 16px; right: 16px; z-index: 9999;
            background: rgba(15, 23, 42, 0.85); color: #cbd5e1;
            border: 1px solid rgba(255, 255, 255, 0.12);
            padding: 5px 12px; border-radius: 20px; font-size: 0.75rem;
            font-weight: 500; font-family: system-ui, -apple-system, sans-serif;
            display: flex; align-items: center; gap: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2); backdrop-filter: blur(8px);
            pointer-events: none; opacity: 0.9;
        `;
        badge.innerHTML = `<span style="width: 7px; height: 7px; background: #10b981; border-radius: 50%; display: inline-block;"></span> Screen proctoring active`;
        document.body.appendChild(badge);
    }

    // Sample current video frame to 480p WebP Base64 string
    function captureFrame() {
        if (!isCaptured || !videoElement || !canvasCtx) return null;
        try {
            canvasCtx.drawImage(videoElement, 0, 0, canvasElement.width, canvasElement.height);
            // 0.4 quality WebP compresses a 480p frame down to ~6-8 KB
            return canvasElement.toDataURL('image/webp', 0.4);
        } catch (e) {
            return null;
        }
    }

    // Send heartbeat with live WebP screen frame snapshot every 2 seconds
    function startHeartbeatLoop(config) {
        if (heartbeatTimer) clearInterval(heartbeatTimer);

        heartbeatTimer = setInterval(async function () {
            if (!isCaptured) return;

            const payload = {
                session_uuid: getSessionUuid(),
                problem_id: config.problemId || null,
                shared_link_code: config.sharedLinkCode || null,
                frame: captureFrame()
            };

            try {
                const resp = await fetch('/proctor/heartbeat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const res = await resp.json();

                if (res.is_locked) {
                    showLockedOverlay('Your exam session has been locked by the proctor.');
                }
            } catch (err) {
                console.warn('Proctor heartbeat sync issue:', err);
            }
        }, 2000); // 2-second HTTP WebP screen frame stream
    }


    // Send explicit telemetry event (e.g. SCREEN_STOPPED, PASTE_EVENT)
    async function sendTelemetry(eventType, details) {
        try {
            await fetch('/proctor/heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_uuid: getSessionUuid(),
                    event_type: eventType,
                    details: details,
                    frame: captureFrame()
                })
            });
        } catch (e) { }
    }

    function lockSubmissionUI() {
        const btn = document.getElementById('submitBtn');
        if (btn) {
            btn.disabled = true;
            btn.title = '🔒 Screen monitoring is required to submit code.';
        }
        const form = document.getElementById('submitForm');
        if (form && !form._proctorGuardAttached) {
            form._proctorGuardAttached = true;
            form.addEventListener('submit', function (e) {
                if (!isCaptured) {
                    e.preventDefault();
                    e.stopPropagation();
                    alert('🔒 Screen monitoring is required. You must share your entire screen before submitting.');
                    window.initProctoring({ isRequired: true });
                    return false;
                }
            }, true);
        }
    }

    function unlockSubmissionUI() {
        const btn = document.getElementById('submitBtn');
        if (btn) {
            btn.disabled = false;
            btn.title = '';
        }
    }

    // Public API
    window.initProctoring = function (config) {
        if (!config || !config.isRequired) return;

        // If screen capture is ALREADY active and live, skip modal prompt completely!
        if (isCaptured && proctorStream && proctorStream.active && proctorStream.getVideoTracks().some(t => t.readyState === 'live')) {
            unlockSubmissionUI();
            return;
        }

        // Lock submission UI until screen capture is granted
        lockSubmissionUI();

        // Check browser support
        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
            showLockedOverlay('Screen monitoring is required for this exam, but your current browser does not support the Screen Capture API. Please open this exam in Google Chrome, Microsoft Edge, or Firefox on a desktop computer.', false);
            return;
        }

        createProctorModal(function () {
            startScreenCapture(config);
        });
    };

})();
