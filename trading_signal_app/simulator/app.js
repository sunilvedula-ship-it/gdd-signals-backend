// API Base URL Configuration
const API_BASE = "http://127.0.0.1:8000";
const WS_BASE = "ws://127.0.0.1:8000";

let ws = null;
let selectedServices = ["investment", "swing", "intraday"];

// UI Navigation Handles
const screens = {
    login: document.getElementById("screen-login"),
    services: document.getElementById("screen-services"),
    dashboard: document.getElementById("screen-dashboard")
};

// Tab Handles
const tabs = {
    feed: document.getElementById("tab-feed"),
    paper: document.getElementById("tab-paper"),
    consent: document.getElementById("tab-consent"),
    auto: document.getElementById("tab-auto"),
    profile: document.getElementById("tab-profile")
};

// Initialize Application Elements
document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initServiceSelection();
    initSimulatorControls();
    
    // Set date in consent panel
    document.getElementById("consent-today-date").textContent = new Date().toISOString().split('T')[0];
    
    // Initial fetch of profile and configurations
    loadProfile();
    
    // Start backend updates
    checkBackendConnection();
});

// Setup screen routing and bottom navigation tabs
function initNavigation() {
    // Login transition
    document.getElementById("login-btn").addEventListener("click", () => {
        const email = document.getElementById("login-email").value;
        const phone = document.getElementById("login-phone").value;
        if (!email || !phone) {
            alert("Please fill in both Email and Phone.");
            return;
        }
        transitionToScreen("services");
    });

    // Services selection transition
    document.getElementById("save-services-btn").addEventListener("click", () => {
        transitionToScreen("dashboard");
        startPollingBackend();
        connectWebSocket();
    });

    // Dashboard tab items click handler
    document.querySelectorAll(".bottom-nav .nav-item").forEach(item => {
        item.addEventListener("click", (e) => {
            const targetTab = e.currentTarget.getAttribute("data-tab");
            
            // Toggle active nav class
            document.querySelectorAll(".bottom-nav .nav-item").forEach(nav => nav.classList.remove("active"));
            e.currentTarget.classList.add("active");
            
            // Show active tab panel
            Object.keys(tabs).forEach(tabKey => {
                if (tabKey === targetTab) {
                    tabs[tabKey].classList.add("active");
                } else {
                    tabs[tabKey].classList.remove("active");
                }
            });
            
            // Specific tab entry actions
            if (targetTab === "feed") loadSignals();
            if (targetTab === "paper") loadPaperTrades();
            if (targetTab === "consent") loadConsentStatus();
            if (targetTab === "auto") loadBrokerCredentials();
        });
    });

    // Home button reset (simulator reset)
    document.querySelector(".phone-home-btn").addEventListener("click", () => {
        transitionToScreen("login");
        if (ws) {
            ws.close();
        }
    });

    // Sign Consent button click
    document.getElementById("sign-consent-btn").addEventListener("click", () => {
        signDailyConsent();
    });

    // Save credentials button click
    document.getElementById("save-broker-btn").addEventListener("click", () => {
        saveCredentials();
    });
}

function transitionToScreen(screenKey) {
    Object.keys(screens).forEach(key => {
        if (key === screenKey) {
            screens[key].classList.add("active");
        } else {
            screens[key].classList.remove("active");
        }
    });
}

// Service Selection checks toggling
function initServiceSelection() {
    document.querySelectorAll(".service-item").forEach(item => {
        item.addEventListener("click", (e) => {
            const serviceKey = e.currentTarget.getAttribute("data-service");
            e.currentTarget.classList.toggle("active");
            const checkbox = e.currentTarget.querySelector(".checkbox-ui");
            checkbox.classList.toggle("checked");
            
            if (e.currentTarget.classList.contains("active")) {
                if (!selectedServices.includes(serviceKey)) selectedServices.push(serviceKey);
            } else {
                selectedServices = selectedServices.filter(s => s !== serviceKey);
            }
        });
    });
}

