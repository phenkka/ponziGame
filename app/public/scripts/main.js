// Initialize Web3Lib and SPLLite from window.solanaWeb3
(function(){
  if (window.solanaWeb3) {
    const { Connection, PublicKey, Transaction, TransactionInstruction, SystemProgram } = window.solanaWeb3;
    window.Web3Lib = { Connection, PublicKey, Transaction, TransactionInstruction, SystemProgram };
    const TOKEN_PROGRAM_ID = new PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
    const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL');
    window.SPLLite = {
      TOKEN_PROGRAM_ID,
      ASSOCIATED_TOKEN_PROGRAM_ID,
      getAssociatedTokenAddress: async (mint, owner) => {
        const [addr] = await PublicKey.findProgramAddress(
          [owner.toBuffer(), TOKEN_PROGRAM_ID.toBuffer(), mint.toBuffer()],
          ASSOCIATED_TOKEN_PROGRAM_ID
        );
        return addr;
      },
      createTransferCheckedInstruction: (source, mint, destination, owner, amountRaw, decimals) => {
        const data = new Uint8Array(1 + 8 + 1);
        data[0] = 12;
        let n = BigInt(Math.floor(amountRaw));
        for (let i = 0; i < 8; i++) { data[1 + i] = Number(n & 0xffn); n >>= 8n; }
        data[9] = decimals;
        return new TransactionInstruction({
          programId: TOKEN_PROGRAM_ID,
          keys: [
            { pubkey: source, isSigner: false, isWritable: true },
            { pubkey: mint, isSigner: false, isWritable: false },
            { pubkey: destination, isSigner: false, isWritable: true },
            { pubkey: owner, isSigner: true, isWritable: false },
          ],
          data
        });
      }
    };
  }
})();

// Alternative initialization for newer versions
if (!window.Web3Lib && window.solanaWeb3) {
  const { Connection, PublicKey, Transaction, TransactionInstruction, SystemProgram } = window.solanaWeb3;
  window.Web3Lib = { Connection, PublicKey, Transaction, TransactionInstruction, SystemProgram };
}

// Polyfill for Buffer in browser environment
if (typeof Buffer === 'undefined') {
  window.Buffer = {
    from: (data, encoding) => {
      if (typeof data === 'string') {
        return new TextEncoder().encode(data);
      }
      return new Uint8Array(data);
    },
    isBuffer: (obj) => {
      return obj instanceof Uint8Array;
    }
  };
}

// ---------------------- App Logic moved from index.html ----------------------
// Global variables
let currentWallet = null;
let currentUser = null;
const LAST_PAGE_KEY = 'lastPage';

function getPendingReferralCode() {
  try {
    return localStorage.getItem('refSource');
  } catch {
    return null;
  }
}

function clearPendingReferralCode() {
  try {
    localStorage.removeItem('refSource');
  } catch {}
}

function buildAuthPayload(wallet, signature, message) {
  const payload = { wallet, signature, message };
  const pending = getPendingReferralCode();
  if (pending) payload.referrerCode = pending;
  return payload;
}

async function requestAuthChallenge(wallet) {
  const res = await fetch('/api/auth/challenge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ wallet })
  });
  const data = await res.json();
  if (!data || !data.success || !data.message) {
    throw new Error((data && data.error) ? data.error : 'Failed to get auth challenge');
  }
  return data.message;
}

async function performAuth(publicKey) {
  const message = await requestAuthChallenge(publicKey);
  const signature = await signMessage(message, publicKey);
  const response = await fetch('/api/auth', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildAuthPayload(publicKey, signature.signature, message))
  });
  return await response.json();
}

async function dynamicImport(urls) {
  for (const u of urls) {
    try { return await import(u); } catch (e) { /* try next */ }
  }
  throw new Error('Failed to load modules');
}

function checkPhantomInstalled() {
  return !!(window.solana && window.solana.isPhantom);
}

async function connectPhantom() {
  if (!checkPhantomInstalled()) {
    throw new Error('Phantom wallet is not installed. Please install the Phantom extension.');
  }
  const response = await window.solana.connect();
  return response.publicKey.toString();
}

async function signMessage(message, publicKey) {
  const encodedMessage = new TextEncoder().encode(message);
  const signature = await window.solana.signMessage(encodedMessage);
  return { signature: Array.from(signature.signature), publicKey };
}

function showMessage(text, type = 'error') {
  const messageDiv = document.getElementById('message');
  if (!messageDiv) return;
  messageDiv.innerHTML = `<div class="message ${type}">${text}</div>`;
  messageDiv.classList.remove('hidden');
  
  // Auto-hide message after 5 seconds
  setTimeout(() => {
    hideMessage();
  }, 5000);
}

function hideMessage() {
  const el = document.getElementById('message');
  if (el) el.classList.add('hidden');
}

function showUserInfo(wallet, refCode, opts = {}) {
  const { redirect = false } = opts;
  currentWallet = wallet;
  currentUser = { wallet, refCode };
  try {
    sessionStorage.setItem('wallet', wallet);
    if (refCode) localStorage.setItem('refCode', refCode);
  } catch {}
  document.getElementById('user-section')?.classList.remove('hidden');
  document.getElementById('auth-button-section')?.classList.add('hidden');
  const addrShort = document.getElementById('wallet-address-short');
  if (addrShort) addrShort.textContent = `${wallet.slice(0, 4)}...${wallet.slice(-4)}`;
  const ref = document.getElementById('ref-code-info');
  if (ref) ref.textContent = refCode;
  // loadBalance(wallet); // Закомментировано - скрываем баланс
  loadUserChests(wallet);
  const bal = document.getElementById('balance');
  if (bal) {
    const h = () => copyReferralLink(refCode);
    bal.addEventListener('click', h, { once: true });
  }
  updatePackButtons();
  if (redirect) {
    // Redirect to profile page after successful auth
    window.location.href = '/profile';
  }
}

async function loadHomeCollections() {
  try {
    const groups = [
      { rarity: 'basic', grid: document.getElementById('basic-grid'), title: 'BASIC' },
      { rarity: 'rare', grid: document.getElementById('rare-grid'), title: 'RARE' },
      { rarity: 'epic', grid: document.getElementById('epic-grid'), title: 'EPIC' },
      { rarity: 'legendary', grid: document.getElementById('legendary-grid'), title: 'LEGENDARY' }
    ];
    let totalHeightBefore = 0;
    const container = document.getElementById('home-collections');
    if (container) totalHeightBefore = container.scrollHeight;
    for (const { rarity, grid, title } of groups) {
      if (!grid) continue;
      grid.innerHTML = '';
      const r = await fetch(`/api/cards?rarity=${rarity}&hasImage=true`);
      const d = await r.json();
      const arr = (d && d.cards) ? d.cards : [];
      arr.forEach(card => {
        const url = card.image_url || '';
        if (!url) return;
        const el = document.createElement('div');
        el.className = 'legendary-card';
        el.innerHTML = `<img src="${url}" alt="${title}"><div class="legendary-card-title">${title}</div>`;
        grid.appendChild(el);
      });
    }
    // If section is open, recompute maxHeight so it fully expands
    const coll = document.getElementById('home-collections');
    if (coll && coll.classList.contains('open')) {
      // reset first to recalc
      coll.style.maxHeight = '0px';
      void coll.offsetHeight;
      coll.style.maxHeight = coll.scrollHeight + 'px';
    }
  } catch {}
}

function showAuthSection() {
  currentWallet = null;
  currentUser = null;
  updatePackButtons();
  try {
    sessionStorage.removeItem('wallet');
    localStorage.removeItem('refCode');
  } catch {}
  const balanceElement = document.getElementById('balance');
  if (balanceElement && balanceElement.parentNode) {
    const newBalanceElement = balanceElement.cloneNode(true);
    balanceElement.parentNode.replaceChild(newBalanceElement, balanceElement);
  }
  document.getElementById('welcome-section')?.classList.remove('hidden');
  document.getElementById('dashboard')?.classList.add('hidden');
  document.getElementById('user-section')?.classList.add('hidden');
  document.getElementById('auth-button-section')?.classList.remove('hidden');
  
  hideMessage();
  showPage('home');
}

async function loadBalance(wallet) {
  try {
    const response = await fetch(`/api/balance/${wallet}`);
        const data = await response.json();
        if (data.success) {
      const balance = data.balance || { amount: 0, decimals: 0, symbol: 'TOKENS' };
      const amount = Number(balance.amount || 0);
      const symbol = balance.symbol || 'TOKENS';
      const text = `${amount.toFixed(4)} ${symbol}`;
      const balEl = document.getElementById('balance');
      const infoEl = document.getElementById('balance-info');
      if (balEl) balEl.textContent = text;
      if (infoEl) infoEl.textContent = text;
    }
  } catch {
    const balEl = document.getElementById('balance');
    const infoEl = document.getElementById('balance-info');
    if (balEl) balEl.textContent = '0.0000 TOKENS';
    if (infoEl) infoEl.textContent = '0.0000 TOKENS';
  }
}

async function copyReferralLink(refCode) {
  const referralLink = `${window.location.origin}/ref/${refCode}`;
  try {
    await navigator.clipboard.writeText(referralLink);
    showMessage(`Referral link copied: ${referralLink}`, 'success');
  } catch {
    const textArea = document.createElement('textarea');
    textArea.value = referralLink;
    document.body.appendChild(textArea);
    textArea.select();
    document.execCommand('copy');
    document.body.removeChild(textArea);
    showMessage(`Referral link copied: ${referralLink}`, 'success');
  }
}

function showPage(pageId) {
  // Check authentication status FIRST
  let hasSession = !!currentWallet;
  try { if (!hasSession) hasSession = !!sessionStorage.getItem('wallet'); } catch {}
  
  // STRICT access control rules:
  // - Unauthorized: ONLY home page allowed
  // - Authorized: all pages except home
  if (!hasSession) {
    if (pageId !== 'home') {
      showMessage('Please connect your Phantom wallet first');
      pageId = 'home';
      // Force clear any non-home page from storage
      try { localStorage.setItem(LAST_PAGE_KEY, 'home'); } catch {}
    }
  }
  
  // Persist page after validation
  try { localStorage.setItem(LAST_PAGE_KEY, pageId); } catch {}
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.getElementById(`${pageId}-page`)?.classList.remove('hidden');
  // Mark body with current page for CSS-level guards
  try {
    document.body.classList.remove('page-home','page-shop','page-cards','page-battle','page-rules','page-profile');
    document.body.classList.add(`page-${pageId}`);
  } catch {}
  
  // Initialize battle button when on battle page
  if (pageId === 'battle') {
    setTimeout(initializeBattleButton, 100); // Small delay to ensure DOM is updated
  }
  const homeOnlyIds = ['hero-section', 'rtj-section', 'lastj-section', 'packs-section', 'story-banner', 'rules-section', 'footer'];
  const extraHome = document.getElementById('home-collections');
  const toggleWrap = document.getElementById('collections-toggle-wrap');
  if (extraHome && toggleWrap) {
    if (pageId === 'home') {
      // Show toggle, keep collection collapsed and hidden until user expands
      toggleWrap.classList.remove('hidden');
      extraHome.classList.add('hidden');
      extraHome.classList.remove('open');
    } else {
      // Hide completely on non-home pages
      toggleWrap.classList.add('hidden');
      extraHome.classList.add('hidden');
      extraHome.classList.remove('open');
    }
  }
  homeOnlyIds.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    // All home-only elements: show only on home page (same logic as rules-section)
    if (pageId === 'home') el.classList.remove('hidden'); else el.classList.add('hidden');
  });
  
  // Show/hide navigation buttons only on home page
  const headerNav = document.querySelector('.header-nav');
  if (headerNav) {
    if (pageId === 'home') {
      headerNav.style.display = 'flex';
    } else {
      headerNav.style.display = 'none';
    }
  }
  const headerEl = document.querySelector('.header');
  if (headerEl) {
    if (pageId === 'shop') headerEl.classList.add('header-shop');
    else headerEl.classList.remove('header-shop');
  }
  if (pageId === 'chests') loadChests();
  if (pageId === 'home') { loadPacks(); loadHomeCollections(); loadJackpot(); loadSuperJackpot(); }
  if (pageId === 'shop') { loadPacks(true); loadSuperJackpot(); }
  if (pageId === 'rules') { loadJackpot(); loadSuperJackpot(); }
  if (pageId === 'refferal') { loadJackpot(); loadSuperJackpot(); loadReferral(); }
  if (pageId === 'battle') { loadJackpot(); loadSuperJackpot(); }
  if (pageId === 'predict') { loadJackpot(); loadSuperJackpot(); }
  if (pageId === 'cards' && (currentWallet || hasSession)) { loadJackpot(); loadSuperJackpot(); loadUserChests(currentWallet || localStorage.getItem('wallet')); loadUserCards(currentWallet || localStorage.getItem('wallet')); loadMyPacks(currentWallet || localStorage.getItem('wallet')); }
}

// Setup handlers and initial routing
function initialRoute() {
  // Always land on home when loading root. Access control inside showPage will gate navigation.
    try { localStorage.setItem(LAST_PAGE_KEY, 'home'); } catch {}
  showPage('home');
}

