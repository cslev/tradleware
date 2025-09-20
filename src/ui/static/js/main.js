// Fix the toggle function for collapsible
function toggleCollapse(elementId) {
  const content = document.getElementById(elementId);
  const arrowId = elementId.includes('webhook') ? `arrow-webhook-${elementId.split('-')[1]}` : `arrow-${elementId.split('-')[1]}`;
  const arrow = document.getElementById(arrowId);
  
  content.classList.toggle('open');
  if (arrow) {
    arrow.style.transform = content.classList.contains('open') ? 'rotate(180deg)' : '';
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
                    if (log.includes('] ERROR:')) {
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
  const statusElement = document.getElementById(`status-${traderId}`);
  
  // Set loading state
  fiatElement.textContent = 'Loading...';
  stablecoinElement.textContent = 'Loading...';
  cryptoElement.textContent = 'Loading...';
  
  try {
    const response = await fetch(`/balance/${traderId}`);
    const data = await response.json();
    
    if (response.ok) {
      const balance = data.balance;
      
      // Update labels with dynamic units
      fiatLabelElement.textContent = `${balance.fiat_unit}:`;
      stablecoinLabelElement.textContent = `${balance.stablecoin_unit}:`;
      cryptoLabelElement.textContent = `${balance.crypto_unit}:`;
      
      // Update individual balances
      fiatElement.textContent = balance.fiat || '0.00';
      stablecoinElement.textContent = balance.stablecoin || '0.00';
      cryptoElement.textContent = balance.crypto || '0.00';
      
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
    
    statusElement.innerHTML = `
      <div class="flex items-center space-x-2">
        <div class="w-2 h-2 rounded-full bg-red-500"></div>
        <span class="text-red-700">Connection Error</span>
      </div>`;
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
  
  // Initial load
  traderIds.forEach(traderId => {
    refreshBalance(traderId);
    refreshLogs(traderId);
  });
  
  // Set up periodic refresh for logs only
  setInterval(() => {
    traderIds.forEach(traderId => {
      refreshLogs(traderId);
    });
  }, 5000); // Refresh every 5 seconds
});