// ----------------------------------------------------
// BACKEND API CALLS & DATA RENDERING
// ----------------------------------------------------

// Verify backend is alive
function checkBackendConnection() {
    fetch(`${API_BASE}/api/consent`)
        .then(res => res.json())
        .then(() => {
            logAdmin("System API Connection is Active", "success");
        })
        .catch(() => {
            logAdmin("Warning: Local Backend Server not running. Start it to test database features.", "warning");
        });
}

function startPollingBackend() {
    loadSignals();
    loadPaperTrades();
    loadConsentStatus();
    loadBrokerCredentials();
    
    // Poll updates every 6 seconds as a backup
    setInterval(() => {
        if (screens.dashboard.classList.contains("active")) {
            if (tabs.feed.classList.contains("active")) loadSignals();
            if (tabs.paper.classList.contains("active")) loadPaperTrades();
        }
    }, 6000);
}

// Websocket subscription
function connectWebSocket() {
    try {
        ws = new WebSocket(`${WS_BASE}/ws`);
        
        ws.onopen = () => {
            logAdmin("Real-time App Stream Connected", "success");
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.event === "new_signal") {
                logAdmin(`Signal Sourced: ${data.signal.action} ${data.signal.symbol} @ ₹${data.signal.price}`, "info");
                
                // Show notification badge effect
                const bell = document.querySelector(".notification-bell");
                bell.classList.add("pulse");
                setTimeout(() => bell.classList.remove("pulse"), 1000);
                
                // Reload pages in background
                loadSignals();
                loadPaperTrades();
            }
        };
        
        ws.onclose = () => {
            logAdmin("Real-time App Stream closed. Retrying...", "warning");
            setTimeout(connectWebSocket, 5000);
        };
    } catch (e) {
        console.error("WS error: ", e);
    }
}

