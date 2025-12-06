// Battle system JavaScript
let currentBattle = null;
let userCards = [];
let selectedCards = [];
let searchEndTime = null;

// Initialize battle page
function initBattle() {
    const startBtn = document.getElementById('start-battle-btn');
    if (startBtn) {
        startBtn.addEventListener('click', openBattleModal);
    }
    
    const startBtnModal = document.getElementById('start-battle-btn-modal');
    if (startBtnModal) {
        startBtnModal.addEventListener('click', startBattle);
    }
    
    const modalClose = document.getElementById('battle-modal-close');
    if (modalClose) {
        modalClose.addEventListener('click', closeBattleModal);
    }
    
    const battleModalOverlay = document.getElementById('battle-modal-overlay');
    if (battleModalOverlay) {
        battleModalOverlay.addEventListener('click', (e) => {
            if (e.target === battleModalOverlay) {
                closeBattleModal();
            }
        });
    }
    
    const confirmBtn = document.getElementById('confirm-cards-btn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', confirmCardSelection);
    }
    
    const cancelBtn = document.getElementById('cancel-battle-btn');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', cancelBattle);
    }
    
    const newBattleBtn = document.getElementById('new-battle-btn');
    if (newBattleBtn) {
        newBattleBtn.addEventListener('click', () => {
            resetBattle();
            showBattleState('initial');
        });
    }
    
    const closeBattleBtn = document.getElementById('close-battle-btn');
    if (closeBattleBtn) {
        closeBattleBtn.addEventListener('click', () => {
            closeBattleModal();
        });
    }
}

// Open battle modal
function openBattleModal() {
    const overlay = document.getElementById('battle-modal-overlay');
    const modal = document.getElementById('battle-modal');
    if (overlay && modal) {
        overlay.classList.remove('hidden');
        modal.classList.remove('hidden');
        showBattleState('initial');
        // Блокируем скролл фона при открытой модалке
        document.body.style.overflow = 'hidden';
    }
}

// Close battle modal
function closeBattleModal() {
    const overlay = document.getElementById('battle-modal-overlay');
    const modal = document.getElementById('battle-modal');
    if (overlay && modal) {
        overlay.classList.add('hidden');
        modal.classList.add('hidden');
        resetBattle();
        // Восстанавливаем скролл фона при закрытии модалки
        document.body.style.overflow = '';
    }
}

// Start battle - begin searching
async function startBattle() {
    const wallet = sessionStorage.getItem('wallet');
    if (!wallet) {
        showMessage('Please connect your wallet first', 'error');
        return;
    }
    
    try {
        // Get auth headers
        const headers = await getAuthHeaders();
        
        const response = await fetch('/api/battle/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...headers
            },
            body: JSON.stringify({ wallet })
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentBattle = {
                id: data.battle_id,
                status: data.status,
                searchDuration: data.search_duration
            };
            
            // Switch to searching state
            showBattleState('searching');
            
            // Auto-finish search after duration (no timer display)
            setTimeout(() => {
                finishSearch();
            }, data.search_duration * 1000);
        } else {
            showMessage(data.error || 'Failed to start battle', 'error');
            // If error, close modal
            if (data.error && (data.error.includes("don't have any cards") || data.error.includes("active battle"))) {
                closeBattleModal();
            }
        }
    } catch (error) {
        showMessage('Failed to start battle', 'error');
    }
}

// Timer removed - no countdown display

