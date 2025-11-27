// Page initialization scripts
// This file contains the initialization logic for different pages

// Initialize shop page when loaded
function initShopPage() {
    console.log('Shop page loaded');
    
    // Check if user is authenticated
    const storedWallet = sessionStorage.getItem('wallet');
    console.log('Stored wallet:', storedWallet);
    
    if (storedWallet) {
        console.log('User is authenticated, loading shop content...');
        // User is authenticated, show user info and load shop content
        fetch(`/api/user/${storedWallet}`).then(r => r.json()).then(d => { 
            console.log('User data:', d);
            if (d && d.success) {
                console.log('Calling showUserInfo...');
                showUserInfo(storedWallet, d.user.ref_code, { redirect: false });
                
                // Small delay to ensure DOM is ready
                setTimeout(() => {
                    console.log('Calling loadPacks...');
                    if (typeof loadPacks === 'function') {
                        loadPacks(true); // Load shop packs
                    } else {
                        console.error('loadPacks function not found');
                    }
                    console.log('Calling loadUserChests...');
                    if (typeof loadUserChests === 'function') {
                        loadUserChests(storedWallet);
                    } else {
                        console.error('loadUserChests function not found');
                    }
                    console.log('Calling loadJackpot...');
                    if (typeof loadJackpot === 'function') {
                        loadJackpot();
                    } else {
                        console.error('loadJackpot function not found');
                    }
                }, 100);
            }
        }).catch(e => console.error('Error loading user data:', e));
    } else {
        console.log('User not authenticated, redirecting to home...');
        // User not authenticated, redirect to home
        window.location.href = '/';
    }
}

// Initialize battle page when loaded
function initBattlePage() {
    console.log('Battle page loaded');
    
    // Check if user is authenticated
    const storedWallet = sessionStorage.getItem('wallet');
    console.log('Stored wallet:', storedWallet);
    
    if (storedWallet) {
        // User is authenticated, show user info and load battle content
        fetch(`/api/user/${storedWallet}`).then(r => r.json()).then(d => { 
            console.log('User data:', d);
            if (d && d.success) {
                showUserInfo(storedWallet, d.user.ref_code, { redirect: false });
                loadJackpot(); // Load jackpot info
            }
        }).catch(e => console.error('Error loading user data:', e));
    } else {
        console.log('User not authenticated, redirecting to home...');
        // User not authenticated, redirect to home
        window.location.href = '/';
    }
}

// Initialize cards page when loaded
function initCardsPage() {
    console.log('Cards page loaded');
    
    // Check if user is authenticated
    const storedWallet = sessionStorage.getItem('wallet');
    console.log('Stored wallet:', storedWallet);
    
    if (storedWallet) {
        // User is authenticated, show user info and load cards content
        fetch(`/api/user/${storedWallet}`).then(r => r.json()).then(d => { 
            console.log('User data:', d);
            if (d && d.success) {
                showUserInfo(storedWallet, d.user.ref_code, { redirect: false });
                loadUserCards(storedWallet); // Load user cards
                loadMyPacks(storedWallet); // Load user packs
                loadJackpot(); // Load jackpot info
            }
        }).catch(e => console.error('Error loading user data:', e));
    } else {
        console.log('User not authenticated, redirecting to home...');
        // User not authenticated, redirect to home
        window.location.href = '/';
    }
}

// Initialize profile page when loaded
function initProfilePage() {
    console.log('Profile page loaded');
    
    // Check if user is authenticated
    const storedWallet = sessionStorage.getItem('wallet');
    console.log('Stored wallet:', storedWallet);
    
    if (storedWallet) {
        // User is authenticated, show user info and load referral content
        fetch(`/api/user/${storedWallet}`).then(r => r.json()).then(d => { 
            console.log('User data:', d);
            if (d && d.success) {
                showUserInfo(storedWallet, d.user.ref_code, { redirect: false });
                loadReferral(); // Load profile data
                loadJackpot(); // Load jackpot info
                // loadCashback() will be called from main.js when needed
            }
        }).catch(e => console.error('Error loading user data:', e));
    } else {
        console.log('User not authenticated, redirecting to home...');
        // User not authenticated, redirect to home
        window.location.href = '/';
    }
}

// Initialize rules page when loaded
function initRulesPage() {
    console.log('Rules page loaded');
    
    // Check if user is authenticated
    const storedWallet = sessionStorage.getItem('wallet');
    console.log('Stored wallet:', storedWallet);
    
    if (storedWallet) {
        // User is authenticated, show user info and load rules content
        fetch(`/api/user/${storedWallet}`).then(r => r.json()).then(d => { 
            console.log('User data:', d);
            if (d && d.success) {
                showUserInfo(storedWallet, d.user.ref_code, { redirect: false });
                loadJackpot(); // Load jackpot info
            }
        }).catch(e => console.error('Error loading user data:', e));
    } else {
        console.log('User not authenticated, redirecting to home...');
        // User not authenticated, redirect to home
        window.location.href = '/';
    }
}

// Auto-initialize based on current page
document.addEventListener('DOMContentLoaded', () => {
    const currentPage = document.body.className;
    const currentPath = window.location.pathname;
    
    // Determine page based on path or body class
    if (currentPath === '/shop' || currentPage.includes('page-shop')) {
        initShopPage();
    } else if (currentPath === '/battle' || currentPage.includes('page-battle')) {
        initBattlePage();
    } else if (currentPath === '/cards' || currentPage.includes('page-cards')) {
        initCardsPage();
    } else if (currentPath === '/profile' || currentPage.includes('page-profile')) {
        initProfilePage();
    } else if (currentPath === '/rules' || currentPage.includes('page-rules')) {
        initRulesPage();
    }
    
    // Initialize jackpot carousel for non-home pages
    initJackpotCarousel();
});

// Jackpot carousel auto-switch function
function initJackpotCarousel() {
    const carousel = document.querySelector('.jackpot-carousel');
    if (!carousel) return; // Only on non-home pages
    
    const items = carousel.querySelectorAll('.jackpot-carousel-item');
    let currentIndex = 0;
    
    // Switch every 5 seconds
    setInterval(() => {
        // Remove active class from current item
        items[currentIndex].classList.remove('active');
        
        // Move to next item
        currentIndex = (currentIndex + 1) % items.length;
        
        // Add active class to next item
        items[currentIndex].classList.add('active');
    }, 5000); // 5 seconds
}