document.addEventListener('DOMContentLoaded', () => {
  // Check auth status immediately and redirect if needed
  const storedWallet = sessionStorage.getItem('wallet');
  const hasSession = !!storedWallet;
  
  // If unauthorized and not on home, force redirect
  if (!hasSession) {
    const currentPage = window.location.pathname.split('/').pop().replace('.html', '');
    if (currentPage && currentPage !== 'index' && currentPage !== '') {
      // Force redirect to home for unauthorized users
      window.location.href = '/';
        return;
    }
  }
  
  // Route immediately to avoid flashing the wrong page
  initialRoute();
  
  // Setup scroll animations for jackpot blocks
  setupJackpotAnimations();
  
  // Setup scroll animations for How to Play section
  setupHowToPlayAnimations();
  
  // Setup scroll animations for Distribution section
  setupDistributionAnimations();
  
  // Export functions to global scope for use in individual pages
  window.showUserInfo = showUserInfo;
  window.loadPacks = loadPacks;
  window.loadJackpot = loadJackpot;
  window.loadSuperJackpot = loadSuperJackpot;

  const authBtn = document.getElementById('authButtonHeader');
  if (authBtn) {
    authBtn.addEventListener('click', async () => {
      const button = authBtn;
      button.disabled = true;
      button.textContent = 'Connecting...';
      hideMessage();
      try {
        const publicKey = await connectPhantom();
        
        // Check whitelist status and entry requirements
        const wlResponse = await fetch(`/api/whitelist/${publicKey}`);
        const wlData = await wlResponse.json();
        
        if (wlData.success) {
          if (wlData.hasAccess) {
            // User already has access, proceed with normal auth
            const data = await performAuth(publicKey);
            if (data.success) {
              clearPendingReferralCode();
              showUserInfo(publicKey, data.refCode, { redirect: true });
              window.location.href = '/profile';
            } else {
              showMessage(data.error || 'Authentication error');
            }
          } else {
            // User needs to pay for entry
            showEntryModal(publicKey, wlData);
          }
        } else {
          showMessage('Failed to check entry status');
        }
      } catch (error) { console.error('Auth error:', error); showMessage(error.message || 'Wallet connection error'); }
      finally { button.disabled = false; button.textContent = 'CONNECT WALLET'; }
    });
  }

  // Hero connect wallet button
  const heroConnectBtn = document.getElementById('hero-connect-button');
  if (heroConnectBtn) {
    heroConnectBtn.addEventListener('click', async () => {
      const button = heroConnectBtn;
      button.disabled = true;
      button.textContent = 'Connecting...';
      hideMessage();
      try {
        const publicKey = await connectPhantom();
        
        // Check whitelist status and entry requirements
        const wlResponse = await fetch(`/api/whitelist/${publicKey}`);
        const wlData = await wlResponse.json();
        
        if (wlData.success) {
          if (wlData.hasAccess) {
            // User already has access, proceed with normal auth
            const data = await performAuth(publicKey);
            if (data.success) {
              clearPendingReferralCode();
              showUserInfo(publicKey, data.refCode, { redirect: true });
              window.location.href = '/profile';
            } else {
              showMessage(data.error || 'Authentication error');
            }
          } else {
            // User needs to pay for entry
            showEntryModal(publicKey, wlData);
          }
        } else {
          showMessage('Failed to check entry status');
        }
      } catch (error) { console.error('Auth error:', error); showMessage(error.message || 'Wallet connection error'); }
      finally { button.disabled = false; button.textContent = 'CONNECT WALLET'; }
    });
  }

  // Hero pack connect wallet button
  const heroPackConnectBtn = document.getElementById('hero-pack-connect-button');
  if (heroPackConnectBtn) {
    heroPackConnectBtn.addEventListener('click', async () => {
      const button = heroPackConnectBtn;
      button.disabled = true;
      button.textContent = 'Connecting...';
      hideMessage();
      try {
        const publicKey = await connectPhantom();
        
        // Check whitelist status and entry requirements
        const wlResponse = await fetch(`/api/whitelist/${publicKey}`);
        const wlData = await wlResponse.json();
        
        if (wlData.success) {
          if (wlData.hasAccess) {
            // User already has access, proceed with normal auth
            const data = await performAuth(publicKey);
            if (data.success) {
              clearPendingReferralCode();
              showUserInfo(publicKey, data.refCode, { redirect: true });
              window.location.href = '/profile';
            } else {
              showMessage(data.error || 'Authentication error');
            }
          } else {
            // User needs to pay for entry
            showEntryModal(publicKey, wlData);
          }
        } else {
          showMessage('Failed to check entry status');
        }
      } catch (error) { console.error('Auth error:', error); showMessage(error.message || 'Wallet connection error'); }
      finally { button.disabled = false; button.textContent = 'OPEN FIRST PACK'; }
    });
  }

  // How to play connect wallet button
  const howToPlayConnectBtn = document.getElementById('how-to-play-connect-button');
  if (howToPlayConnectBtn) {
    howToPlayConnectBtn.addEventListener('click', async () => {
      const button = howToPlayConnectBtn;
      button.disabled = true;
      button.textContent = 'Connecting...';
      hideMessage();
      try {
        const publicKey = await connectPhantom();
        
        // Check whitelist status and entry requirements
        const wlResponse = await fetch(`/api/whitelist/${publicKey}`);
        const wlData = await wlResponse.json();
        
        if (wlData.success) {
          if (wlData.hasAccess) {
            // User already has access, proceed with normal auth
            const data = await performAuth(publicKey);
            if (data.success) {
              clearPendingReferralCode();
              showUserInfo(publicKey, data.refCode, { redirect: true });
              window.location.href = '/profile';
            } else {
              showMessage(data.error || 'Authentication error');
            }
          } else {
            // User needs to pay for entry
            showEntryModal(publicKey, wlData);
          }
        } else {
          showMessage('Failed to check entry status');
        }
      } catch (error) { console.error('Auth error:', error); showMessage(error.message || 'Wallet connection error'); }
      finally { button.disabled = false; button.textContent = 'CONNECT'; }
    });
  }

  // User dropdown functionality - ЗАКОММЕНТИРОВАНО: скрываем раскрытие кошелька и баланс
  // const userDropdownButton = document.getElementById('userDropdownButton');
  // const userDropdown = document.querySelector('.user-dropdown');
  // 
  // if (userDropdownButton && userDropdown) {
  //   userDropdownButton.addEventListener('click', (e) => {
  //     e.stopPropagation();
  //     userDropdown.classList.toggle('open');
  //   });
  // }
  // 
  // // Close dropdown when clicking outside
  // document.addEventListener('click', (e) => {
  //   if (userDropdown && !userDropdown.contains(e.target)) {
  //     userDropdown.classList.remove('open');
  //   }
  // });

  const logoutBtn = document.getElementById('logoutButton');
  if (logoutBtn) logoutBtn.addEventListener('click', () => {
    showAuthSection();
    // Redirect to home page after logout
    window.location.href = '/';
  });

  // Add navigation functionality for header buttons
  const howItWorksButton = document.getElementById('howItWorksButton');
  const rulesButton = document.getElementById('rulesButton');
  
  if (howItWorksButton) {
    howItWorksButton.addEventListener('click', () => {
      // Scroll to How to Play section
      const howToPlaySection = document.getElementById('how-to-play-section');
      if (howToPlaySection) {
        howToPlaySection.scrollIntoView({ behavior: 'smooth' });
      }
    });
  }
  
  if (rulesButton) {
    rulesButton.addEventListener('click', () => {
      // Scroll to Distribution Rules section
      const distributionSection = document.getElementById('distribution-section');
      if (distributionSection) {
        distributionSection.scrollIntoView({ behavior: 'smooth' });
      }
    });
  }
  document.querySelectorAll('.shop-menu-item').forEach(item => item.addEventListener('click', (e) => { 
    e.preventDefault(); 
    const page = item.getAttribute('data-page'); 
    
    // Check authorization before allowing navigation
    let hasSession = !!currentWallet;
    try { if (!hasSession) hasSession = !!sessionStorage.getItem('wallet'); } catch {}
    
    if (!hasSession) {
      showMessage('Please connect your Phantom wallet first');
      window.location.href = '/';
      return;
    }
    
    // Navigate to separate pages instead of showing pages within index.html
    switch(page) {
      case 'shop':
        window.location.href = '/shop';
        break;
      case 'cards':
        window.location.href = '/cards';
        break;
      case 'battle':
        window.location.href = '/battle';
        break;
      // Profile page disabled temporarily
      // case 'profile':
      //   window.location.href = '/profile';
      //   break;
      case 'rules':
        window.location.href = '/rules';
        break;
      default:
        window.location.href = '/';
    }
  }));
  window.addEventListener('load', () => {
    hideMessage();
    const urlParams = new URLSearchParams(window.location.search);
    const refCode = urlParams.get('ref');
    if (refCode) {
      const normalizedRef = refCode.trim().toUpperCase();
      if (normalizedRef) {
        try {
          localStorage.setItem('refSource', normalizedRef);
          localStorage.setItem('refCode', normalizedRef);
        } catch {}
        showMessage('Referral code saved! It will be used automatically on sign-in.', 'success');
      }
    }
    const storedWallet = sessionStorage.getItem('wallet');
    if (storedWallet) {
      fetch(`/api/user/${storedWallet}`).then(r => r.json()).then(d => { if (d && d.success) showUserInfo(storedWallet, d.user.ref_code, { redirect: false }); }).catch(() => {});
    }
    loadJackpot();
    loadSuperJackpot();
    const toggleBtn = document.getElementById('toggle-collections-btn');
    const coll = document.getElementById('home-collections');
    if (toggleBtn && coll) {
      toggleBtn.addEventListener('click', () => {
        const isOpen = coll.classList.contains('open');
        if (isOpen) {
          coll.style.maxHeight = '0px';
          coll.classList.remove('open');
          toggleBtn.textContent = 'SHOW CARD COLLECTION';
        } else {
          coll.classList.remove('hidden');
          // force reflow for transition
          void coll.offsetHeight;
          coll.classList.add('open');
          coll.style.maxHeight = coll.scrollHeight + 'px';
          toggleBtn.textContent = 'HIDE CARD COLLECTION';
        }
      });
    }
    // If we landed on shop/home, also load packs for that page
    const lp = localStorage.getItem(LAST_PAGE_KEY) || 'home';
    if (lp === 'shop') loadPacks(true); else if (lp === 'home') loadPacks();
    
    // MPA mode - load content based on current page
    const currentPage = window.location.pathname.split('/').pop().replace('.html', '');
    if (currentPage === 'rules') {
        // Load jackpot for rules page
        const amountElRules = document.getElementById('rtj-amount-rules');
        const timerElRules = document.getElementById('rtj-timer-rules');
        if (amountElRules && timerElRules) {
            loadJackpot();
            loadSuperJackpot();
        }
    }
  });
});

let jackpotTimerInterval = null;
let superJackpotUpdateInterval = null;

async function loadJackpot() {
    console.log('=== loadJackpot() function called ===');
    try {
    const r = await fetch('/api/jackpot');
    const data = await r.json();
    if (data && data.success) {
      const amountText = `${data.jackpot} $TOKENS`;
      const timerElHome = document.getElementById('rtj-timer');
      const timerElShop = document.getElementById('rtj-timer-shop');
      const timerElRules = document.getElementById('rtj-timer-rules');
      const timerElRef = document.getElementById('rtj-timer-ref');
      const timerElCards = document.getElementById('rtj-timer-cards');
      const timerElBattle = document.getElementById('rtj-timer-battle');
      const timerElPredict = document.getElementById('rtj-timer-predict');
      const amountElHome = document.getElementById('rtj-amount');
      const amountElShop = document.getElementById('rtj-amount-shop');
      const amountElRules = document.getElementById('rtj-amount-rules');
      const amountElRef = document.getElementById('rtj-amount-ref');
      const amountElCards = document.getElementById('rtj-amount-cards');
      const amountElBattle = document.getElementById('rtj-amount-battle');
      const amountElPredict = document.getElementById('rtj-amount-predict');
      if (amountElHome) amountElHome.textContent = amountText;
      if (amountElShop) amountElShop.textContent = amountText;
      if (amountElRules) amountElRules.textContent = amountText;
      if (amountElRef) amountElRef.textContent = amountText;
      if (amountElCards) amountElCards.textContent = amountText;
      if (amountElBattle) amountElBattle.textContent = amountText;
      if (amountElPredict) amountElPredict.textContent = amountText;
      
      // Load last jackpot data
      console.log('Loading last jackpot data...');
      const lastJackpotRes = await fetch('/api/jackpot/last');
      const lastJackpotData = await lastJackpotRes.json();
      console.log('Last jackpot data:', lastJackpotData);
      
      let lastAmountText = '0 $TOKENS';
      let lastDateText = '-';
      
      if (lastJackpotData && lastJackpotData.success && lastJackpotData.lastJackpot) {
        console.log('Last jackpot found:', lastJackpotData.lastJackpot);
        // Prize/amount fallback handling
        const lj = lastJackpotData.lastJackpot;
        const prizeVal = lj.prize ?? lj.amount ?? lj.value ?? 0;
        lastAmountText = `${Math.round(prizeVal)} $TOKENS`;
        const date = new Date(lastJackpotData.lastJackpot.date);
        lastDateText = date.toLocaleDateString('ru-RU', { 
          day: '2-digit', 
          month: '2-digit', 
          year: 'numeric' 
        });
        
        // Determine winner wallet with robust key fallback
        const winnerWalletValue = lj.winnerWallet || lj.winner_wallet || lj.winner || lj.wallet || '';

        // Show winner info on all pages
        const winnerInfoEls = document.querySelectorAll('[id^="lastj-winner-"]');
        console.log('Winner info elements found:', winnerInfoEls.length);
        winnerInfoEls.forEach(el => {
          console.log('Showing winner element:', el.id);
          el.style.display = 'block';
        });
        
        // Update winner wallet on all pages
        const winnerWalletEls = document.querySelectorAll('[id^="lastj-winner-wallet-"]');
        console.log('Winner wallet elements found:', winnerWalletEls.length);
        winnerWalletEls.forEach(el => {
          console.log('Updating wallet element:', el.id, 'with:', winnerWalletValue);
          el.textContent = winnerWalletValue;
        });
        
        // Also check for specific elements on home page
        const homeWinnerEl = document.getElementById('lastj-winner');
        const homeWinnerWalletEl = document.getElementById('lastj-winner-wallet');
        console.log('Home page winner element:', homeWinnerEl);
        console.log('Home page winner wallet element:', homeWinnerWalletEl);
        if (homeWinnerEl) {
          console.log('Setting home winner element display to block');
          homeWinnerEl.style.display = 'block';
        }
        if (homeWinnerWalletEl) {
          console.log('Setting home winner wallet text to:', winnerWalletValue);
          homeWinnerWalletEl.textContent = winnerWalletValue;
        }
      } else {
        console.log('No last jackpot data found or API error');
      }
      
      // Update last jackpot on all pages
      const lastAmountElHome = document.getElementById('lastj-amount');
      const lastAmountElShop = document.getElementById('lastj-amount-shop');
      const lastAmountElRules = document.getElementById('lastj-amount-rules');
      const lastAmountElRef = document.getElementById('lastj-amount-ref');
      const lastAmountElCards = document.getElementById('lastj-amount-cards');
      const lastAmountElBattle = document.getElementById('lastj-amount-battle');
      const lastAmountElPredict = document.getElementById('lastj-amount-predict');
      
      const lastDateElHome = document.getElementById('lastj-date');
      const lastDateElShop = document.getElementById('lastj-date-shop');
      const lastDateElRules = document.getElementById('lastj-date-rules');
      const lastDateElRef = document.getElementById('lastj-date-ref');
      const lastDateElCards = document.getElementById('lastj-date-cards');
      const lastDateElBattle = document.getElementById('lastj-date-battle');
      const lastDateElPredict = document.getElementById('lastj-date-predict');
      
      if (lastAmountElHome) lastAmountElHome.textContent = lastAmountText;
      if (lastAmountElShop) lastAmountElShop.textContent = lastAmountText;
      if (lastAmountElRules) lastAmountElRules.textContent = lastAmountText;
      if (lastAmountElRef) lastAmountElRef.textContent = lastAmountText;
      if (lastAmountElCards) lastAmountElCards.textContent = lastAmountText;
      if (lastAmountElBattle) lastAmountElBattle.textContent = lastAmountText;
      if (lastAmountElPredict) lastAmountElPredict.textContent = lastAmountText;
      
      if (lastDateElHome) lastDateElHome.textContent = lastDateText;
      if (lastDateElShop) lastDateElShop.textContent = lastDateText;
      if (lastDateElRules) lastDateElRules.textContent = lastDateText;
      if (lastDateElRef) lastDateElRef.textContent = lastDateText;
      if (lastDateElCards) lastDateElCards.textContent = lastDateText;
      if (lastDateElBattle) lastDateElBattle.textContent = lastDateText;
      if (lastDateElPredict) lastDateElPredict.textContent = lastDateText;
      
      if (jackpotTimerInterval) clearInterval(jackpotTimerInterval);
      const endsAt = new Date(data.endsAt);
      const updateTimer = async () => {
            const now = new Date();
        const diff = Math.max(0, endsAt.getTime() - now.getTime());
        const h = Math.floor(diff / 3600000);
        const m = Math.floor((diff % 3600000) / 60000);
        const s = Math.floor((diff % 60000) / 1000);
        const timerText = `Time Left: ${h.toString().padStart(2,'0')}:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}`;
        if (timerElHome) timerElHome.textContent = timerText;
        if (timerElShop) timerElShop.textContent = timerText;
        if (timerElRules) timerElRules.textContent = timerText;
        if (timerElRef) timerElRef.textContent = timerText;
        if (timerElCards) timerElCards.textContent = timerText;
        if (timerElBattle) timerElBattle.textContent = timerText;
        if (timerElPredict) timerElPredict.textContent = timerText;
        if (diff <= 0) { clearInterval(jackpotTimerInterval); await drawJackpot(); await loadJackpot(); }
      };
      updateTimer();
      jackpotTimerInterval = setInterval(updateTimer, 1000);
      
      // Load super jackpot
      loadSuperJackpot();
      
      // Обновляем супер джекпот каждые 30 секунд (как обычный джекпот обновляется)
      if (superJackpotUpdateInterval) {
        clearInterval(superJackpotUpdateInterval);
      }
      superJackpotUpdateInterval = setInterval(() => {
        loadSuperJackpot();
      }, 30000); // Обновляем каждые 30 секунд
        } else {
      const amountErr = 'Failed to load jackpot';
      const timerErr = 'Time Left: --:--:--';
      const amountElHome2 = document.getElementById('rtj-amount');
      const amountElShop2 = document.getElementById('rtj-amount-shop');
      const amountElRules2 = document.getElementById('rtj-amount-rules');
      const amountElRef2 = document.getElementById('rtj-amount-ref');
      const amountElCards2 = document.getElementById('rtj-amount-cards');
      const amountElBattle2 = document.getElementById('rtj-amount-battle');
      const amountElPredict2 = document.getElementById('rtj-amount-predict');
      const timerElHome2 = document.getElementById('rtj-timer');
      const timerElShop2 = document.getElementById('rtj-timer-shop');
      const timerElRules2 = document.getElementById('rtj-timer-rules');
      const timerElRef2 = document.getElementById('rtj-timer-ref');
      const timerElCards2 = document.getElementById('rtj-timer-cards');
      const timerElBattle2 = document.getElementById('rtj-timer-battle');
      const timerElPredict2 = document.getElementById('rtj-timer-predict');
      if (amountElHome2) amountElHome2.textContent = amountErr;
      if (amountElShop2) amountElShop2.textContent = amountErr;
      if (amountElRules2) amountElRules2.textContent = amountErr;
      if (amountElRef2) amountElRef2.textContent = amountErr;
      if (amountElCards2) amountElCards2.textContent = amountErr;
      if (amountElBattle2) amountElBattle2.textContent = amountErr;
      if (amountElPredict2) amountElPredict2.textContent = amountErr;
      if (timerElHome2) timerElHome2.textContent = timerErr;
      if (timerElShop2) timerElShop2.textContent = timerErr;
      if (timerElRules2) timerElRules2.textContent = timerErr;
      if (timerElRef2) timerElRef2.textContent = timerErr;
      if (timerElCards2) timerElCards2.textContent = timerErr;
      if (timerElBattle2) timerElBattle2.textContent = timerErr;
      if (timerElPredict2) timerElPredict2.textContent = timerErr;
    }
  } catch (e) {
    const amountElHome3 = document.getElementById('rtj-amount');
    const amountElShop3 = document.getElementById('rtj-amount-shop');
    const amountElRules3 = document.getElementById('rtj-amount-rules');
    const amountElRef3 = document.getElementById('rtj-amount-ref');
    const amountElCards3 = document.getElementById('rtj-amount-cards');
    const amountElBattle3 = document.getElementById('rtj-amount-battle');
    const amountElPredict3 = document.getElementById('rtj-amount-predict');
    const timerElHome3 = document.getElementById('rtj-timer');
    const timerElShop3 = document.getElementById('rtj-timer-shop');
    const timerElRules3 = document.getElementById('rtj-timer-rules');
    const timerElRef3 = document.getElementById('rtj-timer-ref');
    const timerElCards3 = document.getElementById('rtj-timer-cards');
    const timerElBattle3 = document.getElementById('rtj-timer-battle');
    const timerElPredict3 = document.getElementById('rtj-timer-predict');
    if (amountElHome3) amountElHome3.textContent = 'Load error';
    if (amountElShop3) amountElShop3.textContent = 'Load error';
    if (amountElRules3) amountElRules3.textContent = 'Load error';
    if (amountElRef3) amountElRef3.textContent = 'Load error';
    if (amountElCards3) amountElCards3.textContent = 'Load error';
    if (amountElBattle3) amountElBattle3.textContent = 'Load error';
    if (amountElPredict3) amountElPredict3.textContent = 'Load error';
    if (timerElHome3) timerElHome3.textContent = 'Time Left: --:--:--';
    if (timerElShop3) timerElShop3.textContent = 'Time Left: --:--:--';
    if (timerElRules3) timerElRules3.textContent = 'Time Left: --:--:--';
    if (timerElRef3) timerElRef3.textContent = 'Time Left: --:--:--';
    if (timerElCards3) timerElCards3.textContent = 'Time Left: --:--:--';
    if (timerElBattle3) timerElBattle3.textContent = 'Time Left: --:--:--';
    if (timerElPredict3) timerElPredict3.textContent = 'Time Left: --:--:--';
  }
}

