// Predict page JavaScript

let selectedOutcome = null;
let currentPredictionId = null;

// Загрузка пари
async function loadPredictions(forceRefresh = false) {
    console.log('=== loadPredictions() called ===', { forceRefresh });
    
    try {
        const container = document.getElementById('predict-content');
        if (!container) {
            console.error('Predict content container not found!');
            return;
        }
        
        console.log('Container found, setting loading message...');
        container.innerHTML = '<div style="color: white; text-align: center; padding: 20px;">Loading predictions...</div>';
        
        // Добавляем параметр force_refresh для принудительной загрузки из Polymarket
        const url = `/api/predictions/markets?period=24h&limit=20${forceRefresh ? '&force_refresh=true' : ''}`;
        console.log('Fetching predictions from:', url);
        
        // Подготавливаем заголовки для авторизации (если пользователь авторизован)
        const headers = {};
        const wallet = currentWallet || sessionStorage.getItem('wallet') || localStorage.getItem('wallet');
        const signature = sessionStorage.getItem('signature') || '';
        const message = sessionStorage.getItem('message') || '';
        
        if (wallet && signature && message) {
            headers['X-Wallet'] = wallet;
            headers['X-Signature'] = signature;
            headers['X-Message'] = message;
        }
        
        const response = await fetch(url, {
            headers: headers,
            credentials: 'include'
        });
        console.log('Predictions API response status:', response.status);
        
        if (!response.ok) {
            console.error('Predictions API error:', response.status, response.statusText);
            container.innerHTML = `<div style="color: #f44336; text-align: center; padding: 20px;">Error loading predictions: ${response.status} ${response.statusText}</div>`;
            return;
        }
        
        const data = await response.json();
        console.log('Predictions API data:', data);
        console.log('Predictions API data.success:', data.success);
        console.log('Predictions API data.markets:', data.markets);
        console.log('Predictions API data.markets length:', data.markets ? data.markets.length : 0);
        
        if (!data.success) {
            console.error('Predictions API returned error:', data.error || 'Unknown error');
            container.innerHTML = `<div style="color: #f44336; text-align: center; padding: 20px;">Error: ${data.error || 'Failed to load predictions'}</div>`;
            return;
        }
        
        if (!data.markets || data.markets.length === 0) {
            console.warn('No markets in response. Response data:', JSON.stringify(data));
            console.warn('No markets in response, trying to load from Polymarket...');
            // Попробуем принудительно загрузить из Polymarket
            if (!forceRefresh) {
                console.log('Retrying with force_refresh=true');
                await loadPredictions(true);
                return;
            }
            container.innerHTML = '<div style="color: #9aa0a6; text-align: center; padding: 20px;">No predictions available at the moment. Please try again later.</div>';
            return;
        }
        
        console.log(`Loaded ${data.markets.length} predictions`);
        
        container.innerHTML = '';
        
        data.markets.forEach(market => {
            const marketCard = document.createElement('div');
            marketCard.className = 'predict-market-card';
            marketCard.setAttribute('data-prediction-id', market.id_prediction);
            
            // Определяем цвет карточки на основе вероятностей (зеленый если близко к 50/50, красный если далеко)
            const probDiff = Math.abs(market.outcome_a_probability - 50);
            const cardColorClass = probDiff <= 5 ? 'predict-card-green' : probDiff <= 10 ? 'predict-card-yellow' : 'predict-card-red';
            
            marketCard.classList.add(cardColorClass);
            
            marketCard.innerHTML = `
                <div class="predict-market-title">${escapeHtml(market.title)}</div>
                ${market.description ? `<div class="predict-market-description">${escapeHtml(market.description)}</div>` : ''}
                <div class="predict-market-outcomes">
                    <button class="predict-outcome-btn" data-outcome="A" data-prediction-id="${market.id_prediction}">
                        <div>${escapeHtml(market.outcome_a)}</div>
                        <div class="predict-outcome-probability" data-outcome="A">${market.outcome_a_probability.toFixed(1)}%</div>
                    </button>
                    <button class="predict-outcome-btn" data-outcome="B" data-prediction-id="${market.id_prediction}">
                        <div>${escapeHtml(market.outcome_b)}</div>
                        <div class="predict-outcome-probability" data-outcome="B">${market.outcome_b_probability.toFixed(1)}%</div>
                    </button>
                </div>
                <button class="predict-bet-btn" data-prediction-id="${market.id_prediction}" disabled>
                    PLACE BET
                </button>
            `;
            
            container.appendChild(marketCard);
        });
        
        // Добавляем обработчики для кнопок исходов
        document.querySelectorAll('.predict-outcome-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                // Убираем выделение с других кнопок в этой карточке
                const card = this.closest('.predict-market-card');
                card.querySelectorAll('.predict-outcome-btn').forEach(b => {
                    b.classList.remove('selected');
                });
                
                // Выделяем выбранную кнопку
                this.classList.add('selected');
                
                // Активируем кнопку ставки
                const betBtn = card.querySelector('.predict-bet-btn');
                betBtn.disabled = false;
                betBtn.dataset.outcome = this.dataset.outcome;
            });
        });
        
        // Добавляем обработчики для кнопок ставок
        document.querySelectorAll('.predict-bet-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                if (this.disabled) return;
                
                const predictionId = parseInt(this.dataset.predictionId);
                const outcome = this.dataset.outcome;
                
                if (!predictionId || !outcome) return;
                
                await placeBet(predictionId, outcome, this);
            });
        });
        
    } catch (error) {
        console.error('Error loading predictions:', error);
        const container = document.getElementById('predict-content');
        if (container) {
            container.innerHTML = '<div style="color: #f44336; text-align: center; padding: 20px;">Error loading predictions. Please try again later.</div>';
        }
    }
}