// Finish search and move to card selection
async function finishSearch() {
    if (!currentBattle) return;
    
    const wallet = sessionStorage.getItem('wallet');
    if (!wallet) return;
    
    try {
        const headers = await getAuthHeaders();
        
        const response = await fetch('/api/battle/finish-search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...headers
            },
            body: JSON.stringify({
                wallet,
                battle_id: currentBattle.id
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentBattle.status = 'card_selection';
            
            // Убеждаемся, что модальное окно открыто
            const modal = document.getElementById('battle-modal');
            const overlay = document.getElementById('battle-modal-overlay');
            if (modal && modal.classList.contains('hidden')) {
                modal.classList.remove('hidden');
            }
            if (overlay && overlay.classList.contains('hidden')) {
                overlay.classList.remove('hidden');
            }
            
            showBattleState('card_selection');
            loadUserCardsForBattle();
        } else {
            showMessage(data.error || 'Failed to finish search', 'error');
        }
    } catch (error) {
        showMessage('Failed to finish search', 'error');
    }
}

// Load user cards for selection
async function loadUserCardsForBattle() {
    const wallet = sessionStorage.getItem('wallet');
    if (!wallet) return;
    
    try {
        const headers = await getAuthHeaders();
        
        const response = await fetch(`/api/user/${wallet}/cards`, {
            headers
        });
        
        const data = await response.json();
        
        if (data.success && data.cards) {
            userCards = data.cards.filter(c => c.quantity > 0);
            renderCardSelection();
        } else {
            showMessage('Failed to load cards', 'error');
        }
    } catch (error) {
        showMessage('Failed to load cards', 'error');
    }
}

// Render card selection grid
function renderCardSelection() {
    const grid = document.getElementById('cards-selection-grid');
    if (!grid) return;
    
    grid.innerHTML = '';
    selectedCards = [];
    updateSelectedCardsPreview();
    
    if (userCards.length === 0) {
        grid.innerHTML = '<p style="color: #fff; text-align: center; padding: 20px;">No cards available</p>';
        return;
    }
    
    userCards.forEach(card => {
        const cardEl = document.createElement('div');
        cardEl.className = 'battle-card-selectable';
        cardEl.dataset.cardId = card.id_card;
        
        const img = card.image_url ? `<img src="${card.image_url}" alt="${card.name || 'Card'}">` : '';
        const name = card.name || `Card #${card.id_card}`;
        const tickets = card.start_bounty || 0;
        const quantity = card.quantity || 0;
        
        cardEl.innerHTML = `
            ${img}
            <div class="card-name">${name}</div>
            <div class="card-tickets">${tickets} tickets</div>
            <div class="card-quantity">Available: ${quantity}</div>
        `;
        
        cardEl.addEventListener('click', () => toggleCardSelection(card));
        grid.appendChild(cardEl);
    });
}

// Toggle card selection
function toggleCardSelection(card) {
    const index = selectedCards.findIndex(c => c.id_card === card.id_card);
    
    if (index >= 0) {
        // Deselect
        selectedCards.splice(index, 1);
        const cardEl = document.querySelector(`[data-card-id="${card.id_card}"]`);
        if (cardEl) cardEl.classList.remove('selected');
    } else {
        // Select (max 5 cards)
        if (selectedCards.length >= 5) {
            showMessage('You can select maximum 5 cards', 'error');
            return;
        }
        
        // Check if user has this card
        const userCard = userCards.find(c => c.id_card === card.id_card);
        if (!userCard || userCard.quantity <= 0) {
            showMessage('You don\'t have this card', 'error');
            return;
        }
        
        selectedCards.push({
            id_card: card.id_card,
            quantity: 1,
            name: card.name,
            image_url: card.image_url,
            start_bounty: card.start_bounty
        });
        
        const cardEl = document.querySelector(`[data-card-id="${card.id_card}"]`);
        if (cardEl) cardEl.classList.add('selected');
    }
    
    updateSelectedCardsPreview();
}

// Update selected cards preview
function updateSelectedCardsPreview() {
    const preview = document.getElementById('selected-cards-list');
    const ticketsEl = document.getElementById('selected-tickets');
    const confirmBtn = document.getElementById('confirm-cards-btn');
    
    if (!preview) return;
    
    preview.innerHTML = '';
    
    let totalTickets = 0;
    
    selectedCards.forEach(card => {
        totalTickets += (card.start_bounty || 0) * card.quantity;
        
        const cardItem = document.createElement('div');
        cardItem.className = 'selected-card-item';
        
        const img = card.image_url ? `<img src="${card.image_url}" alt="${card.name}">` : '';
        cardItem.innerHTML = `
            ${img}
            <div>
                <div>${card.name || `Card #${card.id_card}`}</div>
                <div style="font-size: 12px; opacity: 0.7;">${card.start_bounty} tickets</div>
            </div>
        `;
        
        preview.appendChild(cardItem);
    });
    
    if (ticketsEl) {
        ticketsEl.textContent = totalTickets;
    }
    
    if (confirmBtn) {
        confirmBtn.disabled = selectedCards.length === 0 || selectedCards.length > 5;
    }
}

// Confirm card selection
async function confirmCardSelection() {
    if (selectedCards.length === 0 || selectedCards.length > 5) {
        showMessage('Please select 1-5 cards', 'error');
        return;
    }
    
    if (!currentBattle) {
        showMessage('No active battle', 'error');
        return;
    }
    
    const wallet = sessionStorage.getItem('wallet');
    if (!wallet) return;
    
    try {
        const headers = await getAuthHeaders();
        
        const response = await fetch('/api/battle/select-cards', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...headers
            },
            body: JSON.stringify({
                wallet,
                battle_id: currentBattle.id,
                cards: selectedCards
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentBattle.status = 'fighting';
            currentBattle.userTickets = data.user_tickets;
            currentBattle.opponentTickets = data.opponent_tickets;
            
            showBattleState('fighting');
            startFight();
        } else {
            showMessage(data.error || 'Failed to select cards', 'error');
        }
    } catch (error) {
        showMessage('Failed to select cards', 'error');
    }
}

// Start fight
async function startFight() {
    if (!currentBattle) return;
    
    const wallet = sessionStorage.getItem('wallet');
    if (!wallet) return;
    
    // Show user and opponent tickets
    const userTicketsEl = document.getElementById('user-battle-tickets');
    const opponentTicketsEl = document.getElementById('opponent-battle-tickets');
    
    if (userTicketsEl) userTicketsEl.textContent = currentBattle.userTickets || 0;
    if (opponentTicketsEl) opponentTicketsEl.textContent = currentBattle.opponentTickets || 0;
    
    // Show cards
    renderBattleCards();
    
    // Wait a bit for animation, then fight
    setTimeout(async () => {
        await fightBattle();
    }, 2000);
}

// Render battle cards
function renderBattleCards() {
    const userCardsEl = document.getElementById('user-battle-cards');
    const opponentCardsEl = document.getElementById('opponent-battle-cards');
    
    if (userCardsEl) {
        userCardsEl.innerHTML = '';
        selectedCards.forEach(card => {
            if (card.image_url) {
                const img = document.createElement('img');
                img.src = card.image_url;
                img.className = 'battle-card-mini';
                img.alt = card.name || 'Card';
                userCardsEl.appendChild(img);
            }
        });
    }
    
    // Opponent cards will be shown after fight
    if (opponentCardsEl) {
        opponentCardsEl.innerHTML = '<div>Loading...</div>';
    }
}

// Fight battle
async function fightBattle() {
    if (!currentBattle) return;
    
    const wallet = sessionStorage.getItem('wallet');
    if (!wallet) return;
    
    try {
        const headers = await getAuthHeaders();
        
        const response = await fetch('/api/battle/fight', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...headers
            },
            body: JSON.stringify({
                wallet,
                battle_id: currentBattle.id
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showBattleResult(data);
        } else {
            showMessage(data.error || 'Failed to fight', 'error');
        }
    } catch (error) {
        showMessage('Failed to fight', 'error');
    }
}

// Show battle result
function showBattleResult(data) {
    // Переключаемся на этап результата
    showBattleState('result');
    
    const resultTextEl = document.getElementById('battle-result-text');
    const rewardsEl = document.getElementById('battle-rewards');
    const userTicketsEl = document.getElementById('user-result-tickets');
    const opponentTicketsEl = document.getElementById('opponent-result-tickets');
    const userCardsEl = document.getElementById('user-result-cards');
    const opponentCardsEl = document.getElementById('opponent-result-cards');
    
    if (!resultTextEl) return;
    
    const isVictory = data.winner === 'user';
    
    resultTextEl.textContent = isVictory ? 'VICTORY!' : 'DEFEAT!';
    resultTextEl.className = isVictory ? 'victory' : 'defeat';
    
    // Показываем билеты
    if (userTicketsEl) userTicketsEl.textContent = data.user_tickets || 0;
    if (opponentTicketsEl) opponentTicketsEl.textContent = data.opponent_tickets || 0;
    
    // Показываем карты пользователя
    if (userCardsEl && data.user_cards) {
        userCardsEl.innerHTML = '';
        data.user_cards.forEach(card => {
            if (card.image_url) {
                const img = document.createElement('img');
                img.src = card.image_url;
                img.className = 'battle-card-mini';
                img.alt = card.name || 'Card';
                userCardsEl.appendChild(img);
            }
        });
    }
    
    // Показываем карты противника
    if (opponentCardsEl && data.opponent_cards) {
        opponentCardsEl.innerHTML = '';
        data.opponent_cards.forEach(card => {
            if (card.image_url) {
                const img = document.createElement('img');
                img.src = card.image_url;
                img.className = 'battle-card-mini';
                img.alt = card.name || 'Card';
                opponentCardsEl.appendChild(img);
            }
        });
    }
    
    // Показываем награды
    if (rewardsEl) {
        rewardsEl.innerHTML = '';
        
        if (isVictory && data.cards_won && data.cards_won.length > 0) {
            const h4 = document.createElement('h4');
            h4.textContent = 'Cards Won:';
            h4.style.color = '#ffffff';
            h4.style.marginTop = '20px';
            rewardsEl.appendChild(h4);
            
            const grid = document.createElement('div');
            grid.className = 'rewards-grid';
            
            data.cards_won.forEach(card => {
                const cardEl = document.createElement('div');
                cardEl.className = 'reward-card';
                
                const img = card.image_url ? `<img src="${card.image_url}" alt="${card.name || 'Card'}">` : '';
                const name = card.name || `Card #${card.id_card}`;
                
                cardEl.innerHTML = `
                    ${img}
                    <div class="reward-card-name">${name}</div>
                `;
                
                grid.appendChild(cardEl);
            });
            
            rewardsEl.appendChild(grid);
        } else if (!isVictory && data.cards_lost && data.cards_lost.length > 0) {
            const p = document.createElement('p');
            p.textContent = `You lost ${data.cards_lost.length} card(s)`;
            p.style.color = '#ffffff';
            p.style.opacity = '0.7';
            p.style.marginTop = '20px';
            rewardsEl.appendChild(p);
        }
    }
}