async function loadDailyCheckin() {
  try {
    const wallet = currentWallet || sessionStorage.getItem('wallet') || localStorage.getItem('wallet');
    if (!wallet) return;
    
    // Пробуем получить заголовки авторизации, если нет - используем cookie
    const headers = {};
    const signature = sessionStorage.getItem('signature') || '';
    const message = sessionStorage.getItem('message') || '';
    
    if (signature && message) {
      headers['X-Wallet'] = wallet;
      headers['X-Signature'] = signature;
      headers['X-Message'] = message;
    }
    
    const r = await fetch(`/api/daily-checkin/status/${wallet}`, {
      headers: headers,
      credentials: 'include' // Важно для отправки cookies
    });
    const d = await r.json();
    if (!d || !d.success) return;
    
    // Обновляем UI
    const consecutiveDaysEl = document.getElementById('consecutive-days');
    const checkinButton = document.getElementById('checkin-button');
    const checkinMessage = document.getElementById('checkin-message');
    const checkinInput = document.getElementById('checkin-code-input');
    const checkinInputSection = document.getElementById('checkin-input-section');
    
    // Обновляем отображение прогресса в формате "X / 3 days"
    if (consecutiveDaysEl) {
      const days = d.consecutive_days || 0;
      consecutiveDaysEl.textContent = `${days} / 3 days`;
    }
    
    // Показываем сообщение о прогрессе
    const progressMessageEl = document.getElementById('checkin-progress-message');
    const progressTextEl = progressMessageEl ? progressMessageEl.querySelector('.checkin-progress-text') : null;
    if (progressMessageEl && progressTextEl) {
      const days = d.consecutive_days || 0;
      if (days > 0 && days < 3) {
        const daysLeft = 3 - days;
        progressTextEl.textContent = `Keep going! ${daysLeft} more day${daysLeft > 1 ? 's' : ''} until reward!`;
        progressTextEl.style.color = '#FFC107';
        progressMessageEl.style.display = 'block';
        progressMessageEl.style.background = 'rgba(255, 193, 7, 0.1)';
        progressMessageEl.style.borderColor = 'rgba(255, 193, 7, 0.3)';
      } else if (days >= 3) {
        progressTextEl.textContent = '🎉 Reward unlocked! Check in to claim your prize!';
        progressTextEl.style.color = '#4CAF50';
        progressMessageEl.style.display = 'block';
        progressMessageEl.style.background = 'rgba(76, 175, 80, 0.1)';
        progressMessageEl.style.borderColor = 'rgba(76, 175, 80, 0.3)';
      } else {
        progressMessageEl.style.display = 'none';
      }
    }
    
    if (checkinButton && checkinInput) {
      if (d.checked_in_today) {
        // Уже зашел сегодня - скрываем поле ввода, показываем статус
        if (checkinInputSection) checkinInputSection.style.display = 'none';
        checkinButton.disabled = true;
        checkinButton.textContent = 'ALREADY CHECKED IN';
        checkinButton.style.display = 'block';
        // Убираем любые inline стили, которые могут влиять на отступы
        checkinButton.style.marginTop = '';
        checkinButton.style.marginBottom = '';
        if (checkinMessage) {
          checkinMessage.textContent = 'You have already checked in today!';
          checkinMessage.style.display = 'block';
          checkinMessage.style.color = '#4CAF50';
        }
      } else {
        // Еще не зашел - показываем поле ввода
        if (checkinInputSection) {
          checkinInputSection.style.display = 'flex';
          // Убеждаемся, что gap сохраняется
          checkinInputSection.style.gap = '24px';
        }
        checkinButton.disabled = true; // Будет активирована при вводе кода
        checkinButton.textContent = 'CHECK IN';
        // Убираем любые inline стили, которые могут влиять на отступы
        checkinButton.style.marginTop = '';
        checkinButton.style.marginBottom = '';
        checkinButton.style.display = '';
        if (checkinInput) {
          checkinInput.value = '';
          checkinInput.disabled = false;
          checkinInput.focus();
        }
        if (checkinMessage) {
          checkinMessage.style.display = 'none';
        }
        
        // Активируем кнопку при вводе кода с валидацией
        checkinInput.oninput = () => {
          // Фильтруем: оставляем только буквы A-Z и цифры 0-9
          let code = checkinInput.value.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
          
          // Ограничиваем длину до 8 символов
          if (code.length > 8) {
            code = code.substring(0, 8);
          }
          
          // Обновляем значение поля (убираем спецсимволы)
          if (checkinInput.value !== code) {
            checkinInput.value = code;
          }
          
          // Активируем кнопку только если код ровно 8 символов
          checkinButton.disabled = code.length !== 8;
          
          if (checkinMessage && checkinMessage.style.display !== 'none') {
            checkinMessage.style.display = 'none';
          }
        };
        
        // Дополнительная валидация при вставке (paste)
        checkinInput.onpaste = (e) => {
          e.preventDefault();
          const pastedText = (e.clipboardData || window.clipboardData).getData('text');
          // Фильтруем и ограничиваем длину
          let code = pastedText.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
          if (code.length > 8) {
            code = code.substring(0, 8);
          }
          checkinInput.value = code;
          checkinButton.disabled = code.length !== 8;
          // Триггерим событие input для обновления UI
          checkinInput.dispatchEvent(new Event('input'));
        };
        
        // Обработка Enter
        checkinInput.onkeypress = (e) => {
          if (e.key === 'Enter' && !checkinButton.disabled) {
            checkinButton.click();
          }
        };
      }
      
      // Обработчик клика
      checkinButton.onclick = async () => {
        if (checkinButton.disabled) return;
        
        const code = checkinInput ? checkinInput.value.trim().toUpperCase() : '';
        if (!code || code.length !== 8) {
          if (checkinMessage) {
            checkinMessage.textContent = 'Please enter a valid 8-character code';
            checkinMessage.style.display = 'block';
            checkinMessage.style.color = '#f44336';
          }
          return;
        }
        
        checkinButton.disabled = true;
        checkinButton.textContent = 'Processing...';
        if (checkinInput) checkinInput.disabled = true;
        
        try {
          const checkinHeaders = {
            'Content-Type': 'application/json'
          };
          const signature = sessionStorage.getItem('signature') || '';
          const message = sessionStorage.getItem('message') || '';
          
          if (signature && message) {
            checkinHeaders['X-Wallet'] = wallet;
            checkinHeaders['X-Signature'] = signature;
            checkinHeaders['X-Message'] = message;
          }
          
          const checkinR = await fetch(`/api/daily-checkin/checkin/${wallet}`, {
            method: 'POST',
            headers: checkinHeaders,
            credentials: 'include',
            body: JSON.stringify({ daily_code: code })
          });
          
          const checkinData = await checkinR.json();
          
          if (checkinData.success) {
            checkinButton.textContent = 'CHECKED IN!';
            if (checkinInput) {
              checkinInput.value = '';
              checkinInput.disabled = true;
            }
            
            // Обновляем UI сразу на основе ответа от сервера
            const consecutiveDaysEl = document.getElementById('consecutive-days');
            if (consecutiveDaysEl) {
              const days = checkinData.consecutive_days || 0;
              consecutiveDaysEl.textContent = `${days} / 3 days`;
            }
            
            // Обновляем сообщение о прогрессе
            const progressMessageEl = document.getElementById('checkin-progress-message');
            const progressTextEl = progressMessageEl ? progressMessageEl.querySelector('.checkin-progress-text') : null;
            if (progressMessageEl && progressTextEl) {
              const days = checkinData.consecutive_days || 0;
              if (days > 0 && days < 3) {
                const daysLeft = 3 - days;
                progressTextEl.textContent = `Keep going! ${daysLeft} more day${daysLeft > 1 ? 's' : ''} until reward!`;
                progressTextEl.style.color = '#FFC107';
                progressMessageEl.style.display = 'block';
                progressMessageEl.style.background = 'rgba(255, 193, 7, 0.1)';
                progressMessageEl.style.borderColor = 'rgba(255, 193, 7, 0.3)';
              } else if (days >= 3) {
                progressTextEl.textContent = '🎉 Reward unlocked! Check in to claim your prize!';
                progressTextEl.style.color = '#4CAF50';
                progressMessageEl.style.display = 'block';
                progressMessageEl.style.background = 'rgba(76, 175, 80, 0.1)';
                progressMessageEl.style.borderColor = 'rgba(76, 175, 80, 0.3)';
              } else {
                progressMessageEl.style.display = 'none';
              }
            }
            
            // Скрываем поле ввода, показываем статус
            const checkinInputSection = document.getElementById('checkin-input-section');
            if (checkinInputSection) {
              checkinInputSection.style.display = 'none';
            }
            checkinButton.disabled = true;
            checkinButton.textContent = 'ALREADY CHECKED IN';
            // Убираем любые inline стили, которые могут влиять на отступы
            checkinButton.style.marginTop = '';
            checkinButton.style.marginBottom = '';
            
            if (checkinMessage) {
              let message = `Checked in! Consecutive days: ${checkinData.consecutive_days}`;
              if (checkinData.reward_issued && checkinData.rewards && checkinData.rewards.length > 0) {
                const rewards = checkinData.rewards.map(r => {
                  if (r.type === 'broken_packs') return `${r.quantity} Broken Packs`;
                  if (r.type === 'common_pack') return '1 Common Pack';
                  if (r.type === 'legendary_pack') return '1 Legendary Pack';
                  if (r.type === 'card') return `1 ${r.rarity} Card`;
                  if (r.type === 'boost') return 'Personal Boost Activated!';
                  return 'Reward';
                }).join(', ');
                message += `\nRewards: ${rewards}`;
              }
              checkinMessage.textContent = message;
              checkinMessage.style.display = 'block';
              checkinMessage.style.color = '#4CAF50';
            }
            
            // Обновляем только chests, без перезагрузки всего статуса (чтобы избежать редиректа)
            if (typeof loadUserChests === 'function') {
              setTimeout(() => {
                loadUserChests(wallet);
              }, 1000);
            }
          } else {
            checkinButton.disabled = false;
            checkinButton.textContent = 'CHECK IN';
            // Восстанавливаем правильные стили для секции
            const checkinInputSection = document.getElementById('checkin-input-section');
            if (checkinInputSection) {
              checkinInputSection.style.display = 'flex';
              checkinInputSection.style.gap = '24px';
            }
            // Убираем любые inline стили с кнопки
            checkinButton.style.marginTop = '';
            checkinButton.style.marginBottom = '';
            checkinButton.style.display = '';
            if (checkinInput) checkinInput.disabled = false;
            if (checkinMessage) {
              checkinMessage.textContent = checkinData.error || 'Invalid code. Please check the code from Twitter and try again.';
              checkinMessage.style.display = 'block';
              checkinMessage.style.color = '#f44336';
            }
            // Фокусируемся на поле ввода для повторной попытки
            if (checkinInput) {
              checkinInput.focus();
              checkinInput.select();
            }
          }
        } catch (error) {
          console.error('Check-in error:', error);
          checkinButton.disabled = false;
          checkinButton.textContent = 'CHECK IN';
          // Восстанавливаем правильные стили для секции
          const checkinInputSection = document.getElementById('checkin-input-section');
          if (checkinInputSection) {
            checkinInputSection.style.display = 'flex';
            checkinInputSection.style.gap = '24px';
          }
          // Убираем любые inline стили с кнопки
          checkinButton.style.marginTop = '';
          checkinButton.style.marginBottom = '';
          checkinButton.style.display = '';
          if (checkinInput) checkinInput.disabled = false;
          if (checkinMessage) {
            checkinMessage.textContent = 'Error checking in. Please try again.';
            checkinMessage.style.display = 'block';
            checkinMessage.style.color = '#f44336';
          }
        }
      };
    }
  } catch (error) {
    console.error('Error loading daily checkin:', error);
  }
}

