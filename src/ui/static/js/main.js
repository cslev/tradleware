// Tab switching for card tabs
window.showTab = function(traderId, tab) {
  const tabs = ['summary', 'webhook', 'logs'];
  tabs.forEach(t => {
    const content = document.getElementById(`tab-${t}-${traderId}`);
    if (content) {
      if (t === tab) {
        content.classList.remove('hidden');
      } else {
        content.classList.add('hidden');
      }
    }
  });
  // Highlight active tab button for this card only
  document.querySelectorAll(`.tab-btn`).forEach(btn => {
    if (btn.getAttribute('onclick')?.includes(`'${traderId}', '${tab}'`)) {
      btn.classList.add('bg-cyan-100', 'text-cyan-700');
    } else if (btn.getAttribute('onclick')?.includes(`'${traderId}'`)) {
      btn.classList.remove('bg-cyan-100', 'text-cyan-700');
    }
  });
}

// Set default tab to Summary for all cards on page load
document.addEventListener('DOMContentLoaded', () => {
  const traderIds = JSON.parse(document.getElementById('trader-ids').textContent);
  traderIds.forEach(traderId => {
    showTab(traderId, 'summary');
  });
  // ...existing code...
});
// Fix the toggle function for collapsible
function toggleCollapse(elementId) {
  const content = document.getElementById(elementId);
  // Extract traderId from elementId (supports IDs like 'collapse-TRADERID' and 'webhook-TRADERID')
  const match = elementId.match(/^(collapse|webhook)-(.+)$/);
  let arrowId = null;
  if (match) {
    const type = match[1];
    const traderId = match[2];
    arrowId = type === 'webhook' ? `arrow-webhook-${traderId}` : `arrow-${traderId}`;
  }
  const arrow = arrowId ? document.getElementById(arrowId) : null;
  if (content) {
    content.classList.toggle('open');
    if (arrow) {
      arrow.style.transform = content.classList.contains('open') ? 'rotate(180deg)' : '';
    }
  }
}

// Update your refreshLogs function to parse and color the logs AND auto-scroll
async function refreshLogs(traderId) {
    const debugLogElement = document.getElementById(`debug-log-${traderId}`);
    const debugLogContainer = debugLogElement.closest('.debug-log-container');
    
    try {
        const response = await fetch(`/logs/${traderId}`);
        const data = await response.json();
        
        if (response.ok) {
            const logs = data.logs;
            
            if (logs && logs.length > 0) {
                // Process each log line to add color classes
                const coloredLogs = logs.map(log => {
                    if (log.includes('] CRITICAL:')) {
                        return `<span class="log-critical">${log}</span>`;
                    } else if (log.includes('] ERROR:')) {
                        return `<span class="log-error">${log}</span>`;
                    } else if (log.includes('] WARNING:')) {
                        return `<span class="log-warning">${log}</span>`;
                    } else if (log.includes('] SUCCESS:')) {
                        return `<span class="log-success">${log}</span>`;
                    } else if (log.includes('] INFO:')) {
                        return `<span class="log-info">${log}</span>`;
                    } else {
                        return log; // Default color
                    }
                }).join('\n');
                
                debugLogElement.innerHTML = coloredLogs || 'No recent logs';
            } else {
                debugLogElement.textContent = 'No recent logs';
            }
            
            debugLogElement.className = 'whitespace-pre-wrap text-cyan-600 text-xs m-0';
            
            // Auto-scroll to bottom after updating content
            if (debugLogContainer) {
                setTimeout(() => {
                    debugLogContainer.scrollTop = debugLogContainer.scrollHeight;
                }, 10); // Small delay to ensure content is rendered
            }
            
        } else {
            debugLogElement.innerHTML = `<span class="log-error">Error fetching logs: ${data.error}</span>`;
            debugLogElement.className = 'whitespace-pre-wrap text-red-600 text-xs m-0';
        }
    } catch (error) {
        debugLogElement.innerHTML = `<span class="log-error">Error: ${error.message}</span>`;
        debugLogElement.className = 'whitespace-pre-wrap text-red-600 text-xs m-0';
    }
}

