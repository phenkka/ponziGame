// Dynamic Menu JavaScript - МАКСИМАЛЬНО ПРОСТАЯ ВЕРСИЯ
document.addEventListener('DOMContentLoaded', function() {
    const dynamicMenuContainer = document.querySelector('.dynamic-menu-container');
    const dynamicMenuTrigger = document.querySelector('.dynamic-menu-trigger');
    const dynamicMenu = document.querySelector('.dynamic-menu');
    const dynamicMenuOverlay = document.querySelector('.dynamic-menu-overlay');
    const menuItems = document.querySelectorAll('.dynamic-menu-item');
    
    if (!dynamicMenuContainer || !dynamicMenuTrigger || !dynamicMenu) {
        console.error('Меню не найдено!');
        return;
    }
    
    let isMenuOpen = false;
    let clickBlocked = false; // Блокировка открытия после клика
    
    // Открыть меню
    function openMenu() {
        if (isMenuOpen || clickBlocked) return;
        isMenuOpen = true;
        dynamicMenuContainer.classList.add('menu-open');
        
        // Принудительно показываем меню через inline стили
        dynamicMenu.style.transform = 'translateX(0)';
        if (dynamicMenuOverlay) {
            dynamicMenuOverlay.style.opacity = '1';
            dynamicMenuOverlay.style.visibility = 'visible';
            dynamicMenuOverlay.style.pointerEvents = 'auto'; // Делаем overlay кликабельным
        }
        
        dynamicMenuTrigger.style.animation = 'none';
        menuItems.forEach((item, index) => {
            item.style.animationDelay = `${index * 0.1}s`;
            item.classList.add('animate-in');
        });
    }
    
    // Закрыть меню
    function closeMenu() {
        if (!isMenuOpen) return;
        isMenuOpen = false;
        dynamicMenuContainer.classList.remove('menu-open');
        
        // Принудительно скрываем меню через inline стили
        dynamicMenu.style.transform = 'translateX(-100%)';
        if (dynamicMenuOverlay) {
            dynamicMenuOverlay.style.opacity = '0';
            dynamicMenuOverlay.style.visibility = 'hidden';
            dynamicMenuOverlay.style.pointerEvents = 'none'; // Отключаем кликабельность overlay
        }
        
        dynamicMenuTrigger.style.animation = 'menuPulse 3s ease-in-out infinite';
        menuItems.forEach(item => {
            item.classList.remove('animate-in');
        });
    }
    
    // 1. Открытие/закрытие при клике на trigger (для мобильных и десктопа)
    dynamicMenuTrigger.addEventListener('click', function(e) {
        e.stopPropagation(); // Предотвращаем всплытие события
        if (isMenuOpen) {
            closeMenu();
        } else {
            if (!clickBlocked) {
                openMenu();
            }
        }
    });
    
    // 2. Открытие при наведении на trigger (для десктопа)
    dynamicMenuTrigger.addEventListener('mouseenter', function() {
        if (!clickBlocked && !isMenuOpen) {
            openMenu();
        }
    });
    
    // 3. Закрытие при уходе с меню (для десктопа)
    dynamicMenu.addEventListener('mouseleave', function(e) {
        if (!dynamicMenuTrigger.contains(e.relatedTarget)) {
            closeMenu();
        }
    });
    
    // 4. Закрытие при уходе с trigger (для десктопа)
    dynamicMenuTrigger.addEventListener('mouseleave', function(e) {
        if (isMenuOpen && !dynamicMenu.contains(e.relatedTarget)) {
            closeMenu();
        }
    });
    
    // 5. ЗАКРЫТИЕ ПРИ КЛИКЕ НА OVERLAY (пустую зону)
    if (dynamicMenuOverlay) {
        dynamicMenuOverlay.addEventListener('click', function(e) {
            e.stopPropagation();
            closeMenu();
        });
    }
    
    // 6. ЗАКРЫТИЕ ПРИ КЛИКЕ ВНЕ МЕНЮ (на пустую зону)
    document.addEventListener('click', function(e) {
        if (!isMenuOpen) return;
        
        // Если клик на overlay - закрываем
        if (e.target === dynamicMenuOverlay) {
            closeMenu();
            return;
        }
        
        // Если клик НЕ внутри меню и НЕ на триггере - закрываем
        if (!dynamicMenu.contains(e.target) && !dynamicMenuTrigger.contains(e.target) && e.target !== dynamicMenuOverlay) {
            clickBlocked = true; // Блокируем открытие
            closeMenu();
            
            // Разблокируем через 500мс
            setTimeout(() => {
                clickBlocked = false;
            }, 500);
        }
    }, true); // Capture phase - срабатывает первым
    
    // Эффекты для элементов меню
    menuItems.forEach(item => {
        item.addEventListener('click', function(e) {
            // Предотвращаем закрытие меню при клике на сам элемент меню
            // Меню закроется только после перехода на другую страницу
            // Не закрываем сразу, чтобы пользователь мог видеть, что клик прошел
            setTimeout(() => closeMenu(), 300);
        });
        
        item.addEventListener('mouseenter', function() {
            this.style.transform = 'translateX(10px) scale(1.02)';
        });
        
        item.addEventListener('mouseleave', function() {
            this.style.transform = 'translateX(0) scale(1)';
        });
    });
    
    // Клавиатура
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && isMenuOpen) {
            closeMenu();
        }
    });
});

// CSS анимации
const styleSheet = document.createElement('style');
styleSheet.textContent = `
    .dynamic-menu-container.menu-open .dynamic-menu {
        transform: translateX(0);
    }
    
    .dynamic-menu-container.menu-open .dynamic-menu-overlay {
        opacity: 1;
        visibility: visible;
    }
    
    .dynamic-menu-item.animate-in {
        animation: slideInLeft 0.3s ease forwards;
    }
    
    .dynamic-menu-trigger {
        transition: all 0.3s ease;
    }
    
    .dynamic-menu-item {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
`;
document.head.appendChild(styleSheet);