// Load signals in Feed Tab
function loadSignals() {
    fetch(`${API_BASE}/api/signals`)
        .then(res => res.json())
        .then(signals => {
            const container = document.getElementById("signal-feed-list");
            if (!signals || signals.length === 0) {
                container.innerHTML = `
                    <div class="empty-feed">
                        <i class="fa-solid fa-wind"></i>
                        <p>No trading signals received yet. Trigger one from the simulator controls on the left!</p>
                    </div>`;
                return;
            }
            
            container.innerHTML = signals.map(sig => {
                const actionClass = sig.action.toLowerCase(); // long, short, exit
                const timeString = new Date(sig.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
                
                return `
                    <div class="glass-card signal-card ${actionClass}">
                        <div class="signal-header">
                            <span class="sig-symbol">${sig.symbol}</span>
                            <span class="sig-action-badge">${sig.action}</span>
                        </div>
                        <div class="signal-body">
                            <div class="sig-price">
                                ₹${sig.price.toLocaleString('en-IN', {minimumFractionDigits: 2})} 
                                <span>Entry</span>
                            </div>
                            <div class="sig-meta">
                                <span class="sig-source">${sig.source_name}</span>
                                <span>${timeString}</span>
                            </div>
                        </div>
                    </div>`;
            }).join('');
        })
        .catch(err => console.error("Error loading signals: ", err));
}

// Load paper trades in Stats / Positions Tab
function loadPaperTrades() {
    fetch(`${API_BASE}/api/paper-trades`)
        .then(res => res.json())
        .then(data => {
            // Update stats
            const stats = data.stats;
            const pnlEl = document.getElementById("stat-pnl");
            
            pnlEl.textContent = `₹${stats.total_pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}`;
            pnlEl.className = stats.total_pnl > 0 ? "positive" : (stats.total_pnl < 0 ? "negative" : "neutral");
            
            document.getElementById("stat-winrate").textContent = `${stats.win_rate.toFixed(1)}%`;
            document.getElementById("stat-trades").textContent = stats.total_trades;
            
            // Render position list
            const container = document.getElementById("positions-list");
            const positions = data.positions;
            
            if (!positions || positions.length === 0) {
                container.innerHTML = `
                    <div class="empty-feed">
                        <i class="fa-solid fa-folder-open"></i>
                        <p>No paper trade records. Entry/exit signals will execute here automatically.</p>
                    </div>`;
                return;
            }
            
            container.innerHTML = positions.map(pos => {
                const directionClass = pos.direction.toLowerCase();
                const isClosed = pos.status === "CLOSED";
                const pnlClass = pos.pnl > 0 ? "positive" : (pos.pnl < 0 ? "negative" : "");
                const formattedPnL = isClosed 
                    ? `₹${pos.pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}` 
                    : `₹${pos.pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})} (OPEN)`;
                
                return `
                    <div class="glass-card position-card">
                        <div class="pos-header">
                            <div class="pos-info">
                                <span class="pos-dir-indicator ${directionClass}">${pos.direction}</span>
                                <span class="pos-symbol">${pos.symbol}</span>
                                <span class="pos-qty">${pos.qty} Qty</span>
                            </div>
                            <span class="pos-pnl ${pnlClass}">${formattedPnL}</span>
                        </div>
                        <div class="pos-details">
                            <div>Entry: ₹${pos.entry_price.toLocaleString('en-IN')}${!isClosed && pos.current_price ? ` | LTP: ₹${pos.current_price.toLocaleString('en-IN', {minimumFractionDigits: 2})}` : ''}</div>
                            ${isClosed 
                                ? `<div>Exit: ₹${pos.exit_price.toLocaleString('en-IN')}</div>` 
                                : `<button class="pos-exit-btn" onclick="exitPosition(${pos.id})">Manual Close</button>`
                            }
                        </div>
                    </div>`;
            }).join('');
        })
        .catch(err => console.error("Error loading positions: ", err));
}

// Close position manually
window.exitPosition = function(posId) {
    fetch(`${API_BASE}/api/paper-trades/manual-exit/${posId}`, { method: "POST" })
        .then(res => res.json())
        .then(() => {
            logAdmin(`Manually Closed Position ID ${posId}`, "success");
            loadPaperTrades();
        })
        .catch(err => console.error("Error exiting trade: ", err));
};

// Check daily consent
function loadConsentStatus() {
    fetch(`${API_BASE}/api/consent`)
        .then(res => res.json())
        .then(data => {
            const statusBox = document.getElementById("consent-status-box");
            const signBtn = document.getElementById("sign-consent-btn");
            
            if (data.consent_signed) {
                statusBox.className = "consent-status alert-success";
                statusBox.innerHTML = `<i class="fa-solid fa-circle-check"></i> <span>Daily Consent Signed. Auto-trading enabled.</span>`;
                signBtn.disabled = true;
                signBtn.textContent = "Signed for Today";
            } else {
                statusBox.className = "consent-status alert-warning";
                statusBox.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> <span>Auto-Trading paused until consent is signed.</span>`;
                signBtn.disabled = false;
                signBtn.innerHTML = `<i class="fa-solid fa-signature"></i> I Consent for Today`;
            }
        })
        .catch(err => console.error("Error fetching consent: ", err));
}

// Sign consent for today
function signDailyConsent() {
    fetch(`${API_BASE}/api/consent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agreement_version: "v1.0" })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "success") {
            logAdmin("Consent signed successfully", "success");
            loadConsentStatus();
        }
    })
    .catch(err => console.error("Error signing consent: ", err));
}

// Broker Credentials Loading
function loadBrokerCredentials() {
    fetch(`${API_BASE}/api/credentials`)
        .then(res => res.json())
        .then(data => {
            const container = document.getElementById("configured-brokers");
            const allCreds = [...data.brokers, ...data.crypto];
            const configured = allCreds.filter(b => b.configured);
            
            if (configured.length === 0) {
                container.innerHTML = `<p class="mt-1" style="font-size: 11px; color: var(--text-muted);">No brokers linked yet.</p>`;
                return;
            }
            
            container.innerHTML = `
                <h5 class="mt-2" style="font-size: 12px; font-weight:700;">Linked Accounts:</h5>
                ` + configured.map(b => `
                    <div class="configured-broker-badge">
                        <div>
                            <strong>${b.name}</strong>
                            <div style="font-size: 10px; color: var(--text-muted);">${b.info.api_key_masked}</div>
                        </div>
                        <button class="del-cred-btn" onclick="deleteBrokerCredentials('${b.id}')">
                            <i class="fa-regular fa-trash-can"></i>
                        </button>
                    </div>
                `).join('');
        })
        .catch(err => console.error("Error loading broker creds: ", err));
}