async function refreshBalance(traderId) {
  const fiatElement = document.getElementById(`fiat-balance-${traderId}`);
  const stablecoinElement = document.getElementById(`stablecoin-balance-${traderId}`);
  const cryptoElement = document.getElementById(`crypto-balance-${traderId}`);
  const fiatLabelElement = document.getElementById(`fiat-label-${traderId}`);
  const stablecoinLabelElement = document.getElementById(`stablecoin-label-${traderId}`);
  const cryptoLabelElement = document.getElementById(`crypto-label-${traderId}`);
  const priceElement = document.getElementById(`crypto-price-${traderId}`);
  const statusElement = document.getElementById(`status-${traderId}`);
  
  // Set loading state
  fiatElement.textContent = 'Loading...';
  stablecoinElement.textContent = 'Loading...';
  cryptoElement.textContent = 'Loading...';
  if (priceElement) priceElement.textContent = 'Loading...';
  
  try {
    const response = await fetch(`/balance/${traderId}`);
    const data = await response.json();
    
    if (response.ok) {
      const balance = data.balance;
      
      // Update labels with dynamic units
  fiatLabelElement.textContent = `FIAT - ${balance.fiat_unit}:`;
  stablecoinLabelElement.textContent = `Stablecoin - ${balance.stablecoin_unit}:`;
  cryptoLabelElement.innerHTML = `Crypto - <span class="font-bold">${balance.crypto_unit}</span>:`;
      
      // Update individual balances
      fiatElement.textContent = formatNumber(balance.fiat);
      stablecoinElement.textContent = formatNumber(balance.stablecoin);
      cryptoElement.textContent = formatNumber(balance.crypto);

      function formatNumber(val) {
        if (val === undefined || val === null || isNaN(val)) return '0.00';
        let num = typeof val === 'string' ? parseFloat(val.replace(/,/g, '')) : val;
        if (isNaN(num)) return val;
        return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 });
      }

      // Fetch price separately so balance errors don't mask price errors and vice versa
      if (priceElement) {
        priceElement.textContent = 'Loading...';
        fetch(`/price/${traderId}`)
          .then(r => r.json())
          .then(pd => {
            if (pd.price != null) {
              const formatted = pd.price.toLocaleString('en-US', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 8
              });
              priceElement.textContent = `${formatted} ${pd.quote_currency || ''}`;
            } else {
              priceElement.textContent = `Error: ${pd.error || 'unknown'}`;
            }
          })
          .catch(err => {
            priceElement.textContent = `Error: ${err.message}`;
            console.error(`Error fetching price for ${traderId}:`, err);
          });
      }
      
      statusElement.innerHTML = `
        <div class="flex items-center space-x-2">
          <div class="w-2 h-2 rounded-full bg-green-500"></div>
          <span class="text-green-700">Connected</span>
        </div>`;
    } else {
      throw new Error(data.error);
    }
  } catch (error) {
    fiatElement.textContent = 'Error';
    stablecoinElement.textContent = 'Error';
    cryptoElement.textContent = 'Error';
    if (priceElement) priceElement.textContent = 'Error';
    
    statusElement.innerHTML = `
      <div class="flex items-center space-x-2">
        <div class="w-2 h-2 rounded-full bg-red-500 animate-pulse"></div>
        <span class="text-red-700 font-semibold">ERROR (check logs)</span>
      </div>`;
    
    console.error(`Error refreshing balance for ${traderId}:`, error);
  }
}