async function loadReferral() {
  try {
    const wallet = currentWallet || localStorage.getItem('wallet');
    if (!wallet) return;
    const r = await fetch(`/api/referral/summary/${wallet}`);
    const d = await r.json();
    if (!d || !d.success) return;
    const linkInput = document.getElementById('referral-link');
    const code = d.refCode || (localStorage.getItem('refCode') || '');
    const link = `${window.location.origin}/ref/${code}`;
    if (linkInput) linkInput.value = link;
    const cntEl = document.getElementById('ref-count');
    if (cntEl) cntEl.textContent = String(d.referrals || 0);

    const copyBtn = document.getElementById('ref-copy');
    if (copyBtn) copyBtn.onclick = async () => { try { await navigator.clipboard.writeText(link); showMessage('Referral link copied!', 'success'); } catch {} };

    const rewardsRes = await fetch(`/api/referral/rewards/${wallet}?limit=1`);
    const rewardsData = await rewardsRes.json();
    if (rewardsData && rewardsData.success) {
      const totalEarned = Number(rewardsData.totalEarned || 0);
      const availableToClaim = Number(rewardsData.availableToClaim || 0);
      const incomeEl = document.getElementById('ref-income');
      if (incomeEl) incomeEl.textContent = `${totalEarned.toFixed(2)} $TOKENS`;
      const claimAmountInput = document.getElementById('referral-claim-amount');
      if (claimAmountInput) claimAmountInput.value = `${availableToClaim.toFixed(2)} $TOKENS`;

      const claimBtn = document.getElementById('ref-claim');
      if (claimBtn) {
        claimBtn.disabled = availableToClaim <= 0;
        claimBtn.onclick = async () => {
          try {
            claimBtn.disabled = true;
            const cr = await fetch(`/api/referral/claim/${wallet}`, { method: 'POST' });
            const cd = await cr.json();
            if (cd && cd.success) {
              const claimed = Number(cd.claimed || 0);
              if (claimed > 0) {
                showMessage(`Claimed: ${claimed.toFixed(2)} $TOKENS`, 'success');
              } else {
                showMessage('Nothing to claim.', 'success');
              }
              await loadReferral();
            } else {
              claimBtn.disabled = false;
              showMessage('Failed to claim referral rewards.', 'error');
            }
          } catch {
            claimBtn.disabled = false;
            showMessage('Failed to claim referral rewards.', 'error');
          }
        };
      }
    }
  } catch {} 
}

async function loadSuperJackpot() {
  console.log('=== loadSuperJackpot() function called ===');
  try {
    const res = await fetch('/api/super-jackpot');
    const data = await res.json();
    console.log('Super jackpot data:', data);
    
    if (data && data.success) {
      // API возвращает amount, а не superJackpot
      const amount = data.amount || 0;
      const amountText = `${Math.round(parseFloat(amount))} $TOKENS`;
      console.log('Super jackpot amount text:', amountText);
      
      // Update all super jackpot amount elements (как у обычного джекпота)
      const amountEls = [
        document.getElementById('superj-amount'),           // home
        document.getElementById('superj-amount-shop'),      // shop
        document.getElementById('superj-amount-cards'),     // cards
        document.getElementById('superj-amount-battle'),    // battle
        document.getElementById('superj-amount-ref'),       // referral
        document.getElementById('superj-amount-rules'),     // rules
        document.getElementById('superj-amount-predict'),   // predict
      ];
      
      amountEls.forEach(el => {
        if (el) {
          el.textContent = amountText;
          console.log('Updated super jackpot element:', el.id, 'with:', amountText);
        }
      });
      
      // Если ни один элемент не найден, выводим предупреждение
      const foundElements = amountEls.filter(el => el !== null);
      if (foundElements.length === 0) {
        console.warn('No super jackpot elements found on current page');
      }
    } else {
      console.error('Super jackpot API returned error:', data);
      // Устанавливаем значение по умолчанию при ошибке
      const amountEls = [
        document.getElementById('superj-amount'),
        document.getElementById('superj-amount-shop'),
        document.getElementById('superj-amount-cards'),
        document.getElementById('superj-amount-battle'),
        document.getElementById('superj-amount-ref'),
        document.getElementById('superj-amount-rules'),
        document.getElementById('superj-amount-predict'),
      ];
      amountEls.forEach(el => {
        if (el) el.textContent = '0 $TOKENS';
      });
    }
  } catch (e) {
    console.error('Load super jackpot error:', e);
    // Устанавливаем значение по умолчанию при ошибке
    const amountEls = [
      document.getElementById('superj-amount'),
      document.getElementById('superj-amount-shop'),
      document.getElementById('superj-amount-cards'),
      document.getElementById('superj-amount-battle'),
      document.getElementById('superj-amount-ref'),
      document.getElementById('superj-amount-rules'),
    ];
    amountEls.forEach(el => {
      if (el) el.textContent = '0 $TOKENS';
    });
  }
}

async function drawJackpot() {
  try {
    const r = await fetch('/api/jackpot/draw', { method: 'POST' });
    const data = await r.json();
    if (data && data.success) showMessage(`Raffle finished! Winner: #${data.winnerUserId}, prize: ${data.prize} TOKENS`, 'success');
  } catch (e) { console.error('drawJackpot error', e); }
}

async function loadBoostNotification() {
  try {
    const wallet = currentWallet || sessionStorage.getItem('wallet') || localStorage.getItem('wallet');
    if (!wallet) return;
    
    const headers = {};
    const signature = sessionStorage.getItem('signature') || '';
    const message = sessionStorage.getItem('message') || '';
    
    if (signature && message) {
      headers['X-Wallet'] = wallet;
      headers['X-Signature'] = signature;
      headers['X-Message'] = message;
    }
    
    const r = await fetch(`/api/daily-checkin/status/${wallet}`, {
      headers: headers,
      credentials: 'include'
    });
    const d = await r.json();
    if (!d || !d.success || !d.boost || !d.boost.active) {
      const boostSection = document.getElementById('boost-notification');
      if (boostSection) boostSection.style.display = 'none';
      return;
    }
    
    const boostSection = document.getElementById('boost-notification');
    const boostValueEl = document.getElementById('boost-value');
    const boostTimeLeftEl = document.getElementById('boost-time-left');
    
    if (boostSection) boostSection.style.display = 'block';
    if (boostValueEl) boostValueEl.textContent = String(d.boost.boost_value || 10);
    
    // Обновляем таймер
    if (boostTimeLeftEl && d.boost.expires_at) {
      const updateTimer = () => {
        const expiresAt = new Date(d.boost.expires_at);
        const now = new Date();
        const diff = expiresAt - now;
        
        if (diff <= 0) {
          boostTimeLeftEl.textContent = 'Expired';
          if (boostSection) boostSection.style.display = 'none';
          return;
        }
        
        const hours = Math.floor(diff / (1000 * 60 * 60));
        const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((diff % (1000 * 60)) / 1000);
        
        boostTimeLeftEl.textContent = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
      };
      
      updateTimer();
      setInterval(updateTimer, 1000);
    }
  } catch (error) {
    console.error('Error loading boost notification:', error);
  }
}

async function loadPacks(forShop = false) {
    try {
        console.log('Loading packs, forShop:', forShop);
        const response = await fetch('/api/chests');
        const data = await response.json();
        console.log('Packs data:', data);
    if (!data.success) return;
    const container = document.getElementById(forShop ? 'shop-packs-grid' : 'packs-grid');
    console.log('Container found:', container);
    if (!container) return;
    // Очищаем контейнер перед добавлением
    container.innerHTML = '';
    // Защита от повторного вызова - проверяем, не загружаются ли уже паки
    if (container.dataset.loading === 'true') {
        console.warn('Packs are already loading, skipping...');
        return;
    }
    container.dataset.loading = 'true';
    // Reorder packs for shop: Broke -> Common -> Rare -> Epic -> Legendary
    const chestsOrdered = (() => {
      const arr = (data.chests || []).slice();
      console.log('Chests array length:', arr.length);
      if (arr.length === 0) {
        console.warn('No chests found in database!');
        return arr;
      }
      // Убираем дубликаты по id_chest (оставляем только первый)
      const uniqueChests = [];
      const seenIds = new Set();
      arr.forEach(chest => {
        if (!seenIds.has(chest.id_chest)) {
          seenIds.add(chest.id_chest);
          uniqueChests.push(chest);
        }
      });
      console.log('Unique chests count:', uniqueChests.length);
      if (!forShop) return uniqueChests;
      const order = { 5: 0, 1: 1, 2: 2, 3: 3, 4: 4 };
      return uniqueChests.sort((a, b) => (order[a.id_chest] ?? 99) - (order[b.id_chest] ?? 99));
    })();
    console.log('Chests ordered length:', chestsOrdered.length);
    chestsOrdered.forEach((chest) => {
      let packImage = 'common.png';
      if (chest.id_chest === 2) packImage = 'rare.png'; else if (chest.id_chest === 3) packImage = 'epic.png'; else if (chest.id_chest === 4) packImage = 'legendary.png'; else if (chest.id_chest === 5) packImage = 'broke.png';
      const el = document.createElement('div');
      el.className = 'pack-container';
      el.innerHTML = `
        <img src="img/${packImage}" alt="Pack ${chest.id_chest}" class="pack-image">
        <div class="pack-card">
          <div class="pack-name">${getPackLabel(chest.id_chest)}</div>
          <div class="pack-price">PRICE: ${Number(chest.price).toString()} $TOKENS</div>
          <div class="pack-rates">CARD DROP RATES: Common: ${chest.prob_common}%; Rare: ${chest.prob_rare}%; Epic: ${chest.prob_epic}%; Legendary: ${chest.prob_legendary}%${chest.chance_loss > 0 ? `; Loss: ${chest.chance_loss}%` : ''}</div>
          <div class="pack-quantity-selector">
            <div class="quantity-controls">
              <button class="quantity-btn quantity-minus" data-id="${chest.id_chest}" aria-label="Decrease quantity">−</button>
              <input type="number" class="quantity-input" data-id="${chest.id_chest}" value="1" min="1" max="100" readonly>
              <button class="quantity-btn quantity-plus" data-id="${chest.id_chest}" aria-label="Increase quantity">+</button>
            </div>
            <div class="quantity-total" data-id="${chest.id_chest}">Total: ${Number(chest.price).toString()} $TOKENS</div>
          </div>
          <button class="pack-buy-btn" data-id="${chest.id_chest}">${currentWallet ? 'Buy' : 'Connect Wallet'}</button>
        </div>
      `;
      container.appendChild(el);
    });
    // Removed price increase info block on shop page per request
    // Закомментировано - кнопка теперь показывает "Soon" и не работает
    container.querySelectorAll('.pack-buy-btn').forEach(btn => btn.addEventListener('click', async () => { if (!currentWallet) { await connectWallet(); return; } const id = btn.getAttribute('data-id'); await purchaseChest(id, data.chests); }));
    
    // Добавляем обработчики для кнопок количества
    container.querySelectorAll('.quantity-plus').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        const input = container.querySelector(`.quantity-input[data-id="${id}"]`);
        const chest = data.chests.find(c => c.id_chest === Number(id));
        if (input && chest) {
          const current = parseInt(input.value) || 1;
          const max = 100;
          if (current < max) {
            input.value = current + 1;
            updateQuantityTotal(id, chest.price, container);
          }
        }
      });
    });
    
    container.querySelectorAll('.quantity-minus').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        const input = container.querySelector(`.quantity-input[data-id="${id}"]`);
        const chest = data.chests.find(c => c.id_chest === Number(id));
        if (input && chest) {
          const current = parseInt(input.value) || 1;
          if (current > 1) {
            input.value = current - 1;
            updateQuantityTotal(id, chest.price, container);
          }
        }
      });
    });
    
    // Инициализируем total для всех паков
    data.chests.forEach(chest => {
      updateQuantityTotal(chest.id_chest, chest.price, container);
    });
    
    updatePackButtons();
    // Снимаем флаг загрузки
    container.dataset.loading = 'false';
  } catch (e) { 
    console.error('Load packs error', e);
    // Снимаем флаг загрузки в случае ошибки
    if (container) container.dataset.loading = 'false';
  }
}

function updatePackButtons() {
  const packButtons = document.querySelectorAll('.pack-buy-btn');
  packButtons.forEach(btn => { 
    btn.textContent = currentWallet ? 'Buy' : 'Connect Wallet';
  });
}

function updateQuantityTotal(id, price, container) {
  const input = container.querySelector(`.quantity-input[data-id="${id}"]`);
  const totalEl = container.querySelector(`.quantity-total[data-id="${id}"]`);
  if (input && totalEl) {
    const quantity = parseInt(input.value) || 1;
    const total = (Number(price) * quantity).toFixed(2);
    totalEl.textContent = `Total: ${total} $TOKENS`;
  }
}

async function connectWallet() {
  try {
    hideMessage();
    const publicKey = await connectPhantom();
    const data = await performAuth(publicKey);
    if (data.success) {
      clearPendingReferralCode();
      showUserInfo(publicKey, data.refCode, { redirect: true });
      window.location.href = '/profile';
    } else {
      showMessage(data.error || 'Authentication error');
    }
  } catch (error) { console.error('Connect wallet error:', error); showMessage(error.message || 'Wallet connection error'); }
}