// Save Broker Credentials
function saveCredentials() {
    const broker_id = document.getElementById("broker-select").value;
    const api_key = document.getElementById("api-key-input").value;
    const api_secret = document.getElementById("api-secret-input").value;
    
    if (!api_key || !api_secret) {
        alert("Please enter API Key and Secret.");
        return;
    }
    
    fetch(`${API_BASE}/api/credentials`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            broker_id: broker_id,
            api_key: api_key,
            api_secret: api_secret,
            extra: {}
        })
    })
    .then(res => res.json())
    .then(() => {
        logAdmin(`Successfully saved api credentials for ${broker_id}`, "success");
        // Clear fields
        document.getElementById("api-key-input").value = "";
        document.getElementById("api-secret-input").value = "";
        loadBrokerCredentials();
    })
    .catch(err => console.error("Error saving broker credentials: ", err));
}

// Delete Broker Credentials
window.deleteBrokerCredentials = function(brokerId) {
    if (!confirm(`Are you sure you want to delete linked credentials for ${brokerId}?`)) return;
    
    fetch(`${API_BASE}/api/credentials/${brokerId}`, { method: "DELETE" })
        .then(res => res.json())
        .then(() => {
            logAdmin(`Removed credentials for ${brokerId}`, "success");
            loadBrokerCredentials();
        })
        .catch(err => console.error("Error deleting broker creds: ", err));
};

// Fetch User Profile info
function loadProfile() {
    fetch(`${API_BASE}/api/user`)
        .then(res => res.json())
        .then(data => {
            document.getElementById("profile-name").textContent = data.name;
            document.getElementById("profile-email").textContent = data.email;
            document.getElementById("profile-phone").textContent = data.phone;
        })
        .catch(err => console.log("Profile load failed (expected if DB not yet loaded)"));
}

// ----------------------------------------------------
// SIMULATION PANEL (SIDEBAR CONTROLS)
// ----------------------------------------------------
function initSimulatorControls() {
    document.getElementById("send-signal-btn").addEventListener("click", () => {
        const symbol = document.getElementById("sim-symbol").value;
        const action = document.getElementById("sim-action").value;
        const price = parseFloat(document.getElementById("sim-price").value);
        const source = document.getElementById("sim-source").value;
        
        // Trigger alert POST payload
        const payload = {
            auth: "TradeSignal2024",
            symbol: symbol,
            action: action,
            price: price,
            orderId: `AutoAlert_${action}_${symbol}`,
            source: source
        };
        
        logAdmin(`Triggering Alert webhook...`, "info");
        
        fetch(`${API_BASE}/api/signals/webhook`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === "success") {
                logAdmin(`Webhook Processed! Position actions: ${data.actions.join(', ')}`, "success");
                // Reload values
                loadSignals();
                loadPaperTrades();
            } else {
                logAdmin(`Error: Webhook returned failed status.`, "danger");
            }
        })
        .catch(err => {
            logAdmin(`Error connecting to Backend Server. Make sure 'python run_simulator.py' is running.`, "danger");
            console.error("Alert send error: ", err);
        });
    });
}

// Helper to log logs in the control panel log board
function logAdmin(msg, type = "info") {
    const logBox = document.getElementById("activity-logs");
    const item = document.createElement("div");
    item.className = `log-item ${type}`;
    const time = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
    item.textContent = `[${time}] ${msg}`;
    logBox.appendChild(item);
    logBox.scrollTop = logBox.scrollHeight;
}