// Function to refresh position data for stock traders
async function refreshPosition(traderId) {
  const quantityElement = document.getElementById(`position-quantity-${traderId}`);
  const priceElement = document.getElementById(`position-price-${traderId}`);
  const pnlElement = document.getElementById(`position-pnl-${traderId}`);
  const pnlPctElement = document.getElementById(`position-pnl-pct-${traderId}`);
  const cashElement = document.getElementById(`position-cash-${traderId}`);
  const marketStatusElement = document.getElementById(`market-status-${traderId}`);
  const canTradeElement = document.getElementById(`can-trade-${traderId}`);
  const marketOpensContainer = document.getElementById(`market-opens-container-${traderId}`);
  const marketOpensElement = document.getElementById(`market-opens-${traderId}`);
  const statusElement = document.getElementById(`status-${traderId}`);
  
  // Set loading state
  quantityElement.textContent = 'Loading...';
  priceElement.textContent = 'Loading...';
  pnlElement.textContent = 'Loading...';
  pnlPctElement.textContent = '';
  cashElement.textContent = 'Loading...';
  marketStatusElement.textContent = 'Loading...';
  canTradeElement.textContent = 'Loading...';
  
  try {
    const response = await fetch(`/position/${traderId}`);
    const data = await response.json();
    
    if (response.ok) {
      const { position, current_price, market } = data;
      
      // Update position data
      quantityElement.textContent = `${position.quantity} shares`;
      priceElement.textContent = current_price ? `$${current_price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : 'N/A';
      
      // Format P&L with color
      const pnl = position.unrealized_pnl;
      const pnlPct = position.unrealized_pnl_pct;
      const pnlColor = pnl >= 0 ? 'text-green-600' : 'text-red-600';
      const pnlSign = pnl >= 0 ? '+' : '';
      
      pnlElement.textContent = `${pnlSign}$${Math.abs(pnl).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      pnlElement.className = `text-sm font-bold ${pnlColor}`;
      pnlPctElement.textContent = ` (${pnlSign}${pnlPct.toFixed(2)}%)`;
      pnlPctElement.className = `text-xs ${pnlColor}`;
      
      // Update cash balance
      const cash = position.cash || 0;
      cashElement.textContent = `$${cash.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      
      // Update market status
      const statusIcons = {
        'open': '🟢',
        'closed': '⚫',
        'pre-market': '🟡',
        'after-hours': '🟡'
      };
      marketStatusElement.textContent = `${statusIcons[market.status] || ''} ${market.status.charAt(0).toUpperCase() + market.status.slice(1)}`;
      canTradeElement.textContent = market.can_trade ? '✅ Yes' : '❌ No';
      
      // Show/hide "Opens in" based on market status
      if (market.time_until_open) {
        marketOpensContainer.classList.remove('hidden');
        marketOpensContainer.classList.add('flex');
        marketOpensElement.textContent = `⏰ ${market.time_until_open}`;
      } else {
        marketOpensContainer.classList.add('hidden');
        marketOpensContainer.classList.remove('flex');
      }
      
      // Update connection status
      statusElement.innerHTML = `
        <div class="flex items-center space-x-2">
          <div class="w-2 h-2 rounded-full bg-green-500"></div>
          <span class="text-green-700">Connected</span>
        </div>`;
        
    } else {
      throw new Error(data.error);
    }
  } catch (error) {
    quantityElement.textContent = 'Error';
    priceElement.textContent = 'Error';
    pnlElement.textContent = 'Error';
    cashElement.textContent = 'Error';
    marketStatusElement.textContent = 'Error';
    canTradeElement.textContent = 'Error';
    
    statusElement.innerHTML = `
      <div class="flex items-center space-x-2">
        <div class="w-2 h-2 rounded-full bg-red-500 animate-pulse"></div>
        <span class="text-red-700 font-semibold">ERROR (check logs)</span>
      </div>`;
    
    console.error(`Error refreshing position for ${traderId}:`, error);
  }
}

// Updated function using modern event handling
async function convertFiatToStablecoin(traderId, event) {
  const button = event.currentTarget;
  const originalContent = button.innerHTML;
  
  // Show loading state
  button.innerHTML = `
    <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
    </svg>
  `;
  button.disabled = true;
  
  try {
    const response = await fetch(`/convert/${traderId}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    const data = await response.json();
    
    if (response.ok) {
      if (data.status === 'success') {
        // Show success message
        button.innerHTML = `
          <svg class="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
          </svg>
        `;
        
        // Refresh balance after successful conversion
        setTimeout(() => {
          refreshBalance(traderId);
        }, 1000);
        
      } else {
        // Show warning (no fiat to convert)
        alert(data.message);
      }
    } else {
      throw new Error(data.error || 'Conversion failed');
    }
    
  } catch (error) {
    console.error(`Error converting fiat for ${traderId}:`, error);
    // Don't add "Conversion failed:" prefix since the error message is already descriptive
    alert(error.message);
  } finally {
    // Restore original button after 2 seconds
    setTimeout(() => {
      button.innerHTML = originalContent;
      button.disabled = false;
    }, 2000);
  }
}


// Initial load and periodic refresh
document.addEventListener('DOMContentLoaded', () => {
  const traderIds = JSON.parse(document.getElementById('trader-ids').textContent);
  const traderTypes = JSON.parse(document.getElementById('trader-types').textContent);
  const traderTickers = JSON.parse(document.getElementById('trader-tickers').textContent);
  const logRefreshInterval = JSON.parse(document.getElementById('log-refresh-interval').textContent);
  const webhookPath = document.getElementById('webhook-path').textContent;

  // Show just the webhook path with placeholder for domain
  const webhookUrl = `https://[YOUR_TRADLEWARE_DOMAIN]/${webhookPath}`;
  
  // Update card webhook URL displays - show only the path part
  document.querySelectorAll('.webhook-url').forEach(el => {
    el.textContent = webhookPath;
  });
  
  // Update footer webhook URL displays - show full URL
  document.querySelectorAll('.footer-webhook-url').forEach(el => {
    el.textContent = webhookUrl;
  });
  
  // Set cURL example for each trader
  traderIds.forEach(traderId => {
    const curlPre = document.getElementById(`curl-example-${traderId}`);
    if (curlPre) {
      const ticker = traderTickers[traderId] || 'BTC/USDT';
      curlPre.textContent =
        `curl -X POST ${webhookUrl}?alert_name=MyStrategyAlert \\
  -H "Content-Type: application/json" \\
  -d '{\n    "api_key": "YOUR_BOT_TRADLEWARE_API_KEY",\n    "trader_id": "${traderId}",\n    "ticker": "${ticker}",\n    "action": "buy",\n    "timestamp": "'"$(date +%s)"'",\n    "alert_name": "MyStrategyAlertFromBody",\n    "order_size": 100,\n    "order_size_type": "percentage",\n    "dry_run": false\n  }'`;
    }
  });
  
  // Initial load - call appropriate refresh function based on trader type
  traderIds.forEach(traderId => {
    const traderType = traderTypes[traderId];
    if (traderType === 'stock') {
      refreshPosition(traderId);
    } else {
      refreshBalance(traderId);
    }
    refreshLogs(traderId);
  });
  
  // Set up periodic refresh for logs only
  setInterval(() => {
    traderIds.forEach(traderId => {
      refreshLogs(traderId);
    });
  }, logRefreshInterval); // Use configurable refresh interval from environment
});