async function purchaseChest(idChest, chestsCache) {
  if (!currentWallet) { showMessage('Please connect your Phantom wallet first'); return; }
  
  // Получаем количество паков из input
  const quantityInput = document.querySelector(`.quantity-input[data-id="${idChest}"]`);
  const quantity = quantityInput ? parseInt(quantityInput.value) || 1 : 1;
  
  if (quantity < 1 || quantity > 100) {
    showMessage('Invalid quantity. Please select between 1 and 100 packs.');
    return;
  }
  
  const packText = quantity === 1 ? 'pack' : 'packs';
  const ok = confirm(`Buy ${quantity} ${packText}?`);
  if (!ok) return;
  
  // Показываем модальное окно загрузки сразу после подтверждения
  showPurchaseLoadingModal(quantity, idChest);
  
  try {
    updatePurchaseLoadingStatus('preparing', 'Preparing transaction...');
    const cfgResp = await fetch('/api/config');
    const cfg = await cfgResp.json();
    if (!cfg || !cfg.success) { closeModal(); showMessage('Failed to fetch config'); return; }
    const provider = window.phantom?.solana || window.solana;
    if (!provider) { closeModal(); showMessage('Phantom not found'); return; }
    try { await provider.connect(); } catch (e) { closeModal(); showMessage('Phantom connection rejected'); return; }
    updatePurchaseLoadingStatus('signing', 'Please sign the transaction in Phantom...');
    const purchaseMsg = `Gamba Purchase Verify\nwallet=${currentWallet}\nchest=${idChest}\nnonce=${Date.now()}`;
    try { const enc = new TextEncoder().encode(purchaseMsg); const signed = await provider.signMessage(enc, 'utf8'); void Array.from(signed.signature); } catch { closeModal(); showMessage('Signature was rejected in Phantom'); return; }
    if (!window.Web3Lib || !window.SPLLite) { closeModal(); showMessage('Initialization error, please reload the page'); return; }
    const { Connection, PublicKey, Transaction } = window.Web3Lib;
    const { getAssociatedTokenAddress, createTransferCheckedInstruction } = window.SPLLite;
    // Проверяем конфигурацию (mint может быть пустым для SOL transfers)
    const cfgOk = cfg && cfg.merchant && cfg.rpcUrl;
    if (!cfgOk) { 
      console.error('Invalid config:', cfg);
      showMessage('Invalid config: missing merchant or RPC URL'); 
      return; 
    }
    // Если mint не указан, используем SOL transfer (но это не поддерживается сейчас)
    if (!cfg.mint) {
      console.warn('TOKEN_MINT not configured, token transfers will fail');
      showMessage('Token mint not configured. Please contact support.');
      return;
    }
    let price = 0;
    if (Array.isArray(chestsCache)) { const c = chestsCache.find(c => c.id_chest === Number(idChest)); price = c ? Number(c.price) : 0; }
    else { try { const resp = await fetch('/api/chests'); const d = await resp.json(); if (d && d.success) { const c = (d.chests || []).find(c => c.id_chest === Number(idChest)); price = c ? Number(c.price) : 0; } } catch {} }
    
    // Вычисляем общую цену за количество паков
    const totalPrice = price * quantity;
    updatePurchaseLoadingStatus('sending', 'Sending transaction to blockchain...');
    try {
      console.log('Creating connection to:', cfg.rpcUrl);
      const connection = new Connection(cfg.rpcUrl);
      console.log('Creating PublicKeys...');
      const mintPk = new PublicKey(cfg.mint);
      const buyerPk = new PublicKey(currentWallet);
      const merchantPk = new PublicKey(cfg.merchant);
      console.log('Getting associated token addresses...');
      const buyerAta = await getAssociatedTokenAddress(mintPk, buyerPk);
      const merchantAta = await getAssociatedTokenAddress(mintPk, merchantPk);
      console.log('Getting mint info...');
      const mintInfo = await connection.getParsedAccountInfo(mintPk);
      const decimals = (mintInfo && mintInfo.value && mintInfo.value.data && mintInfo.value.data.parsed && mintInfo.value.data.parsed.info && mintInfo.value.data.parsed.info.decimals) ? mintInfo.value.data.parsed.info.decimals : 0;
      console.log('Mint decimals:', decimals, 'Price:', price);
      const amountRaw = Math.round(Number(totalPrice) * Math.pow(10, decimals));
      console.log('Amount raw:', amountRaw);
      const tx = new Transaction();
      console.log('Checking merchant ATA...');
      const merchantAtaInfo = await connection.getAccountInfo(merchantAta);
      if (!merchantAtaInfo) {
        console.log('Creating merchant ATA...');
        const rentSysvar = new PublicKey('SysvarRent111111111111111111111111111111111');
        const keys = [ { pubkey: buyerPk, isSigner: true, isWritable: true }, { pubkey: merchantAta, isSigner: false, isWritable: true }, { pubkey: merchantPk, isSigner: false, isWritable: false }, { pubkey: mintPk, isSigner: false, isWritable: false }, { pubkey: window.Web3Lib.SystemProgram.programId, isSigner: false, isWritable: false }, { pubkey: window.SPLLite.TOKEN_PROGRAM_ID, isSigner: false, isWritable: false }, { pubkey: rentSysvar, isSigner: false, isWritable: false } ];
        tx.add(new window.Web3Lib.TransactionInstruction({ keys, programId: window.SPLLite.ASSOCIATED_TOKEN_PROGRAM_ID, data: new Uint8Array([]) }));
      }
      console.log('Adding transfer instruction...');
      tx.add(createTransferCheckedInstruction(buyerAta, mintPk, merchantAta, buyerPk, amountRaw, decimals));
      console.log('Getting latest blockhash...');
      const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash();
      tx.recentBlockhash = blockhash; tx.feePayer = buyerPk;
      let txSignature;
      console.log('Signing and sending transaction...');
      if (provider.signAndSendTransaction) { 
        try {
          const sendRes = await provider.signAndSendTransaction(tx); 
          txSignature = sendRes.signature || sendRes;
          console.log('Transaction sent, signature:', txSignature);
        } catch (txError) {
          console.error('Transaction error:', txError);
          closeModal(); // Закрываем модальное окно при ошибке
          showMessage(txError?.message || 'Transaction failed. Check console for details.');
          return;
        }
      }
      else if (provider.signTransaction) { 
        try {
          const signedTx = await provider.signTransaction(tx); 
          txSignature = await connection.sendRawTransaction(signedTx.serialize(), { skipPreflight: false, maxRetries: 3 });
          console.log('Transaction sent, signature:', txSignature);
        } catch (txError) {
          console.error('Transaction error:', txError);
          closeModal(); // Закрываем модальное окно при ошибке
          showMessage(txError?.message || 'Transaction failed. Check console for details.');
          return;
        }
      }
      else { 
        closeModal(); // Закрываем модальное окно при ошибке
        showMessage('Your Phantom version does not support sending transactions'); 
        return; 
      }
      console.log('Confirming transaction...');
      updatePurchaseLoadingStatus('confirming', 'Confirming transaction on blockchain...');
      try { 
        await connection.confirmTransaction({ signature: txSignature, blockhash, lastValidBlockHeight }, 'confirmed'); 
        console.log('Transaction confirmed');
      } catch (confirmError) {
        console.warn('Transaction confirmation error (non-critical):', confirmError);
      }
      
      // Обновляем статус - проверка на бэкенде
      updatePurchaseLoadingStatus('verifying', 'Verifying purchase with server...');
      console.log('Sending purchase request to backend...');
      const res = await fetch('/api/chests/buy', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wallet: currentWallet, id_chest: Number(idChest), txSignature, quantity: quantity }) });
      const resp = await res.json();
      console.log('Backend response:', resp);
      
      // Закрываем модальное окно загрузки
      closeModal();
      
      if (resp.success) {
        // Обновляем джекпоты после успешной покупки
        if (typeof loadJackpot === 'function') {
          loadJackpot();
        }
        if (typeof loadSuperJackpot === 'function') {
          loadSuperJackpot();
        }
        
        if (quantity === 1) {
          showPurchaseModal(Number(idChest));
        } else {
          showMessage(`Successfully purchased ${quantity} packs!`, 'success');
          // Обновляем список паков
          if (typeof loadUserChests === 'function') {
            loadUserChests(currentWallet);
          }
        }
      } else {
        showMessage(resp.error || 'Failed to buy pack');
      }
    } catch (e) { 
      console.error('Transfer error:', e); 
      console.error('Error stack:', e.stack);
      closeModal(); // Закрываем модальное окно при ошибке
      showMessage(e?.message || 'Transfer error. Check console for details.'); 
    }
  } catch (e) { 
    console.error('Buy flow error', e); 
    closeModal(); // Закрываем модальное окно при ошибке
    const msg = (e && (e.message || e.code || e.toString())) || 'Purchase error'; 
    showMessage(msg); 
  }
}

async function loadChests() {
  try {
    const response = await fetch('/api/chests');
        const data = await response.json();
    if (!data.success) return;
    const container = document.getElementById('chests-list');
    if (!container) return;
    container.innerHTML = '';
    (data.chests || []).slice(0, 4).forEach((chest) => {
      const el = document.createElement('div');
      el.className = 'card';
      el.innerHTML = `<h3>Сундук #${chest.id_chest}</h3><p>Цена: ${Number(chest.price).toString()} TOKENS</p><p>Шансы: C:${chest.prob_common}% R:${chest.prob_rare}% E:${chest.prob_epic}% L:${chest.prob_legendary}%</p><button data-id="${chest.id_chest}" class="buy-chest">Купить</button>`;
      container.appendChild(el);
    });
    container.querySelectorAll('.buy-chest').forEach(btn => btn.addEventListener('click', async () => { if (!currentWallet) { showMessage('Please connect your Phantom wallet first'); return; } const id = btn.getAttribute('data-id'); await purchaseChest(id, data.chests); }));
  } catch (e) { console.error('Load chests error', e); }
}