// Cancel battle
async function cancelBattle() {
    if (!currentBattle) {
        closeBattleModal();
        return;
    }
    
    if (confirm('Are you sure you want to cancel this battle?')) {
        const wallet = sessionStorage.getItem('wallet');
        if (!wallet) {
            closeBattleModal();
            return;
        }
        
        try {
            const headers = await getAuthHeaders();
            
            const response = await fetch('/api/battle/cancel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...headers
                },
                body: JSON.stringify({
                    wallet,
                    battle_id: currentBattle.id
                })
            });
            
            const data = await response.json();
            if (data.success) {
                closeBattleModal();
            } else {
                showMessage(data.error || 'Failed to cancel battle', 'error');
            }
        } catch (error) {
            showMessage('Failed to cancel battle', 'error');
            // Все равно закрываем модальное окно
            closeBattleModal();
        }
    }
}

// Reset battle to initial state
function resetBattle() {
    currentBattle = null;
    selectedCards = [];
    userCards = [];
    
    searchEndTime = null;
    
    showBattleState('initial');
}

// Show specific battle state
function showBattleState(state) {
    const states = ['initial', 'searching', 'card_selection', 'fighting', 'result'];
    
    states.forEach(s => {
        // Преобразуем подчеркивания в дефисы для ID элементов
        const elementId = `battle-${s.replace(/_/g, '-')}`;
        const el = document.getElementById(elementId);
        if (el) {
            if (s === state) {
                el.classList.remove('hidden');
            } else {
                el.classList.add('hidden');
            }
        }
    });
}

// Get auth headers
async function getAuthHeaders() {
    const wallet = sessionStorage.getItem('wallet');
    if (!wallet) return {};
    
    // Try to get from cookie first
    const authToken = document.cookie.split('; ').find(row => row.startsWith('auth_token='));
    if (authToken) {
        return {};
    }
    
    // Otherwise, we need signature headers (handled by middleware)
    return {};
}

// Initialize on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBattle);
} else {
    initBattle();
}