// Размещение ставки
async function placeBet(predictionId, outcome, buttonElement) {
    try {
        const wallet = currentWallet || sessionStorage.getItem('wallet') || localStorage.getItem('wallet');
        if (!wallet) {
            showMessage('Please connect your wallet first', 'error');
            return;
        }
        
        buttonElement.disabled = true;
        buttonElement.textContent = 'Processing...';
        
        const headers = {
            'Content-Type': 'application/json'
        };
        
        const signature = sessionStorage.getItem('signature') || '';
        const message = sessionStorage.getItem('message') || '';
        
        if (signature && message) {
            headers['X-Wallet'] = wallet;
            headers['X-Signature'] = signature;
            headers['X-Message'] = message;
        }
        
        const response = await fetch(`/api/predictions/bet/${wallet}`, {
            method: 'POST',
            headers: headers,
            credentials: 'include',
            body: JSON.stringify({
                prediction_id: predictionId,
                chosen_outcome: outcome
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showMessage('Bet placed successfully!', 'success');
            // Перезагружаем пари и ставки
            await loadPredictions();
            await loadUserBets();
        } else {
            showMessage(data.error || 'Failed to place bet', 'error');
            buttonElement.disabled = false;
            buttonElement.textContent = 'PLACE BET';
        }
        
    } catch (error) {
        console.error('Error placing bet:', error);
        showMessage('Error placing bet. Please try again.', 'error');
        buttonElement.disabled = false;
        buttonElement.textContent = 'PLACE BET';
    }
}

// Загрузка ставок пользователя
async function loadUserBets() {
    try {
        const wallet = currentWallet || sessionStorage.getItem('wallet') || localStorage.getItem('wallet');
        if (!wallet) return;
        
        const container = document.getElementById('predict-bets-content');
        if (!container) return;
        
        const headers = {};
        const signature = sessionStorage.getItem('signature') || '';
        const message = sessionStorage.getItem('message') || '';
        
        if (signature && message) {
            headers['X-Wallet'] = wallet;
            headers['X-Signature'] = signature;
            headers['X-Message'] = message;
        }
        
        const response = await fetch(`/api/predictions/user/${wallet}`, {
            headers: headers,
            credentials: 'include'
        });
        
        const data = await response.json();
        
        if (!data.success || !data.bets || data.bets.length === 0) {
            container.innerHTML = '<div style="color: #9aa0a6; text-align: center; padding: 20px;">No bets yet</div>';
            return;
        }
        
        container.innerHTML = '';
        
        data.bets.forEach(bet => {
            const betCard = document.createElement('div');
            betCard.className = 'predict-bet-card';
            
            // Определяем цвет карточки на основе статуса
            let cardColorClass = 'predict-card-gray'; // По умолчанию серый
            if (bet.status === 'won') {
                cardColorClass = 'predict-card-green';
            } else if (bet.status === 'lost') {
                cardColorClass = 'predict-card-red';
            } else if (bet.status === 'pending') {
                cardColorClass = 'predict-card-yellow';
            } else if (bet.status === 'cancelled') {
                cardColorClass = 'predict-card-gray';
            }
            
            betCard.classList.add(cardColorClass);
            
            const statusClass = bet.status.toLowerCase();
            const statusText = bet.status.charAt(0).toUpperCase() + bet.status.slice(1);
            
            const chosenText = bet.chosen_outcome === 'A' ? bet.outcome_a : bet.outcome_b;
            
            betCard.innerHTML = `
                <div class="predict-bet-title">${escapeHtml(bet.title)}</div>
                <div style="margin-top: 10px; color: #ffffff; font-size: 16px; font-weight: bold;">
                    Your bet: <span style="color: ${bet.status === 'won' ? '#4CAF50' : bet.status === 'lost' ? '#f44336' : '#FFC107'};">
                        ${escapeHtml(chosenText)}
                    </span>
                </div>
                <div style="margin-top: 15px;">
                    <span class="predict-bet-status ${statusClass}" style="
                        padding: 8px 16px;
                        border-radius: 4px;
                        font-weight: bold;
                        background: ${bet.status === 'won' ? '#4CAF50' : bet.status === 'lost' ? '#f44336' : bet.status === 'pending' ? '#FFC107' : '#9aa0a6'};
                        color: #000;
                    ">${statusText}</span>
                    ${bet.reward_issued ? '<span style="color: #4CAF50; margin-left: 10px; font-weight: bold;">✓ Reward issued</span>' : ''}
                </div>
                <div style="margin-top: 15px; color: #9aa0a6; font-size: 12px;">
                    Placed: ${new Date(bet.created_at).toLocaleString()}
                    ${bet.resolved_at ? `<br>Resolved: ${new Date(bet.resolved_at).toLocaleString()}` : ''}
                </div>
                ${bet.prediction_status === 'resolved' && bet.winner_outcome ? `
                    <div style="margin-top: 10px; color: #ffffff; font-size: 14px;">
                        Winner: <strong>${bet.winner_outcome === 'A' ? bet.outcome_a : bet.outcome_b}</strong>
                    </div>
                ` : ''}
            `;
            
            container.appendChild(betCard);
        });
        
    } catch (error) {
        console.error('Error loading user bets:', error);
        const container = document.getElementById('predict-bets-content');
        if (container) {
            container.innerHTML = '<div style="color: #f44336; text-align: center; padding: 20px;">Error loading bets</div>';
        }
    }
}

// Обновление процентов в реальном времени
let probabilityUpdateInterval = null;

function startProbabilityUpdates() {
    // Обновляем проценты каждые 10 секунд
    if (probabilityUpdateInterval) {
        clearInterval(probabilityUpdateInterval);
    }
    
    probabilityUpdateInterval = setInterval(async () => {
        try {
            // Подготавливаем заголовки для авторизации
            const headers = {};
            const wallet = currentWallet || sessionStorage.getItem('wallet') || localStorage.getItem('wallet');
            const signature = sessionStorage.getItem('signature') || '';
            const message = sessionStorage.getItem('message') || '';
            
            if (wallet && signature && message) {
                headers['X-Wallet'] = wallet;
                headers['X-Signature'] = signature;
                headers['X-Message'] = message;
            }
            
            const response = await fetch(`/api/predictions/markets?period=24h&limit=20`, {
                headers: headers,
                credentials: 'include'
            });
            const data = await response.json();
            
            if (data.success && data.markets) {
                // Обновляем проценты для каждого пари
                data.markets.forEach(market => {
                    const card = document.querySelector(`.predict-market-card[data-prediction-id="${market.id_prediction}"]`);
                    if (card) {
                        // Обновляем процент для исхода A
                        const probA = card.querySelector('.predict-outcome-probability[data-outcome="A"]');
                        if (probA) {
                            probA.textContent = `${market.outcome_a_probability.toFixed(1)}%`;
                        }
                        
                        // Обновляем процент для исхода B
                        const probB = card.querySelector('.predict-outcome-probability[data-outcome="B"]');
                        if (probB) {
                            probB.textContent = `${market.outcome_b_probability.toFixed(1)}%`;
                        }
                        
                        // Обновляем цвет карточки на основе новых вероятностей
                        const probDiff = Math.abs(market.outcome_a_probability - 50);
                        card.classList.remove('predict-card-green', 'predict-card-yellow', 'predict-card-red');
                        if (probDiff <= 5) {
                            card.classList.add('predict-card-green');
                        } else if (probDiff <= 10) {
                            card.classList.add('predict-card-yellow');
                        } else {
                            card.classList.add('predict-card-red');
                        }
                    }
                });
            }
        } catch (error) {
            console.error('Error updating probabilities:', error);
        }
    }, 10000); // Обновляем каждые 10 секунд
}

function stopProbabilityUpdates() {
    if (probabilityUpdateInterval) {
        clearInterval(probabilityUpdateInterval);
        probabilityUpdateInterval = null;
    }
}

// Инициализация страницы predict
function initPredictPage() {
    console.log('=== initPredictPage() called from predict.js ===');
    
    // Помечаем, что инициализация началась
    window.predictPageInitialized = true;
    
    // Загружаем пари по умолчанию (с принудительным обновлением, если БД пуста)
    console.log('Loading initial predictions...');
    loadPredictions(true);
    
    // Загружаем ставки пользователя
    console.log('Loading user bets...');
    loadUserBets();
    
    // Запускаем обновление процентов в реальном времени
    console.log('Starting probability updates...');
    startProbabilityUpdates();
    
    // Запускаем обновление ставок пользователя
    const betsUpdateInterval = setInterval(async () => {
        if (currentWallet || sessionStorage.getItem('wallet')) {
            await loadUserBets();
        }
    }, 30000); // Обновляем каждые 30 секунд
    
    // Запускаем полное обновление активных пари (на случай добавления новых)
    const fullUpdateInterval = setInterval(async () => {
        await loadPredictions();
    }, 60000); // Полное обновление каждую минуту
    
    // Сохраняем intervals для очистки
    window.predictBetsUpdateInterval = betsUpdateInterval;
    window.predictFullUpdateInterval = fullUpdateInterval;
    
    console.log('=== initPredictPage() completed ===');
}

// Останавливаем обновления при уходе со страницы
window.addEventListener('beforeunload', () => {
    stopProbabilityUpdates();
    if (window.predictBetsUpdateInterval) {
        clearInterval(window.predictBetsUpdateInterval);
    }
    if (window.predictFullUpdateInterval) {
        clearInterval(window.predictFullUpdateInterval);
    }
});

// Вспомогательная функция для экранирования HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Экспортируем функции для использования в других скриптах
window.loadPredictions = loadPredictions;
window.loadUserBets = loadUserBets;
window.initPredictPage = initPredictPage;

// Автоматическая инициализация при загрузке DOM
// Если page-init.js не вызвал функцию, вызываем сами
(function() {
    function tryInit() {
        if (window.location.pathname === '/predict' || document.body.className.includes('page-predict')) {
            if (!window.predictPageInitialized) {
                console.log('predict.js: page-init.js did not call initPredictPage, calling directly');
                if (typeof initPredictPage === 'function') {
                    initPredictPage();
                } else if (typeof window.loadPredictions === 'function') {
                    console.log('predict.js: initPredictPage not found, calling loadPredictions directly');
                    window.loadPredictions(true);
                } else {
                    console.error('predict.js: Neither initPredictPage nor loadPredictions found!');
                }
            }
        }
    }
    
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            console.log('DOMContentLoaded - predict.js');
            setTimeout(tryInit, 2000); // Даем page-init.js время
        });
    } else {
        console.log('DOM already loaded - predict.js');
        setTimeout(tryInit, 2000);
    }
})();