async function loadUserChests(wallet) {
  try {
    const res = await fetch(`/api/user/${wallet}/chests`);
    const data = await res.json();
    if (!data.success) return;
    const list = document.getElementById('user-chests');
    if (!list) return;
    list.innerHTML = '';
    const chests = (data.chests || []).filter(x => !x.is_opened);
    if (chests.length === 0) { list.textContent = 'No purchased packs yet'; return; }
    chests.forEach((c) => {
      const el = document.createElement('div');
      el.style.marginBottom = '8px';
      const openBtnId = `open-${c.id_purchase}`;
      el.innerHTML = `${getPackLabel(c.id_chest)} — purchased at ${c.created_at} <button id="${openBtnId}">Open</button>`;
      list.appendChild(el);
      const btn = document.getElementById(openBtnId);
      if (btn) btn.addEventListener('click', async () => {
        try {
          const ok = confirm('Open this pack?');
          if (!ok) return;
          const resp = await fetch('/api/chests/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wallet: wallet, id_purchase: c.id_purchase }) });
          const r = await resp.json();
          if (r.success) { 
            // Get cashback info from server response
            if (r.cashback) {
              const multiplier = r.cashback.multiplier;
              const cashbackAmount = r.cashback.amount.toFixed(2);
              showMessage(`Card dropped: ${r.rarity} | Cashback: ${cashbackAmount} $TOKENS (${multiplier}x)`, 'success');
            } else {
              showMessage(`Card dropped: ${r.rarity}`, 'success');
            }
            loadUserChests(wallet); 
            loadUserCards(wallet); 
            loadCashback(); // Update cashback data
          }
          else { showMessage(r.error || 'Failed to open pack'); }
        } catch { showMessage('Pack opening error'); }
      });
    });
  } catch (e) { console.error('Load user chests error', e); }
}

// Aggregate unopened packs and render as shop-like cards with Open button
async function loadMyPacks(wallet) {
  try {
    const response = await fetch(`/api/user/${wallet}/chests`);
    const data = await response.json();
    if (!data.success) return;
    const unopened = (data.chests || []).filter(c => !c.is_opened);
    const section = document.getElementById('mypacks-section');
    const grid = document.getElementById('mypacks-grid');
    if (!section || !grid) return;
    if (unopened.length === 0) { section.classList.add('hidden'); grid.innerHTML = ''; return; }
    section.classList.remove('hidden');
    // group by id_chest
    const byId = new Map();
    unopened.forEach(c => { const v = byId.get(c.id_chest) || { chest: c, count: 0 }; v.count += 1; byId.set(c.id_chest, v); });
    grid.innerHTML = '';
    byId.forEach(({ chest, count }) => {
      let packImage = 'common.png';
      if (chest.id_chest === 2) packImage = 'rare.png'; else if (chest.id_chest === 3) packImage = 'epic.png'; else if (chest.id_chest === 4) packImage = 'legendary.png'; else if (chest.id_chest === 5) packImage = 'broke.png';
      const el = document.createElement('div');
      el.className = 'pack-container';
      el.innerHTML = `
        <img src="img/${packImage}" alt="Pack ${chest.id_chest}" class="pack-image">
        <div class="pack-card">
          <div class="pack-name">${getPackLabel(chest.id_chest)}</div>
          <!-- Закомментирована информация о паках
          <div class="pack-price">PRICE: ${Number(chest.price).toString()} $TOKENS</div>
          <div class="pack-rates">CARD DROP RATES: Common: ${chest.prob_common}%; Rare: ${chest.prob_rare}%; Epic: ${chest.prob_epic}%; Legendary: ${chest.prob_legendary}%</div>
          -->
          <button class="pack-buy-btn" data-open-chest="${chest.id_chest}">Open</button>
        </div>`;
      grid.appendChild(el);
    });
    grid.querySelectorAll('[data-open-chest]').forEach(btn => btn.addEventListener('click', async () => {
      const idChest = Number(btn.getAttribute('data-open-chest'));
      // Reuse the same beautiful modal flow as the immediate open path, but avoid double handlers
      showPurchaseModal(idChest, { attachOpenHandler: false });
      const openBtn = document.getElementById('pm-open');
      if (openBtn) {
        openBtn.addEventListener('click', async () => { await openPurchasedPack(idChest); }, { once: true });
      }
    }));
  } catch (e) { console.error('loadMyPacks error', e); }
}

async function loadUserCards(wallet) {
  try {
    const res = await fetch(`/api/user/${wallet}/cards`);
    const data = await res.json();
    const grid = document.getElementById('user-cards');
    if (!grid) return;
    const tabs = document.querySelectorAll('.collection-tab');
    const cards = (data && data.cards) ? data.cards : [];
    cards.forEach(c => { if (c.rarity === 'common') c.rarity = 'basic'; });
    const byRarity = {
      basic: cards.filter(c => c.rarity === 'basic'),
      rare: cards.filter(c => c.rarity === 'rare'),
      epic: cards.filter(c => c.rarity === 'epic'),
      legendary: cards.filter(c => c.rarity === 'legendary'),
    };
    let allCardsTotal = null;
    // Получить общее число карт в игре из /api/cards
    try {
      const allRes = await fetch('/api/cards');
      const allData = await allRes.json();
      if (allData.success && Array.isArray(allData.cards)) allCardsTotal = allData.cards.length;
    } catch(_) {}
    function render(r) {
      grid.innerHTML = '';
      const arr = byRarity[r] || [];
      if (arr.length === 0) { grid.textContent = 'No cards of this rarity yet'; return; }
      arr.forEach(card => {
        const el = document.createElement('div');
        el.className = 'collection-card';
        const img = card.image_url ? `<img src="${card.image_url}" style="width:100%;height:auto;border-radius:12px;margin-bottom:8px;"/>` : '';
        const title = card.name || `CARD #${card.id_card}`;
        const qty = Number(card.quantity || 1);
        const tickets = (card.effective_tickets != null) ? Number(card.effective_tickets) : Number(card.start_bounty || 0);
        el.innerHTML = `${img}
          <div class="collection-card-title">${title}</div>
          <div class="collection-card-qty">quantity: ${qty}</div>
          <div class="collection-card-qty">tickets: ${tickets}</div>`;
        grid.appendChild(el);
      });
    }
    let active = 'basic';
    const activeBtn = document.querySelector('.collection-tab.active');
    if (activeBtn) active = activeBtn.getAttribute('data-rarity');
    tabs.forEach(btn => btn.onclick = () => { tabs.forEach(b => b.classList.remove('active')); btn.classList.add('active'); render(btn.getAttribute('data-rarity'));
      showUniqueCounter();
    });
    render(active);
    function showUniqueCounter() {
      const uniqueIds = new Set(cards.map(c => c.id_card));
      const uniqueCount = uniqueIds.size;
      const block = document.getElementById('collection-unique-counter');
      let totalHtml = '?';
      if (typeof allCardsTotal === 'number' && allCardsTotal > 0) totalHtml = allCardsTotal;
      if (block) {
        block.innerHTML = `
          <div style="display:flex;gap:6px;align-items:center;justify-content:center;font-family:'Inter',sans-serif;font-size:18px;margin-bottom:6px;">
            <span style="color:#fff;">Unique cards collected:</span>
            <span class="trade-qty-badge" style="position:static;top:auto;right:auto;min-width:38px;font-size:16px;">${uniqueCount} / ${totalHtml}</span>
          </div>`;
      }
    }
    showUniqueCounter();

    // Attach trade button handler
    try {
      const tradeBtn = document.getElementById('openTradeModal');
      if (tradeBtn) {
        tradeBtn.onclick = () => openTradeModal();
      }
    } catch(_) {}
  } catch (e) { console.error('Load user cards error', e); }
}

async function openTradeModal(requestedRarity) {
  const { body } = modalElements();
  if (!body) return;
  const reqCounts = { basic: 4, rare: 3, epic: 2 }; // Убрали legendary
  let active = requestedRarity || 'basic';
  
  // Load user's cards with quantity
  let cardsByRarity = { basic: [], rare: [], epic: [] };
  try {
    const res = await fetch(`/api/user/${currentWallet}/cards`, {
      headers: {
        'X-Wallet': currentWallet,
        'X-Signature': sessionStorage.getItem('signature') || '',
        'X-Message': sessionStorage.getItem('message') || ''
      }
    });
    const data = await res.json();
    const cards = (data && data.cards) ? data.cards : [];
    cards.forEach(c => { if (c.rarity === 'common') c.rarity = 'basic'; });
    cardsByRarity = {
      basic: cards.filter(c => c.rarity === 'basic' && c.quantity > 0),
      rare: cards.filter(c => c.rarity === 'rare' && c.quantity > 0),
      epic: cards.filter(c => c.rarity === 'epic' && c.quantity > 0)
    };
    if (requestedRarity && Array.isArray(cardsByRarity[requestedRarity]) && cardsByRarity[requestedRarity].length > 0) {
      active = requestedRarity;
    } else if (cardsByRarity.basic.length === 0 && cardsByRarity.rare.length === 0 && cardsByRarity.epic.length === 0) {
      showMessage('You have no cards to trade');
      return;
    } else if (cardsByRarity[active].length === 0) {
      // Если активная вкладка пуста, выбираем первую непустую
      if (cardsByRarity.basic.length > 0) active = 'basic';
      else if (cardsByRarity.rare.length > 0) active = 'rare';
      else if (cardsByRarity.epic.length > 0) active = 'epic';
    }
  } catch (e) {
    console.error('Error loading cards for trade:', e);
    showMessage('Failed to load cards');
    return;
  }
  
  body.innerHTML = `
    <div class="modal-title">TRADE CARDS</div>
    <div style="display:flex;gap:12px;margin:8px 0;">
      <button class="btn ${active === 'basic' ? 'btn-primary' : 'btn-secondary'}" data-r="basic">BASIC</button>
      <button class="btn ${active === 'rare' ? 'btn-primary' : 'btn-secondary'}" data-r="rare">RARE</button>
      <button class="btn ${active === 'epic' ? 'btn-primary' : 'btn-secondary'}" data-r="epic">EPIC</button>
    </div>
    <div id="trade-grid" style="width:100%;max-height:50vh;overflow:auto;text-align:left"></div>
    <div style="margin-top:8px;opacity:.9;font-family:'Inter',sans-serif">Select <span id="needCnt">${reqCounts[active]}</span> cards to trade</div>
    <div class="modal-actions">
      <button class="btn btn-secondary" id="trade-cancel">Cancel</button>
      <button class="btn btn-primary" id="trade-submit" disabled>Trade</button>
    </div>
  `;
  openModal();
  const grid = document.getElementById('trade-grid');
  const needCntEl = document.getElementById('needCnt');
  const submitBtn = document.getElementById('trade-submit');
  const cancelBtn = document.getElementById('trade-cancel');
  const tabBtns = body.querySelectorAll('button[data-r]');

  // Selected cards with quantities: { id_card: quantity }
  let selectedCardsLocal = {};

  function renderTrade(r) {
    active = r;
    tabBtns.forEach(b => {
      b.classList.toggle('btn-primary', b.getAttribute('data-r') === r);
      b.classList.toggle('btn-secondary', b.getAttribute('data-r') !== r);
    });
    needCntEl.textContent = String(reqCounts[r]);
    submitBtn.disabled = true;
    selectedCardsLocal = {}; // Сбрасываем выбор при смене вкладки
    const arr = cardsByRarity[r] || [];
    grid.innerHTML = '';
    
    if (arr.length === 0) {
      grid.innerHTML = '<div style="text-align:center;padding:20px;opacity:0.7;font-family:\'Inter\',sans-serif;">No cards of this rarity</div>';
      return;
    }
    
    arr.forEach(card => {
      const item = document.createElement('div');
      item.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #222;position:relative;';
      const maxQuantity = Math.min(card.quantity, reqCounts[r]);
      item.innerHTML = `
        ${card.image_url ? `<img src="${card.image_url}" style="width:48px;height:auto;border-radius:6px;"/>` : ''}
        <div style="flex:1;">
          <div style="font-family:\'Roboto Flex\',sans-serif;font-weight:800;">${card.name || `CARD #${card.id_card}`}</div>
          <div style="font-family:\'Inter\',sans-serif;opacity:.9;font-size:12px;">Tickets: ${card.start_bounty} | Available: ${card.quantity}</div>
        </div>
        <div style="min-width:120px;display:flex;align-items:center;gap:8px;">
          <button class="trade-qty-btn trade-qty-minus" data-card="${card.id_card}" style="width:28px;height:28px;border-radius:6px;border:1px solid #444;background:#2a2a2a;color:#fff;cursor:pointer;font-weight:700;">−</button>
          <input type="number" class="trade-qty-input" data-card="${card.id_card}" value="0" min="0" max="${maxQuantity}" style="width:40px;text-align:center;background:#1a1a1a;border:1px solid #444;border-radius:4px;color:#fff;padding:4px;font-family:\'Roboto Flex\',sans-serif;font-weight:700;" readonly>
          <button class="trade-qty-btn trade-qty-plus" data-card="${card.id_card}" style="width:28px;height:28px;border-radius:6px;border:1px solid #444;background:#2a2a2a;color:#fff;cursor:pointer;font-weight:700;">+</button>
        </div>
      `;
      grid.appendChild(item);
    });
    
    // Обработчики для кнопок количества
    grid.querySelectorAll('.trade-qty-plus').forEach(btn => {
      btn.addEventListener('click', () => {
        const cardId = Number(btn.getAttribute('data-card'));
        const input = grid.querySelector(`.trade-qty-input[data-card="${cardId}"]`);
        const card = arr.find(c => c.id_card === cardId);
        if (input && card) {
          const current = parseInt(input.value) || 0;
          const max = Math.min(card.quantity, reqCounts[r]);
          if (current < max) {
            input.value = current + 1;
            selectedCardsLocal[cardId] = (selectedCardsLocal[cardId] || 0) + 1;
            updateSubmitState();
          }
        }
      });
    });
    
    grid.querySelectorAll('.trade-qty-minus').forEach(btn => {
      btn.addEventListener('click', () => {
        const cardId = Number(btn.getAttribute('data-card'));
        const input = grid.querySelector(`.trade-qty-input[data-card="${cardId}"]`);
        if (input) {
          const current = parseInt(input.value) || 0;
          if (current > 0) {
            input.value = current - 1;
            selectedCardsLocal[cardId] = (selectedCardsLocal[cardId] || 0) - 1;
            if (selectedCardsLocal[cardId] <= 0) {
              delete selectedCardsLocal[cardId];
            }
            updateSubmitState();
          }
        }
      });
    });
    
    updateSubmitState();
  }
  
  function updateSubmitState() {
    const needCount = reqCounts[active];
    const totalSelected = Object.values(selectedCardsLocal).reduce((sum, qty) => sum + qty, 0);
    submitBtn.disabled = totalSelected !== needCount;
  }
  
  tabBtns.forEach(b => b.addEventListener('click', () => renderTrade(b.getAttribute('data-r'))));
  cancelBtn?.addEventListener('click', closeModal);
  submitBtn?.addEventListener('click', async () => {
    try {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Processing...';
      
      // Формируем массив карт для обмена: [{ id_card, quantity }, ...]
      const cardsToTrade = Object.entries(selectedCardsLocal)
        .filter(([_, qty]) => qty > 0)
        .map(([id_card, quantity]) => ({ id_card: Number(id_card), quantity: Number(quantity) }));
      
      const resp = await fetch('/api/cards/trade', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-Wallet': currentWallet,
          'X-Signature': sessionStorage.getItem('signature') || '',
          'X-Message': sessionStorage.getItem('message') || ''
        },
        body: JSON.stringify({ wallet: currentWallet, cards: cardsToTrade, rarity: active })
      });
      const d = await resp.json();
      if (d && d.success) {
        body.innerHTML = `
          <div class="modal-title">Trade complete</div>
          ${d.card?.image_url ? `<img class="modal-image" src="${d.card.image_url}" alt="card">` : ''}
          <div style="font-family:'Inter',sans-serif;font-size:18px;opacity:.95;">You received a ${String(d.card?.rarity || '').toUpperCase()} card!</div>
          <div class="modal-actions"><button class="btn btn-primary" id="trade-ok">OK</button></div>
        `;
        document.getElementById('trade-ok')?.addEventListener('click', () => { closeModal(); loadUserCards(currentWallet); }, { once: true });
      } else {
        showMessage(d?.error || 'Trade failed');
        submitBtn.disabled = false; submitBtn.textContent = 'Trade';
      }
    } catch (e) {
      console.error('Trade error:', e);
      showMessage('Trade failed');
      submitBtn.disabled = false; submitBtn.textContent = 'Trade';
    }
  });
  renderTrade(active);
}

// Function to scroll to specific section
function scrollToSection(sectionName) {
  const section = document.querySelector(`[data-section="${sectionName}"]`);
  if (section) {
    // First show the home page to make sections visible
    showPage('home');
    
    // Then scroll to the section with a small delay
    setTimeout(() => {
      section.scrollIntoView({ 
        behavior: 'smooth',
        block: 'start'
      });
    }, 100);
  }
}

// ------- Modal helpers --------
function modalElements(){
  return {
    overlay: document.getElementById('modal-overlay'),
    modal: document.getElementById('purchase-modal'),
    body: document.getElementById('modal-body'),
  };
}

function openModal(){ const {overlay, modal} = modalElements(); overlay?.classList.remove('hidden'); modal?.classList.remove('hidden'); }
function closeModal(){ const {overlay, modal} = modalElements(); overlay?.classList.add('hidden'); modal?.classList.add('hidden'); }

function getPackImage(id){
  if (id === 2) return 'rare.png';
  if (id === 3) return 'epic.png';
  if (id === 4) return 'legendary.png';
  if (id === 5) return 'broke.png';
  return 'common.png';
}

function getPackLabel(id){
  if (id === 2) return 'RARE PACK';
  if (id === 3) return 'EPIC PACK';
  if (id === 4) return 'LEGENDARY PACK';
  if (id === 5) return 'BROKE PACK';
  return 'BASIC PACK';
}

function showPurchaseLoadingModal(quantity, idChest) {
  const { body } = modalElements();
  if (!body) return;
  
  const packText = quantity === 1 ? 'pack' : 'packs';
  body.innerHTML = `
    <div class="purchase-loading-modal">
      <div class="modal-title">Processing Purchase</div>
      <div class="purchase-loading-content">
        <div class="purchase-spinner"></div>
        <div class="purchase-status-text" id="purchase-status-text">Sending transaction...</div>
        <div class="purchase-status-subtitle" id="purchase-status-subtitle">Please wait, this may take a few moments</div>
      </div>
      <div class="purchase-info">
        <div class="purchase-quantity">Quantity: ${quantity} ${packText}</div>
        <div class="purchase-note">Do not close this window or navigate away</div>
      </div>
    </div>
  `;
  openModal();
}

function updatePurchaseLoadingStatus(stage, message) {
  const statusText = document.getElementById('purchase-status-text');
  const statusSubtitle = document.getElementById('purchase-status-subtitle');
  
  if (statusText) {
    statusText.textContent = message;
  }
  
  // Обновляем подзаголовок в зависимости от этапа
  if (statusSubtitle) {
    switch(stage) {
      case 'confirming':
        statusSubtitle.textContent = 'Waiting for blockchain confirmation...';
        break;
      case 'verifying':
        statusSubtitle.textContent = 'Verifying your purchase with our servers...';
        break;
      default:
        statusSubtitle.textContent = 'Please wait, this may take a few moments';
    }
  }
}

function showPurchaseModal(idChest, opts = {}){
  const { attachOpenHandler = true } = opts;
  const { body } = modalElements();
  if (!body) return;
  body.innerHTML = `
    <div class="modal-title">Purchase successful</div>
    <img class="modal-image" src="img/${getPackImage(idChest)}" alt="pack">
    <div class="modal-actions">
      <button class="btn btn-secondary" id="pm-later">Open later</button>
      <button class="btn btn-primary" id="pm-open">Open now</button>
    </div>
  `;
  openModal();
  document.getElementById('pm-later')?.addEventListener('click', closeModal, { once: true });
  if (attachOpenHandler) {
    document.getElementById('pm-open')?.addEventListener('click', async () => { await openPurchasedPack(idChest); }, { once: true });
  }
}

async function openPurchasedPack(idChest){
  try {
    // fetch unopened chests and find one by type
    const res = await fetch(`/api/user/${currentWallet}/chests`);
    const d = await res.json();
    const unopened = (d.chests || []).filter(x => !x.is_opened && x.id_chest === Number(idChest));
    if (!unopened.length) { closeModal(); return; }
    const c = unopened[0];
    const resp = await fetch('/api/chests/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ wallet: currentWallet, id_purchase: c.id_purchase }) });
    const r = await resp.json();
    if (r && r.success) {
      // Get cashback info from server response
      let cashbackInfo = '';
      if (r.cashback) {
        const multiplier = r.cashback.multiplier;
        const cashbackAmount = r.cashback.amount.toFixed(2);
        cashbackInfo = `<div style="color: #FFD700; font-size: 16px; margin-top: 10px; font-weight: 600;">Cashback: ${cashbackAmount} $TOKENS (${multiplier}x)</div>`;
      }

      const { body } = modalElements();
      if (body) {
        const title = r.lost ? 'Oops...' : 'Congratulations!';
        const sub = r.lost ? 'Nothing dropped' : `You received a card: ${String(r.rarity).toUpperCase()}`;
        const img = (!r.lost && r.image_url) ? `<img class="modal-image" src="${r.image_url}" alt="card">` : '';
        body.innerHTML = `
          <div class="modal-title">${title}</div>
          ${img}
          <div style="font-family: 'Inter', sans-serif; font-size: 18px; opacity: .95;">${sub}</div>
          ${cashbackInfo}
          <div class="modal-actions">
            <button class="btn btn-primary" id="pm-ok">OK</button>
          </div>
        `;
        document.getElementById('pm-ok')?.addEventListener('click', () => { 
          closeModal(); 
          loadMyPacks(currentWallet); 
          loadUserCards(currentWallet); 
          loadCashback(); 
          // Обновляем джекпоты после открытия пака (может быть выигран супер джекпот)
          if (typeof loadJackpot === 'function') loadJackpot();
          if (typeof loadSuperJackpot === 'function') loadSuperJackpot();
        });
      }
    } else {
      showMessage(r?.error || 'Failed to open pack');
      closeModal();
    }
  } catch {
    closeModal();
  }
}

// ==================== BATTLE SYSTEM ====================

let currentBattleId = null;
let battleTimer = null;
let battleTimeExpired = false;
let pollInterval = null;
let currentUserId = null;

// Initialize battle button handler
function initializeBattleButton() {
  const startBattleBtn = document.getElementById('start-battle-btn');
  if (startBattleBtn && !startBattleBtn.hasAttribute('data-listener-added')) {
    startBattleBtn.addEventListener('click', async () => {
      if (!currentWallet) {
        showMessage('Please connect your Phantom wallet first');
        return;
      }
      await startBattle();
    });
    startBattleBtn.setAttribute('data-listener-added', 'true');
  }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', initializeBattleButton);

// Also initialize when the script loads (in case DOM is already ready)
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initializeBattleButton);
} else {
  initializeBattleButton();
}

async function startBattle() {
  try {
    const startBtn = document.getElementById('start-battle-btn');
    if (startBtn) {
      startBtn.disabled = true;
      startBtn.textContent = 'Checking cards...';
    }

    // First check if user has cards
    const cardsResponse = await fetch(`/api/user/${currentWallet}/cards`);
    const cardsData = await cardsResponse.json();
    
    if (!cardsData.success || !cardsData.cards || cardsData.cards.length === 0) {
      showMessage('You need to have cards to participate in battles. Please buy some packs first!');
      return;
    }

    // Check if user has any cards with quantity > 0
    const hasCards = cardsData.cards.some(card => card.quantity > 0);
    if (!hasCards) {
      showMessage('You need to have cards to participate in battles. Please buy some packs first!');
      return;
    }

    if (startBtn) {
      startBtn.textContent = 'Verifying wallet...';
    }

    // Get config for transaction (rpc and merchant)
    const configResponse = await fetch('/api/config');
    const config = await configResponse.json();
    if (!config.success) {
      showMessage('Failed to get configuration');
      return;
    }

    // Create signature for wallet verification
    const message = `Gamba Battle Verify\nwallet=${currentWallet}\nnonce=${Date.now()}`;
    const signature = await signMessage(message, currentWallet);

    if (startBtn) {
      startBtn.textContent = 'Creating transaction...';
    }

    // Create SOL transfer transaction for 0.01 SOL
    const provider = window.solana;
    if (!provider) { 
      showMessage('Phantom not found'); 
      return; 
    }

    const { Connection, PublicKey, Transaction, TransactionInstruction, SystemProgram } = window.Web3Lib;
    const connection = new Connection(config.rpcUrl);
    const buyerPk = new PublicKey(currentWallet);
    const merchantPk = new PublicKey(config.merchant);

    const tx = new Transaction();
    // 0.01 SOL in lamports
    const amountLamports = Math.floor(0.01 * 1e9);
    const instructionData = new Uint8Array(12);
    instructionData[0] = 2; // Transfer
    const amountBigInt = BigInt(amountLamports);
    for (let i = 0; i < 8; i++) instructionData[4 + i] = Number((amountBigInt >> BigInt(i * 8)) & 0xFFn);
    const transferInstruction = new TransactionInstruction({
      keys: [
        { pubkey: buyerPk, isSigner: true, isWritable: true },
        { pubkey: merchantPk, isSigner: false, isWritable: true },
      ],
      programId: SystemProgram.programId,
      data: instructionData
    });
    tx.add(transferInstruction);
    
    // Send transaction
    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash();
    tx.recentBlockhash = blockhash; 
    tx.feePayer = buyerPk;
    
    let txSignature;
    if (provider.signAndSendTransaction) { 
      const sendRes = await provider.signAndSendTransaction(tx); 
      txSignature = sendRes.signature || sendRes; 
    } else if (provider.signTransaction) { 
      const signedTx = await provider.signTransaction(tx); 
      txSignature = await connection.sendRawTransaction(signedTx.serialize(), { skipPreflight: false, maxRetries: 3 }); 
    } else { 
      showMessage('Your Phantom version does not support sending transactions'); 
      return; 
    }
    
    // Confirm transaction
    try { 
      await connection.confirmTransaction({ signature: txSignature, blockhash, lastValidBlockHeight }, 'confirmed'); 
    } catch {}
    
    if (startBtn) {
      startBtn.textContent = 'Starting battle...';
    }

    // Start battle with transaction signature (server will verify 0.01 SOL)
    const response = await fetch('/api/battle/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        wallet: currentWallet, 
        signature: signature.signature,
        message: message,
        txSignature: txSignature
      })
    });

    const data = await response.json();
    if (data.success) {
      currentBattleId = data.battleId;
      currentUserId = data.userId;
      showBattleModal();
    } else {
      showMessage(data.error || 'Failed to start battle');
    }
  } catch (error) {
    console.error('Start battle error:', error);
    showMessage('Failed to start battle: ' + (error.message || 'Unknown error'));
  } finally {
    const startBtn = document.getElementById('start-battle-btn');
    if (startBtn) {
      startBtn.disabled = false;
      startBtn.textContent = 'START BATTLE';
    }
  }
}

function showBattleModal() {
  const { body } = modalElements();
  if (!body) return;

  // Clear any previous card selections
  selectedCards = [];
  selectedCardsData = [];
  console.log('Cleared card selections in showBattleModal');

  body.innerHTML = `
    <div class="battle-modal" id="battle-modal">
      <!-- Stage 1: Searching for opponent -->
      <div class="battle-stage active" id="search-stage">
        <div class="searching-opponent">
          <div class="searching-spinner"></div>
          <div class="searching-text">Searching for opponent...</div>
          <div class="searching-subtitle">Finding a worthy challenger</div>
        </div>
      </div>

      <!-- Stage 2: Card selection -->
      <div class="battle-stage" id="selection-stage">
        <div class="battle-header">
          <div class="battle-title">Choose Your Cards</div>
          <div class="battle-subtitle">Select up to 5 cards for battle</div>
          <div class="battle-timer" id="selection-timer">Time Left: 30</div>
        </div>
        <div class="battle-cards-selection" id="battle-cards-grid"></div>
        <div class="battle-summary" id="selection-summary" style="display: none;">
          <h3>Selected Cards</h3>
          <div id="selected-cards-list"></div>
          <div class="modal-actions">
            <button class="btn btn-primary" id="battle-confirm" disabled>Confirm Selection</button>
          </div>
        </div>
      </div>

      <!-- Stage 3: Waiting for opponent -->
      <div class="battle-stage" id="waiting-stage">
        <div class="battle-header">
          <div class="battle-title">Waiting for Opponent</div>
          <div class="battle-subtitle">Your cards are ready, waiting for opponent to confirm...</div>
        </div>
        <div class="searching-opponent">
          <div class="searching-spinner"></div>
          <div class="searching-text">Opponent is selecting cards...</div>
          <div class="searching-subtitle">Please wait</div>
        </div>
      </div>

      <!-- Stage 4: Battle execution -->
      <div class="battle-stage" id="battle-stage">
        <div class="battle-header">
          <div class="battle-title">Battle in Progress</div>
          <div class="battle-subtitle">May the strongest cards win!</div>
        </div>
        <div class="battle-summary">
          <h3>Battle Summary</h3>
          <div class="battle-summary-stats">
            <div class="battle-player">
              <div class="battle-player-name">You</div>
              <div class="battle-player-power" id="player-power">0</div>
            </div>
            <div class="battle-vs">VS</div>
            <div class="battle-player">
              <div class="battle-player-name">Opponent</div>
              <div class="battle-player-power" id="opponent-power">0</div>
            </div>
          </div>
          <div class="battle-progress">
            <div class="battle-progress-bar" id="battle-progress-bar"></div>
          </div>
        </div>
      </div>

      <!-- Stage 5: Battle result -->
      <div class="battle-stage" id="result-stage">
        <div class="battle-result">
          <div class="battle-result-title" id="result-title">Victory!</div>
          <div class="battle-result-subtitle" id="result-subtitle">You won the battle!</div>
          <div class="battle-rewards" id="battle-rewards" style="display: none;">
            <h4>Cards Won</h4>
            <div class="battle-rewards-grid" id="rewards-grid"></div>
          </div>
          <div class="modal-actions">
            <button class="btn btn-primary" id="battle-close">Close</button>
          </div>
        </div>
      </div>
    </div>
  `;

  openModal();
  
  // Start polling for opponent
  pollInterval = setInterval(pollForOpponent, 2000); // Check every 2 seconds
}

function showBattleStage(stage) {
  // Handle all battle stages
  const stages = ['search', 'selection', 'waiting', 'battle', 'result'];
  stages.forEach(s => {
    const element = document.getElementById(`${s}-stage`);
    if (element) {
      element.classList.remove('active');
    }
  });
  
  const targetElement = document.getElementById(`${stage}-stage`);
  if (targetElement) {
    targetElement.classList.add('active');
  }
}

async function pollForOpponent() {
  if (!currentBattleId) return;
  
  try {
    console.log('Polling for opponent, battleId:', currentBattleId);
    const response = await fetch(`/api/battle/${currentBattleId}/status`);
    const data = await response.json();
    console.log('Battle status response:', data);
    
    if (data.success && data.status === 'preparing') {
      // Opponent found! Switch to card selection
      console.log('Opponent found! Both players ready for battle');
      clearInterval(pollInterval);
      pollInterval = null;
      showBattleStage('selection');
      loadBattleCards();
      startSelectionTimer();
    } else if (data.success && data.status === 'searching') {
      // Still searching, continue polling
      console.log('Still searching for opponent...');
    } else if (data.success && data.status === 'battling') {
      // This should not happen - battle should not start without both players
      console.error('ERROR: Battle started without both players! This should not happen.');
      clearInterval(pollInterval);
      pollInterval = null;
      showNotification('Battle error: Invalid state', 'error');
      hideBattleModal();
    } else {
      // Error or battle cancelled
      console.log('Battle error or cancelled:', data);
      clearInterval(pollInterval);
      pollInterval = null;
      showNotification('Battle cancelled or error occurred', 'error');
      hideBattleModal();
    }
  } catch (error) {
    console.error('Error polling for opponent:', error);
    clearInterval(pollInterval);
    pollInterval = null;
    showNotification('Error checking battle status', 'error');
    hideBattleModal();
  }
}

async function pollForBattleExecution() {
  if (!currentBattleId) return;
  
  try {
    console.log('Polling for battle execution, battleId:', currentBattleId);
    const response = await fetch(`/api/battle/${currentBattleId}/status`);
    const data = await response.json();
    console.log('Battle execution status response:', data);
    
    if (data.success && data.status === 'battling') {
      // Both players ready! Show battle stage
      console.log('Both players ready, showing battle stage');
      clearInterval(pollInterval);
      pollInterval = null;
      
      // Show battle stage
      showBattleStage('battle');
      
      // Update battle display with real powers
      updateBattleDisplay(data);
      
      // Show battle animation for 2 seconds
      setTimeout(async () => {
        // Execute battle
        const executeResponse = await fetch(`/api/battle/${currentBattleId}/execute`, {
          method: 'POST'
        });
        const executeData = await executeResponse.json();
        
        if (executeData.success) {
          console.log('Battle executed successfully');
          // Get battle result
          const resultResponse = await fetch(`/api/battle/${currentBattleId}/result?userId=${currentUserId}`);
          const resultData = await resultResponse.json();
          
          if (resultData.success) {
            showBattleResult(resultData.battle);
          } else {
            showNotification('Failed to get battle result', 'error');
            hideBattleModal();
          }
        } else {
          showNotification('Failed to execute battle', 'error');
          hideBattleModal();
        }
      }, 2000);
    } else if (data.success && data.status === 'preparing') {
      // Still waiting for both players to submit
      console.log('Still waiting for both players to submit cards...');
    } else {
      // Error or battle cancelled
      console.log('Battle error or cancelled:', data);
      clearInterval(pollInterval);
      pollInterval = null;
      showNotification('Battle cancelled or error occurred', 'error');
      hideBattleModal();
    }
  } catch (error) {
    console.error('Error polling for battle execution:', error);
    clearInterval(pollInterval);
    pollInterval = null;
    showNotification('Error checking battle status', 'error');
    hideBattleModal();
  }
}

async function loadBattleCards() {
  try {
    console.log('Loading battle cards for wallet:', currentWallet);
    
    // Clear previous selections
    selectedCards = [];
    selectedCardsData = [];
    console.log('Cleared previous card selections');
    
    const response = await fetch(`/api/user/${currentWallet}/copies`);
    const data = await response.json();
    console.log('Battle cards response:', data);
    
    if (!data.success) {
      console.error('Failed to load battle cards:', data.error);
      return;
    }

    const grid = document.getElementById('battle-cards-grid');
    if (!grid) {
      console.error('Battle cards grid not found');
      return;
    }

    grid.innerHTML = '';
    const copies = (data.copies || []);
    console.log('Copies for battle:', copies);

    // Render copies as individual selectable items
    copies.forEach(copy => {
      const el = document.createElement('div');
      el.className = 'battle-card-item';
      el.setAttribute('data-item-id', copy.id_item);
      
      const img = copy.image_url ? `<img src="${copy.image_url}" alt="${copy.name}">` : '';
      const effectivePower = Math.round(Number(copy.effective_power || 0));
      
      // Show both base and effective power
      const powerDisplay = `Power: ${effectivePower}`;
      
      el.innerHTML = `
        ${img}
        <div class="battle-card-name">${copy.name || `Card #${copy.id_card}`}</div>
        <div class="battle-card-power">${powerDisplay}</div>
      `;

      el.addEventListener('click', () => toggleCopySelection(el, copy));
      grid.appendChild(el);
    });
  } catch (error) {
    console.error('Load battle cards error:', error);
  }
}

let selectedItemIds = [];
let selectedCopiesData = [];

function toggleCopySelection(element, copy) {
  const id = Number(copy.id_item);
  if (selectedItemIds.includes(id)) {
    selectedItemIds = selectedItemIds.filter(x => x !== id);
    selectedCopiesData = selectedCopiesData.filter(c => c.id_item !== id);
    element.classList.remove('selected');
  } else if (selectedItemIds.length < 5) {
    selectedItemIds.push(id);
    selectedCopiesData.push(copy);
    element.classList.add('selected');
  }
  updateSelectionSummary();
}

function updateSelectionSummary() {
  const summary = document.getElementById('selection-summary');
  const list = document.getElementById('selected-cards-list');
  const confirmBtn = document.getElementById('battle-confirm');

  if (selectedItemIds.length === 0) {
    summary.style.display = 'none';
    return;
  }

  summary.style.display = 'block';
  list.innerHTML = '';

  let totalPower = 0;
  selectedCopiesData.forEach(card => {
    const power = Math.round(Number(card.effective_power || 0));
    totalPower += power;
    
    const item = document.createElement('div');
    item.style.cssText = 'display: flex; align-items: center; gap: 10px; margin-bottom: 8px;';
    
    item.innerHTML = `
      ${card.image_url ? `<img src="${card.image_url}" style="width: 40px; height: auto; border-radius: 6px;">` : ''}
      <div style="flex: 1;">
        <div style="font-weight: 800;">${card.name || `Card #${card.id_card}`}</div>
        <div style="opacity: 0.8; font-size: 14px;">Power: ${power}</div>
      </div>
    `;
    list.appendChild(item);
  });

  const totalItem = document.createElement('div');
  totalItem.style.cssText = 'margin-top: 12px; padding-top: 12px; border-top: 1px solid #333; font-weight: 800; font-size: 18px;';
  totalItem.textContent = `Total Power: ${totalPower}`;
  list.appendChild(totalItem);

  confirmBtn.disabled = selectedItemIds.length === 0;
}

function startSelectionTimer() {
  // Clear any existing timer first
  if (battleTimer) {
    clearInterval(battleTimer);
    battleTimer = null;
  }
  
  let timeLeft = 30;
  const timerEl = document.getElementById('selection-timer');
  battleTimeExpired = false;
  
  battleTimer = setInterval(() => {
    timeLeft--;
    if (timerEl) timerEl.textContent = `Time Left: ${timeLeft}`;
    
    if (timeLeft <= 0) {
      clearInterval(battleTimer);
      battleTimer = null;
      battleTimeExpired = true;
      showNotification('Time up! Battle cancelled.', 'error');
      hideBattleModal();
    }
  }, 1000);
}

async function submitBattleCards() {
  try {
    // Clear timer immediately when submitting
    clearInterval(battleTimer);
    battleTimer = null;
    
    // Check if time has expired
    if (battleTimeExpired) {
      return; // Don't submit if time already expired
    }
    
    const response = await fetch(`/api/battle/${currentBattleId}/cards`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        wallet: currentWallet,
        selectedItemIds: selectedItemIds
      })
    });

    const data = await response.json();
    if (data.success) {
      console.log('Cards submitted successfully, response:', data);
      
      // Clear timer
      if (battleTimer) {
        clearInterval(battleTimer);
        battleTimer = null;
      }
      
      // Check if both players are ready (status should be battling)
      console.log('Cards submitted, checking if both players are ready...');
      showBattleStage('waiting');
      
      // Start polling for battle execution
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      pollInterval = setInterval(pollForBattleExecution, 1000);
    } else {
      showNotification(data.error || 'Failed to submit cards', 'error');
    }
  } catch (error) {
    console.error('Submit battle cards error:', error);
    showNotification('Failed to submit cards', 'error');
  }
}

function updateBattleDisplay(battleData) {
  console.log('Updating battle display with data:', battleData);
  
  const playerPowerEl = document.getElementById('player-power');
  const opponentPowerEl = document.getElementById('opponent-power');
  const progressBar = document.getElementById('battle-progress-bar');
  
  if (!playerPowerEl || !opponentPowerEl || !progressBar) {
    console.error('Battle display elements not found');
    return;
  }
  
  // Get current user's power from selected cards
  const playerPower = selectedCardsData.reduce((sum, card) => {
    const power = Number(card.effective_tickets || card.start_bounty || 0);
    return sum + power;
  }, 0);
  
  // Get opponent's power from battle data
  let opponentPower = 0;
  if (battleData.player1Power !== null && battleData.player2Power !== null) {
    // Determine which power belongs to opponent
    if (battleData.player1Id == currentUserId) {
      opponentPower = Number(battleData.player2Power);
      console.log(`User is Player 1. Player Power: ${playerPower}, Opponent Power: ${opponentPower}`);
    } else {
      opponentPower = Number(battleData.player1Power);
      console.log(`User is Player 2. Player Power: ${playerPower}, Opponent Power: ${opponentPower}`);
    }
  }
  
  // Update display
  playerPowerEl.textContent = Math.round(playerPower);
  opponentPowerEl.textContent = Math.round(opponentPower);
  
  // Animate progress bar
  const totalPower = playerPower + opponentPower;
  const playerChance = totalPower > 0 ? playerPower / totalPower : 0.5;
  
  console.log(`Progress bar: playerPower=${playerPower}, opponentPower=${opponentPower}, totalPower=${totalPower}, playerChance=${(playerChance * 100).toFixed(1)}%`);
  
  setTimeout(() => {
    progressBar.style.width = `${playerChance * 100}%`;
    console.log(`Progress bar set to: ${(playerChance * 100).toFixed(1)}%`);
  }, 1000);
}

// Removed pollForBattleResult function - using simple timeout approach

function showBattleResult(result) {
  console.log(`[Frontend Battle] Showing battle result:`, result);
  console.log(`[Frontend Battle] Current user ID: ${currentUserId}`);
  console.log(`[Frontend Battle] Winner ID: ${result.winner_id}`);
  console.log(`[Frontend Battle] Is Winner: ${result.isWinner}`);
  
  showBattleStage('result');
  
  const isWinner = result.isWinner;
  // Normalize transferred cards field (server may return snake_case)
  let transferred = Array.isArray(result.transferredCards) ? result.transferredCards : null;
  if (!transferred) {
    if (Array.isArray(result.transferred_cards)) transferred = result.transferred_cards;
    else if (typeof result.transferred_cards === 'string') { try { transferred = JSON.parse(result.transferred_cards); } catch { transferred = []; } }
    else if (typeof result.transferredCards === 'string') { try { transferred = JSON.parse(result.transferredCards); } catch { transferred = []; } }
  }
  if (!Array.isArray(transferred)) transferred = [];
  const titleEl = document.getElementById('result-title');
  const subtitleEl = document.getElementById('result-subtitle');
  const rewardsEl = document.getElementById('battle-rewards');
  const rewardsGrid = document.getElementById('rewards-grid');

  if (titleEl) {
    titleEl.textContent = isWinner ? 'Victory!' : 'Defeat';
    titleEl.className = `battle-result-title ${isWinner ? 'victory' : 'defeat'}`;
  }

  if (subtitleEl) {
    subtitleEl.textContent = isWinner ? 
      `You won ${transferred.length} cards!` : 
      'Better luck next time!';
  }

  if (isWinner && transferred.length > 0) {
    rewardsEl.style.display = 'block';
    rewardsGrid.innerHTML = '';
    
    transferred.forEach(card => {
      const cardEl = document.createElement('div');
      cardEl.className = 'battle-reward-card';
      cardEl.innerHTML = `
        ${card.image_url ? `<img src="${card.image_url}" alt="${card.name}">` : ''}
        <div class="battle-reward-card-name">${card.name || `Card #${card.id_card}`}</div>
      `;
      rewardsGrid.appendChild(cardEl);
    });
  }

  // Setup close button
  const closeBtn = document.getElementById('battle-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      // Clear battle before closing
      if (currentBattleId && currentWallet) {
        try {
          fetch('/api/battle/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ wallet: currentWallet })
          }).catch(error => {
            console.log('Battle clear error (non-critical):', error);
          });
        } catch (error) {
          console.log('Battle clear error (non-critical):', error);
        }
      }
      
      closeModal();
      // Refresh user cards if on cards page
      if (currentWallet) {
        loadUserCards(currentWallet);
      }
    }, { once: true });
  }
}

function hideBattleModal() {
  // Clear all timers
  if (battleTimer) {
    clearInterval(battleTimer);
    battleTimer = null;
  }
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }

  // Clear battle when modal is closed
  if (currentBattleId && currentWallet) {
    try {
      fetch('/api/battle/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wallet: currentWallet })
      }).catch(error => {
        console.log('Battle clear error (non-critical):', error);
      });
    } catch (error) {
      console.log('Battle clear error (non-critical):', error);
    }
  }

  battleTimeExpired = false;
  currentBattleId = null;
  currentUserId = null;
  
  // Clear card selections
  selectedCards = [];
  selectedCardsData = [];
  console.log('Cleared card selections in hideBattleModal');

  const modal = document.getElementById('battle-modal');
  if (modal) {
    modal.style.display = 'none';
    document.body.classList.remove('modal-open');
  }
  // Also close the main modal overlay
  closeModal();
}

function showNotification(message, type = 'error') {
  // Remove existing notifications
  const existingNotifications = document.querySelectorAll('.notification');
  existingNotifications.forEach(n => n.remove());
  
  const notification = document.createElement('div');
  notification.className = `notification ${type}`;
  notification.textContent = message;
  
  document.body.appendChild(notification);
  
  // Auto remove after 5 seconds
  setTimeout(() => {
    if (notification.parentNode) {
      notification.remove();
    }
  }, 5000);
}

// Show entry payment modal
function showEntryModal(wallet, wlData) {
  const { body } = modalElements();
  if (!body) return;
  
  const entryPrice = wlData.entryPrice;
  const isWhitelisted = wlData.isWhitelisted;
  const statusText = isWhitelisted ? 'Whitelisted User' : 'Regular User';
  const statusColor = isWhitelisted ? '#4CAF50' : '#FF9800';
  
  body.innerHTML = `
    <div class="entry-modal">
      <div class="modal-title">Game Entry Required</div>
      <div class="entry-info">
        <div class="entry-status" style="color: ${statusColor}; font-weight: 800; margin-bottom: 16px;">
          ${statusText}
        </div>
        <div class="entry-price" style="font-size: 24px; font-weight: 800; margin-bottom: 16px;">
          Entry Fee: ${entryPrice} SOL
        </div>
        <div class="entry-description" style="margin-bottom: 24px; opacity: 0.8;">
          ${isWhitelisted ? 
            'You are on the whitelist! Pay only 0.11 SOL to enter the game.' : 
            'Pay 0.18 SOL to enter the game and start collecting cards.'
          }
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" id="entry-cancel">Cancel</button>
        <button class="btn btn-primary" id="entry-pay">Pay ${entryPrice} SOL</button>
      </div>
    </div>
  `;
  
  openModal();
  
  // Setup event handlers
  document.getElementById('entry-cancel')?.addEventListener('click', closeModal, { once: true });
  document.getElementById('entry-pay')?.addEventListener('click', async () => {
    await processEntryPayment(wallet, entryPrice);
  }, { once: true });
}

// Process entry payment
async function processEntryPayment(wallet, entryPrice) {
  try {
    const payBtn = document.getElementById('entry-pay');
    if (payBtn) {
      payBtn.disabled = true;
      payBtn.textContent = 'Processing...';
    }
    
    // Get config
    const configResponse = await fetch('/api/config');
    const config = await configResponse.json();
    if (!config.success) {
      showMessage('Failed to get configuration');
      return;
    }
    
    // Create signature for verification
    const message = `Gamba Entry Payment\nwallet=${wallet}\nnonce=${Date.now()}`;
    const signature = await signMessage(message, wallet);
    
    // Create SOL transfer transaction
    const provider = window.solana;
    if (!provider) {
      showMessage('Phantom not found');
      return;
    }
    
    const { Connection, PublicKey, Transaction, TransactionInstruction, SystemProgram } = window.Web3Lib;
    
    // Verify all components are available
    if (!Connection || !PublicKey || !Transaction || !SystemProgram) {
      showMessage('Web3 components not properly loaded. Please refresh the page.');
      return;
    }
    
    const connection = new Connection(config.rpcUrl);
    
    const buyerPk = new PublicKey(wallet);
    const merchantPk = new PublicKey(config.merchant);
    
    // Convert SOL to lamports
    const amountLamports = Math.floor(entryPrice * 1e9);
    console.log('Entry payment:', { entryPrice, amountLamports, type: typeof amountLamports });
    
    const tx = new Transaction();
    
    // Create transfer instruction manually to avoid lamports format issues
    const instructionData = new Uint8Array(12);
    instructionData[0] = 2; // Transfer instruction
    instructionData[1] = 0;
    instructionData[2] = 0;
    instructionData[3] = 0;
    
    // Add amount in little-endian format
    const amountBigInt = BigInt(amountLamports);
    for (let i = 0; i < 8; i++) {
      instructionData[4 + i] = Number((amountBigInt >> BigInt(i * 8)) & 0xFFn);
    }
    
    const transferInstruction = new TransactionInstruction({
      keys: [
        { pubkey: buyerPk, isSigner: true, isWritable: true },
        { pubkey: merchantPk, isSigner: false, isWritable: true },
      ],
      programId: SystemProgram.programId,
      data: instructionData
    });
    
    tx.add(transferInstruction);
    
    // Send transaction
    const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash();
    tx.recentBlockhash = blockhash;
    tx.feePayer = buyerPk;
    
    let txSignature;
    if (provider.signAndSendTransaction) {
      const sendRes = await provider.signAndSendTransaction(tx);
      txSignature = sendRes.signature || sendRes;
    } else if (provider.signTransaction) {
      const signedTx = await provider.signTransaction(tx);
      txSignature = await connection.sendRawTransaction(signedTx.serialize(), { skipPreflight: false, maxRetries: 3 });
    } else {
      showMessage('Your Phantom version does not support sending transactions');
      return;
    }
    
    // Confirm transaction
    try {
      await connection.confirmTransaction({ signature: txSignature, blockhash, lastValidBlockHeight }, 'confirmed');
    } catch {}
    
    // Send payment to server
    const response = await fetch('/api/entry/pay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        wallet: wallet,
        txSignature: txSignature,
        signature: signature.signature,
        message: message
      })
    });
    
    const data = await response.json();
    if (data.success) {
      showMessage(`Entry successful! Welcome to the game!`, 'success');
      closeModal();
      showUserInfo(wallet, data.refCode, { redirect: true });
      window.location.href = '/profile';
    } else {
      showMessage(data.error || 'Entry payment failed');
    }
    
  } catch (error) {
    console.error('Entry payment error:', error);
    showMessage('Entry payment failed: ' + (error.message || 'Unknown error'));
  } finally {
    const payBtn = document.getElementById('entry-pay');
    if (payBtn) {
      payBtn.disabled = false;
      payBtn.textContent = `Pay ${entryPrice} SOL`;
    }
  }
}

// Setup battle modal event handlers
document.addEventListener('DOMContentLoaded', () => {
  // Confirm button
  document.addEventListener('click', (e) => {
    if (e.target.id === 'battle-confirm') {
      submitBattleCards();
    }
  });
});

// Setup scroll animations for jackpot blocks
function setupJackpotAnimations() {
  const jackpotSection = document.getElementById('rtj-section');
  const lastJackpotSection = document.getElementById('lastj-section');
  const superJackpotSection = document.getElementById('superj-section');
  
  if (!jackpotSection || !lastJackpotSection || !superJackpotSection) return;
  
  // Only apply animations on home page
  if (!document.body.classList.contains('page-home')) return;
  
  let lastScrollY = window.scrollY;
  let isScrollingDown = true;
  
  function handleScroll() {
    const currentScrollY = window.scrollY;
    const windowHeight = window.innerHeight;
    
    // Determine scroll direction
    isScrollingDown = currentScrollY > lastScrollY;
    lastScrollY = currentScrollY;
    
    // Get element positions
    const jackpotRect = jackpotSection.getBoundingClientRect();
    const lastJackpotRect = lastJackpotSection.getBoundingClientRect();
    const superJackpotRect = superJackpotSection.getBoundingClientRect();
    
    // Simple appearance animation - when elements come into view
    if (jackpotRect.top < windowHeight * 0.8 && jackpotRect.bottom > 0) {
      jackpotSection.classList.add('animate-in');
      jackpotSection.classList.remove('animate-out');
    } else {
      jackpotSection.classList.add('animate-out');
      jackpotSection.classList.remove('animate-in');
    }
    
    if (lastJackpotRect.top < windowHeight * 0.8 && lastJackpotRect.bottom > 0) {
      lastJackpotSection.classList.add('animate-in');
      lastJackpotSection.classList.remove('animate-out');
    } else {
      lastJackpotSection.classList.add('animate-out');
      lastJackpotSection.classList.remove('animate-in');
    }
    
    if (superJackpotRect.top < windowHeight * 0.8 && superJackpotRect.bottom > 0) {
      superJackpotSection.classList.add('animate-in');
      superJackpotSection.classList.remove('animate-out');
    } else {
      superJackpotSection.classList.add('animate-out');
      superJackpotSection.classList.remove('animate-in');
    }
  }
  
  // Initial check
  handleScroll();
  
  // Add scroll listener with throttling for smooth performance
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (!ticking) {
      requestAnimationFrame(() => {
        handleScroll();
        ticking = false;
      });
      ticking = true;
    }
  });
  
  // Reset animations on page change
  const originalShowPage = window.showPage;
  if (originalShowPage) {
    window.showPage = function(pageId) {
      // Remove animation classes
      jackpotSection.classList.remove('animate-in', 'animate-out');
      lastJackpotSection.classList.remove('animate-in', 'animate-out');
      superJackpotSection.classList.remove('animate-in', 'animate-out');
      
      // Call original function
      return originalShowPage.call(this, pageId);
    };
  }
}

// Setup scroll animations for How to Play section
function setupHowToPlayAnimations() {
  const howToPlaySection = document.getElementById('how-to-play-section');
  
  if (!howToPlaySection) return;
  
  function handleScroll() {
    const windowHeight = window.innerHeight;
    const howToPlayRect = howToPlaySection.getBoundingClientRect();
    
    // Check if section is in view (80% of viewport)
    if (howToPlayRect.top < windowHeight * 0.8 && howToPlayRect.bottom > 0) {
      howToPlaySection.classList.add('animate-in');
    } else {
      howToPlaySection.classList.remove('animate-in');
    }
  }
  
  // Initial check
  handleScroll();
  
  // Add scroll listener with throttling for smooth performance
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (!ticking) {
      requestAnimationFrame(() => {
        handleScroll();
        ticking = false;
      });
      ticking = true;
    }
  });
}

// Setup scroll animations for Distribution section
function setupDistributionAnimations() {
  const distributionSection = document.getElementById('distribution-section');
  
  if (!distributionSection) return;
  
  function handleScroll() {
    const windowHeight = window.innerHeight;
    const distributionRect = distributionSection.getBoundingClientRect();
    
    // Check if section is in view (80% of viewport)
    if (distributionRect.top < windowHeight * 0.8 && distributionRect.bottom > 0) {
      distributionSection.classList.add('animate-in');
    } else {
      distributionSection.classList.remove('animate-in');
    }
  }
  
  // Initial check
  handleScroll();
  
  // Add scroll listener with throttling for smooth performance
  let ticking = false;
  window.addEventListener('scroll', () => {
    if (!ticking) {
      requestAnimationFrame(() => {
        handleScroll();
        ticking = false;
      });
      ticking = true;
    }
  });
}


